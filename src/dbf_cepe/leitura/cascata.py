"""Leitura em cascata: cada ato do caderno passa pelo critério de aprovação
(leitura/criterio.py); o que reprova é relido no nível seguinte, até algum aprovar.

    nível 1  leitura atual do separador (ordem do PDF, fecho atual)    -> 2020 em diante
    nível 2  ordem do PDF, aceitando o fecho de até 2010                -> 2010 e antes
    nível 3  leitura por coluna (separador.linhas_por_coluna), os dois  -> 2012 a jun/2019
             fechos aceitos                                                e páginas de 2026
                                                                           com colunas fora
                                                                           de ordem

O ato aprovado no nível 1 sai exatamente como o separador o entrega, com uma exceção: o
decreto da DBF aprovado no nível 1 ou 2 com um trecho de outro ato no meio (bloco contínuo
que a leitura por coluna põe fora dele; ver `trecho_intruso`) sai com o texto da leitura
por coluna e um aviso. Um ato aprovado num nível de recuperação sai com o texto desse
nível e um aviso. O ato que nenhum nível aprova continua na lista, com
`nivel_de_leitura=None`, os motivos em `reprovacao` e um aviso: ele vai para revisão
manual, não para pendentes.

Cada nível é um `Nivel` com a função `reler(caminho, ato)`, que devolve uma nova leitura
do mesmo ato (ou None, se ele não achar o ato). O texto do caderno de cada nível é lido
uma vez por caderno (`_texto_do_caderno`, com cache).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from dbf_cepe.leitura.criterio import da_dbf, motivos_de_reprovacao
from dbf_cepe.leitura.separador import (
    AtoSeparado,
    ErroSeparacao,
    ResultadoSeparacao,
    ler_caderno,
    separar_caderno,
    separar_texto,
)
from dbf_cepe.regras.especies import ESPECIES_ATIVAS, Especie

AVISO_DE_REPROVACAO = "reprovado no critério de leitura ({motivos}) em todos os níveis; revisão manual"
AVISO_DE_RECUPERACAO = "lido no nível {numero} ({descricao}): a leitura atual reprovou ({motivos}); confira o texto"


@dataclass(frozen=True)
class Nivel:
    numero: int
    descricao: str
    # Relê o ato reprovado no nível anterior; None = este nível não achou o ato.
    reler: Callable[[Path, AtoSeparado], AtoSeparado | None]
    # O fecho de outra época (Especie.fechos_anteriores) conta como fecho no critério.
    aceita_fechos_anteriores: bool = False


# ----------------------------------------------------------------------------
# Releituras
# ----------------------------------------------------------------------------

_CACHE: dict[tuple[str, bool], tuple[int, str, list[int]]] = {}
_TAMANHO_DO_CACHE = 4


def _texto_do_caderno(caminho: Path, por_coluna: bool) -> tuple[str, list[int]]:
    """Texto do caderno (na ordem do PDF ou por coluna), lido uma vez por caderno."""
    caminho = Path(caminho)
    chave = (str(caminho.resolve()), por_coluna)
    versao = caminho.stat().st_mtime_ns
    guardado = _CACHE.get(chave)
    if guardado is not None and guardado[0] == versao:
        return guardado[1], guardado[2]
    texto, inicios, _, _ = ler_caderno(caminho, por_coluna)
    if len(_CACHE) >= _TAMANHO_DO_CACHE:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[chave] = (versao, texto, inicios)
    return texto, inicios


def com_fechos_anteriores(especie: Especie) -> Especie:
    """A espécie com o fecho atual OU um dos anteriores (para o separador achar o fim do
    ato de outra época)."""
    if especie.fecho is None or not especie.fechos_anteriores:
        return especie
    padroes = [especie.fecho, *especie.fechos_anteriores]
    fecho = re.compile("|".join(f"(?:{p.pattern})" for p in padroes), re.IGNORECASE)
    return replace(especie, fecho=fecho)


def _mesmo_ato(atos: list[AtoSeparado], original: AtoSeparado) -> AtoSeparado | None:
    """O ato de mesmo número na releitura; havendo mais de um (número repetido no caderno),
    o que começa na página mais próxima da original."""
    candidatos = [a for a in atos if a.especie.nome == original.especie.nome and a.numero == original.numero]
    if not candidatos:
        return None
    return min(candidatos, key=lambda a: abs(a.pagina_inicial - original.pagina_inicial))


def _reler(caminho: Path, ato: AtoSeparado, por_coluna: bool) -> AtoSeparado | None:
    try:
        texto, inicios = _texto_do_caderno(caminho, por_coluna)
    except (ErroSeparacao, OSError):
        return None  # o caderno não pôde ser relido: este nível não acha o ato
    atos, _ = separar_texto(texto, inicios, (com_fechos_anteriores(ato.especie),))
    relido = _mesmo_ato(atos, ato)
    if relido is None:
        return None
    # O resto do pipeline continua vendo a espécie original e os dados da publicação.
    return replace(
        relido, especie=ato.especie, data_publicacao=ato.data_publicacao, caderno=ato.caderno,
        marcado_como_republicacao=ato.marcado_como_republicacao,
    )


def _reler_com_fecho_antigo(caminho: Path, ato: AtoSeparado) -> AtoSeparado | None:
    return _reler(caminho, ato, por_coluna=False)


def _reler_por_coluna(caminho: Path, ato: AtoSeparado) -> AtoSeparado | None:
    return _reler(caminho, ato, por_coluna=True)


# Níveis de recuperação, em ordem. O nível 1 é a própria leitura do separador.
NIVEIS: tuple[Nivel, ...] = (
    Nivel(2, "fecho de até 2010", _reler_com_fecho_antigo, aceita_fechos_anteriores=True),
    Nivel(3, "leitura por coluna", _reler_por_coluna, aceita_fechos_anteriores=True),
)


# ----------------------------------------------------------------------------
# Cascata
# ----------------------------------------------------------------------------


# ----------------------------------------------------------------------------
# Trecho de outro ato (intruso)
# ----------------------------------------------------------------------------

# Um decreto aprovado no critério ainda pode ter, no meio, um trecho de outro ato que o PDF
# guarda fora de ordem (ex.: Decreto 35.173/2010, com a "PLANILHA DE CÁLCULO ANALÍTICO" de
# outro ato entre as páginas 8 e 9; Decreto 48.719/2020, com um aviso da Casa Civil). O
# critério não vê: fecho e abertura estão certos. A leitura por coluna põe o trecho no lugar.
# Medição de 13/11/2026 (3.868 decretos, 2010 a 2026): 10 decretos da DBF, todos intrusos
# de verdade; nos demais, as duas leituras são iguais ou diferem de outro jeito.
MIN_PALAVRAS_DO_INTRUSO = 30
MAX_OUTRAS_DIFERENCAS = 5  # palavras diferentes fora do trecho intruso
AVISO_DE_INTRUSO = (
    "trecho de outro ato removido ({quantas} palavras, lido por coluna): \"{inicio}...\"; confira o texto"
)
_PALAVRA = re.compile(r"\w+")


def trecho_intruso(aceito: str, por_coluna: str) -> str | None:
    """O trecho (palavras) que só existe no texto aceito, se for um bloco contínuo de pelo
    menos MIN_PALAVRAS_DO_INTRUSO palavras e o resto dos dois textos for igual (até
    MAX_OUTRAS_DIFERENCAS palavras de diferença); senão None."""
    a, b = _PALAVRA.findall(aceito.casefold()), _PALAVRA.findall(por_coluna.casefold())
    diferencas = [op for op in SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op[0] != "equal"]
    so_no_aceito = [op for op in diferencas if op[0] == "delete"]
    if not so_no_aceito:
        return None
    maior = max(so_no_aceito, key=lambda op: op[2] - op[1])
    if maior[2] - maior[1] < MIN_PALAVRAS_DO_INTRUSO:
        return None
    resto = sum(max(op[2] - op[1], op[4] - op[3]) for op in diferencas if op is not maior)
    if resto > MAX_OUTRAS_DIFERENCAS:
        return None
    return " ".join(_PALAVRA.findall(aceito)[maior[1]:maior[2]])


def _sem_intruso(ato: AtoSeparado, caminho: Path, e_da_dbf: bool) -> AtoSeparado:
    """O ato aprovado, ou a leitura por coluna dele se o aprovado tiver um trecho intruso."""
    relido = _reler_por_coluna(caminho, ato)
    if relido is None or motivos_de_reprovacao(relido, e_da_dbf, True):
        return ato
    trecho = trecho_intruso(ato.texto, relido.texto)
    if trecho is None:
        return ato
    aviso = AVISO_DE_INTRUSO.format(quantas=len(trecho.split()), inicio=" ".join(trecho.split()[:10]))
    anteriores = tuple(a for a in ato.avisos if a.startswith("lido no nível"))
    return replace(relido, nivel_de_leitura=3, avisos=(*relido.avisos, *anteriores, aviso))


# ----------------------------------------------------------------------------
# Cascata
# ----------------------------------------------------------------------------


def avaliar(ato: AtoSeparado, niveis: tuple[Nivel, ...], caminho: Path) -> AtoSeparado:
    """O ato aprovado no primeiro nível que o aceitar, ou o ato do nível 1 marcado como
    reprovado (com os motivos do nível 1). O decreto da DBF aprovado no nível 1 ou 2 ainda
    passa pela conferência do trecho intruso (`_sem_intruso`)."""
    e_da_dbf = da_dbf(ato)
    motivos = motivos_de_reprovacao(ato, e_da_dbf)
    if not motivos:
        return _sem_intruso(ato, caminho, e_da_dbf) if e_da_dbf else ato
    for nivel in niveis:
        relido = nivel.reler(caminho, ato)
        if (
            relido is not None
            and relido.numero == ato.numero
            and not motivos_de_reprovacao(relido, e_da_dbf, nivel.aceita_fechos_anteriores)
        ):
            aviso = AVISO_DE_RECUPERACAO.format(
                numero=nivel.numero, descricao=nivel.descricao, motivos=", ".join(motivos)
            )
            aprovado = replace(relido, nivel_de_leitura=nivel.numero, avisos=(*relido.avisos, aviso))
            return _sem_intruso(aprovado, caminho, e_da_dbf) if e_da_dbf else aprovado
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
