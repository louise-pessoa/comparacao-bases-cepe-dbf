"""Critério de aprovação da leitura em cascata: o texto separado de um ato está inteiro e
só dele?

A leitura atual do PDF falha em cadernos antigos (colunas fora de ordem, fecho de outra
época) e, em todos os anos, quando o expediente do Diário divide a página com o ato. A
cascata (leitura/cascata.py) relê com outra estratégia o ato que reprova aqui. Um ato
só é aprovado se cumprir tudo:

  - fecho da espécie exatamente uma vez. Sem ele, o motivo é "fecho antigo" quando há um
    dos `fechos_anteriores` da espécie (no decreto, o de até 2010), senão "sem fecho";
    duas ou mais vezes, "fecho repetido" (o ato levou o final de outro);
  - abertura do corpo da espécie logo depois do cabeçalho (no decreto, "A GOVERNADORA",
    "O GOVERNADOR" ou quem está "NO EXERCÍCIO DO CARGO DE GOVERNADOR(A)");
  - nenhum outro cabeçalho da espécie no início de uma linha do texto;
  - nenhum trecho do expediente do Diário no texto;
  - ato de programa da DBF: tipo reconhecido. Como o ato que cita o programa só nos
    considerandos e não tem tipo já fica fora da DBF (`da_dbf`), este motivo só aparece
    quando a ementa cita o programa.

Espécie sem fecho ou sem abertura definidos não é cobrada por eles.
"""

from __future__ import annotations

from dbf_cepe.leitura.identificador import StatusIdentificacao, identificar
from dbf_cepe.leitura.separador import TRECHO_DO_EXPEDIENTE, AtoSeparado
from dbf_cepe.regras.programas import cita_programa_so_nos_considerandos, em_escopo, programas_do_ato

SEM_FECHO = "sem fecho"
FECHO_ANTIGO = "fecho antigo"
FECHO_REPETIDO = "fecho repetido"
SEM_ABERTURA = "sem abertura"
OUTRO_CABECALHO = "outro cabeçalho"
EXPEDIENTE = "expediente"
DBF_SEM_TIPO = "DBF sem tipo"

# Até onde, desde o início do ato, a abertura do corpo pode estar (a ementa vem antes).
JANELA_DA_ABERTURA = 1800


def da_dbf(ato: AtoSeparado) -> bool:
    """O ato é de programa da DBF: o cabeçalho cita um programa e, se o programa estiver
    só nos considerandos, a ementa tem tipo reconhecido (ver `fora_por_ementa_sem_tipo`)."""
    return em_escopo(ato.especie, programas_do_ato(ato.texto, ato.especie)) and not fora_por_ementa_sem_tipo(ato)


def fora_por_ementa_sem_tipo(ato: AtoSeparado) -> bool:
    """Cita o programa só nos considerandos e a ementa não tem tipo: fora do escopo
    (decisão de 12/11/2026, Decreto 34.519/2010). Só vale para o texto identificado como
    um ato só: o decreto misturado com o cabeçalho de outro também perde o tipo, e esse
    tem de continuar reprovando (revisão manual)."""
    ident = identificar(ato.texto, (ato.especie,))
    return (
        ident.status is StatusIdentificacao.IDENTIFICADO
        and ident.tipo is None
        and cita_programa_so_nos_considerandos(ato.texto, ato.especie, ident.ementa)
    )


def motivos_de_reprovacao(
    ato: AtoSeparado, e_da_dbf: bool | None = None, aceitar_fechos_anteriores: bool = False
) -> list[str]:
    """Por que o ato reprova no critério; lista vazia = aprovado. `e_da_dbf` evita
    recalcular o escopo quando quem chama já o sabe. Com `aceitar_fechos_anteriores`
    (níveis 2 e 3 da cascata), o fecho de outra época (`Especie.fechos_anteriores`) conta
    como fecho."""
    especie, texto = ato.especie, ato.texto
    motivos: list[str] = []
    if especie.fecho is not None:
        fechos = len(especie.fecho.findall(texto))
        if aceitar_fechos_anteriores:
            fechos += sum(len(f.findall(texto)) for f in especie.fechos_anteriores)
        if fechos == 0:
            antigo = any(f.search(texto) for f in especie.fechos_anteriores)
            motivos.append(FECHO_ANTIGO if antigo else SEM_FECHO)
        elif fechos > 1:
            motivos.append(FECHO_REPETIDO)
    if especie.abertura_do_corpo is not None and not especie.abertura_do_corpo.search(
        texto, 0, JANELA_DA_ABERTURA
    ):
        motivos.append(SEM_ABERTURA)
    for achado in especie.cabecalho.finditer(texto):
        inicio_da_linha = texto.rfind("\n", 0, achado.start()) + 1
        if achado.start() > 0 and not texto[inicio_da_linha : achado.start()].strip():
            motivos.append(OUTRO_CABECALHO)
            break
    if TRECHO_DO_EXPEDIENTE.search(texto):
        motivos.append(EXPEDIENTE)
    if (da_dbf(ato) if e_da_dbf is None else e_da_dbf) and identificar(texto, (especie,)).tipo is None:
        motivos.append(DBF_SEM_TIPO)
    return motivos
