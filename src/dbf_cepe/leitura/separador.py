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
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

import pymupdf

from dbf_cepe.regras.especies import ESPECIES_ATIVAS, Especie
from dbf_cepe.regras.regras_atos import NOMES_DE_MARCADOR, limpar_ligaduras  # uma fonte só
from dbf_cepe.regras.regras_atos import identificar as identificar_atos
from dbf_cepe.leitura.reader import _pagina_digitalizada

# Linhas de cabeçalho/rodapé de página do Diário Oficial de Pernambuco. Só são
# removidas nas primeiras linhas de cada página (ver LINHAS_DE_TOPO).
_DIA_DA_SEMANA = r"(?:(?:segunda|ter[çc]a|quarta|quinta|sexta)-feira|s[áa]bado|domingo),\s*"
PADROES_TOPO_DE_PAGINA: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\d+\s*[-–]\s*Ano\s+[A-Z]+\b.*$"),  # "8 - Ano CX Ć NÀ 114", "9 – Ano XCIV • Nº 47"
    re.compile(r"^Ano\s+[A-Z]+\b.*[-–]\s*\d+\s*$"),  # "Ano CX Ć NÀ 114 - 9", "Ano XCIV • Nº 47 – 9"
    re.compile(r"^Ano\s+[A-Z]+\s+\S\s+N\S{0,2}\s*\d+\s*$"),  # "Ano XCV • Nº 47" (sem a página)
    # Sem o "Ano": "CX Ć NÀ 31 3" (Extra, páginas ímpares), "CII Ć NÀ 47- 9", "C Ć NÀ 52 - 9"
    re.compile(r"^[A-Z]{1,7}\s+\S\s+N\S{0,2}\s*\d+\s*(?:[-–]\s*)?\d+\s*$"),
    re.compile(r"^Di[aá]rio Oficial do Estado de Pernambuco\b.*$"),
    # "Recife, 26 de junho de 2026", "Recife, 1À de outubro de 2026" (o "º" de "1º" extraído
    # como "À"), "Recife, quinta-feira, 9 de março de 2017", "Recife,9 de setembro de 2015",
    # "Recife, 9 de maio de 2017 - 9" (com a página) e a data com o título do Diário na mesma
    # linha. Uma data terminada em ponto ("Recife, 21 de outubro de 2026.", a data de um ato)
    # NÃO casa.
    re.compile(
        rf"^Recife,\s*(?:{_DIA_DA_SEMANA})?\d{{1,2}}[ºª°À]?\s+de\s+\w+\s+de\s+\d{{4}}"
        r"(?:\s*[-–]\s*\d+|\s+Di[aá]rio\s+Oficial\b.*)?\s*$"
    ),
)
LINHAS_DE_TOPO = 4

# Expediente do Diário: o quadro com governador(a), vice, secretários, preços de
# publicação e endereço da CEPE, impresso uma vez por caderno. Quando ele divide a página
# com um decreto, o texto do quadro entra no meio do decreto (em todos os anos, até 2026).
# Ele é localizado pelas linhas típicas abaixo e removido pela POSIÇÃO: toda linha da
# página que cai dentro do retângulo dessas linhas sai do texto. Só age se a página tiver
# pelo menos `MINIMO_DE_MARCAS_DO_EXPEDIENTE` tipos de marca (de 7), entre eles o nome da CEPE e
# o cargo de governador(a) em linha própria; o rodapé de certificação digital da capa
# ("COMPANHIA EDITORA DE PERNAMBUCO" com o texto da MP 2.200-2) não basta.
MARCAS_DO_EXPEDIENTE: dict[str, re.Pattern[str]] = {
    "governador": re.compile(r"^\s*GOVERNADORA?\s*$"),
    "vice": re.compile(r"^\s*VICE[- ]?GOVERNADORA?\s*$"),
    "secretarios": re.compile(r"^\s*SECRET\S{1,2}RIOS\s+DE\s+ESTADO\s*$"),
    "cepe": re.compile(r"^\s*COMPANHIA\s+EDITORA\s+DE\s+PERNAMBUCO\s*$"),
    "contato": re.compile(r"(?:ouvidoria|cepecom|comercial)@cepe\.com\.br", re.IGNORECASE),
    "diretoria": re.compile(r"^\s*DIRETORA?\s+PRESIDENTE\s*$"),
    # Título do quadro, em maiúsculas (o cabeçalho de página é "Diário Oficial do Estado de
    # Pernambuco - Poder Executivo", em minúsculas, e já sai antes).
    "titulo": re.compile(r"^\s*DI\S{1,2}RIO\s+OFICIAL\s*-\s*PODER\s+EXECUTIVO\s*$"),
}
MINIMO_DE_MARCAS_DO_EXPEDIENTE = 4
_ATE_O_VICE = 3  # linhas entre "GOVERNADOR(A)" e "VICE-GOVERNADOR(A)" no quadro (o nome fica no meio)
# Cargo do quadro, em maiúsculas e sozinho na linha ("SECRETÁRIO DE DEFESA SOCIAL",
# "PROCURADORA-GERAL DO ESTADO"): a lista de secretários pode descer além da última marca.
# O quadro cresce PARA BAIXO (o par governador/vice é sempre o alto do quadro) para incluir
# o BLOCO de um cargo (cargo e nome) colado à borda de baixo (até `_FOLGA_DO_QUADRO` pontos)
# e alinhado à lista de secretários (margem esquerda a até `_ALINHAMENTO_DA_LISTA` pontos).
_CARGO_DO_QUADRO = re.compile(
    r"^\s*(?:SECRET\S{1,2}RI[OA]|PROCURADORA?-GERAL|CHEFE|SUPERINTENDENTE|DIRETORA?|GERENTE|COORDENADORA?)"
    r"\b[^a-zà-ú]*$"
)
_FOLGA_DO_QUADRO = 30.0
_ALINHAMENTO_DA_LISTA = 15.0
# Trechos que só existem no expediente: se algum sobrar no texto de um ato (quadro não
# removido), o critério da leitura em cascata reprova o ato (leitura/criterio.py). São
# linhas inteiras em maiúsculas, como no quadro: "Secretários de Estado" e "Diretor
# Presidente da EPTI" no corpo de um decreto não contam.
TRECHO_DO_EXPEDIENTE = re.compile(
    r"^[ \t]*(?:COMPANHIA\s+EDITORA\s+DE\s+PERNAMBUCO|SECRET\S{1,2}RIOS\s+DE\s+ESTADO|DIRETOR\s+PRESIDENTE)[ \t]*$"
    r"|Quaisquer\s+reclama[çc][õo]es\s+sobre\s+mat[ée]rias\s+publicadas",
    re.MULTILINE,
)

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


# Nome do caderno baixado pela etapa 1: "20260715_ResumoDiario.pdf", "20250916_PoderExecutivoExtra.pdf".
_DATA_NO_NOME = re.compile(r"^(\d{4})(\d{2})(\d{2})_")


def data_do_caderno(nome: str) -> date | None:
    """Data de publicação do caderno, tirada do nome do arquivo (AAAAMMDD_...);
    None se o nome não começar por uma data válida."""
    casamento = _DATA_NO_NOME.match(nome)
    if not casamento:
        return None
    try:
        return date(*(int(parte) for parte in casamento.groups()))
    except ValueError:
        return None


class ErroSeparacao(Exception):
    """O caderno não pôde ser aberto ou lido."""


@dataclass(frozen=True)
class AtoSeparado:
    """Transporte um ato recortado do caderno, com páginas e avisos de leitura."""
    especie: Especie
    numero: str  # sem separador de milhar, ex.: "60929"
    cabecalho: str  # ex.: "DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026"
    texto: str  # texto limpo, uma linha do Diário por linha
    pagina_inicial: int  # base 1
    pagina_final: int
    avisos: tuple[str, ...] = ()
    # Publicação: data do caderno (tirada do nome do arquivo, AAAAMMDD_...) e nome
    # do caderno. None quando o ato não veio de um caderno com data no nome.
    data_publicacao: date | None = None
    caderno: str | None = None
    # Leitura em cascata (leitura/cascata.py): o nível que aprovou o ato (1 = leitura
    # atual) ou None se nenhum aprovou; nesse caso, `reprovacao` diz por quê.
    nivel_de_leitura: int | None = 1
    reprovacao: tuple[str, ...] = ()
    # O caderno traz a marca de republicação ligada a este ato ("(REPUBLICADO POR HAVER
    # SAÍDO COM INCORREÇÃO NO ORIGINAL)" depois das assinaturas), pela mesma regra do
    # catálogo (regras_atos.identificar). A marca fica fora de `texto`.
    marcado_como_republicacao: bool = False


@dataclass(frozen=True)
class ResultadoSeparacao:
    """Agrupe atos separados, páginas digitalizadas e avisos da edição."""
    atos: tuple[AtoSeparado, ...]
    paginas: int
    paginas_digitalizadas: tuple[int, ...] = ()
    avisos: tuple[str, ...] = field(default=())


def _regex_marcadores(especies: tuple[Especie, ...]) -> re.Pattern[str]:
    """Início de QUALQUER ato conhecido (ativo ou não), no começo da linha."""
    # A lista fixa mora em regras_atos.py (uma fonte só); somam-se as espécies pedidas.
    nomes = set(NOMES_DE_MARCADOR) | {e.nome.upper() for e in especies}
    alternativas = "|".join(re.escape(n) for n in sorted(nomes, key=len, reverse=True))
    return re.compile(rf"(?m)^[ \t]*(?:{alternativas})\b")


# Uma linha da página com a posição: (texto, caixa da linha, caixa do bloco, nº do bloco).
_Linha = tuple[str, tuple[float, float, float, float], tuple[float, float, float, float], int]


def _itens_da_pagina(pagina: pymupdf.Page) -> list[_Linha]:
    return [
        ("".join(trecho["text"] for trecho in linha["spans"]), linha["bbox"], bloco["bbox"], numero)
        for numero, bloco in enumerate(pagina.get_text("dict")["blocks"])
        if bloco["type"] == 0
        for linha in bloco["lines"]
    ]


def _quadro_do_expediente(itens: list[_Linha]) -> tuple[float, float, float, float] | None:
    """Retângulo do quadro do expediente (ver MARCAS_DO_EXPEDIENTE), ou None se a página
    não o tiver."""
    marcadas = {
        marca: [i for i, item in enumerate(itens) if padrao.search(item[0])]
        for marca, padrao in MARCAS_DO_EXPEDIENTE.items()
    }
    # "GOVERNADOR(A)" sozinho na linha também aparece na assinatura de outros atos; no
    # quadro, ele vem seguido (até 3 linhas depois) de "VICE-GOVERNADOR(A)". Só esse par conta.
    vice = set(marcadas["vice"])
    marcadas["governador"] = [
        i for i in marcadas["governador"] if any(j in vice for j in range(i + 1, i + 1 + _ATE_O_VICE))
    ]
    marcadas["vice"] = [
        j for j in marcadas["vice"] if any(j - k in marcadas["governador"] for k in range(1, 1 + _ATE_O_VICE))
    ]
    tipos = sum(bool(posicoes) for posicoes in marcadas.values())
    if tipos < MINIMO_DE_MARCAS_DO_EXPEDIENTE or not marcadas["cepe"] or not marcadas["governador"]:
        return None
    # O quadro é o retângulo dos BLOCOS de texto das marcas: as linhas das marcas são
    # curtas e deixariam de fora preços e avisos que ficam ao lado delas.
    caixas = [itens[i][2] for posicoes in marcadas.values() for i in posicoes]
    x0, y0 = min(c[0] for c in caixas), min(c[1] for c in caixas)
    x1, y1 = max(c[2] for c in caixas), max(c[3] for c in caixas)
    margens = {itens[i][2][0] for i in marcadas["secretarios"]}
    cargos = [
        bloco for texto, _, bloco, _ in itens
        if _CARGO_DO_QUADRO.match(texto) and any(abs(bloco[0] - m) <= _ALINHAMENTO_DA_LISTA for m in margens)
    ]
    cresceu = True
    while cresceu:
        cresceu = False
        for b in cargos:
            if y1 < b[3] and y0 <= b[1] <= y1 + _FOLGA_DO_QUADRO and x0 <= (b[0] + b[2]) / 2 <= x1:
                y1 = b[3]
                cresceu = True
    return x0, y0, x1, y1


def _dentro(caixa: tuple[float, float, float, float], quadro: tuple[float, float, float, float]) -> bool:
    cx, cy = (caixa[0] + caixa[2]) / 2, (caixa[1] + caixa[3]) / 2
    return quadro[0] <= cx <= quadro[2] and quadro[1] <= cy <= quadro[3]


def _sem_expediente(pagina: pymupdf.Page, linhas: list[str]) -> list[str]:
    """As linhas da página sem as que caem no quadro do expediente do Diário (ver
    MARCAS_DO_EXPEDIENTE). Sem expediente, ou se a posição das linhas não puder ser
    conferida, devolve as linhas como vieram."""
    if not any(MARCAS_DO_EXPEDIENTE["cepe"].match(linha) for linha in linhas):
        return linhas  # atalho: quase nenhuma página tem o quadro
    itens = _itens_da_pagina(pagina)
    if [item[0] for item in itens] != linhas:
        return linhas  # a posição não corresponde ao texto extraído: não arrisca
    quadro = _quadro_do_expediente(itens)
    if quadro is None:
        return linhas
    return [texto for texto, caixa, _, _ in itens if not _dentro(caixa, quadro)]


def _sem_topo(linhas: list[str]) -> list[str]:
    topo = [
        i
        for i, linha in enumerate(linhas[:LINHAS_DE_TOPO])
        if any(p.match(linha.strip()) for p in PADROES_TOPO_DE_PAGINA)
    ]
    return [linha for i, linha in enumerate(linhas) if i not in set(topo)]


# Leitura por coluna (nível 3 da leitura em cascata, leitura/cascata.py). Um bloco de texto
# mais largo que esta fração da página atravessa as colunas (título, tabela larga) e divide
# a página em trechos; dentro de cada trecho, as colunas são as faixas horizontais ocupadas
# por blocos (a união das larguras deles, com `_FOLGA_ENTRE_COLUNAS` de tolerância), e os
# blocos são lidos coluna a coluna, de cima para baixo. A ementa recuada dentro da coluna
# continua na coluna dela.
_LARGURA_QUE_ATRAVESSA = 0.6
_FOLGA_ENTRE_COLUNAS = 2.0


def linhas_por_coluna(pagina: pymupdf.Page) -> list[str] | None:
    """As linhas da página na ordem visual (coluna a coluna), sem o expediente e o topo da
    página; None se a posição das linhas não corresponder ao texto extraído."""
    texto = pagina.get_text("text").split("\n")
    if texto and texto[-1] == "":
        texto.pop()
    itens = _itens_da_pagina(pagina)
    if [item[0] for item in itens] != texto:
        return None
    quadro = (
        _quadro_do_expediente(itens) if any(MARCAS_DO_EXPEDIENTE["cepe"].match(t) for t in texto) else None
    )
    blocos: dict[int, tuple[tuple[float, float, float, float], list[str]]] = {}
    for linha, caixa_da_linha, caixa_do_bloco, numero in itens:
        if quadro is not None and _dentro(caixa_da_linha, quadro):
            continue
        blocos.setdefault(numero, (caixa_do_bloco, []))[1].append(linha)
    limite = _LARGURA_QUE_ATRAVESSA * pagina.rect.width
    largos = sorted((b for b in blocos.values() if b[0][2] - b[0][0] > limite), key=lambda b: b[0][1])
    estreitos = [b for b in blocos.values() if b[0][2] - b[0][0] <= limite]
    cortes = [b[0][1] for b in largos]
    trechos: dict[int, list] = {}
    for bloco in estreitos:
        trechos.setdefault(sum(1 for c in cortes if c <= bloco[0][1]), []).append(bloco)
    ordem = []
    for k in range(len(largos) + 1):
        if k:
            ordem.append(largos[k - 1])
        do_trecho = trechos.get(k, [])
        colunas: list[list[float]] = []
        for x0, x1 in sorted((b[0][0], b[0][2]) for b in do_trecho):
            if colunas and x0 <= colunas[-1][1] + _FOLGA_ENTRE_COLUNAS:
                colunas[-1][1] = max(colunas[-1][1], x1)
            else:
                colunas.append([x0, x1])

        def coluna(bloco, colunas=colunas) -> int:
            return next(i for i, (x0, x1) in enumerate(colunas) if x0 - _FOLGA_ENTRE_COLUNAS <= bloco[0][0] <= x1)

        ordem += sorted(do_trecho, key=lambda b: (coluna(b), b[0][1]))
    return _sem_topo([linha for _, linhas in ordem for linha in linhas])


def _linhas_da_pagina(pagina: pymupdf.Page) -> list[str]:
    linhas = pagina.get_text("text").split("\n")
    # `get_text` termina com "\n": o último elemento é vazio.
    if linhas and linhas[-1] == "":
        linhas.pop()
    return _sem_topo(_sem_expediente(pagina, linhas))


def _montar_texto(
    doc: pymupdf.Document, por_coluna: bool = False
) -> tuple[str, list[int], tuple[int, ...]]:
    """Texto do caderno sem cabeçalhos de página.

    Devolve (texto, início_de_cada_página, páginas_digitalizadas); o segundo
    item permite descobrir em que página cai qualquer posição do texto. Com
    `por_coluna`, cada página é lida na ordem visual (`linhas_por_coluna`; a página
    em que a posição das linhas não confere sai na ordem do PDF).
    """
    partes: list[str] = []
    inicios: list[int] = []
    digitalizadas: list[int] = []
    posicao = 0
    for numero, pagina in enumerate(doc, start=1):
        if _pagina_digitalizada(pagina, pagina.get_text("text")):
            digitalizadas.append(numero)
        linhas = (linhas_por_coluna(pagina) if por_coluna else None) or _linhas_da_pagina(pagina)
        trecho = limpar_ligaduras("\n".join(linhas) + "\n")
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


def ler_caderno(caminho: Path, por_coluna: bool = False) -> tuple[str, list[int], tuple[int, ...], int]:
    """Abre o caderno e devolve (texto limpo, início de cada página, páginas
    digitalizadas, total de páginas). Só lê; levanta `ErroSeparacao` se o PDF não
    puder ser aberto. É o mesmo texto que `separar_caderno` usa (com `por_coluna`, o
    texto do nível 3 da leitura em cascata)."""
    try:
        with pymupdf.open(caminho) as doc:
            if doc.needs_pass:
                raise ErroSeparacao("PDF protegido por senha")
            if doc.page_count == 0:
                raise ErroSeparacao("PDF sem páginas")
            texto, inicios, digitalizadas = _montar_texto(doc, por_coluna)
            paginas = doc.page_count
    except ErroSeparacao:
        raise
    except Exception as erro:  # inexistente, não-PDF, estrutura quebrada
        raise ErroSeparacao(f"{type(erro).__name__}: {erro}") from erro
    return texto, inicios, digitalizadas, paginas


def separar_caderno(
    caminho: Path, especies: tuple[Especie, ...] = ESPECIES_ATIVAS
) -> ResultadoSeparacao:
    """Lê o caderno e devolve um `AtoSeparado` por ato das espécies pedidas."""
    texto, inicios, digitalizadas, paginas = ler_caderno(caminho)

    atos, avisos = separar_texto(texto, inicios, especies)
    nome = Path(caminho).name  # aceita também o caminho como texto
    publicado_em = data_do_caderno(nome)
    marcados = {
        (achado.especie, achado.numero, achado.pagina)
        for achado in identificar_atos(texto, inicios)
        if achado.republicacao
    }
    atos = [
        replace(
            ato, data_publicacao=publicado_em, caderno=nome,
            marcado_como_republicacao=(ato.especie.nome, ato.numero, ato.pagina_inicial) in marcados,
        )
        for ato in atos
    ]
    if digitalizadas:
        avisos.append(
            f"páginas digitalizadas {list(digitalizadas)}: atos nelas não foram "
            "vistos (precisam de OCR)"
        )
    if not atos and not digitalizadas and not texto.strip():
        avisos.append("nenhum texto extraível no caderno")
    return ResultadoSeparacao(tuple(atos), paginas, digitalizadas, tuple(avisos))
