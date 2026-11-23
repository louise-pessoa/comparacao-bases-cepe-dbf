"""Leitura por OCR (Tesseract) de uma página de PDF: texto e confiança por palavra.

Em que etapa entra: leitura (etapas 1b e 3). Fase 1 do plano (docs/plano_ocr.md): este
módulo só lê. Quem decide se o texto do OCR vale (cascata de OCR, travas, comparação com
a base) são as fases seguintes; aqui não há regra de domínio.

Recebe uma página (`pymupdf.Page`) ou um PDF e o número da página; devolve `PaginaOcr`:
o texto (linhas na ordem do Tesseract, blocos separados por linha em branco), as palavras
com a confiança (0 a 100) e a versão do Tesseract que leu.

Decisões e limites:
  - a página é renderizada pelo PyMuPDF em tons de cinza (`AjustesOcr.dpi`, no máximo
    `config.DPI_MAXIMO`) e entregue ao pytesseract como imagem em memória; o pytesseract
    grava um PNG no diretório temporário do sistema e o apaga, nada fica no projeto;
  - o Tesseract roda por `subprocess` sem shell (pytesseract). O executável vem do .env
    (`TESSERACT_CMD`) e só é aceito se existir e se chamar "tesseract"/"tesseract.exe";
    o idioma é validado em `config.py`; nenhum texto do PDF vai para a linha de comando;
  - `timeout` por página: um PDF defeituoso não trava o lote;
  - cache em disco (`CacheOcr`), um JSON por (SHA-256 do PDF, página, ajustes, versão do
    Tesseract): o OCR leva segundos por página e a cascata relê o caderno. O cache é
    derivado: o arquivo ilegível é apagado e refeito. Gravação atômica (.tmp + os.replace);
  - o PDF é sempre aberto só para leitura (a base da DBF também passa por aqui);
  - `OCR_TESSDATA`: pasta de idiomas fora da instalação do Tesseract (sem administrador).
    Vai para o Tesseract pela variável TESSDATA_PREFIX, só durante a chamada; o SHA-256 dos
    .traineddata usados entra na chave do cache (outro modelo, outra leitura);
  - o modo de segmentação padrão é o 3 (automático, sem detecção de orientação): o 1
    precisaria também do osd.traineddata, e as páginas do Diário não vêm giradas.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

import pymupdf

from dbf_cepe.config import DPI_MAXIMO, DPI_MINIMO, OCR_NAO, OCR_SIM, ConfiguracaoOcr

# Versão do formato do cache: mudar invalida o cache antigo (outra chave).
VERSAO_DO_CACHE = 1
_NOMES_DO_EXECUTAVEL = {"tesseract", "tesseract.exe"}


class ErroOcr(Exception):
    """O Tesseract não está disponível ou falhou ao ler a página."""


@dataclass(frozen=True)
class AjustesOcr:
    """Como ler a página. `psm` é o modo de segmentação do Tesseract (3: automático; 4: uma
    coluna; 6: um bloco)."""
    idioma: str = "por"
    dpi: int = 300
    psm: int = 3
    timeout: int = 120

    def __post_init__(self) -> None:
        if not DPI_MINIMO <= self.dpi <= DPI_MAXIMO:
            raise ValueError(f"dpi fora do limite ({DPI_MINIMO} a {DPI_MAXIMO}): {self.dpi}")
        if self.psm not in range(0, 14):
            raise ValueError(f"psm inválido: {self.psm}")

    @property
    def opcoes(self) -> str:
        return f"--oem 1 --psm {self.psm}"


@dataclass(frozen=True)
class Palavra:
    texto: str
    confianca: float  # 0 a 100
    caixa: tuple[int, int, int, int]  # x, y, largura, altura, em pixels da imagem
    linha: tuple[int, int, int]  # (bloco, parágrafo, linha) do Tesseract


@dataclass(frozen=True)
class PaginaOcr:
    texto: str
    palavras: tuple[Palavra, ...]
    versao: str  # versão do Tesseract

    @property
    def confianca_media(self) -> float:
        """Média da confiança das palavras (0 se a página não tem palavras)."""
        if not self.palavras:
            return 0.0
        return sum(p.confianca for p in self.palavras) / len(self.palavras)

    def para_dict(self) -> dict[str, object]:
        return {
            "texto": self.texto, "versao": self.versao,
            "palavras": [[p.texto, p.confianca, list(p.caixa), list(p.linha)] for p in self.palavras],
        }

    @classmethod
    def de_dict(cls, dados: dict[str, object]) -> PaginaOcr:
        palavras = tuple(
            Palavra(str(t), float(c), tuple(int(v) for v in caixa), tuple(int(v) for v in linha))  # type: ignore[arg-type]
            for t, c, caixa, linha in dados["palavras"]  # type: ignore[union-attr]
        )
        return cls(str(dados["texto"]), palavras, str(dados["versao"]))


# ----------------------------------------------------------------------------
# Tesseract
# ----------------------------------------------------------------------------


@contextmanager
def _tessdata(cfg: ConfiguracaoOcr) -> Iterator[None]:
    """Com OCR_TESSDATA, o Tesseract chamado dentro do bloco usa essa pasta de idiomas."""
    if cfg.tessdata is None:
        yield
        return
    anterior = os.environ.get("TESSDATA_PREFIX")
    os.environ["TESSDATA_PREFIX"] = str(cfg.tessdata)
    try:
        yield
    finally:
        if anterior is None:
            os.environ.pop("TESSDATA_PREFIX", None)
        else:
            os.environ["TESSDATA_PREFIX"] = anterior


def _conferir_tessdata(cfg: ConfiguracaoOcr) -> None:
    if cfg.tessdata is not None and not cfg.tessdata.is_dir():
        raise ErroOcr(f"OCR_TESSDATA={cfg.tessdata} não existe. Crie a pasta e copie o por.traineddata para ela.")


def localizar_tesseract(cfg: ConfiguracaoOcr) -> Path | None:
    """O executável do Tesseract, ou None se o OCR está desligado ou (com OCR_ATIVO=auto)
    não foi encontrado. Levanta `ErroOcr` com OCR_ATIVO=sim e Tesseract ausente, e sempre
    que TESSERACT_CMD aponta para algo que não é o Tesseract."""
    if cfg.ativo == OCR_NAO:
        return None
    _conferir_tessdata(cfg)
    if cfg.tesseract_cmd is not None:
        caminho = cfg.tesseract_cmd
        if caminho.name.casefold() not in _NOMES_DO_EXECUTAVEL:
            raise ErroOcr(f"TESSERACT_CMD={caminho} não é o executável do Tesseract (tesseract.exe).")
        if not caminho.is_file():
            raise ErroOcr(f"TESSERACT_CMD={caminho} não existe. Confira o caminho no .env.")
        return caminho
    achado = shutil.which("tesseract")
    if achado is not None:
        return Path(achado)
    if cfg.ativo == OCR_SIM:
        raise ErroOcr(
            "OCR_ATIVO=sim, mas o Tesseract não foi encontrado no PATH. Informe TESSERACT_CMD no .env "
            r"(ex.: C:\Program Files\Tesseract-OCR\tesseract.exe)."
        )
    return None


def _pytesseract(executavel: Path):
    try:
        import pytesseract
    except ImportError as erro:  # dependência do requirements.txt
        raise ErroOcr("o pacote pytesseract não está instalado (pip install -r requirements.txt)") from erro
    pytesseract.pytesseract.tesseract_cmd = str(executavel)
    return pytesseract


def versao_do_tesseract(executavel: Path) -> str:
    try:
        return str(_pytesseract(executavel).get_tesseract_version())
    except ErroOcr:
        raise
    except Exception as erro:
        raise ErroOcr(f"o Tesseract em {executavel} não respondeu: {type(erro).__name__}: {erro}") from erro


def idiomas_do_tesseract(executavel: Path) -> list[str]:
    try:
        return sorted(_pytesseract(executavel).get_languages(config=""))
    except Exception as erro:
        raise ErroOcr(f"não foi possível listar os idiomas do Tesseract: {erro}") from erro


def _imagem(pagina: pymupdf.Page, dpi: int):
    from PIL import Image

    pixmap = pagina.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    return Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples)


def montar_texto(dados: dict[str, list]) -> tuple[str, tuple[Palavra, ...]]:
    """Texto e palavras a partir do `image_to_data` do Tesseract (dicionário de listas).
    Linhas na ordem do Tesseract; blocos separados por uma linha em branco. Entradas sem
    texto ou com confiança -1 (estrutura, não palavra) ficam de fora."""
    palavras: list[Palavra] = []
    for i, texto in enumerate(dados["text"]):
        texto = (texto or "").strip()
        confianca = float(dados["conf"][i])
        if not texto or confianca < 0:
            continue
        palavras.append(
            Palavra(
                texto, confianca,
                (int(dados["left"][i]), int(dados["top"][i]), int(dados["width"][i]), int(dados["height"][i])),
                (int(dados["block_num"][i]), int(dados["par_num"][i]), int(dados["line_num"][i])),
            )
        )
    linhas: list[str] = []
    atual: list[str] = []
    linha_anterior: tuple[int, int, int] | None = None
    for palavra in palavras:
        if linha_anterior is not None and palavra.linha != linha_anterior:
            linhas.append(" ".join(atual))
            if palavra.linha[0] != linha_anterior[0]:
                linhas.append("")  # novo bloco
            atual = []
        atual.append(palavra.texto)
        linha_anterior = palavra.linha
    if atual:
        linhas.append(" ".join(atual))
    texto = "\n".join(linhas)
    return (texto + "\n" if texto else ""), tuple(palavras)


def ler_pagina(pagina: pymupdf.Page, ajustes: AjustesOcr, executavel: Path, versao: str = "") -> PaginaOcr:
    """OCR de uma página, sem cache. Levanta `ErroOcr` se o Tesseract falhar ou estourar o
    tempo."""
    pytesseract = _pytesseract(executavel)
    try:
        imagem = _imagem(pagina, ajustes.dpi)
        dados = pytesseract.image_to_data(
            imagem, lang=ajustes.idioma, config=ajustes.opcoes, timeout=ajustes.timeout,
            output_type=pytesseract.Output.DICT,
        )
    except Exception as erro:  # TesseractError, TesseractNotFoundError, timeout, imagem grande demais...
        # O pytesseract avisa o timeout com um RuntimeError genérico (TesseractError também é um).
        if type(erro) is RuntimeError and "timeout" in str(erro).casefold():
            raise ErroOcr(f"página {pagina.number + 1}: o Tesseract passou de {ajustes.timeout} s") from erro
        raise ErroOcr(f"página {pagina.number + 1}: {type(erro).__name__}: {erro}") from erro
    texto, palavras = montar_texto(dados)
    return PaginaOcr(texto, palavras, versao or versao_do_tesseract(executavel))


# ----------------------------------------------------------------------------
# Cache
# ----------------------------------------------------------------------------


def sha256_do_arquivo(caminho: Path) -> str:
    resumo = hashlib.sha256()
    with open(caminho, "rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1 << 20), b""):
            resumo.update(bloco)
    return resumo.hexdigest()


class CacheOcr:
    """Resultados do OCR em disco, um JSON por leitura. A pasta é criada na primeira
    gravação. Só grava dentro de `pasta`."""

    def __init__(self, pasta: Path) -> None:
        self.pasta = Path(pasta)

    def _arquivo(self, chave: dict[str, object]) -> Path:
        nome = hashlib.sha256(json.dumps(chave, sort_keys=True).encode("utf-8")).hexdigest()
        return self.pasta / f"{nome}.json"

    def obter(self, chave: dict[str, object]) -> PaginaOcr | None:
        arquivo = self._arquivo(chave)
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
            if dados.get("chave") != chave:
                raise ValueError("chave diferente")
            return PaginaOcr.de_dict(dados["pagina"])
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError):
            arquivo.unlink(missing_ok=True)  # derivado: refaz
            return None

    def guardar(self, chave: dict[str, object], pagina: PaginaOcr) -> None:
        self.pasta.mkdir(parents=True, exist_ok=True)
        arquivo = self._arquivo(chave)
        temporario = arquivo.with_suffix(".tmp")
        with open(temporario, "w", encoding="utf-8") as saida:
            json.dump({"chave": chave, "pagina": pagina.para_dict()}, saida, ensure_ascii=False)
            saida.flush()
            os.fsync(saida.fileno())
        os.replace(temporario, arquivo)


class LeitorOcr:
    """OCR com cache das páginas de um PDF. Criado por `criar_leitor`; None lá quer dizer
    OCR desligado ou Tesseract ausente com OCR_ATIVO=auto."""

    def __init__(self, executavel: Path, cfg: ConfiguracaoOcr, cache: CacheOcr | None) -> None:
        self.executavel = executavel
        self.cfg = cfg
        self.cache = cache
        self.versao = versao_do_tesseract(executavel)
        self._hashes: dict[tuple[str, int, int], str] = {}
        with _tessdata(cfg):
            pasta = cfg.tessdata or _pasta_dos_idiomas(executavel)
        self.modelos = {
            idioma: sha256_do_arquivo(arquivo) if pasta and (arquivo := pasta / f"{idioma}.traineddata").is_file() else ""
            for idioma in cfg.idioma.split("+")
        }

    def ajustes(self, psm: int = 3, dpi: int | None = None) -> AjustesOcr:
        return AjustesOcr(idioma=self.cfg.idioma, dpi=dpi or self.cfg.dpi, psm=psm, timeout=self.cfg.timeout)

    def _hash(self, caminho: Path) -> str:
        info = caminho.stat()
        chave = (str(caminho.resolve()), info.st_mtime_ns, info.st_size)
        if chave not in self._hashes:
            self._hashes[chave] = sha256_do_arquivo(caminho)
        return self._hashes[chave]

    def ler(self, caminho: Path, numero_pagina: int, ajustes: AjustesOcr | None = None) -> PaginaOcr:
        """OCR da página `numero_pagina` (base 1) do PDF `caminho`. Levanta `ErroOcr`."""
        caminho = Path(caminho)
        ajustes = ajustes or self.ajustes()
        chave = {
            "formato": VERSAO_DO_CACHE, "pdf": self._hash(caminho), "pagina": numero_pagina,
            "ajustes": asdict(ajustes) | {"timeout": None}, "tesseract": self.versao,
            "modelos": self.modelos,
        }
        if self.cache is not None and (guardado := self.cache.obter(chave)) is not None:
            return guardado
        try:
            with pymupdf.open(caminho) as doc:
                if not 1 <= numero_pagina <= doc.page_count:
                    raise ErroOcr(f"{caminho.name}: não há página {numero_pagina}")
                with _tessdata(self.cfg):
                    lido = ler_pagina(doc[numero_pagina - 1], ajustes, self.executavel, self.versao)
        except ErroOcr:
            raise
        except Exception as erro:
            raise ErroOcr(f"{caminho.name}: {type(erro).__name__}: {erro}") from erro
        if self.cache is not None:
            try:
                self.cache.guardar(chave, lido)
            except OSError:
                pass  # sem cache, só fica mais lento
        return lido


def criar_leitor(cfg: ConfiguracaoOcr, pasta_cache: Path | None) -> LeitorOcr | None:
    """O leitor de OCR conforme o .env, ou None (OCR desligado, ou Tesseract ausente com
    OCR_ATIVO=auto). Levanta `ErroOcr` quando o .env pede o OCR e ele não funciona."""
    executavel = localizar_tesseract(cfg)
    if executavel is None:
        return None
    return LeitorOcr(executavel, cfg, CacheOcr(pasta_cache) if pasta_cache is not None else None)


# ----------------------------------------------------------------------------
# Diagnóstico (--verificar-ocr)
# ----------------------------------------------------------------------------

FRASE_DE_TESTE = "DECRETO Nº 12.345, DE 1º DE MARÇO DE 2010."
NUMERO_DE_TESTE = "12.345"


@dataclass
class Diagnostico:
    """O que `verificar` achou. `ok` é falso se faltar algo para o OCR funcionar."""
    linhas: list[str]
    ok: bool


def _pasta_dos_idiomas(executavel: Path) -> Path | None:
    """A pasta tessdata, tirada de `tesseract --list-langs` ('List of available languages
    in "/caminho/" (3):'). Sem shell; só lê a saída."""
    import subprocess

    try:
        saida = subprocess.run(
            [str(executavel), "--list-langs"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    primeira = (saida.stdout or saida.stderr).splitlines()[:1]
    if primeira and '"' in primeira[0]:
        return Path(primeira[0].split('"')[1])
    return None


def _pagina_de_teste() -> pymupdf.Document:
    """PDF em memória com uma página só de imagem (a frase de teste renderizada)."""
    com_texto = pymupdf.open()
    pagina = com_texto.new_page(width=420, height=120)
    pagina.insert_text((20, 60), FRASE_DE_TESTE, fontsize=14)
    pixmap = pagina.get_pixmap(dpi=200)
    so_imagem = pymupdf.open()
    nova = so_imagem.new_page(width=420, height=120)
    nova.insert_image(nova.rect, pixmap=pixmap)
    com_texto.close()
    return so_imagem


def verificar(cfg: ConfiguracaoOcr) -> Diagnostico:
    """Confere o Tesseract do .env: executável, versão, idiomas (exige os de OCR_IDIOMA),
    SHA-256 dos arquivos de idioma e OCR de uma página de teste. Só lê."""
    linhas: list[str] = [f"OCR_ATIVO: {cfg.ativo}"]
    if cfg.ativo == OCR_NAO:
        return Diagnostico(linhas + ["OCR desligado (OCR_ATIVO=nao): o pipeline não usa o Tesseract."], True)
    try:
        executavel = localizar_tesseract(cfg)
    except ErroOcr as erro:
        return Diagnostico(linhas + [f"ERRO: {erro}"], False)
    if executavel is None:
        return Diagnostico(
            linhas + [
                "ERRO: Tesseract não encontrado no PATH. Com OCR_ATIVO=auto, o pipeline segue sem OCR.",
                r"Informe TESSERACT_CMD no .env (ex.: C:\Program Files\Tesseract-OCR\tesseract.exe).",
            ],
            False,
        )
    linhas.append(f"Executável: {executavel}")
    if cfg.tessdata is not None:
        linhas.append(f"OCR_TESSDATA: {cfg.tessdata}")
    with _tessdata(cfg):
        return _verificar_com(cfg, executavel, linhas)


def _verificar_com(cfg: ConfiguracaoOcr, executavel: Path, linhas: list[str]) -> Diagnostico:
    try:
        versao = versao_do_tesseract(executavel)
        idiomas = idiomas_do_tesseract(executavel)
    except ErroOcr as erro:
        return Diagnostico(linhas + [f"ERRO: {erro}"], False)
    linhas.append(f"Versão: {versao}")
    linhas.append(f"Idiomas instalados: {', '.join(idiomas) or '(nenhum)'}")
    pasta = _pasta_dos_idiomas(executavel)
    linhas.append(f"Pasta dos idiomas (tessdata) usada pelo Tesseract: {pasta or '(não identificada)'}")
    prefixo = os.environ.get("TESSDATA_PREFIX")
    if prefixo and cfg.tessdata is None:
        linhas.append(f"TESSDATA_PREFIX (variável de ambiente): {prefixo}")
    faltam = [i for i in cfg.idioma.split("+") if i not in idiomas]
    if faltam:
        if not idiomas:
            linhas.append(
                "Nenhum idioma: o Tesseract não achou nenhum .traineddata nessa pasta. Copie o por.traineddata "
                "para ela, ou, sem permissão de gravar nela, para uma pasta sua informada em OCR_TESSDATA no .env."
            )
        return Diagnostico(
            linhas + [
                f"ERRO: falta o idioma {', '.join(faltam)} (OCR_IDIOMA={cfg.idioma}). Instale o "
                f"{faltam[0]}.traineddata nessa pasta (ver docs/uso.md, 'OCR')."
            ],
            False,
        )
    for idioma in cfg.idioma.split("+"):
        arquivo = pasta / f"{idioma}.traineddata" if pasta else None
        if arquivo is not None and arquivo.is_file():
            linhas.append(f"{arquivo.name}: {arquivo} (SHA-256 {sha256_do_arquivo(arquivo)})")
        else:
            linhas.append(f"{idioma}.traineddata: pasta tessdata não identificada (SHA-256 não conferido)")
    try:
        with _pagina_de_teste() as doc:
            lido = ler_pagina(doc[0], AjustesOcr(idioma=cfg.idioma, dpi=cfg.dpi, psm=6, timeout=60), executavel, versao)
    except ErroOcr as erro:
        return Diagnostico(linhas + [f"ERRO na página de teste: {erro}"], False)
    texto = " ".join(lido.texto.split())
    linhas.append(f"Página de teste: \"{texto}\" (confiança média {lido.confianca_media:.0f})")
    if NUMERO_DE_TESTE not in texto:
        return Diagnostico(linhas + [f"ERRO: o OCR não leu o número {NUMERO_DE_TESTE} da página de teste."], False)
    linhas.append("OK: o OCR está pronto para uso.")
    return Diagnostico(linhas, True)
