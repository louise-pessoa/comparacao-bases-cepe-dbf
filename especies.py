"""Regras de domínio por espécie de ato.

Este é o ÚNICO arquivo a alterar para passar a tratar uma nova espécie (por
exemplo, Edital, em fase futura): basta definir uma nova `Especie`, com seu
cabeçalho, seu padrão de nomenclatura e seus tipos, e incluí-la em
`ESPECIES_ATIVAS`. Nenhuma outra etapa do pipeline precisa ser reescrita.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Tipo:
    """Tipo de decreto, identificado pelo início da ementa."""

    nome: str
    padrao: re.Pattern[str]  # aplicado com .match() no INÍCIO da ementa
    modelo: str | None  # decreto-modelo (template da etapa 4); None = sem amostra


@dataclass(frozen=True)
class Especie:
    nome: str  # "Decreto"
    cabecalho: re.Pattern[str]  # precisa ter o grupo nomeado "numero"
    padrao_arquivo: str  # ex.: "{especie} {numero}.pdf"
    tipos: tuple[Tipo, ...] = ()

    def nome_arquivo(self, numero: str) -> str:
        """Nome de salvamento e de comparação, ex.: 'Decreto 60929.pdf'."""
        return self.padrao_arquivo.format(especie=self.nome, numero=numero)


# ----------------------------------------------------------------------------
# Decreto
# ----------------------------------------------------------------------------

# Cabeçalho em MAIÚSCULAS, ex.: "DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026".
# É propositalmente sensível a maiúsculas: as citações no corpo do texto
# ("Decreto nº 21.959, de 27 de dezembro de 1999") não podem ser confundidas
# com o cabeçalho do ato.
_CABECALHO_DECRETO = re.compile(
    r"(?<![A-Za-zÀ-ú])DECRETO\s+N\s*\.?\s*[º°o]\.?\s*"
    r"(?P<numero>\d{1,3}(?:\.\d{3})+|\d+)\s*,\s*"
    r"DE\s+\d{1,2}\s*[º°o]?\s+DE\s+[A-ZÇÃÕÁÀÂÉÊÍÓÔÚ]+\s+DE\s+\d{4}"
)


def _inicio(expressao: str) -> re.Pattern[str]:
    return re.compile(expressao, re.IGNORECASE)


# A classificação olha só o INÍCIO da ementa. Buscar a palavra no texto todo
# erraria: a ementa de uma revogação pode citar "prorrogação", e o corpo da
# renovação (60945) cita "prorrogação" e "prorrogados".
# A ordem importa: as variantes "segunda" vêm antes das simples.
TIPOS_DECRETO: tuple[Tipo, ...] = (
    Tipo("CONCESSAO", _inicio(r"Concede\b"), "60929"),
    Tipo("ALTERACAO", _inicio(r"Introduz\s+altera[çc][õo]es\b"), "60951"),
    # Sem amostra real: padrão presumido a partir do texto dos demais.
    Tipo(
        "SEGUNDA_PRORROGACAO",
        _inicio(r"Disp[õo]e\s+sobre\s+a\s+segunda\s+prorroga[çc][ãa]o\b"),
        "60950",  # mesmo template da prorrogação (decisão do projeto)
    ),
    Tipo(
        "SEGUNDA_RENOVACAO",
        _inicio(r"Disp[õo]e\s+sobre\s+a\s+segunda\s+renova[çc][ãa]o\b"),
        "60945",  # mesmo template da renovação (decisão do projeto)
    ),
    Tipo(
        "PRORROGACAO",
        _inicio(r"Disp[õo]e\s+sobre\s+a\s+prorroga[çc][ãa]o\b"),
        "60950",
    ),
    Tipo(
        "RENOVACAO",
        _inicio(r"Disp[õo]e\s+sobre\s+a\s+renova[çc][ãa]o\b"),
        "60945",
    ),
    Tipo(
        "TRANSFERENCIA",
        _inicio(r"Disp[õo]e\s+sobre\s+a\s+transfer[êe]ncia\b"),
        "60505",
    ),
    # Sem amostra real de revogação: sem modelo até a etapa 4 definir o tratamento.
    Tipo("REVOGACAO", _inicio(r"Revoga\b"), None),
    Tipo(
        "TERCEIRIZACAO",
        _inicio(r"Autoriza\s+a\s+terceiriza[çc][ãa]o\b"),
        "60233",
    ),
)

DECRETO = Especie(
    nome="Decreto",
    cabecalho=_CABECALHO_DECRETO,
    padrao_arquivo="{especie} {numero}.pdf",
    tipos=TIPOS_DECRETO,
)

# ----------------------------------------------------------------------------
# Registro
# ----------------------------------------------------------------------------

# Espécies tratadas nesta fase. Para incluir editais no futuro, defina
# EDITAL acima e acrescente-o aqui.
ESPECIES_ATIVAS: tuple[Especie, ...] = (DECRETO,)

# Espécies que, quando o documento COMEÇA por elas, são reconhecidas como
# "outra espécie" e descartadas. Nomes de espécies ativas são ignorados
# automaticamente. Documentos que não começam por nenhuma espécie conhecida
# NÃO são descartados: vão para revisão manual.
NOMES_OUTRAS_ESPECIES: tuple[str, ...] = (
    "LEI COMPLEMENTAR",
    "LEI",
    "PORTARIA",
    "EDITAL",
    "ERRATA",
    "RESOLUÇÃO",
)
