"""Regras de domínio por espécie de ato.

Este é o ÚNICO arquivo a alterar para passar a tratar uma nova espécie (por
exemplo, Edital, em fase futura): basta definir uma nova `Especie`, com seu
cabeçalho, seu padrão de nomenclatura e seus tipos, e incluí-la em
`ESPECIES_ATIVAS`. Nenhuma outra etapa do pipeline precisa ser reescrita.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from programas import PROGRAMAS_DBF, Programa

# Origem do padrão de um tipo (documentação e testes; não altera o comportamento):
#   amostra   - conferido em decreto real da DBF versionado em tests/amostras/ ou no
#               caderno de 26/06/2026;
#   publicado - conferido em decreto real publicado (Alepe/SEFAZ), sem amostra versionada;
#   presumido - deduzido da legislação ou do texto dos demais; falta decreto real.
EVIDENCIA_AMOSTRA = "amostra"
EVIDENCIA_PUBLICADO = "publicado"
EVIDENCIA_PRESUMIDO = "presumido"


@dataclass(frozen=True)
class Tipo:
    """Tipo de decreto, identificado pelo início da ementa."""

    nome: str
    padrao: re.Pattern[str]  # aplicado com .match() no INÍCIO da ementa
    modelo: str | None  # decreto-modelo (template da etapa 4); None = sem amostra
    evidencia: str = EVIDENCIA_PRESUMIDO


@dataclass(frozen=True)
class Especie:
    """Tudo o que muda de uma espécie de ato para outra.

    As etapas do pipeline (separação, identificação, escopo, relatório) não
    conhecem "decreto": leem estes campos. Para tratar uma espécie nova, basta
    definir uma `Especie` e incluí-la em `ESPECIES_ATIVAS`.

    Campos opcionais: com `None`, a regra correspondente não se aplica à
    espécie (o separador não procura fecho, o identificador usa o tamanho
    padrão de ementa, o escopo não filtra por programa etc.).
    """

    nome: str  # "Decreto"
    cabecalho: re.Pattern[str]  # precisa ter o grupo nomeado "numero"
    padrao_arquivo: str  # ex.: "{especie} {numero}.pdf"
    tipos: tuple[Tipo, ...] = ()
    # Fecho que encerra o texto do ato, antes das assinaturas (separador).
    fecho: re.Pattern[str] | None = None
    # Cargo de quem assina o ato, em linha própria (separador).
    cargo_assinante: re.Pattern[str] | None = None
    # Primeira frase depois da ementa: delimita a ementa (identificador) e
    # confirma que o cabeçalho abre um ato e não é uma citação (separador).
    abertura_do_corpo: re.Pattern[str] | None = None
    # Onde termina a parte do ato em que se procura o programa (escopo).
    fim_do_cabecalho: re.Pattern[str] | None = None
    # Programas que definem o escopo da espécie. None = sem filtro: todo ato
    # da espécie entra no escopo.
    programas: tuple[Programa, ...] | None = None

    def nome_arquivo(self, numero: str) -> str:
        """Nome de salvamento e de comparação, ex.: 'Decreto 60929.pdf'."""
        return self.padrao_arquivo.format(especie=self.nome, numero=numero)


# ----------------------------------------------------------------------------
# Decreto
# ----------------------------------------------------------------------------

# Cabeçalho em MAIÚSCULAS, ex.: "DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026".
# É propositalmente sensível a maiúsculas: as citações no corpo do texto
# ("Decreto nº 21.959, de 27 de dezembro de 1999") não podem ser confundidas
# com o cabeçalho do ato. Tolera "N°", "N.º", "Nº", número sem ponto de milhar,
# vírgula ausente antes de "DE" e dia "1º".
_CABECALHO_DECRETO = re.compile(
    r"(?<![A-Za-zÀ-ú])DECRETO\s+N\s*\.?\s*[º°o]\.?\s*"
    r"(?P<numero>\d{1,3}(?:\.\d{3})+|\d+)\s*,?\s*"
    r"DE\s+\d{1,2}\s*[º°o]?\s+DE\s+[A-ZÇÃÕÁÀÂÉÊÍÓÔÚ]+\s+DE\s+\d{4}"
)


def _inicio(expressao: str) -> re.Pattern[str]:
    return re.compile(expressao, re.IGNORECASE)


# Peças reutilizadas nos padrões abaixo.
#
# O texto do Diário traz as ligaduras "ﬁ" seguidas de um espaço espúrio
# ("modiﬁ cações", "ﬁ scal"). O separador as converte, mas um PDF avulso lido
# direto pode chegar com elas; por isso as palavras com "fi" usam _FI.
_FI = r"(?:fi|ﬁ\s?)"
# "ção" ou "ções" (a, depois o plural), com ou sem acento.
_CAO = r"(?:[çc][ãa]o|[çc][õo]es)"
# "Dispõe sobre a(s) <assunto>": o Diário usa o plural quando o decreto trata
# de mais de um ato (ex.: "Dispõe sobre as prorrogações dos prazos...").
_DISPOE_SOBRE_A = r"Disp[õo]e\s+sobre\s+(?:a|as)\s+"
# "Decreto nº" com as variantes de grafia do "nº".
_DECRETO_N = r"Decreto\s+n\s*\.?\s*[ºo°]\.?\s*"
# Decretos que regulamentam os programas da DBF: instituição do PRODEPE
# (21.959/1999), do PEAP (34.560/2010) e o RICMS-PE (44.650/2017, com os anexos
# dos programas). Alterar um deles é alterar a NORMA, não o ato de uma empresa.
_DECRETOS_DE_PROGRAMA = rf"{_DECRETO_N}(?:21\.?959|34\.?560|44\.?650)\b"

# A classificação olha só o INÍCIO da ementa. Buscar a palavra no texto todo
# erraria: a ementa de uma revogação pode citar "prorrogação", e o corpo da
# renovação (60945) cita "prorrogação" e "prorrogados".
# A ordem importa: as variantes mais específicas vêm antes das gerais
# (segunda prorrogação antes de prorrogação; alteração da norma antes da
# alteração de empresa). O credenciamento no PEAP é feito por EDITAL, não por
# decreto, e por isso não é um tipo de decreto (fica para a fase dos editais).
TIPOS_DECRETO: tuple[Tipo, ...] = (
    # --- Concessão do estímulo a uma empresa ---
    # Amostra: 60929 e os 28 do caderno de 26/06/2026 ("Concede estímulo previsto...").
    Tipo(
        "CONCESSAO",
        _inicio(r"(?:Concede\b|Disp[õo]e\s+sobre\s+a\s+concess[ãa]o\b)"),
        "60929",
        EVIDENCIA_AMOSTRA,
    ),
    # --- Alteração da NORMA do programa (não é ato de empresa) ---
    # Publicado: "Introduz modificações no Decreto nº 21.959" (32.016/2008,
    # 40.607/2014), "Altera o Decreto nº 21.959" (23.735/2001), "Modifica o
    # Decreto nº 44.650 ... Atividade Portuária" (52.040/2021, 59.024/2025).
    # O discriminador é o decreto citado: o de programa ou o RICMS-PE.
    # Sem modelo: a DBF decide o tratamento (hoje o template de alteração é de empresa).
    Tipo(
        "ALTERACAO_NORMA",
        _inicio(
            rf"(?:Introduz\s+(?:altera[çc][õo]es|modi{_FI}ca[çc][õo]es)|"
            rf"Altera|Modi{_FI}ca|D[áa]\s+nova\s+reda[çc][ãa]o)\b"
            rf"(?=.{{0,400}}?{_DECRETOS_DE_PROGRAMA})"
        ),
        None,
        EVIDENCIA_PUBLICADO,
    ),
    # --- Alteração do decreto de uma empresa ---
    # Amostra: 60951 ("Introduz alterações nos Decretos nº ..., que concedem incentivo
    # do PRODEPE à empresa X"). Publicado: 60.134/2025 usa o singular
    # ("Introduz alterações no Decreto nº 41.447").
    Tipo(
        "ALTERACAO",
        _inicio(r"Introduz\s+altera[çc][õo]es\b"),
        "60951",
        EVIDENCIA_AMOSTRA,
    ),
    # --- Segunda prorrogação / renovação ---
    # Sem amostra real: padrão presumido a partir do texto dos demais. Usam o
    # mesmo template da prorrogação e da renovação (decisão do projeto).
    Tipo(
        "SEGUNDA_PRORROGACAO",
        _inicio(
            rf"{_DISPOE_SOBRE_A}(?:segunda|2\s*[ªa°º]?)\s+prorroga{_CAO}\b"
        ),
        "60950",
    ),
    Tipo(
        "SEGUNDA_RENOVACAO",
        _inicio(rf"{_DISPOE_SOBRE_A}(?:segunda|2\s*[ªa°º]?)\s+renova{_CAO}\b"),
        "60945",
    ),
    # --- Prorrogação e renovação ---
    # Amostra: 60950 e 60945. Publicado: o plural em 58.817/2025 ("Dispõe sobre as
    # prorrogações dos prazos de fruição de estímulos do PRODEPE").
    Tipo(
        "PRORROGACAO",
        _inicio(rf"{_DISPOE_SOBRE_A}prorroga{_CAO}\b"),
        "60950",
        EVIDENCIA_AMOSTRA,
    ),
    Tipo(
        "RENOVACAO",
        _inicio(rf"{_DISPOE_SOBRE_A}renova{_CAO}\b"),
        "60945",
        EVIDENCIA_AMOSTRA,
    ),
    # --- Transferência do estímulo para outra empresa ---
    # Amostra: 60505. "Transfere" é presumido.
    Tipo(
        "TRANSFERENCIA",
        _inicio(rf"(?:{_DISPOE_SOBRE_A}transfer[êe]ncias?\b|Transfere\b)"),
        "60505",
        EVIDENCIA_AMOSTRA,
    ),
    # --- PROIND ---
    # Amostra: 60.903 e 60.913 (caderno de 26/06/2026): "Autoriza a utilização do
    # incentivo fiscal previsto no Anexo 33 ... que dispõe sobre o PROIND pelo
    # contribuinte X". Ainda sem decreto-modelo: a DBF vai indicar um dia publicado.
    Tipo(
        "AUTORIZACAO_PROIND",
        _inicio(r"Autoriza\s+a\s+utiliza[çc][ãa]o\s+do\s+(?:incentivo|benef[íi]cio)\b"),
        None,
        EVIDENCIA_AMOSTRA,
    ),
    # --- Revogação ---
    # Publicado: 39.773/2013 ("Revoga o Decreto nº 39.617, de 22 de julho de 2013",
    # que prorrogara um estímulo do PRODEPE). Sem modelo até a etapa 4 definir o
    # tratamento. "Torna sem efeito" e "Dispõe sobre a revogação" são presumidos.
    Tipo(
        "REVOGACAO",
        _inicio(r"(?:Revoga\b|Torna\s+sem\s+efeito\b|Disp[õo]e\s+sobre\s+a\s+revoga[çc][ãa]o\b)"),
        None,
        EVIDENCIA_PUBLICADO,
    ),
    # --- Terceirização da industrialização ---
    # Amostra: 60233. A ementa não cita o programa (os considerandos citam a Lei 11.675).
    Tipo(
        "TERCEIRIZACAO",
        _inicio(r"Autoriza\s+a\s+terceiriza[çc][ãa]o\b"),
        "60233",
        EVIDENCIA_AMOSTRA,
    ),
)

DECRETO = Especie(
    nome="Decreto",
    cabecalho=_CABECALHO_DECRETO,
    padrao_arquivo="{especie} {numero}.pdf",
    tipos=TIPOS_DECRETO,
    # "... e a Independência do Brasil." encerra o texto, antes das assinaturas.
    fecho=re.compile(r"Independência\s+do\s+Brasil\.", re.IGNORECASE),
    cargo_assinante=re.compile(r"^Governador(?:a)? do Estado$"),
    # "A GOVERNADORA DO ESTADO, no uso das atribuições..." vem logo após a ementa.
    abertura_do_corpo=re.compile(r"\b(?:A GOVERNADORA|O GOVERNADOR)\b"),
    # Ementa e considerandos terminam no "DECRETA:".
    fim_do_cabecalho=re.compile(r"\bDECRETA\s*:"),
    # Escopo: só os decretos dos programas de benefício fiscal da DBF.
    programas=PROGRAMAS_DBF,
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
    "INSTRUÇÃO NORMATIVA",
    "COMUNICADO",
    "DECISÃO",
    "RETIFICAÇÃO",
    "DELIBERAÇÃO",
    "ORDEM DE SERVIÇO",
)