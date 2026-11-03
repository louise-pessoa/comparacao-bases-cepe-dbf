"""Atos acessórios: errata e republicação.

Os dois não são atos "com número" como o decreto, o edital e a portaria:

  - a ERRATA não tem número próprio. A chave dela é o ATO REFERIDO (espécie,
    número e ano) e o destino: decreto, edital, portaria ou outro (despacho,
    termo de fomento, publicação...);
  - a REPUBLICAÇÃO é um marcador ("(REPUBLICADO POR HAVER SAÍDO COM INCORREÇÃO NO
    ORIGINAL)") que se liga a um ato. Citação "republicado no DOE" dentro de texto
    normativo NÃO é republicação.

As RegEx moram em `regras_atos.py` (fonte única, enviada à CEPE); este módulo
dá a elas a forma que o pipeline usa. Ligar o marcador ao ato certo é feito por
`regras_atos.identificar`, que conhece o trecho de cada ato.
"""

from __future__ import annotations

from dataclasses import dataclass

from especies import DECRETO, EDITAL, PORTARIA, Especie
from regras_atos import (
    ERRATA_LINHA,
    REPUBLICACAO_MARCADOR,
    RETIFICACAO_LINHA,
    AtoReferido,
    ato_referido_da_errata,
    juntar_linhas,
)

# Destino da errata -> espécie do pipeline. "outro" não tem espécie.
ESPECIE_DO_DESTINO: dict[str, Especie] = {
    "decreto": DECRETO,
    "edital": EDITAL,
    "portaria": PORTARIA,
}

# Quantos caracteres depois do "ERRATA" bastam para ler o ato referido.
_JANELA_DA_ERRATA = 300
# Contexto mostrado em volta de um marcador de republicação.
_CONTEXTO_REPUBLICACAO = 120


@dataclass(frozen=True)
class Errata:
    posicao: int  # início da linha da errata no texto
    trecho: str  # começo da errata, em uma linha
    referido: AtoReferido

    @property
    def destino(self) -> str:
        """'decreto' | 'edital' | 'portaria' | 'outro'."""
        return self.referido.destino

    @property
    def especie_destino(self) -> Especie | None:
        """A espécie do pipeline que a errata corrige; None se for 'outro'."""
        return ESPECIE_DO_DESTINO.get(self.destino)


@dataclass(frozen=True)
class MarcadorDeRepublicacao:
    posicao: int
    fim: int
    marcador: str  # o que casou: "REPUBLICADO POR HAVER SAÍDO"
    contexto: str  # o marcador com o texto em volta, em uma linha


def ato_referido(trecho: str) -> str:
    """O ato que a errata corrige, lido do começo do trecho: "Decreto 61.560",
    "Despacho" (sem número) ou "?" (nenhuma espécie conhecida)."""
    return ato_referido_da_errata(trecho).rotulo


def achar_erratas(texto: str) -> list[Errata]:
    """Toda linha que começa com "ERRATA"/"Errata", com o ato que ela corrige."""
    erratas = []
    for m in ERRATA_LINHA.finditer(texto):
        posicao = m.start() + len(m.group(0)) - len(m.group(0).lstrip())
        trecho = texto[posicao : posicao + _JANELA_DA_ERRATA]
        erratas.append(Errata(posicao, juntar_linhas(trecho), ato_referido_da_errata(trecho)))
    return erratas


def achar_marcadores_de_republicacao(texto: str) -> list[MarcadorDeRepublicacao]:
    marcadores = []
    for m in REPUBLICACAO_MARCADOR.finditer(texto):
        inicio = max(0, m.start() - _CONTEXTO_REPUBLICACAO)
        contexto = juntar_linhas(texto[inicio : m.end() + _CONTEXTO_REPUBLICACAO])
        marcadores.append(MarcadorDeRepublicacao(m.start(), m.end(), m.group(0), contexto))
    return marcadores


__all__ = [
    "ERRATA_LINHA",
    "ESPECIE_DO_DESTINO",
    "REPUBLICACAO_MARCADOR",
    "RETIFICACAO_LINHA",
    "AtoReferido",
    "Errata",
    "MarcadorDeRepublicacao",
    "achar_erratas",
    "achar_marcadores_de_republicacao",
    "ato_referido",
]
