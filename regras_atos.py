"""Regras para reconhecer atos no texto do Diário Oficial de Pernambuco.

Arquivo único, SÓ com a biblioteca padrão do Python (sem PyMuPDF, sem o resto
do projeto): pode ser copiado e rodado sozinho. Reconhece, em texto já extraído
do PDF de um caderno, estas espécies:

    Decreto, Edital, Portaria      -> cabeçalho, número, ano, órgão, tipo, data do ato
    Errata                         -> ato referido (espécie, número, ano) e destino
    Republicação                   -> marcador "(REPUBLICADO ...)" ligado ao ato certo

Uso mínimo:

    from regras_atos import identificar
    for achado in identificar(texto_do_caderno):
        print(achado.especie, achado.numero, achado.tipo, achado.data_do_ato)

Rodar `python regras_atos.py` confere a tabela EXEMPLOS (entrada -> resultado
esperado) e diz se alguma regra deixou de funcionar.

O que o código pressupõe sobre o texto (ver `limpar_texto`):
  - uma linha do Diário por linha do texto (as quebras do PDF são mantidas);
  - ligaduras "ﬁ", "ﬂ"... já convertidas (o Diário as traz seguidas de um espaço
    espúrio: "beneﬁ ciário");
  - o cabeçalho do ato está no INÍCIO de uma linha e em MAIÚSCULAS. Citações no
    corpo do texto ("o Decreto nº 21.959, de ...") nunca contam.

Casos de borda tratados (viram `avisos` do achado, nunca descarte em silêncio):
  - cabeçalho sem número ("EDITAL DE CONVOCAÇÃO", "PORTARIA SEE/SEGP DE 20 DE ...");
  - cabeçalho que parece citação quebrada em duas linhas ("... designada, por
    meio da" + "PORTARIA Nº 2224/2026, de 29 de julho de 2026, para compor ...");
  - data do ato que não está no cabeçalho (lida da linha "Recife, 22 de abril de
    2026." do fecho) ou que não existe;
  - marcador de republicação que não se liga a nenhum ato conhecido.

Evidência de cada padrão (mesma convenção do projeto): "amostra" = conferido em
caderno real; "publicado" = conferido em ato publicado, sem caderno versionado;
"presumido" = deduzido, falta amostra. Os tipos de edital e portaria nasceram
dos cadernos de 25/04, 26/06, 15/07, 21/10 e 22/10/2026.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from datetime import date

EVIDENCIA_AMOSTRA = "amostra"
EVIDENCIA_PUBLICADO = "publicado"
EVIDENCIA_PRESUMIDO = "presumido"

# ----------------------------------------------------------------------------
# 1. Limpeza de texto
# ----------------------------------------------------------------------------

_LIGADURAS = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"}
# Ligadura + espaço espúrio (cada ligadura do Diário vem seguida de um espaço).
_LIGADURA_COM_ESPACO = re.compile("([" + "".join(_LIGADURAS) + "]) ")
_ESPACOS_ESTRANHOS = str.maketrans({" ": " ", " ": " ", " ": " ", "﻿": " "})
_ESPACOS = re.compile(r"\s+")


def limpar_ligaduras(texto: str) -> str:
    """'beneﬁ ciário' -> 'beneficiário'; ligadura sem espaço também é convertida."""
    texto = _LIGADURA_COM_ESPACO.sub(lambda m: _LIGADURAS[m.group(1)], texto)
    for ligadura, letras in _LIGADURAS.items():
        texto = texto.replace(ligadura, letras)
    return texto


def limpar_texto(texto: str) -> str:
    """Ligaduras convertidas e espaços estranhos (não separável etc.) trocados
    por espaço comum. Mantém as quebras de linha. Em texto já limpo não muda
    nada, então as posições calculadas antes e depois coincidem."""
    return limpar_ligaduras(texto).translate(_ESPACOS_ESTRANHOS)


def juntar_linhas(trecho: str) -> str:
    """Uma linha só, espaços colapsados. O texto justificado do Diário chega com
    palavras separadas por quebra de linha ('Na \\npublicação \\nreferente')."""
    return _ESPACOS.sub(" ", trecho).strip()


# ----------------------------------------------------------------------------
# 2. Peças reutilizadas
# ----------------------------------------------------------------------------

# "ﬁ" seguido de espaço espúrio ("modiﬁ cações"): um PDF avulso pode chegar assim.
_FI = r"(?:fi|ﬁ\s?)"
# "ção" ou "ções" (a, depois o plural), com ou sem acento.
_CAO = r"(?:[çc][ãa]o|[çc][õo]es)"
# "Dispõe sobre a(s) <assunto>": o Diário usa o plural quando o decreto trata de
# mais de um ato (ex.: "Dispõe sobre as prorrogações dos prazos...").
_DISPOE_SOBRE_A = r"Disp[õo]e\s+sobre\s+(?:a|as)\s+"
# "Decreto nº" com as variantes de grafia do "nº".
_DECRETO_N = r"Decreto\s+n\s*\.?\s*[ºo°]\.?\s*"
# Decretos que regulamentam os programas da DBF: PRODEPE (21.959/1999), PEAP
# (34.560/2010) e RICMS-PE (44.650/2017). Alterar um deles é alterar a NORMA,
# não o ato de uma empresa.
_DECRETOS_DE_PROGRAMA = rf"{_DECRETO_N}(?:21\.?959|34\.?560|44\.?650)\b"

# "Nº", "N°", "N.º", "Nº." e também "n°" (minúsculo). Sem letra colada antes
# ("PE" + "Nº" colados valem só com o símbolo º/°) nem depois ("NOTIFICAÇÃO" não é "No").
_ORDINAL = (
    r"(?:(?<![A-Za-zÀ-ú])[Nn]\s*\.?\s*[º°o](?![A-Za-zÀ-ú])"
    r"|[Nn]\s*\.?\s*[º°])\.?"
)
# Número do ato: "60.929" (com ponto de milhar), "017", "56" ou código composto
# "4050.2025.0021.SES" / "0014.2026.PNE" (edital de credenciamento).
_COMPOSTO = r"\d{2,4}\.\d{4}\.(?:\d{4}\.)?[A-Z]{2,}"
_NUMERO = rf"(?:{_COMPOSTO}|\d{{1,3}}(?:\.\d{{3}})+|\d+)"

# ----------------------------------------------------------------------------
# 3. Decreto
# ----------------------------------------------------------------------------

# Cabeçalho em MAIÚSCULAS, ex.: "DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026".
# Propositalmente sensível a maiúsculas: as citações no corpo do texto ("Decreto
# nº 21.959, de 27 de dezembro de 1999") não podem ser confundidas com o
# cabeçalho. Tolera "N°", "N.º", "Nº", número sem ponto de milhar, vírgula
# ausente antes de "DE" e dia "1º".
CABECALHO_DECRETO = re.compile(
    r"(?<![A-Za-zÀ-ú])DECRETO\s+N\s*\.?\s*[º°o]\.?\s*"
    r"(?P<numero>\d{1,3}(?:\.\d{3})+|\d+)\s*,?\s*"
    r"DE\s+\d{1,2}\s*[º°o]?\s+DE\s+[A-ZÇÃÕÁÀÂÉÊÍÓÔÚ]+\s+DE\s+(?P<ano>\d{4})"
)

# Início de QUALQUER decreto (palavra em maiúsculas no começo da linha), inclusive o
# que não casa com o cabeçalho completo (sem data, número ilegível...): sai com aviso.
INICIO_DECRETO = re.compile(r"(?m)^[ \t]*DECRETO\b")

# Primeira frase depois da ementa: delimita a ementa e confirma que o cabeçalho
# abre um ato e não é uma citação.
ABERTURA_DO_CORPO_DECRETO = re.compile(r"\b(?:A GOVERNADORA|O GOVERNADOR)\b")
# "... e a Independência do Brasil." encerra o texto do decreto, antes das assinaturas.
FECHO_DECRETO = re.compile(r"Independência\s+do\s+Brasil\.", re.IGNORECASE)


def _inicio(expressao: str) -> re.Pattern[str]:
    return re.compile(expressao, re.IGNORECASE)


# A classificação olha só o INÍCIO da ementa (aplicada com .match()). Buscar a
# palavra no texto todo erraria: a ementa de uma revogação pode citar
# "prorrogação", e o corpo da renovação cita "prorrogação" e "prorrogados".
# A ORDEM IMPORTA: as variantes mais específicas vêm antes das gerais (segunda
# prorrogação antes de prorrogação; alteração da norma antes da alteração de
# empresa). O credenciamento no PEAP é feito por EDITAL, não por decreto.
# Formato: (nome, padrão, evidência).
TIPOS_DECRETO: tuple[tuple[str, re.Pattern[str], str], ...] = (
    # Concessão do estímulo a uma empresa. Amostra: 60929 e os 28 do caderno de 26/06/2026.
    (
        "CONCESSAO",
        _inicio(r"(?:Concede\b|Disp[õo]e\s+sobre\s+a\s+concess[ãa]o\b)"),
        EVIDENCIA_AMOSTRA,
    ),
    # Alteração da NORMA do programa (não é ato de empresa). Publicado: "Introduz
    # modificações no Decreto nº 21.959", "Altera o Decreto nº 21.959", "Modifica o
    # Decreto nº 44.650 ...". O discriminador é o decreto citado.
    (
        "ALTERACAO_NORMA",
        _inicio(
            rf"(?:Introduz\s+(?:altera[çc][õo]es|modi{_FI}ca[çc][õo]es)|"
            rf"Altera|Modi{_FI}ca|D[áa]\s+nova\s+reda[çc][ãa]o)\b"
            rf"(?=.{{0,400}}?{_DECRETOS_DE_PROGRAMA})"
        ),
        EVIDENCIA_PUBLICADO,
    ),
    # Alteração do decreto de uma empresa. Amostra: 60951 (plural); 60.134/2025 usa o singular.
    ("ALTERACAO", _inicio(r"Introduz\s+altera[çc][õo]es\b"), EVIDENCIA_AMOSTRA),
    # Segunda prorrogação / renovação: sem amostra real, padrão presumido.
    (
        "SEGUNDA_PRORROGACAO",
        _inicio(rf"{_DISPOE_SOBRE_A}(?:segunda|2\s*[ªa°º]?)\s+prorroga{_CAO}\b"),
        EVIDENCIA_PRESUMIDO,
    ),
    (
        "SEGUNDA_RENOVACAO",
        _inicio(rf"{_DISPOE_SOBRE_A}(?:segunda|2\s*[ªa°º]?)\s+renova{_CAO}\b"),
        EVIDENCIA_PRESUMIDO,
    ),
    # Prorrogação e renovação. Amostra: 60950 e 60945; publicado o plural em 58.817/2025.
    ("PRORROGACAO", _inicio(rf"{_DISPOE_SOBRE_A}prorroga{_CAO}\b"), EVIDENCIA_AMOSTRA),
    ("RENOVACAO", _inicio(rf"{_DISPOE_SOBRE_A}renova{_CAO}\b"), EVIDENCIA_AMOSTRA),
    # Transferência do estímulo para outra empresa. Amostra: 60505. "Transfere" é presumido.
    (
        "TRANSFERENCIA",
        _inicio(rf"(?:{_DISPOE_SOBRE_A}transfer[êe]ncias?\b|Transfere\b)"),
        EVIDENCIA_AMOSTRA,
    ),
    # PROIND. Amostra: 60.903 e 60.913 (caderno de 26/06/2026).
    (
        "AUTORIZACAO_PROIND",
        _inicio(r"Autoriza\s+a\s+utiliza[çc][ãa]o\s+do\s+(?:incentivo|benef[íi]cio)\b"),
        EVIDENCIA_AMOSTRA,
    ),
    # Revogação. Publicado: 39.773/2013. "Torna sem efeito" e "Dispõe sobre a revogação": presumidos.
    (
        "REVOGACAO",
        _inicio(
            r"(?:Revoga\b|Torna\s+sem\s+efeito\b|Disp[õo]e\s+sobre\s+a\s+revoga[çc][ãa]o\b)"
        ),
        EVIDENCIA_PUBLICADO,
    ),
    # Terceirização da industrialização. Amostra: 60233.
    ("TERCEIRIZACAO", _inicio(r"Autoriza\s+a\s+terceiriza[çc][ãa]o\b"), EVIDENCIA_AMOSTRA),
)

# ----------------------------------------------------------------------------
# 4. Edital
# ----------------------------------------------------------------------------

# Início de QUALQUER edital: a palavra em maiúsculas no começo da linha.
INICIO_EDITAL = re.compile(r"(?m)^[ \t]*EDITAL\b")

# Cabeçalho com número. O "qualificador" (entre EDITAL e o número, na mesma
# linha, sem dígitos) é o órgão ("DBF", "FACEPE/SECTI") ou o tipo ("DE
# CREDENCIAMENTO"); quem separa os dois é `_qualificador`. Formatos vistos:
#   EDITAL DBF Nº 017 /2026                 EDITAL DE INTIMAÇÃO Nº 005/2026
#   EDITAL Nº 43/2026                       EDITAL FACEPE 35/2026 - PBPG ...
#   EDITAL DE CREDENCIAMENTO Nº. 4050.2025.0021.SES.     EDITAL DE CREDENCIAMENTO N° 0014.2026.PNE
CABECALHO_EDITAL = re.compile(
    r"(?m)^[ \t]*EDITAL\b"
    r"(?P<qualificador>[^\n\d]{0,70}?)"
    rf"(?:\s*{_ORDINAL}\s*|\s+(?=\d+\s*/\s*\d{{4}}))"
    rf"(?P<numero>{_NUMERO})"
    r"(?:\s*/\s*(?P<ano>\d{4}))?"
)

# Tipo = início do título (qualificador "DE <tipo>" ou a linha seguinte ao cabeçalho).
# Aplicado com .match(). Amostra: dbf (credenciamento), SES/SAD/FUNDARPE, FACEPE etc.
TIPOS_EDITAL: tuple[tuple[str, re.Pattern[str], str], ...] = (
    # "resolve descredenciar" apareceu em edital da DPC; falta um da DBF: presumido.
    ("DESCREDENCIAMENTO", _inicio(r"(?:Descredenciamento|Descredenciar)\b"), EVIDENCIA_PRESUMIDO),
    ("CREDENCIAMENTO", _inicio(r"Credenciamento\b"), EVIDENCIA_AMOSTRA),
    ("INTIMACAO", _inicio(r"Intima[çc][ãa]o\b"), EVIDENCIA_AMOSTRA),
    ("CONVOCACAO", _inicio(r"Convoca[çc][ãa]o\b"), EVIDENCIA_AMOSTRA),
    ("CHAMAMENTO_PUBLICO", _inicio(r"Chamamento\s*P[úu]blico\b"), EVIDENCIA_AMOSTRA),
    ("CONCURSO_PUBLICO", _inicio(r"Concurso\s+P[úu]blico\b"), EVIDENCIA_AMOSTRA),
    ("NAO_CONCORRENCIA", _inicio(r"N[ãa]o\s+Concorr[êe]ncia\b"), EVIDENCIA_AMOSTRA),
    ("DIVULGACAO", _inicio(r"Divulga[çc][ãa]o\b"), EVIDENCIA_AMOSTRA),
    ("NOTIFICACAO", _inicio(r"Notifica[çc][ãa]o\b"), EVIDENCIA_AMOSTRA),
)

# Órgãos que contam como SEFAZ/DBF no escopo deste projeto. DBM foi informado pelo
# responsável do projeto como outro nome válido da DBF; não aparece em nenhum dos
# cadernos de amostra (evidência presumida).
ORGAOS_EDITAL_SEFAZ = frozenset({"DBF", "DBM"})

# ----------------------------------------------------------------------------
# 5. Portaria
# ----------------------------------------------------------------------------

INICIO_PORTARIA = re.compile(r"(?m)^[ \t]*PORTARIA\b")

# Formatos vistos (todos com "Nº"):
#   PORTARIA SF Nº 76, DE 24.04.2026          PORTARIA FUNASE Nº 396/2026, de 25 de junho de 2026.
#   PORTARIA SEE Nº 3669DE 25 DE JUNHO DE 2026   PORTARIA CONJUNTA SAD/UPE Nº 126 DO DIA 24 DE ABRIL DE 2026
#   PORTARIA Nº 937/2026 de 22.04.2026        PORTARIA DO COMANDO-GERAL Nº 340/DGP3, de 22 de abril de 2026.
#   PORTARIA ADMINISTRATIVA nº 56 / 2026 - CBMPE - DGP - DA, DE 19 de junho de 2026.
CABECALHO_PORTARIA = re.compile(
    r"(?m)^[ \t]*PORTARIA\b"
    r"(?P<qualificador>[^\n\d]{0,70}?)"
    rf"\s*{_ORDINAL}\s*"
    rf"(?P<numero>{_NUMERO})"
    r"(?:\s*/\s*(?P<ano>\d{4}))?"
)

# Tipo = primeiro verbo operativo do ato (depois de "RESOLVE:" quando houver).
# Formato: (nome, padrão, evidência). Procurado em qualquer ponto da janela.
TIPOS_PORTARIA: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("DESIGNACAO", _inicio(r"\bDesignar\b"), EVIDENCIA_AMOSTRA),
    ("DISPENSA", _inicio(r"\bDispensar\b"), EVIDENCIA_AMOSTRA),
    ("EXONERACAO", _inicio(r"\bExonerar\b"), EVIDENCIA_AMOSTRA),
    ("CONCESSAO", _inicio(r"\bConceder\b"), EVIDENCIA_AMOSTRA),
    ("PRORROGACAO", _inicio(r"\bProrrogar\b"), EVIDENCIA_AMOSTRA),
    ("SUBSTITUICAO", _inicio(r"\bProceder\s+à\s+substitui[çc][ãa]o\b"), EVIDENCIA_AMOSTRA),
    # SF 128/2026: "A Portaria SF nº 002 ... passa a vigorar com as seguintes modificações".
    ("ALTERACAO", _inicio(r"\bpassa\s+a\s+vigorar\b"), EVIDENCIA_AMOSTRA),
    # SF 200/2026: "Declarar a perda por renúncia ao incentivo do PRODEPE".
    ("PERDA_INCENTIVO", _inicio(r"\bDeclarar\s+a\s+perda\b"), EVIDENCIA_AMOSTRA),
)
_RESOLVE = re.compile(r"\bRESOLVE\s*:?")
_JANELA_TIPO_PORTARIA = 900  # caracteres depois do cabeçalho

# Portaria da SEFAZ: órgão "SF" no cabeçalho (PORTARIA SF Nº 76, DE 24.04.2026).
ORGAOS_PORTARIA_SEFAZ = frozenset({"SF"})

# ----------------------------------------------------------------------------
# 6. Errata, retificação e republicação
# ----------------------------------------------------------------------------

# Errata: linha que começa com "ERRATA" ou "Errata" (não tem número próprio; a
# chave é o ato referido).
ERRATA_LINHA = re.compile(r"(?m)^[ \t]*(?:ERRATA|Errata)\b")
# Retificação é outro mecanismo (não é errata); fica separada para não misturar.
RETIFICACAO_LINHA = re.compile(r"(?m)^[ \t]*(?:AVISO DE )?RETIFICA[ÇC][ÃA]O\b")

# Marcador de republicação. "republicado no DOE" dentro de texto normativo
# ("o Decreto nº 56.378, republicado no DOE de 06/06/2024") e a "Revolução
# Republicana" do fecho dos decretos NÃO são republicação.
REPUBLICACAO_MARCADOR = re.compile(
    r"republicad[oa]s?\s+por\s+haver(?:em)?\s+sa[ií]d[oa]s?"
    r"|republicad[oa]s?\s+por\s+(?:haver\s+)?(?:incorre[çc][ãa]o|inconsist[êe]ncia)"
    r"|\d[ªº]\s*republica[çc][ãa]o"
    r"|aviso\s+de\s+republica[çc][ãa]o"
    r"|republica[çc][ãa]o\s+d[oe]s?\s+edital",
    re.IGNORECASE,
)

# Espécies que uma errata pode corrigir (as três primeiras são as de interesse;
# as demais viram destino "outro").
ESPECIES_REFERIDAS = (
    "Decreto", "Portaria", "Edital", "Despacho", "Resolução", "Lei Complementar", "Lei",
    "Instrução Normativa", "Termo de Fomento", "Contrato", "Aviso", "Ato",
)
ATO_REFERIDO = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, ESPECIES_REFERIDAS), key=len, reverse=True)) + r")\b"
    r"(?:[^\n.;:]{0,80}?\bn[ºo°]\.?\s*(\d[\d./-]*))?",
    re.IGNORECASE,
)
DESTINOS_ERRATA = {"decreto": "decreto", "edital": "edital", "portaria": "portaria"}

# ----------------------------------------------------------------------------
# 7. Datas
# ----------------------------------------------------------------------------

_MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4, "maio": 5,
    "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12,
}
# "24.04.2026", "25/06/2026", "13.4.2016"
_DATA_NUMERICA = re.compile(r"(?<![\d.])(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)")
# "25 DE JUNHO DE 2026", "1º de julho de 2026", "DE22 DE ABRIL DE 2026"
_DATA_EXTENSO = re.compile(
    r"(?<!\d)(\d{1,2})\s*[º°]?\s*(?:de|do)\s*([A-Za-zçÇãÃ]{3,9})\s*(?:de|do)\s*(\d{4})(?!\d)",
    re.IGNORECASE,
)
# Fecho do edital e de algumas portarias: "Recife, 22 de abril de 2026."
_DATA_DO_FECHO = re.compile(
    r"Recife,?\s+(?P<data>\d{1,2}\s*[º°]?\s*de\s+[A-Za-zçÇãÃ]{3,9}\s+de\s+\d{4})", re.IGNORECASE
)


def _montar_data(dia: str, mes: int | None, ano: str) -> str | None:
    if mes is None:
        return None
    try:
        return date(int(ano), mes, int(dia)).isoformat()
    except ValueError:
        return None


def data_no_texto(texto: str) -> str | None:
    """Primeira data do trecho, como AAAA-MM-DD; None se não houver data válida."""
    candidatas: list[tuple[int, str | None]] = []
    for m in _DATA_NUMERICA.finditer(texto):
        candidatas.append((m.start(), _montar_data(m.group(1), int(m.group(2)), m.group(3))))
    for m in _DATA_EXTENSO.finditer(texto):
        mes = _MESES.get(m.group(2).casefold())
        candidatas.append((m.start(), _montar_data(m.group(1), mes, m.group(3))))
    validas = sorted((pos, data) for pos, data in candidatas if data)
    return validas[0][1] if validas else None


# ----------------------------------------------------------------------------
# 8. Marcadores de início de ato (onde o trecho de um ato termina)
# ----------------------------------------------------------------------------

# Quando o documento COMEÇA por uma destas espécies, é outro tipo de ato.
NOMES_OUTRAS_ESPECIES: tuple[str, ...] = (
    "LEI COMPLEMENTAR",
    "LEI",
    "PORTARIA",
    "EDITAL",
    "ERRATA",
    "RESOLUÇÃO",
    "INSTRUÇÃO NORMATIVA",
    "COMUNICADO",
    "DECISÃO",
    "RETIFICAÇÃO",
    "DELIBERAÇÃO",
    "ORDEM DE SERVIÇO",
)
_NOMES_DE_MARCADOR = (
    set(NOMES_OUTRAS_ESPECIES)
    | {"DECRETO", "ATOS DO DIA", "ATO DO DIA", "EXTRATO", "AVISO", "DESPACHO"}
)
MARCADOR_DE_ATO = re.compile(
    r"(?m)^[ \t]*(?:"
    + "|".join(re.escape(n) for n in sorted(_NOMES_DE_MARCADOR, key=len, reverse=True))
    + r"|Errata)\b"
)

# Linha que continua uma frase quebrada: termina em vírgula ou em preposição/
# artigo minúsculos ("... designada, por meio da" + "PORTARIA Nº 2224/2026"). Um
# cabeçalho precedido por ela é provavelmente uma citação, não um ato.
_CONTINUA_FRASE = re.compile(
    r"(?:,|\b(?:da|de|do|das|dos|a|o|as|os|e|ou|em|no|na|nos|nas|ao|aos|à|às|por|"
    r"para|com|que|pela|pelo|pelas|pelos|conforme|nº|n°))\s*$"
)

# Linha de assinatura/cargo: curta, sem dígitos nem pontuação final ("Secretário
# da Fazenda", "RAQUEL TEIXEIRA LYRA LUCENA", "Diretor").
_PONTUACAO_FINAL = (".", ":", ";", ",")
_MAX_PALAVRAS_ASSINATURA = 12

# Linha que fecha o ato (usada para dizer se a republicação veio logo após o fecho).
_FECHO_POR_ESPECIE = {
    "Decreto": FECHO_DECRETO,
    "Portaria": re.compile(r"\bentra(?:r[áa])?\s+em\s+vigor\b|\bRESOLVE\b|\bPublique-se\b", re.IGNORECASE),
    "Edital": re.compile(r"Recife,?\s+\d", re.IGNORECASE),
}

_LIMITE_DATA_DO_FECHO = 3000  # caracteres do ato em que se procura "Recife, <data>"


# ----------------------------------------------------------------------------
# 9. Resultado
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Achado:
    """Um ato (ou errata, ou republicação sem ato) reconhecido no texto."""

    especie: str  # "Decreto" | "Edital" | "Portaria" | "Errata" | "Republicação"
    numero: str | None  # sem separador de milhar ("60929"); None = sem número no cabeçalho
    ano: str | None
    orgao: str | None  # "DBF", "SF", "SEE/SEGP"; None = o cabeçalho não traz órgão
    tipo: str | None  # None = nenhum tipo conhecido casou ("a confirmar")
    data_do_ato: str | None  # AAAA-MM-DD
    posicao: int  # início do cabeçalho no texto
    fim: int  # fim do trecho do ato (próximo marcador de ato)
    trecho: str  # começo do ato, em uma linha
    sefaz: bool | None = None  # no escopo SEFAZ/DBF? None = não se aplica ou a confirmar
    republicacao: str = ""  # "" | "sim" (logo após o fecho / antes do cabeçalho) | "provavel"
    destino: str | None = None  # errata: "decreto" | "edital" | "portaria" | "outro"
    ato_referido: str | None = None  # errata: "Decreto 61.560", "Despacho", "?"
    ref_numero: str | None = None  # errata: número do ato referido, sem milhar
    ref_ano: str | None = None  # errata: ano do ato referido, quando consta
    pagina: int | None = None  # base 1, se `inicios_de_pagina` foi passado
    avisos: tuple[str, ...] = field(default=())


def _compactar(texto: str, limite: int = 160) -> str:
    return juntar_linhas(texto)[:limite]


# ----------------------------------------------------------------------------
# 10. Reconhecimento
# ----------------------------------------------------------------------------


def _qualificador(bruto: str, especie: str = "Edital") -> tuple[str | None, str | None]:
    """Separa o texto entre ESPÉCIE e número em (órgão, tipo_do_titulo).

    Edital: "DE CREDENCIAMENTO" -> (None, "CREDENCIAMENTO"); "DBF" -> ("DBF", None).
    Portaria: "DO COMANDO-GERAL" -> ("COMANDO-GERAL", None); "SF" -> ("SF", None)
    (o "DO/DA/DE" só introduz o nome do órgão; a portaria não traz tipo no cabeçalho)."""
    texto = juntar_linhas(bruto)
    if not texto:
        return None, None
    m = re.match(r"(?:DE|DO|DA)\s+(.+)$", texto)
    if m:
        return (m.group(1), None) if especie == "Portaria" else (None, m.group(1))
    return texto, None


def _so_ate_o_numero(resto_da_linha: str) -> str:
    """Do que vem depois de PORTARIA/EDITAL numa linha sem número, só as palavras
    do órgão ou do tipo: para na primeira palavra com dígito ou na data
    ("SEE/SEGP DE 20 DE OUTUBRO ..." -> "SEE/SEGP"; "DE CONVOCAÇÃO" fica inteiro)."""
    palavras = resto_da_linha.split()
    saida: list[str] = []
    for indice, palavra in enumerate(palavras):
        if any(c.isdigit() for c in palavra):
            break
        if palavra in ("DE", "DO", "DA") and indice > 0 and any(
            c.isdigit() for c in "".join(palavras[indice + 1 : indice + 2])
        ):
            break
        saida.append(palavra)
    return " ".join(saida[:8])


def _ano_do_composto(numero: str) -> str | None:
    partes = numero.split(".")
    return partes[1] if len(partes) >= 3 and re.fullmatch(r"\d{4}", partes[1]) else None


def _numero_sem_milhar(numero: str) -> str:
    """'60.929' -> '60929', mas código composto ('4050.2025.0021.SES') fica como está."""
    return numero if re.search(r"[A-Z]", numero) else numero.replace(".", "")


def _linha_anterior(texto: str, posicao: int) -> str:
    """Última linha não vazia antes de `posicao` (sem espaços nas pontas)."""
    for linha in reversed(texto[:posicao].splitlines()):
        if linha.strip():
            return linha.strip()
    return ""


def _parece_citacao(texto: str, posicao: int) -> bool:
    anterior = _linha_anterior(texto, posicao)
    return bool(anterior) and bool(_CONTINUA_FRASE.search(anterior)) and not anterior.isupper()


def _titulo_do_edital(texto: str, fim_cabecalho: int, qualificador_tipo: str | None) -> str:
    if qualificador_tipo:
        return qualificador_tipo
    for linha in texto[fim_cabecalho : fim_cabecalho + 400].splitlines():
        if linha.strip():
            return linha.strip()
    return ""


def _classificar(tipos: tuple[tuple[str, re.Pattern[str], str], ...], texto: str, *, inicio: bool) -> str | None:
    for nome, padrao, _ in tipos:
        achou = padrao.match(texto) if inicio else padrao.search(texto)
        if achou:
            return nome
    return None


def _tipo_da_portaria(texto: str, fim_cabecalho: int) -> str | None:
    janela = texto[fim_cabecalho : fim_cabecalho + _JANELA_TIPO_PORTARIA]
    resolve = _RESOLVE.search(janela)
    if resolve:
        janela = janela[resolve.end() :]
    melhor: tuple[int, str] | None = None
    for nome, padrao, _ in TIPOS_PORTARIA:
        achou = padrao.search(janela)
        if achou and (melhor is None or achou.start() < melhor[0]):
            melhor = (achou.start(), nome)
    return melhor[1] if melhor else None


def _tipo_do_decreto(texto: str, fim_cabecalho: int) -> str | None:
    resto = texto[fim_cabecalho:]
    abertura = ABERTURA_DO_CORPO_DECRETO.search(resto, 0, 1500)
    ementa = resto[: abertura.start()] if abertura else resto[:600]
    ementa = juntar_linhas(ementa).lstrip(" .")
    return _classificar(tuple(TIPOS_DECRETO), ementa, inicio=True)


def _data_do_fecho(texto: str, inicio: int, fim: int) -> str | None:
    m = _DATA_DO_FECHO.search(texto, inicio, min(fim, inicio + _LIMITE_DATA_DO_FECHO))
    return data_no_texto(m.group("data")) if m else None


def _fim_da_linha(texto: str, posicao: int) -> int:
    fim = texto.find("\n", posicao)
    return len(texto) if fim == -1 else fim


@dataclass(frozen=True)
class AtoReferido:
    """O ato que uma errata corrige, lido do começo dela."""

    especie: str | None  # "Decreto", "Despacho"... None = nenhuma espécie conhecida
    numero_texto: str | None  # como está escrito: "61.560", "010/2026"
    numero: str | None  # sem milhar e sem ano: "61560", "010"
    ano: str | None
    destino: str  # "decreto" | "edital" | "portaria" | "outro"

    @property
    def rotulo(self) -> str:
        """"Decreto 61.560", "Despacho" (sem número) ou "?" (nenhuma espécie)."""
        if not self.especie:
            return "?"
        if not self.numero_texto:
            return self.especie
        return f"{self.especie} {self.numero_texto.rstrip('.,;-')}"


def ato_referido_da_errata(trecho: str) -> AtoReferido:
    """Lê, do começo da errata, qual ato ela corrige: espécie, número e ano."""
    resto = re.sub(r"^\s*errata\b", "", juntar_linhas(trecho), flags=re.IGNORECASE)
    achado = ATO_REFERIDO.search(resto)
    if not achado:
        return AtoReferido(None, None, None, None, "outro")
    especie = achado.group(1)
    especie = especie[0].upper() + especie[1:].lower()
    numero_texto = achado.group(2).rstrip(".,;-") if achado.group(2) else None
    numero = ano = None
    if numero_texto:
        pedacos = numero_texto.split("/")
        numero = _numero_sem_milhar(pedacos[0])
        if len(pedacos) > 1 and re.fullmatch(r"\d{4}", pedacos[1]):
            ano = pedacos[1]
        else:
            data = data_no_texto(resto[achado.end() : achado.end() + 80])
            ano = data[:4] if data else None
    return AtoReferido(
        especie, numero_texto, numero, ano, DESTINOS_ERRATA.get(especie.casefold(), "outro")
    )


def _e_linha_de_assinatura(linha: str) -> bool:
    limpa = linha.strip()
    if not limpa:
        return True
    return (
        len(limpa) <= 90
        and not any(c.isdigit() for c in limpa)
        and not limpa.endswith(_PONTUACAO_FINAL)
        and limpa[0].isupper()
        and len(limpa.split()) <= _MAX_PALAVRAS_ASSINATURA
    )


def _logo_apos_o_fecho(texto: str, inicio_do_ato: int, marcador: int, especie: str) -> bool:
    """Entre o fecho do ato e o marcador só há assinaturas/linhas em branco?"""
    fecho = _FECHO_POR_ESPECIE[especie]
    linhas = texto[inicio_do_ato:marcador].splitlines()
    for linha in reversed(linhas):
        if not linha.strip():
            continue
        if _e_linha_de_assinatura(linha) and not fecho.search(linha):
            continue
        return bool(fecho.search(linha))
    return False


def _e_rodape(texto: str, posicao: int) -> bool:
    """O marcador é um rodapé do ato anterior? "(REPUBLICADO POR HAVER SAÍDO ...)" entre
    parênteses, ou "Republicada por haver inconsistência ..." no começo da linha."""
    inicio = texto.rfind("\n", 0, posicao) + 1
    linha = texto[inicio : _fim_da_linha(texto, posicao)].strip()
    return linha.startswith("(") or linha.lower().startswith("republicad")


def _ato_logo_depois(texto: str, posicao: int, por_posicao: dict[int, int]) -> int | None:
    """Índice do ato cujo cabeçalho é a primeira linha não vazia depois da linha de
    `posicao` (até 3 linhas), ou None."""
    cursor = _fim_da_linha(texto, posicao) + 1
    for _ in range(3):
        if cursor >= len(texto):
            return None
        fim_linha = _fim_da_linha(texto, cursor)
        linha = texto[cursor:fim_linha]
        if linha.strip():
            return por_posicao.get(cursor + len(linha) - len(linha.lstrip()))
        cursor = fim_linha + 1
    return None


def _ligar_republicacoes(
    texto: str, atos: list[Achado], pagina_de
) -> tuple[list[Achado], list[Achado]]:
    """Liga cada marcador de republicação ao ato certo.

    DEPOIS do fecho: o rodapé "(REPUBLICADO ...)" vem depois das assinaturas do
    ato (decretos e portarias). ANTES do cabeçalho: "AVISO DE ABERTURA - 2ª
    REPUBLICAÇÃO DE EDITAL" logo acima de "EDITAL DE CREDENCIAMENTO ...". Marcador
    que cai fora do trecho de qualquer ato (lista de atos de outra espécie,
    tabela, resolução) volta como Achado "Republicação", com aviso.
    """
    estado: dict[int, str] = {}  # índice do ato -> "sim" | "provavel"
    orfaos: list[Achado] = []
    inicios = [a.posicao for a in atos]
    por_posicao = {posicao: i for i, posicao in enumerate(inicios)}
    for m in REPUBLICACAO_MARCADOR.finditer(texto):
        alvo: int | None = None
        grau = "provavel"
        if not _e_rodape(texto, m.start()):
            alvo = _ato_logo_depois(texto, m.start(), por_posicao)
            if alvo is not None:
                grau = "sim"
        if alvo is None:
            indice = bisect.bisect_right(inicios, m.start()) - 1
            if indice >= 0 and atos[indice].posicao <= m.start() < atos[indice].fim:
                alvo = indice
                # Só até o começo da linha do marcador: o "(" que o abre não é assinatura.
                inicio_da_linha = texto.rfind("\n", 0, m.start()) + 1
                if _logo_apos_o_fecho(texto, atos[indice].posicao, inicio_da_linha, atos[indice].especie):
                    grau = "sim"
        if alvo is None:
            orfaos.append(
                Achado(
                    "Republicação", None, None, None, None, None, m.start(), m.end(),
                    _compactar(texto[max(0, m.start() - 60) : m.end() + 60]),
                    pagina=pagina_de(m.start()),
                    avisos=("marcador de republicação sem decreto, edital ou portaria associado",),
                )
            )
        elif estado.get(alvo) != "sim":
            estado[alvo] = grau
    ligados = [_com(a, republicacao=estado[i]) if i in estado else a for i, a in enumerate(atos)]
    return ligados, orfaos


def _com(achado: Achado, **mudancas) -> Achado:
    return Achado(**{**achado.__dict__, **mudancas})


def identificar(texto: str, inicios_de_pagina: list[int] | None = None) -> list[Achado]:
    """Reconhece decretos, editais, portarias, erratas e republicações no texto de um
    caderno. Devolve os achados na ordem em que aparecem.

    `inicios_de_pagina` (opcional): posição, no texto limpo, em que cada página
    começa; com ele, cada achado traz a página (base 1).
    """
    texto = limpar_texto(texto)
    inicios = inicios_de_pagina or [0]

    def pagina_de(posicao: int) -> int:
        return max(1, bisect.bisect_right(inicios, posicao))

    marcadores = [m.start() for m in MARCADOR_DE_ATO.finditer(texto)]

    def fim_do_trecho(posicao: int) -> int:
        i = bisect.bisect_right(marcadores, posicao)
        return marcadores[i] if i < len(marcadores) else len(texto)

    atos: list[Achado] = []

    # --- Decreto: cabeçalho exato, no início da linha
    for m in CABECALHO_DECRETO.finditer(texto):
        inicio_da_linha = texto.rfind("\n", 0, m.start()) + 1
        if texto[inicio_da_linha : m.start()].strip():
            continue  # no meio da linha: citação
        avisos = []
        if not ABERTURA_DO_CORPO_DECRETO.search(texto, m.end(), m.end() + 1500):
            avisos.append("sem a abertura do corpo do decreto após o cabeçalho (citação?)")
        atos.append(
            Achado(
                "Decreto", _numero_sem_milhar(m["numero"]), m["ano"], None,
                _tipo_do_decreto(texto, m.end()), data_no_texto(m.group(0)),
                m.start(), fim_do_trecho(inicio_da_linha + 1),
                _compactar(texto[m.start() : m.start() + 200]),
                pagina=pagina_de(m.start()), avisos=tuple(avisos),
            )
        )

    for inicio in INICIO_DECRETO.finditer(texto):
        posicao = inicio.end() - len("DECRETO")
        if any(a.posicao == posicao for a in atos):
            continue  # já reconhecido pelo cabeçalho completo
        atos.append(
            Achado(
                "Decreto", None, None, None, None, None, posicao, fim_do_trecho(posicao + 1),
                _compactar(texto[posicao : posicao + 200]), pagina=pagina_de(posicao),
                avisos=(
                    "cabeçalho de decreto sem o formato completo "
                    "('DECRETO Nº <número>, DE <dia> DE <mês> DE <ano>')",
                ),
            )
        )

    # --- Edital e Portaria: o início da linha reconhece o ato; o cabeçalho completo
    #     (com número) dá os detalhes. Sem número, o achado sai com aviso.
    for especie, inicio_re, cabecalho_re, orgaos_sefaz in (
        ("Edital", INICIO_EDITAL, CABECALHO_EDITAL, ORGAOS_EDITAL_SEFAZ),
        ("Portaria", INICIO_PORTARIA, CABECALHO_PORTARIA, ORGAOS_PORTARIA_SEFAZ),
    ):
        for inicio in inicio_re.finditer(texto):
            posicao = inicio.start()
            while texto[posicao] in " \t":
                posicao += 1
            completo = cabecalho_re.match(texto, inicio.start())
            avisos: list[str] = []
            fim_cabecalho = completo.end() if completo else inicio.end()
            if completo:
                numero = _numero_sem_milhar(completo["numero"])
                ano = completo["ano"] or _ano_do_composto(completo["numero"])
                orgao, tipo_do_titulo = _qualificador(completo["qualificador"], especie)
            else:
                numero, ano = None, None
                avisos.append(f"cabeçalho de {especie.lower()} sem número identificável")
                resto_da_linha = texto[inicio.end() : _fim_da_linha(texto, inicio.end())]
                orgao, tipo_do_titulo = _qualificador(_so_ate_o_numero(resto_da_linha), especie)
            linha_fim = _fim_da_linha(texto, fim_cabecalho)
            fim = fim_do_trecho(posicao + 1)
            data = data_no_texto(texto[fim_cabecalho:linha_fim])
            if data is None:
                data = _data_do_fecho(texto, fim_cabecalho, fim)
                if data:
                    avisos.append("data lida da linha 'Recife, <data>' do fecho (o cabeçalho não traz data)")
            if ano is None and data:
                ano = data[:4]
            if orgao == "ADMINISTRATIVA":
                # "PORTARIA ADMINISTRATIVA nº 56 / 2026 - CBMPE - DGP - DA": o órgão é a sigla depois do ano.
                sigla = re.match(r"[\s/\d]*-\s*([A-Z]{2,})", texto[fim_cabecalho:linha_fim])
                orgao = sigla.group(1) if sigla else orgao
            if _parece_citacao(texto, posicao):
                avisos.append("possível citação quebrada em duas linhas (a linha anterior termina no meio de uma frase)")
            if especie == "Edital":
                titulo = _titulo_do_edital(texto, fim_cabecalho, tipo_do_titulo)
                tipo = _classificar(TIPOS_EDITAL, titulo, inicio=True)
            else:
                tipo = _tipo_da_portaria(texto, fim_cabecalho)
            orgao_norm = orgao.upper() if orgao else None
            sefaz = (orgao_norm in orgaos_sefaz) if orgao_norm else None
            if orgao_norm is None:
                avisos.append("órgão não consta no cabeçalho")
            atos.append(
                Achado(
                    especie, numero, ano, orgao_norm, tipo, data, posicao, fim,
                    _compactar(texto[posicao : posicao + 200]),
                    sefaz=sefaz, pagina=pagina_de(posicao), avisos=tuple(avisos),
                )
            )

    atos.sort(key=lambda a: a.posicao)
    atos, orfaos = _ligar_republicacoes(texto, atos, pagina_de)

    # --- Errata
    erratas: list[Achado] = []
    for m in ERRATA_LINHA.finditer(texto):
        posicao = m.start()
        while texto[posicao] in " \t":
            posicao += 1
        referido = ato_referido_da_errata(texto[posicao : posicao + 300])
        avisos = []
        if referido.especie is None:
            avisos.append("ato referido não identificado (nenhuma espécie conhecida no começo da errata)")
        erratas.append(
            Achado(
                "Errata", None, None, None, None, None, posicao, fim_do_trecho(posicao + 1),
                _compactar(texto[posicao : posicao + 200]),
                destino=referido.destino, ato_referido=referido.rotulo,
                ref_numero=referido.numero, ref_ano=referido.ano,
                pagina=pagina_de(posicao), avisos=tuple(avisos),
            )
        )

    achados = atos + erratas + orfaos
    achados.sort(key=lambda a: a.posicao)
    return achados


# ----------------------------------------------------------------------------
# 11. Exemplos (entrada -> resultado esperado), para a equipe rodar sozinha
# ----------------------------------------------------------------------------

# Cada item: (texto curto, (campos esperados do 1º achado, do 2º achado, ...)).
# O texto tem de gerar EXATAMENTE tantos achados quantos dicionários houver; só os
# campos citados são conferidos. Para propor um caso novo, acrescente um item aqui
# com o trecho real (curto, sem nomes de pessoas) e o resultado que o Diário pede.
# Trechos de cadernos de 25/04, 26/06, 15/07, 21/10 e 22/10/2026.
_ASSINATURAS_DO_DECRETO = (
    "Independência do Brasil.\nRAQUEL TEIXEIRA LYRA LUCENA\nGovernadora do Estado\n"
    "FABRÍCIO MARQUES SANTOS\n"
)
EXEMPLOS: tuple[tuple[str, tuple[dict[str, object], ...]], ...] = (
    # Decreto: número sem ponto, ano, data do cabeçalho e tipo pelo início da ementa.
    (
        "DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026.\n"
        "Concede estímulo previsto na Lei nº 11.675, de 11 de outubro de 1999.\n"
        "A GOVERNADORA DO ESTADO, no uso das atribuições\n",
        (
            {"especie": "Decreto", "numero": "60929", "ano": "2026",
             "data_do_ato": "2026-06-25", "tipo": "CONCESSAO"},
        ),
    ),
    # Decreto de alteração da NORMA do programa (cita o decreto do PRODEPE).
    (
        "DECRETO Nº 52.040, DE 3 DE MARÇO DE 2021.\n"
        "Modifica o Decreto nº 44.650, de 30 de junho de 2017.\n"
        "A GOVERNADORA DO ESTADO\n",
        ({"especie": "Decreto", "numero": "52040", "tipo": "ALTERACAO_NORMA"},),
    ),
    # Citação de decreto no corpo do texto não é ato.
    ("Considerando o Decreto nº 21.959, de 27 de dezembro de 1999, que institui o PRODEPE\n", ()),
    # Edital da DBF: o cabeçalho não traz data; ela vem da linha "Recife, ..." do fecho.
    (
        "EDITAL DBF Nº 063/2026\nCREDENCIAMENTO RELATIVO À CONCESSÃO DE BENEFÍCIO FISCAL DO ICMS\n"
        "A Diretoria de Benefícios Fiscais - DBF, resolve credenciar\nRecife, 21 de outubro de 2026.\n",
        (
            {"especie": "Edital", "numero": "063", "ano": "2026", "orgao": "DBF",
             "tipo": "CREDENCIAMENTO", "sefaz": True, "data_do_ato": "2026-10-21"},
        ),
    ),
    # Edital sem órgão no cabeçalho: fica "a confirmar" (sefaz None).
    (
        "EDITAL Nº 43/2026\nCREDENCIAMENTO PARA FRUIÇÃO DO BENEFÍCIO DE CRÉDITO PRESUMIDO\n",
        ({"especie": "Edital", "numero": "43", "ano": "2026", "orgao": None, "sefaz": None},),
    ),
    # Edital com código composto no lugar do número.
    (
        "EDITAL DE CREDENCIAMENTO N° 0014.2026.PNE\nPROCESSO DE INEXIGIBILIDADE\n",
        ({"especie": "Edital", "numero": "0014.2026.PNE", "ano": "2026", "tipo": "CREDENCIAMENTO"},),
    ),
    # Edital sem número: não some, sai com aviso.
    (
        "EDITAL DE CONVOCAÇÃO\nSINDICATO DOS FISIOTERAPEUTAS\n",
        ({"especie": "Edital", "numero": None, "tipo": "CONVOCACAO"},),
    ),
    # Portaria da SEFAZ (sigla SF): data e ano vêm do cabeçalho; tipo = primeiro verbo operativo.
    (
        "PORTARIA SF Nº 200, DE 20.10.2026\nO SECRETÁRIO DA FAZENDA, RESOLVE:\n"
        "Art. 1° Declarar a perda por renúncia ao incentivo do PRODEPE\n",
        (
            {"especie": "Portaria", "numero": "200", "ano": "2026", "orgao": "SF", "sefaz": True,
             "data_do_ato": "2026-10-20", "tipo": "PERDA_INCENTIVO"},
        ),
    ),
    # Portaria de outro órgão: reconhecida, fora do escopo SEFAZ.
    (
        "PORTARIA SES/PE N° 609, de 21 de Outubro de 2026\nInstitui o Núcleo de Segurança do Paciente\n",
        ({"especie": "Portaria", "numero": "609", "orgao": "SES/PE", "sefaz": False},),
    ),
    # Portaria sem número no cabeçalho: sai com aviso.
    (
        "PORTARIA SEE/SEGP DE 20 DE OUTUBRO DE 2026. A SECRETARIA EXECUTIVA\n",
        ({"especie": "Portaria", "numero": None, "orgao": "SEE/SEGP", "data_do_ato": "2026-10-20"},),
    ),
    # Errata: a chave é o ato referido (espécie, número, ano) e o destino.
    (
        "ERRATA\nNo art. 4º do Decreto nº 61.560, de 19 de outubro de 2026, que redenomina o cargo\n",
        (
            {"especie": "Errata", "destino": "decreto", "ato_referido": "Decreto 61.560",
             "ref_numero": "61560", "ref_ano": "2026"},
        ),
    ),
    (
        "Errata ao Edital do Chamamento Público nº 010/2026, Processo nº 047/CPL/2026\n",
        ({"especie": "Errata", "destino": "edital", "ref_numero": "010", "ref_ano": "2026"},),
    ),
    (
        "ERRATA\nNa Portaria SJDH nº 126, publicada no Diário Oficial do Estado em 30 de setembro de 2026\n",
        ({"especie": "Errata", "destino": "portaria", "ref_numero": "126", "ref_ano": "2026"},),
    ),
    (
        "ERRATA DO DESPACHO SECOGE DE 18/06/2026, PUBLICADO NO DOE DE 19/06/2026.\n",
        ({"especie": "Errata", "destino": "outro", "ato_referido": "Despacho"},),
    ),
    # Republicação: o rodapé "(REPUBLICADO ...)" vem DEPOIS das assinaturas e se liga ao decreto.
    (
        "DECRETO Nº 61.055, DE 13 DE JULHO DE 2026.\nAbre ao Orçamento Fiscal crédito suplementar\n"
        "A GOVERNADORA DO ESTADO\n" + _ASSINATURAS_DO_DECRETO
        + "(REPUBLICADO POR HAVER SAÍDO COM INCORREÇÃO NO ORIGINAL)\nANEXO I\n",
        ({"especie": "Decreto", "numero": "61055", "republicacao": "sim"},),
    ),
    # Republicação em título, acima do edital.
    (
        "AVISO DE ABERTURA – 2ª REPUBLICAÇÃO DE EDITAL\nEDITAL DE CREDENCIAMENTO N° 0014.2026.PNE\n",
        ({"especie": "Edital", "numero": "0014.2026.PNE", "republicacao": "sim"},),
    ),
    # Republicação dentro de lista de atos de outra espécie: não se liga a nada.
    (
        "ATO DO DIA 16 DE OUTUBRO DE 2026.\nNº 5538 - Nomear X para exercer o cargo em comissão.\n"
        "(REPUBLICADO POR HAVER SAÍDO COM INCORREÇÃO NO ORIGINAL).\n",
        ({"especie": "Republicação"},),
    ),
    # "republicado no DOE" dentro de texto normativo NÃO é republicação.
    ("o Decreto nº 56.378, republicado no DOE de 06/06/2024, dispõe sobre\n", ()),
)


def conferir_exemplos() -> list[str]:
    """Roda a tabela EXEMPLOS e devolve as divergências (lista vazia = tudo certo)."""
    erros: list[str] = []
    for indice, (texto, esperados) in enumerate(EXEMPLOS, start=1):
        achados = identificar(texto)
        if len(achados) != len(esperados):
            erros.append(f"exemplo {indice}: {len(achados)} achado(s), esperado(s) {len(esperados)}")
            continue
        for achado, esperado in zip(achados, esperados):
            for campo, valor in esperado.items():
                obtido = getattr(achado, campo)
                if obtido != valor:
                    erros.append(f"exemplo {indice}: {campo} = {obtido!r}, esperado {valor!r}")
    return erros


if __name__ == "__main__":
    divergencias = conferir_exemplos()
    if divergencias:
        print("\n".join(divergencias))
        raise SystemExit(1)
    print(f"{len(EXEMPLOS)} exemplo(s) conferido(s): tudo certo.")
