"""Etapa 2 (parte 2) — identificação do ato por RegEx no texto bruto.

Regras de segurança (falhar para revisão, nunca em silêncio):
  - O cabeçalho precisa estar no INÍCIO do texto. Buscar "DECRETO Nº ..." em
    qualquer ponto pegaria citações do corpo e geraria o número errado.
  - Só é descartado o que COMEÇA por uma outra espécie conhecida (Lei,
    Portaria, Edital, Errata...). Texto sem correspondência vai para revisão.
  - Mais de um cabeçalho distinto no mesmo PDF (ex.: caderno com vários atos)
    vai para revisão, para não ignorar atos em silêncio.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

from especies import ESPECIES_ATIVAS, NOMES_OUTRAS_ESPECIES, Especie, Tipo

_ESPACOS = re.compile(r"\s+")
_TAMANHO_EMENTA_PADRAO = 600


class StatusIdentificacao(Enum):
    IDENTIFICADO = "identificado"
    OUTRA_ESPECIE = "outra_especie"
    SEM_CORRESPONDENCIA = "sem_correspondencia"
    MULTIPLOS_ATOS = "multiplos_atos"


@dataclass(frozen=True)
class Identificacao:
    status: StatusIdentificacao
    especie: Especie | None = None
    numero: str | None = None  # sem separador de milhar, ex.: "60929"
    tipo: Tipo | None = None  # None se a ementa não casar com nenhum
    detalhe: str = ""


def normalizar_espacos(texto: str) -> str:
    """Unicode NFC e espaços colapsados.

    Necessário porque o texto justificado dos PDFs chega com palavras
    separadas por quebras de linha ("de \\njulho \\nde \\n2000").
    """
    return _ESPACOS.sub(" ", unicodedata.normalize("NFC", texto)).strip()


def _regex_outras_especies(ativas: tuple[Especie, ...]) -> re.Pattern[str] | None:
    nomes_ativos = {especie.nome.upper() for especie in ativas}
    nomes = [n for n in NOMES_OUTRAS_ESPECIES if n.upper() not in nomes_ativos]
    if not nomes:
        return None
    # Mais longos primeiro: "LEI COMPLEMENTAR" antes de "LEI".
    alternativas = "|".join(re.escape(n) for n in sorted(nomes, key=len, reverse=True))
    return re.compile(rf"(?:{alternativas})\b", re.IGNORECASE)


def _extrair_ementa(texto: str, fim_cabecalho: int, especie: Especie) -> str:
    """Ementa: do fim do cabeçalho até a abertura do corpo da espécie (no
    decreto, "A GOVERNADORA..."); sem ela, os primeiros caracteres."""
    resto = texto[fim_cabecalho:]
    marcador = especie.abertura_do_corpo.search(resto) if especie.abertura_do_corpo else None
    ementa = resto[: marcador.start()] if marcador else resto[:_TAMANHO_EMENTA_PADRAO]
    return ementa.lstrip(" .")


def _classificar(especie: Especie, ementa: str) -> Tipo | None:
    for tipo in especie.tipos:
        if tipo.padrao.match(ementa):
            return tipo
    return None


def identificar(
    texto_bruto: str, especies: tuple[Especie, ...] = ESPECIES_ATIVAS
) -> Identificacao:
    texto = normalizar_espacos(texto_bruto)
    if not texto:
        return Identificacao(
            StatusIdentificacao.SEM_CORRESPONDENCIA, detalhe="texto vazio"
        )

    for especie in especies:
        casamento = especie.cabecalho.match(texto)
        if not casamento:
            continue

        numeros = {
            m["numero"].replace(".", "") for m in especie.cabecalho.finditer(texto)
        }
        if len(numeros) > 1:
            return Identificacao(
                StatusIdentificacao.MULTIPLOS_ATOS,
                especie=especie,
                detalhe=f"cabeçalhos de mais de um ato no PDF: {sorted(numeros)}",
            )

        numero = casamento["numero"].replace(".", "")
        ementa = _extrair_ementa(texto, casamento.end(), especie)
        return Identificacao(
            StatusIdentificacao.IDENTIFICADO,
            especie=especie,
            numero=numero,
            tipo=_classificar(especie, ementa),
            detalhe=f"ementa: {ementa[:80]!r}",
        )

    outras = _regex_outras_especies(especies)
    if outras and (casamento := outras.match(texto)):
        return Identificacao(
            StatusIdentificacao.OUTRA_ESPECIE,
            detalhe=f"começa por {casamento.group(0).upper()!r}",
        )

    return Identificacao(
        StatusIdentificacao.SEM_CORRESPONDENCIA,
        detalhe=f"cabeçalho não encontrado; início do texto: {texto[:80]!r}",
    )