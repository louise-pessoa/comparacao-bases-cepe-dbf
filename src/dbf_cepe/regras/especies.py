"""Regras de domínio por espécie de ato.

Este é o ÚNICO arquivo a alterar para passar a tratar uma nova espécie: basta
definir uma nova `Especie`, com seu cabeçalho, seu padrão de nomenclatura e seus
tipos, e incluí-la em `ESPECIES_ATIVAS`. Nenhuma outra etapa do pipeline precisa
ser reescrita.

As RegEx (cabeçalhos e tipos) NÃO ficam aqui: moram em `regras_atos.py`, arquivo
só com a biblioteca padrão que também é enviado à equipe da CEPE. Este arquivo as
importa e acrescenta o que é do pipeline (modelo de decreto, nome de arquivo,
escopo por programa, estrutura do texto). EDITAL e PORTARIA estão definidos, mas
NÃO ativos: só entram em `ESPECIES_ATIVAS` por decisão da DBF.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from dbf_cepe.regras.programas import PROGRAMAS_DBF, PROGRAMAS_DE_CREDENCIAMENTO, Programa
from dbf_cepe.regras.regras_atos import (
    ABERTURA_DO_CORPO_DECRETO,
    CABECALHO_DECRETO,
    CABECALHO_EDITAL,
    CABECALHO_PORTARIA,
    EVIDENCIA_AMOSTRA,  # noqa: F401 (reexportado: testes importam daqui)
    EVIDENCIA_PRESUMIDO,
    EVIDENCIA_PUBLICADO,  # noqa: F401 (reexportado)
    FECHO_ANTIGO_DECRETO,
    FECHO_DECRETO,
    NOMES_OUTRAS_ESPECIES,  # noqa: F401 (reexportado: separador e identificador)
    ORGAOS_EDITAL_SEFAZ,
    ORGAOS_PORTARIA_SEFAZ,
)
from dbf_cepe.regras.regras_atos import TIPOS_DECRETO as _REGRAS_TIPOS_DECRETO
from dbf_cepe.regras.regras_atos import TIPOS_EDITAL as _REGRAS_TIPOS_EDITAL

# Origem do padrão de um tipo (documentação e testes; não altera o comportamento):
#   amostra   - conferido em ato real da DBF versionado em tests/amostras/ ou em caderno;
#   publicado - conferido em ato real publicado (Alepe/SEFAZ), sem amostra versionada;
#   presumido - deduzido da legislação ou do texto dos demais; falta ato real.
# (As constantes EVIDENCIA_* vêm de regras_atos.py; continuam importáveis daqui.)


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
    # Fechos usados em anos anteriores (no decreto, o de até 2010). Hoje só servem para o
    # critério da leitura em cascata dizer por que um ato reprovou (leitura/criterio.py).
    fechos_anteriores: tuple[re.Pattern[str], ...] = ()
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
    # Órgãos (sigla do cabeçalho) cujos atos entram no escopo. None = a espécie
    # não se divide por órgão (decreto). Usado por edital e portaria.
    orgaos_do_escopo: frozenset[str] | None = None
    # Estrutura do texto (etapa 4, normalizador). Cada linha do ato que casar
    # com um destes padrões abre um parágrafo novo; as demais continuam o
    # parágrafo anterior (o texto do Diário vem quebrado em linhas fixas).
    # Parágrafos que abrem com `inicios_com_recuo` (artigos, incisos, fecho)
    # saem com recuo na primeira linha; com `inicios_sem_recuo`
    # (considerandos, "DECRETA:"), sem recuo.
    inicios_com_recuo: re.Pattern[str] | None = None
    inicios_sem_recuo: re.Pattern[str] | None = None
    # Republicação (mesmo número publicado de novo, em outra data). Entre as
    # publicações do mesmo número, a de data mais antiga fica com o nome padrão
    # (`nome_arquivo`); as seguintes recebem um sufixo antes da extensão:
    # 1ª republicação: `sufixo_republicacao`; da 2ª em diante: `sufixo_republicacao_n`
    # com {n} = 2, 3... Publicação diferente de um arquivo da base gravado antes do
    # registro de publicações existir: `sufixo_conferir` (vai para conferência manual).
    sufixo_republicacao: str = " (republicação)"
    sufixo_republicacao_n: str = " (republicação {n})"
    sufixo_conferir: str = " (conferir duplicata)"

    def nome_arquivo(self, numero: str, ano: str | None = None) -> str:
        """Nome de salvamento e de comparação, ex.: 'Decreto 60929.pdf', 'Edital 064-2025.pdf'."""
        if "{ano}" in self.padrao_arquivo and not ano:
            raise ValueError(f"{self.nome}: o nome do arquivo exige o ano ({self.padrao_arquivo})")
        return self.padrao_arquivo.format(especie=self.nome, numero=numero, ano=ano)

    def _com_sufixo(self, numero: str, sufixo: str, ano: str | None) -> str:
        nome = self.nome_arquivo(numero, ano)
        raiz, ponto, extensao = nome.rpartition(".")
        return f"{raiz}{sufixo}.{extensao}" if ponto else f"{nome}{sufixo}"

    def nome_da_publicacao(self, numero: str, ordem: int, ano: str | None = None) -> str:
        """Nome da publicação na ordem cronológica do número: 0 = a original
        ('Decreto 61055.pdf'), 1 = 'Decreto 61055 (republicação).pdf',
        2 = 'Decreto 61055 (republicação 2).pdf' e assim por diante."""
        if ordem < 0:
            raise ValueError(f"ordem inválida: {ordem}")
        if ordem == 0:
            return self.nome_arquivo(numero, ano)
        sufixo = self.sufixo_republicacao if ordem == 1 else self.sufixo_republicacao_n.format(n=ordem)
        return self._com_sufixo(numero, sufixo, ano)

    def nome_conferir_duplicata(self, numero: str, ano: str | None = None) -> str:
        """'Decreto 61055 (conferir duplicata).pdf'."""
        return self._com_sufixo(numero, self.sufixo_conferir, ano)

    def ordem_do_nome(self, nome: str, numero: str, ano: str | None = None) -> int | None:
        """Inverso de `nome_da_publicacao`: a ordem de um nome de arquivo da base
        (sem diferença de maiúsculas nem de forma Unicode), ou None se o nome não
        seguir o padrão."""
        nome = unicodedata.normalize("NFC", nome)
        alvo = nome.casefold()
        if alvo == self.nome_arquivo(numero, ano).casefold():
            return 0
        if alvo == self.nome_da_publicacao(numero, 1, ano).casefold():
            return 1
        raiz, _, extensao = self.nome_arquivo(numero, ano).rpartition(".")
        antes, _, depois = self.sufixo_republicacao_n.partition("{n}")
        padrao = re.compile(
            re.escape(raiz + antes) + r"([2-9]|[1-9]\d+)" + re.escape(depois + "." + extensao),
            re.IGNORECASE,
        )
        casamento = padrao.fullmatch(nome)
        return int(casamento.group(1)) if casamento else None

    def e_da_familia(self, nome: str, numero: str, ano: str | None = None) -> bool:
        """True se o nome é deste número: no padrão (`ordem_do_nome`) ou começando
        pelo nome padrão sem extensão seguido de algo que não seja dígito
        ('Decreto 61055 (1).pdf', 'Decreto 61055-A.pdf'); 'Decreto 610551.pdf' não é."""
        if self.ordem_do_nome(nome, numero, ano) is not None:
            return True
        raiz = self.nome_arquivo(numero, ano).rpartition(".")[0].casefold()
        alvo = unicodedata.normalize("NFC", nome).casefold()
        return alvo.startswith(raiz) and len(alvo) > len(raiz) and not alvo[len(raiz)].isdigit()


# ----------------------------------------------------------------------------
# Decreto
# ----------------------------------------------------------------------------

# Decreto-modelo (template da etapa 4) de cada tipo; None = sem modelo. Os padrões
# e a ORDEM dos tipos vêm de regras_atos.TIPOS_DECRETO (a ordem importa: "segunda
# prorrogação" antes de "prorrogação"; ALTERACAO_NORMA antes de ALTERACAO).
_MODELO_POR_TIPO: dict[str, str | None] = {
    "CONCESSAO": "60929",
    "ALTERACAO_NORMA": None,  # a DBF decide o tratamento (hoje o template de alteração é de empresa)
    "ALTERACAO": "60951",
    "SEGUNDA_PRORROGACAO": "60950",
    "SEGUNDA_RENOVACAO": "60945",
    "PRORROGACAO": "60950",
    "RENOVACAO": "60945",
    "TRANSFERENCIA": "60505",
    "AUTORIZACAO_PROIND": None,  # ainda sem decreto-modelo: a DBF vai indicar um dia publicado
    "REVOGACAO": None,  # sem modelo até a etapa 4 definir o tratamento
    "TERCEIRIZACAO": "60233",
}

TIPOS_DECRETO: tuple[Tipo, ...] = tuple(
    Tipo(nome, padrao, _MODELO_POR_TIPO[nome], evidencia)
    for nome, padrao, evidencia in _REGRAS_TIPOS_DECRETO
)

DECRETO = Especie(
    nome="Decreto",
    cabecalho=CABECALHO_DECRETO,
    padrao_arquivo="{especie} {numero}.pdf",
    tipos=TIPOS_DECRETO,
    # "... e a Independência do Brasil." encerra o texto, antes das assinaturas.
    fecho=FECHO_DECRETO,
    fechos_anteriores=(FECHO_ANTIGO_DECRETO,),
    cargo_assinante=re.compile(r"^Governador(?:a)? do Estado$"),
    # "A GOVERNADORA DO ESTADO, no uso das atribuições..." vem logo após a ementa.
    abertura_do_corpo=ABERTURA_DO_CORPO_DECRETO,
    # Ementa e considerandos terminam no "DECRETA:".
    fim_do_cabecalho=re.compile(r"\bDECRETA\s*:"),
    # Escopo: só os decretos dos programas de benefício fiscal da DBF.
    programas=PROGRAMAS_DBF,
    # Dispositivos: "Art. 1º", "§ 1º", "Parágrafo único.", incisos romanos
    # ("IV - ..."), alíneas ("a) ..."), itens numerados ("1.", "1.1.") e o
    # fecho ("Palácio do Campo das Princesas..."). Aceitam aspas de abertura
    # (trecho citado em decreto de alteração).
    inicios_com_recuo=re.compile(
        r"[“\"]?\s*(?:Art\.\s*\d|§\s*\d|Par[áa]grafo\s+[úu]nico\b"
        r"|[IVXLCDM]+\s*[-–—]\s|[a-z]\)\s|\d+(?:\.\d+)*\.\s|Pal[áa]cio\b)"
    ),
    inicios_sem_recuo=re.compile(
        r"(?:CONSIDERANDO\b|DECRETA\s*:|A GOVERNADORA\b|O GOVERNADOR\b)"
    ),
)

# ----------------------------------------------------------------------------
# Edital (NÃO ATIVO)
# ----------------------------------------------------------------------------

# Reconhecimento de cabeçalho, número/ano, órgão e tipo a partir dos cadernos de 25/04,
# 26/06, 15/07, 21/10 e 22/10/2026 (ver regras_atos.py). Fora de ESPECIES_ATIVAS:
# falta a DBF definir o que fazer com o edital (nome do arquivo, escopo por programa,
# template). Pendências:
#   - nome do arquivo: "Edital 064-2025.pdf" (número com os zeros e ano), como na base da DBF;
#   - o credenciamento se liga ao programa pelo TEXTO do edital (`programas` abaixo, que
#     inclui a Lei de Incentivo ao Esporte);
#   - DBM foi informado como outro nome da DBF, mas não aparece em nenhum caderno.
EDITAL = Especie(
    nome="Edital",
    cabecalho=CABECALHO_EDITAL,
    padrao_arquivo="{especie} {numero}-{ano}.pdf",
    tipos=tuple(Tipo(nome, padrao, None, evidencia) for nome, padrao, evidencia in _REGRAS_TIPOS_EDITAL),
    programas=PROGRAMAS_DE_CREDENCIAMENTO,
    orgaos_do_escopo=ORGAOS_EDITAL_SEFAZ,
)

# ----------------------------------------------------------------------------
# Portaria (NÃO ATIVA)
# ----------------------------------------------------------------------------

# Portaria da SEFAZ = órgão "SF" no cabeçalho ("PORTARIA SF Nº 76, DE 24.04.2026").
# O número só é único junto com o órgão e o ano. O tipo da portaria NÃO passa por
# `tipos`: vem do primeiro verbo operativo do ato (regras_atos.TIPOS_PORTARIA,
# procurado em qualquer ponto da janela, e não com .match() no início da ementa).
PORTARIA = Especie(
    nome="Portaria",
    cabecalho=CABECALHO_PORTARIA,
    padrao_arquivo="{especie} {numero}.pdf",
    orgaos_do_escopo=ORGAOS_PORTARIA_SEFAZ,
)

# ----------------------------------------------------------------------------
# Registro
# ----------------------------------------------------------------------------

# Espécies tratadas nesta fase. EDITAL e PORTARIA já estão definidos acima, mas
# só entram aqui por decisão da DBF (ver as pendências em cada um).
ESPECIES_ATIVAS: tuple[Especie, ...] = (DECRETO,)

# Espécies que, quando o documento COMEÇA por elas, são reconhecidas como
# "outra espécie" e descartadas (a lista mora em regras_atos.py, importada acima).
# Nomes de espécies ativas são ignorados automaticamente. Documentos que não
# começam por nenhuma espécie conhecida NÃO são descartados: vão para revisão manual.
# (`NOMES_OUTRAS_ESPECIES` é importada de regras_atos no topo e continua importável daqui.)
