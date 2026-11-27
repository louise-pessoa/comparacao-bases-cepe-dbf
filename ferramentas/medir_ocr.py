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
     da fase 5. Duas medidas (ver `MEDIDAS`): similaridade (o texto todo) e cobertura (quanto
     do decreto do caderno está na base, ignorando o que sobra na base). Como controle, compara também cada decreto com o OCR da base de OUTRO número
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
from dbf_cepe.pipeline.comparacao_ocr import (
    CONFERIR,
    DUPLICATA,
    NAO_COMPARADO,
    comparar_com_ocr,
    decidir_em_periodo_ruim,
)

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


# As duas medidas da parte B. Similaridade: o texto inteiro dos dois lados (o que sobra na
# base também pesa). Cobertura: a fração do texto do decreto do caderno que aparece, na ordem,
# no OCR da base; o que sobra na base não pesa. Em 2010 o PDF da base é um recorte da página
# do Diário, com o cabeçalho da página e pedaços dos atos vizinhos (Decretos 34732 e 34746):
# a similaridade cai, a cobertura não.
MEDIDAS = ("similaridade", "cobertura")
# Terceira medida (a que decide, desde 19/11/2026): quantas diferenças o OCR não explica
# (`pipeline/comparacao_ocr.py`). Similaridade e cobertura não separaram o mesmo decreto (ruído
# do OCR) de outro decreto do mesmo modelo de texto (até 93,7% e 95,4%).
FAIXAS_DE_DIFERENCAS = ((0, 0), (1, 1), (2, 2), (3, 5), (6, None))
MAX_PARES_LISTADOS = 40


@dataclass
class Par:
    rotulo: str  # "20200115_PoderExecutivo.pdf p. 3 | Decreto 48563"
    arquivo_base: str
    texto_ato: str
    texto_base: str
    confianca_base: float
    similaridade: float = 0.0
    cobertura: float = 0.0
    trechos: tuple[str, ...] = ()  # primeiras diferenças com texto do caderno que falta na base
    palavras_base: tuple[tuple[str, float], ...] = ()  # (palavra, confiança) do OCR da base
    nao_explicadas: tuple[str, ...] = ()  # diferenças que o OCR não explica
    periodo_ruim: str = ""  # decisão pela regra do período ruim (comparacao_ocr.decidir_em_periodo_ruim)


def medir_par(texto_ato: str, texto_base: str) -> tuple[float, float, tuple[str, ...]]:
    """(similaridade, cobertura, trechos do caderno que faltam na base)."""
    comparacao = comparar_textos(texto_ato, texto_base, limite=None)
    elementos = len(palavras(texto_ato))
    cobertura = 1 - comparacao.so_no_ato / elementos if elementos else 1.0
    faltam = tuple(t for t in comparacao.trechos if "[caderno: (nada) |" not in t)[:3]
    return comparacao.similaridade, cobertura, faltam


def ler_base_por_ocr(pdf: Path, leitor) -> tuple[str, float, tuple[tuple[str, float], ...]]:
    """Texto do OCR de todas as páginas (até MAX_PAGINAS_DA_BASE), a confiança média e as
    palavras com a confiança."""
    with pymupdf.open(pdf) as doc:
        paginas = min(doc.page_count, MAX_PAGINAS_DA_BASE)
    lidas = [leitor.ler(pdf, numero) for numero in range(1, paginas + 1)]
    palavras_lidas = [p for lida in lidas for p in lida.palavras]
    confianca = sum(p.confianca for p in palavras_lidas) / len(palavras_lidas) if palavras_lidas else 0.0
    return "\n".join(lida.texto for lida in lidas), confianca, tuple((p.texto, p.confianca) for p in palavras_lidas)


def _critica(leitor) -> float:
    cfg = getattr(leitor, "cfg", None)
    return getattr(cfg, "confianca_critica", 90.0)


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
                    texto, confianca, palavras_base = ler_base_por_ocr(base / nome, leitor)
                except Exception as erro:  # ErroOcr, PDF quebrado
                    falhas.append(f"{nome}: OCR falhou: {erro}")
                    continue
                similaridade, cobertura, trechos = medir_par(ato.texto, texto)
                resultado = comparar_com_ocr(ato.texto, palavras_base, _critica(leitor))
                pares.append(Par(
                    f"{caderno.name} p. {ato.pagina_inicial} | {ato.especie.nome} {ato.numero}", nome,
                    ato.texto, texto, confianca, similaridade, cobertura, trechos, palavras_base,
                    tuple(str(d) for d in resultado.nao_explicadas), decidir_em_periodo_ruim(resultado),
                ))
                if max_pares is not None and len(pares) >= max_pares:
                    return pares
    return pares


def controles(pares: list[Par], confianca_critica: float = 90.0) -> dict[str, list]:
    """Cada decreto contra o OCR da base do par SEGUINTE (outro número), nas três medidas
    ("nao_explicadas": quantas diferenças o OCR não explica; "exemplos_controle": o par de
    controle com menos diferenças não explicadas e quais são)."""
    resultado: dict[str, list] = {medida: [] for medida in (*MEDIDAS, "nao_explicadas", "periodo_ruim")}
    if len(pares) < 2:
        return resultado
    menor: tuple[int, str, list[str]] | None = None
    for i, par in enumerate(pares):
        outro = pares[(i + 1) % len(pares)]
        similaridade, cobertura, _ = medir_par(par.texto_ato, outro.texto_base)
        comparado = comparar_com_ocr(par.texto_ato, outro.palavras_base, confianca_critica)
        diferencas = comparado.nao_explicadas
        resultado["periodo_ruim"].append(decidir_em_periodo_ruim(comparado))
        resultado["similaridade"].append(similaridade)
        resultado["cobertura"].append(cobertura)
        resultado["nao_explicadas"].append(len(diferencas))
        if menor is None or len(diferencas) < menor[0]:
            menor = (len(diferencas), f"{par.rotulo} x base {outro.arquivo_base}", [str(d) for d in diferencas[:5]])
    resultado["exemplos_controle"] = [menor] if menor else []
    return resultado


def _faixa_de_diferencas(quantas: int) -> tuple[int, int | None]:
    return next(f for f in FAIXAS_DE_DIFERENCAS if quantas >= f[0] and (f[1] is None or quantas <= f[1]))


def _distribuicao_de_diferencas(valores: list[int]) -> str:
    contagem = Counter(_faixa_de_diferencas(v) for v in valores)
    rotulos = {(0, 0): "0", (1, 1): "1", (2, 2): "2", (3, 5): "3 a 5", (6, None): "6 ou mais"}
    return "; ".join(f"{rotulos[f]}: {contagem[f]}" for f in FAIXAS_DE_DIFERENCAS)


def _contar_decisoes(decisoes: list[str]) -> str:
    contagem = Counter(decisoes)
    return "; ".join(f"{d}: {contagem[d]}" for d in (DUPLICATA, CONFERIR, NAO_COMPARADO))


def _linhas_periodo_ruim(pares: list[Par], controle: list[str]) -> list[str]:
    """A regra (c) do período de digitalização ruim, no mesmo número e no controle."""
    linhas = ["  REGRA DO PERÍODO RUIM (duplicata sem diferença; conferir se há número diferente lido com "
              "confiança; senão não comparado)",
              f"  mesmo número ({len(pares)} pares): {_contar_decisoes([p.periodo_ruim for p in pares])}"]
    if controle:
        linhas.append(f"  números diferentes ({len(controle)} pares, controle): {_contar_decisoes(controle)}")
        if controle.count(DUPLICATA):
            linhas.append("  ATENÇÃO: par de números diferentes como duplicata: a regra NÃO separa")
        else:
            linhas.append(f"  no controle, {controle.count(CONFERIR)} de {len(controle)} pegos (conferir); o resto "
                          "passaria como hoje, sem comparar")
    return linhas


def tabela_diferencas(pares: list[Par], diferentes: dict[str, list]) -> str:
    """A medida que decide: diferenças que o OCR não explica, no mesmo número e no controle."""
    linhas = ["\n  DIFERENÇAS NÃO EXPLICADAS PELO OCR (a medida da fase 5; ver pipeline/comparacao_ocr.py)"]
    mesmos = [len(p.nao_explicadas) for p in pares]
    linhas.append(f"  mesmo número ({len(pares)} pares): {_distribuicao_de_diferencas(mesmos)}")
    controle = diferentes.get("nao_explicadas", [])
    if not controle:
        return "\n".join(linhas + ["  sugestão: (poucos pares para o controle)"])
    linhas.append(f"  números diferentes ({len(controle)} pares, controle): {_distribuicao_de_diferencas(controle)}")
    for quantas, rotulo, exemplos in diferentes.get("exemplos_controle", []):
        linhas.append(f"  controle com menos diferenças ({quantas}): {rotulo}")
        linhas += [f"      {e}" for e in exemplos]
    if min(controle) == 0:
        linhas.append("  ATENÇÃO: há par de números diferentes sem nenhuma diferença não explicada: a regra NÃO separa; "
                      "não use na fase 5 sem rever")
    else:
        duplicatas = sum(1 for q in mesmos if q == 0)
        linhas.append(f"  regra: duplicata se 0 diferenças não explicadas (o controle tem no mínimo {min(controle)}); "
                      f"{duplicatas} de {len(pares)} pares de mesmo número ficam como duplicata, o resto vai para conferir")
        tolerancias = [k for k in (1, 2) if k < min(controle)]
        if tolerancias:
            linhas.append("  se aceitasse até k diferenças não explicadas: " + "; ".join(
                f"k = {k}: {sum(1 for q in mesmos if q <= k)} de {len(pares)} como duplicata" for k in tolerancias))
    linhas += _linhas_periodo_ruim(pares, diferentes.get("periodo_ruim", []))
    restantes = sorted((p for p in pares if p.nao_explicadas), key=lambda p: len(p.nao_explicadas))
    if restantes:
        linhas.append("  mesmo número com diferença não explicada (vão para conferir; olhe se é OCR ou texto diferente):")
        for par in restantes[:MAX_PARES_LISTADOS]:
            linhas.append(f"    {par.rotulo} | base {par.arquivo_base} | {len(par.nao_explicadas)} diferença(s), "
                          f"confiança do OCR {par.confianca_base:.0f}")
            linhas += [f"      {d}" for d in par.nao_explicadas[:3]]
        if len(restantes) > MAX_PARES_LISTADOS:
            linhas.append(f"    ... e mais {len(restantes) - MAX_PARES_LISTADOS} (lista completa no --json)")
    return "\n".join(linhas)


def sugerir_limiar(diferentes: list[float]) -> float | None:
    """O par de números diferentes mais parecido + MARGEM_DO_LIMIAR (no máximo 0,99)."""
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


def tabela_base(pares: list[Par], diferentes: dict[str, list[float]]) -> str:
    linhas = ["B. Pares da base (texto do caderno x OCR do PDF da base em imagem ou ilegível)"]
    if not pares:
        return "\n".join(linhas + ["  nenhum decreto da DBF dos cadernos tem arquivo em imagem na base"])
    for medida in MEDIDAS:
        mesmos = [getattr(p, medida) for p in pares]
        controle = diferentes.get(medida, [])
        linhas.append(f"\n  {medida.upper()}")
        linhas.append(f"  mesmo número ({len(pares)} pares): {_distribuicao(mesmos)}")
        limiar = sugerir_limiar(controle)
        if limiar is None:
            linhas.append("  sugestão: (poucos pares para o controle)")
            continue
        linhas.append(f"  números diferentes ({len(controle)} pares, controle): {_distribuicao(controle)}; "
                      f"o mais parecido: {max(controle):.1%}")
        abaixo = sorted((p for p in pares if getattr(p, medida) < limiar), key=lambda p: getattr(p, medida))
        linhas.append(f"  limiar sugerido: {limiar:.2f} (o par de números diferentes mais parecido + "
                      f"{MARGEM_DO_LIMIAR:.0%}); {len(pares) - len(abaixo)} de {len(pares)} pares de mesmo número "
                      "ficam como duplicata")
    return "\n".join(linhas) + tabela_diferencas(pares, diferentes)


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
        diferentes = controles(pares, _critica(leitor))
        print("\n" + tabela_base(pares, diferentes))
        dados["base"] = {
            "pares": [{"rotulo": p.rotulo, "arquivo": p.arquivo_base, "similaridade": p.similaridade,
                       "cobertura": p.cobertura, "confianca": p.confianca_base, "trechos": list(p.trechos),
                       "nao_explicadas": list(p.nao_explicadas), "periodo_ruim": p.periodo_ruim}
                      for p in pares],
            "diferentes": diferentes,
            "limiar_sugerido": {medida: sugerir_limiar(diferentes[medida]) for medida in MEDIDAS},
        }
    for falha in falhas:
        print(f"AVISO: {falha}", file=sys.stderr)
    if args.json:
        args.json.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
