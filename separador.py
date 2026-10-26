"""Etapa 1b — separação dos atos de um caderno (PDF completo do dia).

O caderno traz dezenas de atos seguidos (leis, decretos, portarias...). Esta
etapa devolve, para cada espécie pedida (hoje só "Decreto"), um bloco de texto
por ato, do cabeçalho ("DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026.") até o fim
do ato. As etapas seguintes (identificação, duplicidade, normalização) trabalham
sobre esse texto.

Por que separar pelo TEXTO e não por intervalo de páginas: os atos começam e
terminam no meio da página (no caderno de 26/06/2026, os decretos 60.900 e
60.902 dividem a página 8 com outros atos). Cortar por páginas misturaria atos
vizinhos.

Regras:
  - o cabeçalho tem de estar no INÍCIO de uma linha e em maiúsculas (a regex
    vem de `especies.py`); citações como "Decreto nº 21.959, de ..." no corpo
    do texto nunca contam;
  - o ato termina antes do próximo cabeçalho de ato (de qualquer espécie
    conhecida) ou, se não houver anexo, logo depois das assinaturas que
    seguem o fecho da espécie (no decreto, "... Independência do Brasil.").
    Anexos ("ANEXO ÚNICO", "ANEXO I"...) fazem parte do ato e vão até o
    próximo ato;
  - o que é específico da espécie (cabeçalho, fecho, cargo de quem assina,
    frase de abertura do corpo) vem de `especies.py`, nunca daqui;
  - cabeçalhos e rodapés de página do Diário ("Diário Oficial do Estado de
    Pernambuco - Poder Executivo", "Recife, 26 de junho de 2026", "Ano CX ...
    114 - 29") são removidos: não pertencem ao ato;
  - o texto do Diário traz ligaduras seguidas de um espaço espúrio
    ("ﬁ cam", "beneﬁ ciário"); elas são convertidas ("ficam", "beneficiário").
    Com isso, o texto do decreto 60.929 extraído do caderno é idêntico ao do
    PDF avulso da DBF (conferido em teste).

Casos de borda tratados (viram `avisos`, nunca descarte silencioso):
  - cabeçalho no meio de uma linha (não é início de ato, mas pode ser um ato
    colado a outro);
  - cabeçalho sem a abertura do corpo da espécie logo depois (no decreto,
    "A GOVERNADORA/O GOVERNADOR"; pode ser citação);
  - fecho não encontrado (fim do ato estimado pelo próximo ato);
  - mesmo número duas vezes no caderno (republicação "por incorreção");
  - páginas digitalizadas (atos nelas não podem ser vistos sem OCR).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from especies import ESPECIES_ATIVAS, NOMES_OUTRAS_ESPECIES, Especie
from reader import _pagina_digitalizada

# Linhas de cabeçalho/rodapé de página do Diário Oficial de Pernambuco. Só são
# removidas nas primeiras linhas de cada página (ver LINHAS_DE_TOPO).
PADROES_TOPO_DE_PAGINA: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\d+\s*-\s*Ano\s+[A-Z]+\b.*$"),  # "8 - Ano CX Ć NÀ 114"
    re.compile(r"^Ano\s+[A-Z]+\b.*-\s*\d+\s*$"),  # "Ano CX Ć NÀ 114 - 9"
    re.compile(r"^Di[aá]rio Oficial do Estado de Pernambuco\b.*$"),
    re.compile(r"^Recife,\s+\d{1,2}\s+de\s+\w+\s+de\s+\d{4}\s*$"),
)
LINHAS_DE_TOPO = 4

_LIGADURAS = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"}
# Ligadura + espaço espúrio (cada ligadura do Diário vem seguida de um espaço).
_LIGADURA_COM_ESPACO = re.compile("([" + "".join(_LIGADURAS) + "]) ")

# Linha de assinatura: nome em maiúsculas. O cargo de quem assina (no decreto,
# "Governadora do Estado") é da espécie: `Especie.cargo_assinante`.
_LINHA_ASSINATURA = re.compile(r"^[A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ'’.\- ]{2,}$")
# Palavras que nunca aparecem em nome de pessoa: distinguem um título de seção
# ("SECRETARIA DE ADMINISTRAÇÃO", "GOVERNO DO ESTADO...") de uma assinatura.
_PALAVRA_DE_TITULO = re.compile(
    r"\b(?:SECRETARIA|GOVERNO|ESTADO|AG[ÊE]NCIA|FUNDA[ÇC][ÃA]O|DIRETORIA|DEPARTAMENTO|"
    r"INSTITUTO|COMPANHIA|PROCURADORIA|PODER|TRIBUNAL|CONSELHO|ASSEMBLEIA|ATOS?|PORTARIA|"
    r"ANEXO|DECRETO|LEI|EDITAL|AVISO|EXTRATO|DESPACHO|ERRATA|RESOLU[ÇC][ÃA]O)\b"
)
_MAX_PALAVRAS_ASSINATURA = 8
_ANEXO = re.compile(r"^ANEXO\b")
_JANELA_ABERTURA = 1500  # caracteres após o cabeçalho para achar a abertura do corpo


class ErroSeparacao(Exception):
    """O caderno não pôde ser aberto ou lido."""


@dataclass(frozen=True)
class AtoSeparado:
    especie: Especie
    numero: str  # sem separador de milhar, ex.: "60929"
    cabecalho: str  # ex.: "DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026"
    texto: str  # texto limpo, uma linha do Diário por linha
    pagina_inicial: int  # base 1
    pagina_final: int
    avisos: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResultadoSeparacao:
    atos: tuple[AtoSeparado, ...]
    paginas: int
    paginas_digitalizadas: tuple[int, ...] = ()
    avisos: tuple[str, ...] = field(default=())


def limpar_ligaduras(texto: str) -> str:
    """'beneﬁ ciário' -> 'beneficiário'; ligadura sem espaço também é convertida."""
    texto = _LIGADURA_COM_ESPACO.sub(lambda m: _LIGADURAS[m.group(1)], texto)
    for ligadura, letras in _LIGADURAS.items():
        texto = texto.replace(ligadura, letras)
    return texto


def _regex_marcadores(especies: tuple[Especie, ...]) -> re.Pattern[str]:
    """Início de QUALQUER ato conhecido (ativo ou não), no começo da linha."""
    nomes = {n.upper() for n in NOMES_OUTRAS_ESPECIES} | {e.nome.upper() for e in especies}
    nomes |= {"ATOS DO DIA", "ATO DO DIA", "EXTRATO", "AVISO", "DESPACHO"}
    alternativas = "|".join(re.escape(n) for n in sorted(nomes, key=len, reverse=True))
    return re.compile(rf"(?m)^[ \t]*(?:{alternativas})\b")


def _linhas_da_pagina(pagina: pymupdf.Page) -> list[str]:
    linhas = pagina.get_text("text").split("\n")
    # `get_text` termina com "\n": o último elemento é vazio.
    if linhas and linhas[-1] == "":
        linhas.pop()
    topo = [
        i
        for i, linha in enumerate(linhas[:LINHAS_DE_TOPO])
        if any(p.match(linha.strip()) for p in PADROES_TOPO_DE_PAGINA)
    ]
    return [linha for i, linha in enumerate(linhas) if i not in set(topo)]


def _montar_texto(doc: pymupdf.Document) -> tuple[str, list[int], tuple[int, ...]]:
    """Texto do caderno sem cabeçalhos de página.

    Devolve (texto, início_de_cada_página, páginas_digitalizadas); o segundo
    item permite descobrir em que página cai qualquer posição do texto.
    """
    partes: list[str] = []
    inicios: list[int] = []
    digitalizadas: list[int] = []
    posicao = 0
    for numero, pagina in enumerate(doc, start=1):
        if _pagina_digitalizada(pagina, pagina.get_text("text")):
            digitalizadas.append(numero)
        trecho = limpar_ligaduras("\n".join(_linhas_da_pagina(pagina)) + "\n")
        inicios.append(posicao)
        partes.append(trecho)
        posicao += len(trecho)
    return "".join(partes), inicios, tuple(digitalizadas)


def _pagina_da_posicao(inicios: list[int], posicao: int) -> int:
    pagina = 1
    for numero, inicio in enumerate(inicios, start=1):
        if inicio <= posicao:
            pagina = numero
        else:
            break
    return pagina


def _e_assinatura(linha: str, especie: Especie) -> bool:
    if especie.cargo_assinante and especie.cargo_assinante.match(linha):
        return True
    if not _LINHA_ASSINATURA.match(linha):
        return False
    return len(linha.split()) <= _MAX_PALAVRAS_ASSINATURA and not _PALAVRA_DE_TITULO.search(linha)


def _fim_sem_anexo(bloco: str, especie: Especie) -> int | None:
    """Posição logo após as assinaturas que seguem o fecho; None se houver
    anexo (o ato continua), se o fecho não for encontrado ou se a espécie não
    tem fecho."""
    fecho = especie.fecho.search(bloco) if especie.fecho else None
    if not fecho:
        return None
    fim = fecho.end()
    for linha in bloco[fecho.end() :].splitlines(keepends=True):
        limpa = linha.strip()
        if not limpa:
            fim += len(linha)
            continue
        if _ANEXO.match(limpa):  # antes da assinatura: "ANEXO ÚNICO" também é maiúsculo
            return None
        if _e_assinatura(limpa, especie):
            fim += len(linha)
            continue
        break
    return fim


def _cabecalhos_no_inicio_da_linha(
    texto: str, especie: Especie
) -> tuple[list[re.Match[str]], list[re.Match[str]]]:
    no_inicio, no_meio = [], []
    for achado in especie.cabecalho.finditer(texto):
        # Início de linha, tolerando espaços antes (o Diário traz " DECRETO Nº ...").
        inicio_da_linha = texto.rfind("\n", 0, achado.start()) + 1
        if not texto[inicio_da_linha : achado.start()].strip():
            no_inicio.append(achado)
        else:
            no_meio.append(achado)
    return no_inicio, no_meio


def separar_texto(
    texto: str,
    inicios_de_pagina: list[int] | None = None,
    especies: tuple[Especie, ...] = ESPECIES_ATIVAS,
) -> tuple[list[AtoSeparado], list[str]]:
    """Separa os atos de um texto de caderno já limpo (ver `separar_caderno`)."""
    inicios = inicios_de_pagina or [0]
    marcadores = _regex_marcadores(especies)
    encontrados: list[tuple[int, AtoSeparado]] = []
    avisos_gerais: list[str] = []

    for especie in especies:
        cabecalhos, no_meio = _cabecalhos_no_inicio_da_linha(texto, especie)
        for achado in no_meio:
            avisos_gerais.append(
                f"{especie.nome}: cabeçalho no meio da linha, ignorado "
                f"({achado.group(0)!r}); confira se há um ato colado a outro"
            )
        for achado in cabecalhos:
            inicio = achado.start()
            avisos: list[str] = []

            # Fim provisório: o próximo marcador de ato depois deste cabeçalho.
            proximo = marcadores.search(texto, achado.end())
            fim = proximo.start() if proximo else len(texto)
            bloco = texto[inicio:fim]

            sem_anexo = _fim_sem_anexo(bloco, especie)
            if sem_anexo is not None:
                fim = inicio + sem_anexo
            elif especie.fecho and not especie.fecho.search(bloco):
                avisos.append(
                    f"fecho da espécie ({especie.fecho.pattern!r}) não encontrado; "
                    "o fim do ato foi estimado pelo próximo ato"
                )
            if especie.abertura_do_corpo and not especie.abertura_do_corpo.search(
                texto, achado.end(), achado.end() + _JANELA_ABERTURA
            ):
                avisos.append(
                    f"sem a abertura do corpo da espécie ({especie.abertura_do_corpo.pattern!r}) "
                    "após o cabeçalho (citação?)"
                )

            conteudo = texto[inicio:fim].strip("\n")
            conteudo = "\n".join(linha.rstrip() for linha in conteudo.split("\n"))
            encontrados.append((
                inicio,
                AtoSeparado(
                    especie=especie,
                    numero=achado["numero"].replace(".", ""),
                    cabecalho=" ".join(achado.group(0).split()),
                    texto=unicodedata.normalize("NFC", conteudo),
                    pagina_inicial=_pagina_da_posicao(inicios, inicio),
                    pagina_final=_pagina_da_posicao(inicios, max(inicio, fim - 1)),
                    avisos=tuple(avisos),
                ),
            ))

    encontrados.sort(key=lambda par: par[0])
    atos = [ato for _, ato in encontrados]
    vistos: dict[tuple[str, str], int] = {}
    for ato in atos:
        chave = (ato.especie.nome, ato.numero)
        vistos[chave] = vistos.get(chave, 0) + 1
    for (nome, numero), vezes in vistos.items():
        if vezes > 1:
            avisos_gerais.append(
                f"{nome} {numero} aparece {vezes} vezes no caderno (republicação?)"
            )
    return atos, avisos_gerais


def separar_caderno(
    caminho: Path, especies: tuple[Especie, ...] = ESPECIES_ATIVAS
) -> ResultadoSeparacao:
    """Lê o caderno e devolve um `AtoSeparado` por ato das espécies pedidas."""
    try:
        with pymupdf.open(caminho) as doc:
            if doc.needs_pass:
                raise ErroSeparacao("PDF protegido por senha")
            if doc.page_count == 0:
                raise ErroSeparacao("PDF sem páginas")
            texto, inicios, digitalizadas = _montar_texto(doc)
            paginas = doc.page_count
    except ErroSeparacao:
        raise
    except Exception as erro:  # inexistente, não-PDF, estrutura quebrada
        raise ErroSeparacao(f"{type(erro).__name__}: {erro}") from erro

    atos, avisos = separar_texto(texto, inicios, especies)
    if digitalizadas:
        avisos.append(
            f"páginas digitalizadas {list(digitalizadas)}: atos nelas não foram "
            "vistos (precisam de OCR)"
        )
    if not atos and not digitalizadas and not texto.strip():
        avisos.append("nenhum texto extraível no caderno")
    return ResultadoSeparacao(tuple(atos), paginas, digitalizadas, tuple(avisos))