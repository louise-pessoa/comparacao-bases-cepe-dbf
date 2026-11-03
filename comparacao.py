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

Só LÊ o PDF da base.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from identificador import normalizar_espacos
from reader import StatusLeitura, ler_pdf
from separador import limpar_ligaduras

_ELEMENTO = re.compile(r"\w+|[^\w\s]")
_PADRONIZAR = str.maketrans(
    {
        "\u2019": "'", "\u2018": "'", "\u00b4": "'", "`": "'",
        "\u201c": '"', "\u201d": '"', "\u00ab": '"', "\u00bb": '"',
        "\u2013": "-", "\u2014": "-", "\u2212": "-", "\u2010": "-", "\u2011": "-",
    }
)


def palavras(texto: str) -> list[str]:
    """Texto do ato como lista de elementos (palavras, números e sinais), sem o layout."""
    return _ELEMENTO.findall(normalizar_espacos(limpar_ligaduras(texto)).translate(_PADRONIZAR))


@dataclass(frozen=True)
class Comparacao:
    lido: bool  # False: o PDF da base não tem texto legível (imagem, corrompido...)
    similaridade: float = 0.0  # 0 a 1
    so_no_ato: int = 0  # palavras do ato que não estão na base
    so_na_base: int = 0  # palavras da base que não estão no ato
    detalhe: str = ""
    trechos: tuple[str, ...] = ()  # as primeiras diferenças, com contexto
    total_trechos: int = 0  # quantas diferenças existem ao todo

    @property
    def identico(self) -> bool:
        return self.lido and self.so_no_ato == 0 and self.so_na_base == 0


LIMITE_DE_TRECHOS = 8
_CONTEXTO = 4  # palavras de cada lado, para situar a diferença


def _trecho(a: list[str], b: list[str], i1: int, i2: int, j1: int, j2: int) -> str:
    antes = " ".join(a[max(0, i1 - _CONTEXTO):i1])
    depois = " ".join(a[i2:i2 + _CONTEXTO])
    no_ato = " ".join(a[i1:i2]) or "(nada)"
    na_base = " ".join(b[j1:j2]) or "(nada)"
    return f"...{antes} [caderno: {no_ato} | base: {na_base}] {depois}..."


def comparar_textos(texto_ato: str, texto_base: str) -> Comparacao:
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
        trechos=tuple(_trecho(a, b, *d) for d in diferencas[:LIMITE_DE_TRECHOS]),
        total_trechos=len(diferencas),
    )


def comparar_com_base(texto_ato: str, pdf_base: Path) -> Comparacao:
    """Compara o texto do ato com o do PDF da base; nunca levanta exceção."""
    leitura = ler_pdf(pdf_base)
    if leitura.status is not StatusLeitura.OK:
        return Comparacao(lido=False, detalhe=f"{leitura.status.value}: {leitura.detalhe}")
    return comparar_textos(texto_ato, leitura.texto)