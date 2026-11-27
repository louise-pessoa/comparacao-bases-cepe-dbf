"""Comparação do texto de um decreto do caderno com o PDF que a DBF já tem.

Serve a duas perguntas da fase de republicações (ver o checklist):
  - o decreto que "já existe na DBF" tem o mesmo texto da publicação que acabou de
    chegar (duplicata verdadeira) ou um texto diferente (candidata a republicação)?
  - quão parecidos são os textos na prática? Os PDFs da base foram feitos no Word,
    e um limiar só pode ser escolhido depois de medir.

A comparação é por "elementos": palavras e números (sequências de letras e dígitos)
e cada sinal de pontuação separado. Espaços e quebras de linha não contam, então
"Recife/ PE" e "Recife/PE", "0001- 42" e "0001-42" são iguais. Também se
padronizam ligaduras, aspas e apóstrofos curvos, e travessões. Maiúsculas, acentos
e pontuação contam: "concedido." e "concedido;" são textos diferentes.

Medida em 29/10/2026 nos 30 decretos de 26/06 que a DBF já tinha: sem desconsiderar
espaços, 10 deles pareciam diferentes (99,39% a 99,98%), todos por espaço depois de
"/" ou de hífen, aspas curvas e apóstrofo curvo. Eram só diferenças de layout.

Pontilhado também não conta: três ou mais pontos seguidos (com ou sem espaço entre eles)
e reticências. São as linhas que, nos decretos de alteração, marcam o trecho não
alterado (".......... (AC)", "Parágrafo único ........"); o Diário as imprime e os PDFs
da base de 2020 não as têm. Contado ponto a ponto, um pontilhado pesava como uma frase
e derrubava a similaridade de decretos iguais para 86% a 94% (Decreto 48.584,
janeiro de 2020). Medido em 10/11/2026.

PDF da base em imagem ou com texto ilegível (fase 5 do plano do OCR, 23/11/2026): com o OCR
ativo (`config.ConfiguracaoOcr`; `comparar_com_base(..., cfg)`), o PDF é lido por OCR e
comparado pela regra calibrada em 19 e 23/11/2026 (`comparacao_ocr.comparar_com_ocr`): é
"idêntico" só sem nenhuma diferença que o OCR não explique. Nunca é idêntico com o OCR de
confiança média abaixo de `CONFIANCA_MINIMA_DA_BASE`; e o OCR com menos de
`MIN_PALAVRAS_DO_OCR` palavras (página em branco, imagem que não é texto) conta como não lido,
como antes do OCR. O PDF com mais de `MAX_PAGINAS_OCR_DA_BASE` páginas não é lido por OCR.

Moldura da base (27/11/2026): o PDF que o SDOE da CEPE emite para cada ato (os editais da base
de 2025 em diante) traz o cabeçalho do Diário ("Poder Executivo / Ano CX • Nº 120 / Recife, 04
de julho de 2026 / CERTIFICADO DIGITALMENTE") e uma página de "PROTOCOLO DE ASSINATURAS"; o
Word de 2010 abre com o título da seção ("SECRETARIA DA FAZENDA"). Nada disso é texto do ato:
sai da base antes de comparar (`sem_moldura_da_base`). Medido no Edital 035/2026 (88,7% ->
idêntico) e no 001/2010 (99,0% -> idêntico).

Só LÊ o PDF da base.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import pymupdf

from dbf_cepe.leitura.identificador import normalizar_espacos
from dbf_cepe.leitura.reader import StatusLeitura, ler_pdf
from dbf_cepe.leitura.separador import limpar_ligaduras

if TYPE_CHECKING:
    from dbf_cepe.config import Configuracao
    from dbf_cepe.leitura.ocr import LeitorOcr

MAX_PAGINAS_OCR_DA_BASE = 10
MIN_PALAVRAS_DO_OCR = 20
# Na calibração, os PDFs da base com OCR de confiança média abaixo disto (Decreto 34732/2010,
# 71) tinham diferenças de verdade e muito ruído: o OCR deles nunca decide duplicata.
CONFIANCA_MINIMA_DA_BASE = 80.0

_ELEMENTO = re.compile(r"\w+|[^\w\s]")
_PONTILHADO = re.compile(r"(?:\.\s*){3,}|…+")
_PADRONIZAR = str.maketrans(
    {
        "\u2019": "'", "\u2018": "'", "\u00b4": "'", "`": "'",
        "\u201c": '"', "\u201d": '"', "\u00ab": '"', "\u00bb": '"',
        "\u2013": "-", "\u2014": "-", "\u2212": "-", "\u2010": "-", "\u2011": "-",
    }
)


def palavras(texto: str) -> list[str]:
    """Texto do ato como lista de elementos (palavras, números e sinais), sem o layout
    (espaços, quebras de linha e pontilhado)."""
    limpo = normalizar_espacos(limpar_ligaduras(texto)).translate(_PADRONIZAR)
    return _ELEMENTO.findall(_PONTILHADO.sub(" ", limpo))


@dataclass(frozen=True)
class Comparacao:
    """Informe se dois textos são idênticos após normalização e suas diferenças."""
    lido: bool  # False: o PDF da base não tem texto legível (imagem, corrompido...)
    similaridade: float = 0.0  # 0 a 1
    so_no_ato: int = 0  # palavras do ato que não estão na base
    so_na_base: int = 0  # palavras da base que não estão no ato
    detalhe: str = ""
    trechos: tuple[str, ...] = ()  # as primeiras diferenças, com contexto
    total_trechos: int = 0  # quantas diferenças existem ao todo
    # Base lida por OCR: `trechos`/`total_trechos` são as diferenças que o OCR não explica.
    por_ocr: bool = False
    confianca_ocr: float = 0.0

    @property
    def identico(self) -> bool:
        """Indique duplicidade somente quando ambos os textos foram lidos e iguais (base lida
        por OCR: nenhuma diferença que o OCR não explique)."""
        if self.por_ocr:
            return self.lido and self.total_trechos == 0
        return self.lido and self.so_no_ato == 0 and self.so_na_base == 0


LIMITE_DE_TRECHOS = 8
_CONTEXTO = 4  # palavras de cada lado, para situar a diferença


def _trecho(a: list[str], b: list[str], i1: int, i2: int, j1: int, j2: int) -> str:
    antes = " ".join(a[max(0, i1 - _CONTEXTO):i1])
    depois = " ".join(a[i2:i2 + _CONTEXTO])
    no_ato = " ".join(a[i1:i2]) or "(nada)"
    na_base = " ".join(b[j1:j2]) or "(nada)"
    return f"...{antes} [caderno: {no_ato} | base: {na_base}] {depois}..."


_TOPO_DO_SDOE = re.compile(
    r"\A\s*Poder Executivo\s*\n\s*Ano [^\n]*N[º°] *\d+[^\n]*\n\s*Recife,[^\n]*\n\s*CERTIFICADO DIGITALMENTE\s*\n"
)
_PROTOCOLO_DO_SDOE = re.compile(r"(?m)^\s*PROTOCOLO DE ASSINATURAS\s*$.*\Z", re.DOTALL)
# Título de seção que o Word da base repete acima do ato (Edital 001/2010).
_TITULO_DE_SECAO_NO_TOPO = re.compile(r"\A\s*SECRETARIA DA FAZENDA\s*\n")


def sem_moldura_da_base(texto_base: str) -> str:
    """O texto do PDF da base sem o que não é do ato: o cabeçalho do Diário e a página de
    protocolo que o SDOE acrescenta, e o título de seção no topo do Word."""
    texto = _PROTOCOLO_DO_SDOE.sub("", texto_base)
    texto = _TOPO_DO_SDOE.sub("", texto)
    return _TITULO_DE_SECAO_NO_TOPO.sub("", texto)


def comparar_textos(texto_ato: str, texto_base: str, limite: int | None = LIMITE_DE_TRECHOS) -> Comparacao:
    """Compare textos sem limiar: igualdade normalizada é o único caso idêntico."""
    a, b = palavras(texto_ato), palavras(texto_base)
    if not a and not b:
        return Comparacao(lido=True, similaridade=1.0)
    operacoes = SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    casadas = sum(i2 - i1 for etiqueta, i1, i2, _, _ in operacoes if etiqueta == "equal")
    diferencas = [(i1, i2, j1, j2) for etiqueta, i1, i2, j1, j2 in operacoes if etiqueta != "equal"]
    return Comparacao(
        lido=True,
        similaridade=2 * casadas / (len(a) + len(b)),
        so_no_ato=len(a) - casadas,
        so_na_base=len(b) - casadas,
        trechos=tuple(_trecho(a, b, *d) for d in diferencas[:limite]),
        total_trechos=len(diferencas),
    )


@lru_cache(maxsize=4)
def _leitor(cfg_ocr, pasta_cache: Path):
    from dbf_cepe.leitura import ocr

    return ocr.criar_leitor(cfg_ocr, pasta_cache)


def leitor_ocr(cfg: Configuracao) -> LeitorOcr | None:
    """O leitor de OCR do .env (um por configuração), ou None (OCR desligado ou Tesseract
    ausente com OCR_ATIVO=auto). Levanta `ErroConfiguracao` quando o .env pede o OCR e ele não
    funciona: quem processa a base chama isto no início, para o erro aparecer antes de tudo."""
    from dbf_cepe.config import ErroConfiguracao
    from dbf_cepe.leitura.ocr import ErroOcr

    try:
        return _leitor(cfg.ocr, cfg.cache_ocr)
    except ErroOcr as erro:
        raise ErroConfiguracao(f"OCR: {erro} (rode python main.py --verificar-ocr)") from erro


def comparar_por_ocr(texto_ato: str, pdf_base: Path, leitor: LeitorOcr) -> Comparacao:
    """Compara o texto do ato com o OCR do PDF da base (ver o início do módulo). Nunca
    levanta exceção: OCR que falha ou não acha texto dá `lido=False`."""
    from dbf_cepe.leitura.ocr import ErroOcr
    from dbf_cepe.pipeline.comparacao_ocr import comparar_com_ocr

    try:
        with pymupdf.open(pdf_base) as doc:
            paginas = doc.page_count
        if paginas > MAX_PAGINAS_OCR_DA_BASE:
            return Comparacao(lido=False, detalhe=f"imagem com {paginas} páginas (OCR só até {MAX_PAGINAS_OCR_DA_BASE})")
        lidas = [leitor.ler(pdf_base, numero) for numero in range(1, paginas + 1)]
    except ErroOcr as erro:
        return Comparacao(lido=False, detalhe=f"imagem; OCR falhou: {erro}")
    except Exception as erro:  # PDF que o PyMuPDF não abre
        return Comparacao(lido=False, detalhe=f"imagem; {type(erro).__name__}: {erro}")
    palavras_ocr = [(p.texto, p.confianca) for lida in lidas for p in lida.palavras]
    if len(palavras_ocr) < MIN_PALAVRAS_DO_OCR:
        return Comparacao(lido=False, detalhe=f"imagem; o OCR não achou texto ({len(palavras_ocr)} palavras)")
    confianca = sum(c for _, c in palavras_ocr) / len(palavras_ocr)
    diferencas = [str(d) for d in comparar_com_ocr(texto_ato, palavras_ocr, leitor.cfg.confianca_critica).nao_explicadas]
    if confianca < CONFIANCA_MINIMA_DA_BASE:
        diferencas.insert(0, f"OCR da base com confiança média {confianca:.0f} (abaixo de "
                             f"{CONFIANCA_MINIMA_DA_BASE:.0f}): não decide duplicata")
    geral = comparar_textos(texto_ato, "\n".join(lida.texto for lida in lidas), limite=0)
    return Comparacao(
        lido=True, similaridade=geral.similaridade, so_no_ato=geral.so_no_ato, so_na_base=geral.so_na_base,
        detalhe=f"base lida por OCR (confiança média {confianca:.0f})",
        trechos=tuple(diferencas[:LIMITE_DE_TRECHOS]), total_trechos=len(diferencas),
        por_ocr=True, confianca_ocr=confianca,
    )


def comparar_com_base(texto_ato: str, pdf_base: Path, cfg: Configuracao | None = None) -> Comparacao:
    """Compara o texto do ato com o do PDF da base; nunca levanta exceção (a não ser a
    configuração do OCR inválida, ver `leitor_ocr`). Com `cfg` e o OCR ativo, o PDF da base em
    imagem ou com texto ilegível é lido por OCR (`comparar_por_ocr`)."""
    leitura = ler_pdf(pdf_base)
    if leitura.status is StatusLeitura.OK:
        return comparar_textos(texto_ato, sem_moldura_da_base(leitura.texto))
    if leitura.status is StatusLeitura.IMAGEM and cfg is not None:
        leitor = leitor_ocr(cfg)
        if leitor is not None:
            return comparar_por_ocr(texto_ato, pdf_base, leitor)
    return Comparacao(lido=False, detalhe=f"{leitura.status.value}: {leitura.detalhe}")