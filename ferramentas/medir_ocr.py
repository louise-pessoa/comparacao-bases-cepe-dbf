"""Ferramenta auxiliar (fora do pipeline): calibra os limiares do OCR (fase 2 do plano,
docs/plano_ocr.md) com os cadernos e a base que já estão no disco. Não baixa nada.

Duas medições:

  A. Páginas de texto como gabarito (`--parte paginas`). Uma página de caderno com texto já
     tem a resposta certa: o texto do PDF. A ferramenta sorteia (de forma reproduzível)
     páginas com texto, renderiza cada uma como imagem, lê por OCR e compara com o texto do
     PDF: acerto de palavras e de números, e a taxa de erro por faixa de confiança do
     Tesseract. Dá OCR_CONFIANCA_CRITICA (a confiança abaixo da qual a palavra erra demais) e
     OCR_CONFIANCA_MEDIA (a confiança média de página com que o OCR ainda acerta quase tudo).
     A comparação é por multiconjunto de elementos (palavras, números e sinais, como em
     `comparacao.palavras`), sem depender da ordem: o OCR e o PDF podem ordenar as colunas de
     outro jeito. Limite: a página renderizada a partir do texto é mais limpa que uma
     digitalização de verdade; o resultado é o teto do OCR, não a média.

  B. Pares da base (`--parte base`). Para cada decreto da DBF dos cadernos (aprovado na
     leitura em cascata) cujo arquivo na base é imagem ou tem texto ilegível, lê a base por
     OCR e compara com o texto do caderno (`comparacao.comparar_textos`): é a situação real
     da fase 5. Como controle, compara também cada decreto com o OCR da base de OUTRO número
     (os decretos têm muito texto padrão em comum). O limiar de duplicata
     (OCR_SIMILARIDADE_DUPLICATA) tem de ficar acima dos pares de números diferentes, com
     margem, e abaixo da maioria dos pares de mesmo número. Os pares de mesmo número abaixo
     do limiar sugerido são listados com as primeiras diferenças: podem ser erro do OCR ou
     outro texto de verdade (republicação), e é isso que se confere à mão.

Só LÊ os cadernos e a base. Grava só o cache do OCR (PASTA_CACHE_OCR) e o JSON pedido em
--json. Usa o Tesseract e a base do .env (ou --base).

Uso:
    python -m ferramentas.medir_ocr dados/historico/2010 dados/historico/2020
    python -m ferramentas.medir_ocr dados/historico/2020 --parte paginas --paginas-por-ano 30 --dpi 300,400
    python -m ferramentas.medir_ocr dados/historico/2020 --parte base --max-pares 50 --json calibracao.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from dbf_cepe.leitura.criterio import da_dbf
from dbf_cepe.leitura.reader import StatusLeitura, _ilegivel, _pagina_digitalizada, ler_pdf
from dbf_cepe.leitura.separador import ErroSeparacao, data_do_caderno, limpar_ligaduras
from dbf_cepe.pipeline.comparacao import comparar_textos, palavras

# Faixas de confiança do Tesseract (limite inferior de cada uma).
FAIXAS_DE_CONFIANCA = (0, 50, 70, 80, 85, 90, 95)
# Erro máximo aceitável numa palavra crítica (número, data): define a confiança crítica sugerida.
ERRO_MAXIMO_CRITICO = 0.05
# Acerto mínimo de página com que a confiança média ainda é aceitável.
ACERTO_MINIMO_DA_PAGINA = 0.98
# Página com menos caracteres que isto não serve de gabarito (página quase vazia).
MIN_CARACTERES_DO_GABARITO = 500
# Margem do limiar de duplicata acima do par de números diferentes mais parecido.
MARGEM_DO_LIMIAR = 0.05
FAIXAS_DE_SIMILARIDADE = (0.98, 0.95, 0.90, 0.80, 0.0)
MAX_PAGINAS_DA_BASE = 10


def ano_do_caderno(caminho: Path) -> str:
    data = data_do_caderno(caminho.name)
    return str(data.year) if data else "sem data"


def _pdfs(pastas: list[Path]) -> list[Path]:
    return sorted({p for pasta in pastas for p in ([pasta] if pasta.is_file() else pasta.rglob("*.pdf"))})


def _faixa_de_confianca(confianca: float) -> int:
    return max(f for f in FAIXAS_DE_CONFIANCA if confianca >= f)


# ----------------------------------------------------------------------------
# A. Páginas de texto como gabarito
# ----------------------------------------------------------------------------


@dataclass
class Gabarito:
    """Acerto do OCR numa amostra de páginas (um ano, um dpi)."""
    paginas: int = 0
    elementos: int = 0  # elementos do gabarito (texto do PDF)
    acertos: int = 0  # elementos do gabarito achados no OCR
    lidos: int = 0  # elementos do OCR
    numeros: int = 0
    numeros_certos: int = 0
    palavras_por_faixa: Counter[int] = field(default_factory=Counter)  # palavras do OCR por faixa de confiança
    erros_por_faixa: Counter[int] = field(default_factory=Counter)
    por_pagina: list[tuple[str, float, float]] = field(default_factory=list)  # (página, confiança, acerto)

    def somar(self, outro: Gabarito) -> None:
        for nome in ("paginas", "elementos", "acertos", "lidos", "numeros", "numeros_certos"):
            setattr(self, nome, getattr(self, nome) + getattr(outro, nome))
        self.palavras_por_faixa.update(outro.palavras_por_faixa)
        self.erros_por_faixa.update(outro.erros_por_faixa)
        self.por_pagina += outro.por_pagina

    @property
    def acerto(self) -> float:
        return self.acertos / self.elementos if self.elementos else 0.0

    @property
    def acerto_numeros(self) -> float:
        return self.numeros_certos / self.numeros if self.numeros else 0.0

    def erro_da_faixa(self, faixa: int) -> float | None:
        total = self.palavras_por_faixa[faixa]
        return self.erros_por_faixa[faixa] / total if total else None


def comparar_pagina(texto_pdf: str, lida, rotulo: str) -> Gabarito:
    """Compara o OCR (`ocr.PaginaOcr`) com o texto do PDF da mesma página, sem depender da
    ordem. Uma palavra do OCR está certa se todos os elementos dela estão no gabarito."""
    gabarito = palavras(limpar_ligaduras(texto_pdf))
    disponiveis = Counter(gabarito)
    medida = Gabarito(paginas=1, elementos=len(gabarito))
    medida.numeros = sum(1 for e in gabarito if any(c.isdigit() for c in e))
    numeros_disponiveis = Counter(e for e in gabarito if any(c.isdigit() for c in e))
    for palavra in lida.palavras:
        elementos = palavras(palavra.texto)
        if not elementos:
            continue
        medida.lidos += len(elementos)
        faixa = _faixa_de_confianca(palavra.confianca)
        medida.palavras_por_faixa[faixa] += 1
        certa = True
        for elemento in elementos:
            if disponiveis[elemento] > 0:
                disponiveis[elemento] -= 1
                medida.acertos += 1
                if numeros_disponiveis[elemento] > 0:
                    numeros_disponiveis[elemento] -= 1
                    medida.numeros_certos += 1
            else:
                certa = False
        if not certa:
            medida.erros_por_faixa[faixa] += 1
    medida.por_pagina.append((rotulo, lida.confianca_media, medida.acerto))
    return medida


def paginas_de_texto(caderno: Path) -> list[int]:
    """As páginas do caderno que servem de gabarito: com texto legível e não digitalizadas."""
    boas: list[int] = []
    with pymupdf.open(caderno) as doc:
        for numero, pagina in enumerate(doc, start=1):
            texto = pagina.get_text("text")
            if len("".join(texto.split())) < MIN_CARACTERES_DO_GABARITO:
                continue
            if _pagina_digitalizada(pagina, texto) or _ilegivel(texto):
                continue
            boas.append(numero)
    return boas


def amostrar(itens: list, quantos: int) -> list:
    """`quantos` itens espalhados pela lista inteira (do começo ao fim), sempre os mesmos."""
    if quantos <= 0 or not itens:
        return []
    if len(itens) <= quantos:
        return list(itens)
    passo = len(itens) / quantos
    return [itens[int(i * passo + passo / 2)] for i in range(quantos)]


def medir_paginas(pastas: list[Path], leitor, paginas_por_ano: int, dpis: list[int], falhas: list[str]):
    """{ano: {dpi: Gabarito}}."""
    candidatas: dict[str, list[tuple[Path, int]]] = {}
    for caderno in _pdfs(pastas):
        try:
            boas = paginas_de_texto(caderno)
        except Exception as erro:
            falhas.append(f"{caderno.name}: {type(erro).__name__}: {erro}")
            continue
        candidatas.setdefault(ano_do_caderno(caderno), []).extend((caderno, p) for p in boas)
    resultado: dict[str, dict[int, Gabarito]] = {}
    for ano, lista in sorted(candidatas.items()):
        for caderno, numero in amostrar(lista, paginas_por_ano):
            with pymupdf.open(caderno) as doc:
                texto_pdf = doc[numero - 1].get_text("text")
            for dpi in dpis:
                try:
                    lida = leitor.ler(caderno, numero, leitor.ajustes(dpi=dpi))
                except Exception as erro:  # ErroOcr
                    falhas.append(f"{caderno.name} p. {numero} ({dpi} dpi): {erro}")
                    continue
                resultado.setdefault(ano, {}).setdefault(dpi, Gabarito()).somar(
                    comparar_pagina(texto_pdf, lida, f"{caderno.name} p. {numero}")
                )
    return resultado


def sugerir_confianca_critica(total: Gabarito) -> int | None:
    """A menor faixa a partir da qual todas as faixas acima erram no máximo ERRO_MAXIMO_CRITICO."""
    sugerida = None
    for faixa in sorted(FAIXAS_DE_CONFIANCA, reverse=True):
        erro = total.erro_da_faixa(faixa)
        if erro is None:
            continue
        if erro > ERRO_MAXIMO_CRITICO:
            break
        sugerida = faixa
    return sugerida


def sugerir_confianca_media(total: Gabarito) -> float | None:
    """A menor confiança média de página X tal que toda página com confiança >= X acerta pelo
    menos ACERTO_MINIMO_DA_PAGINA: logo acima da página ruim de maior confiança (ou a menor
    confiança da amostra, se nenhuma página for ruim)."""
    if not total.por_pagina:
        return None
    ruins = [confianca for _, confianca, acerto in total.por_pagina if acerto < ACERTO_MINIMO_DA_PAGINA]
    if not ruins:
        return min(confianca for _, confianca, _ in total.por_pagina)
    return int(max(ruins)) + 1.0


def tabela_paginas(resultado: dict[str, dict[int, Gabarito]]) -> str:
    linhas = ["A. Páginas de texto como gabarito (OCR da página renderizada x texto do PDF)",
              f"{'ano':<9}{'dpi':>5}{'páginas':>9}{'elementos':>11}{'acerto':>9}{'números':>9}{'precisão':>10}"]
    totais: dict[int, Gabarito] = {}
    for ano, por_dpi in sorted(resultado.items()):
        for dpi, g in sorted(por_dpi.items()):
            precisao = g.acertos / g.lidos if g.lidos else 0.0
            linhas.append(f"{ano:<9}{dpi:>5}{g.paginas:>9}{g.elementos:>11}{g.acerto:>9.1%}"
                          f"{g.acerto_numeros:>9.1%}{precisao:>10.1%}")
            totais.setdefault(dpi, Gabarito()).somar(g)
    for dpi, total in sorted(totais.items()):
        linhas.append(f"\nErro por faixa de confiança ({dpi} dpi; palavra errada = algum elemento fora do gabarito):")
        linhas.append(f"  {'confiança':<12}{'palavras':>10}{'erradas':>10}")
        for faixa in FAIXAS_DE_CONFIANCA:
            erro = total.erro_da_faixa(faixa)
            if erro is not None:
                linhas.append(f"  {f'>= {faixa}':<12}{total.palavras_por_faixa[faixa]:>10}{erro:>10.1%}")
        critica, media = sugerir_confianca_critica(total), sugerir_confianca_media(total)
        linhas.append(f"  sugestão: OCR_CONFIANCA_CRITICA = {critica if critica is not None else '(sem dados)'} "
                      f"(faixas acima erram no máximo {ERRO_MAXIMO_CRITICO:.0%})")
        linhas.append(f"  sugestão: OCR_CONFIANCA_MEDIA = {f'{media:.0f}' if media is not None else '(sem dados)'} "
                      f"(as páginas com confiança média a partir daí acertaram {ACERTO_MINIMO_DA_PAGINA:.0%} ou mais)")
        piores = sorted(total.por_pagina, key=lambda p: p[2])[:5]
        linhas.append("  páginas com menor acerto: " + "; ".join(f"{r} ({c:.0f}, {a:.1%})" for r, c, a in piores))
    return "\n".join(linhas)


# ----------------------------------------------------------------------------
# B. Pares da base
# ----------------------------------------------------------------------------


@dataclass
class Par:
    rotulo: str  # "20200115_PoderExecutivo.pdf p. 3 | Decreto 48563"
    arquivo_base: str
    texto_ato: str
    texto_base: str
    confianca_base: float
    similaridade: float = 0.0
    trechos: tuple[str, ...] = ()


def ler_base_por_ocr(pdf: Path, leitor) -> tuple[str, float]:
    """Texto do OCR de todas as páginas (até MAX_PAGINAS_DA_BASE) e a confiança média."""
    with pymupdf.open(pdf) as doc:
        paginas = min(doc.page_count, MAX_PAGINAS_DA_BASE)
    lidas = [leitor.ler(pdf, numero) for numero in range(1, paginas + 1)]
    palavras_lidas = [p for lida in lidas for p in lida.palavras]
    confianca = sum(p.confianca for p in palavras_lidas) / len(palavras_lidas) if palavras_lidas else 0.0
    return "\n".join(lida.texto for lida in lidas), confianca


def pares_da_base(pastas: list[Path], base: Path, leitor, max_pares: int | None, falhas: list[str]) -> list[Par]:
    from dbf_cepe.leitura.cascata import separar_em_cascata
    from dbf_cepe.pipeline.duplicidade import listar_nomes

    nomes = sorted(listar_nomes(base))
    pares: list[Par] = []
    vistos: set[str] = set()
    for caderno in _pdfs(pastas):
        try:
            atos = separar_em_cascata(caderno).atos
        except ErroSeparacao as erro:
            falhas.append(f"{caderno.name}: {erro}")
            continue
        for ato in atos:
            if ato.nivel_de_leitura is None or not da_dbf(ato):
                continue
            for nome in (n for n in nomes if ato.especie.e_da_familia(n, ato.numero)):
                if nome in vistos or ler_pdf(base / nome).status is not StatusLeitura.IMAGEM:
                    continue
                vistos.add(nome)
                try:
                    texto, confianca = ler_base_por_ocr(base / nome, leitor)
                except Exception as erro:  # ErroOcr, PDF quebrado
                    falhas.append(f"{nome}: OCR falhou: {erro}")
                    continue
                comparacao = comparar_textos(ato.texto, texto, limite=3)
                pares.append(Par(
                    f"{caderno.name} p. {ato.pagina_inicial} | {ato.especie.nome} {ato.numero}", nome,
                    ato.texto, texto, confianca, comparacao.similaridade, comparacao.trechos,
                ))
                if max_pares is not None and len(pares) >= max_pares:
                    return pares
    return pares


def controles(pares: list[Par]) -> list[float]:
    """Similaridade de cada decreto com o OCR da base do par SEGUINTE (outro número)."""
    if len(pares) < 2:
        return []
    return [
        comparar_textos(par.texto_ato, pares[(i + 1) % len(pares)].texto_base, limite=0).similaridade
        for i, par in enumerate(pares)
    ]


def sugerir_limiar(pares: list[Par], diferentes: list[float]) -> float | None:
    if not diferentes:
        return None
    return round(min(max(diferentes) + MARGEM_DO_LIMIAR, 0.99), 2)


def _distribuicao(valores: list[float]) -> str:
    contagem = Counter(next(f for f in FAIXAS_DE_SIMILARIDADE if v >= f) for v in valores)
    partes = []
    for i, faixa in enumerate(FAIXAS_DE_SIMILARIDADE):
        rotulo = f">= {faixa:.0%}" if i == 0 else f"{faixa:.0%} a {FAIXAS_DE_SIMILARIDADE[i - 1]:.0%}"
        partes.append(f"{rotulo}: {contagem[faixa]}")
    return "; ".join(partes)


def tabela_base(pares: list[Par], diferentes: list[float]) -> str:
    linhas = ["B. Pares da base (texto do caderno x OCR do PDF da base em imagem ou ilegível)"]
    if not pares:
        return "\n".join(linhas + ["  nenhum decreto da DBF dos cadernos tem arquivo em imagem na base"])
    mesmos = [p.similaridade for p in pares]
    linhas.append(f"  mesmo número ({len(pares)} pares): {_distribuicao(mesmos)}")
    if diferentes:
        linhas.append(f"  números diferentes ({len(diferentes)} pares, controle): {_distribuicao(diferentes)}; "
                      f"o mais parecido: {max(diferentes):.1%}")
    limiar = sugerir_limiar(pares, diferentes)
    if limiar is None:
        linhas.append("  sugestão: (poucos pares para o controle)")
        return "\n".join(linhas)
    abaixo = sorted((p for p in pares if p.similaridade < limiar), key=lambda p: p.similaridade)
    linhas.append(f"  sugestão: OCR_SIMILARIDADE_DUPLICATA = {limiar:.2f} (o par de números diferentes mais "
                  f"parecido + {MARGEM_DO_LIMIAR:.0%}); {len(pares) - len(abaixo)} de {len(pares)} pares de mesmo "
                  "número ficam como duplicata")
    if abaixo:
        linhas.append("  mesmo número abaixo do limiar (conferir à mão: erro do OCR ou texto diferente?):")
        for par in abaixo:
            linhas.append(f"    {par.rotulo} | base {par.arquivo_base} | {par.similaridade:.1%}, "
                          f"confiança do OCR {par.confianca_base:.0f}")
            for trecho in par.trechos[:2]:
                linhas.append(f"      {trecho}")
    return "\n".join(linhas)


# ----------------------------------------------------------------------------


def _leitor_e_base(base_pedida: Path | None, precisa_da_base: bool):
    from dbf_cepe.config import carregar_configuracao
    from dbf_cepe.leitura import ocr

    cfg = carregar_configuracao()
    leitor = ocr.criar_leitor(cfg.ocr, cfg.cache_ocr)
    if leitor is None:
        raise ocr.ErroOcr("Tesseract não encontrado ou OCR_ATIVO=nao; rode python main.py --verificar-ocr")
    base = base_pedida or cfg.pasta_dbf
    if precisa_da_base and base is None:
        raise ValueError("informe a base com --base ou PASTA_DBF no .env")
    return leitor, base


def _dpis(texto: str) -> list[int]:
    try:
        valores = sorted({int(v) for v in texto.split(",") if v.strip()})
    except ValueError:
        raise argparse.ArgumentTypeError(f"dpi inválido: {texto!r} (ex.: 300 ou 300,400)")
    if not valores or any(not 150 <= v <= 600 for v in valores):
        raise argparse.ArgumentTypeError("dpi de 150 a 600")
    return valores


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pastas", nargs="+", type=Path, help="cadernos ou pastas com cadernos (procura nas subpastas)")
    parser.add_argument("--parte", choices=("paginas", "base", "tudo"), default="tudo")
    parser.add_argument("--paginas-por-ano", type=int, default=20, metavar="N",
                        help="páginas de texto sorteadas por ano na parte A (padrão: 20)")
    parser.add_argument("--dpi", type=_dpis, default=None, metavar="300[,400]",
                        help="resolução(ões) do OCR na parte A (padrão: OCR_DPI do .env)")
    parser.add_argument("--base", type=Path, metavar="PASTA", help="base da DBF (padrão: PASTA_DBF do .env)")
    parser.add_argument("--max-pares", type=int, metavar="N", help="para depois de N pares na parte B")
    parser.add_argument("--json", type=Path, metavar="ARQUIVO", help="grava o resultado em JSON")
    args = parser.parse_args(argv)
    faltando = [p for p in args.pastas if not p.exists()]
    if faltando:
        print(f"Não encontrado: {', '.join(map(str, faltando))}", file=sys.stderr)
        return 2
    try:
        leitor, base = _leitor_e_base(args.base, args.parte in ("base", "tudo"))
    except Exception as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 2
    falhas: list[str] = []
    dados: dict[str, object] = {"tesseract": leitor.versao, "modelos": leitor.modelos}
    if args.parte in ("paginas", "tudo"):
        resultado = medir_paginas(args.pastas, leitor, args.paginas_por_ano, args.dpi or [leitor.cfg.dpi], falhas)
        print(tabela_paginas(resultado))
        dados["paginas"] = {
            ano: {str(dpi): {**{k: v for k, v in g.__dict__.items() if k not in ("palavras_por_faixa", "erros_por_faixa")},
                             "palavras_por_faixa": dict(g.palavras_por_faixa), "erros_por_faixa": dict(g.erros_por_faixa)}
                  for dpi, g in por_dpi.items()}
            for ano, por_dpi in resultado.items()
        }
    if args.parte in ("base", "tudo"):
        pares = pares_da_base(args.pastas, base, leitor, args.max_pares, falhas)
        diferentes = controles(pares)
        print("\n" + tabela_base(pares, diferentes))
        dados["base"] = {
            "pares": [{"rotulo": p.rotulo, "arquivo": p.arquivo_base, "similaridade": p.similaridade,
                       "confianca": p.confianca_base, "trechos": list(p.trechos)} for p in pares],
            "diferentes": diferentes, "limiar_sugerido": sugerir_limiar(pares, diferentes),
        }
    for falha in falhas:
        print(f"AVISO: {falha}", file=sys.stderr)
    if args.json:
        args.json.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
