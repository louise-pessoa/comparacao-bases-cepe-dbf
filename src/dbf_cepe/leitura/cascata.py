"""Leitura em cascata: cada ato do caderno passa pelo critério de aprovação
(leitura/criterio.py); o que reprova é relido no nível seguinte, até algum aprovar.

    nível 1  leitura atual do separador (ordem do PDF, fecho atual)  -> 2020 em diante
    (nível 2, fecho de até 2010, e nível 3, leitura por coluna: etapas seguintes)

O ato aprovado no nível 1 sai exatamente como o separador o entrega: a cascata nunca
muda o que já sai certo. O ato que nenhum nível aprova continua na lista, com
`nivel_de_leitura=None`, os motivos em `reprovacao` e um aviso: ele vai para revisão
manual (quem grava em pendentes decide o que fazer com ele).

Cada nível é um `Nivel` com a função `reler(caminho, ato)`, que devolve uma nova leitura
do mesmo ato (ou None, se ele não achar o ato). Novos níveis entram em `NIVEIS`, sem
mudar o resto do pipeline.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from dbf_cepe.leitura.criterio import da_dbf, motivos_de_reprovacao
from dbf_cepe.leitura.separador import AtoSeparado, ResultadoSeparacao, separar_caderno
from dbf_cepe.regras.especies import ESPECIES_ATIVAS, Especie

AVISO_DE_REPROVACAO = "reprovado no critério de leitura ({motivos}) em todos os níveis; revisão manual"


@dataclass(frozen=True)
class Nivel:
    numero: int
    descricao: str
    # Relê o ato reprovado no nível anterior; None = este nível não achou o ato.
    reler: Callable[[Path, AtoSeparado], AtoSeparado | None]


# Níveis de recuperação, em ordem. O nível 1 é a própria leitura do separador.
NIVEIS: tuple[Nivel, ...] = ()


def avaliar(ato: AtoSeparado, niveis: tuple[Nivel, ...], caminho: Path) -> AtoSeparado:
    """O ato aprovado no primeiro nível que o aceitar, ou o ato do nível 1 marcado como
    reprovado (com os motivos do nível 1)."""
    e_da_dbf = da_dbf(ato)
    motivos = motivos_de_reprovacao(ato, e_da_dbf)
    if not motivos:
        return ato
    for nivel in niveis:
        relido = nivel.reler(caminho, ato)
        if relido is not None and relido.numero == ato.numero and not motivos_de_reprovacao(relido, e_da_dbf):
            return replace(relido, nivel_de_leitura=nivel.numero)
    aviso = AVISO_DE_REPROVACAO.format(motivos=", ".join(motivos))
    return replace(ato, nivel_de_leitura=None, reprovacao=tuple(motivos), avisos=(*ato.avisos, aviso))


def aplicar_cascata(
    resultado: ResultadoSeparacao, caminho: Path, niveis: tuple[Nivel, ...] | None = None
) -> ResultadoSeparacao:
    """Passa cada ato já separado do caderno `caminho` pelo critério e pelos níveis."""
    escolhidos = NIVEIS if niveis is None else niveis
    return replace(resultado, atos=tuple(avaliar(ato, escolhidos, caminho) for ato in resultado.atos))


def separar_em_cascata(
    caminho: Path,
    especies: tuple[Especie, ...] = ESPECIES_ATIVAS,
    niveis: tuple[Nivel, ...] | None = None,
) -> ResultadoSeparacao:
    """Como `separador.separar_caderno`, mas cada ato passa pelo critério e pelos níveis
    de recuperação."""
    return aplicar_cascata(separar_caderno(caminho, especies), caminho, niveis)
