"""Etapa 2: regex dos tipos de decreto (início da ementa) e do cabeçalho.

Cada tipo tem pelo menos um caso aqui. A coluna de evidência de `especies.py`
diz de onde veio o padrão; os casos marcados "publicado" usam a ementa de
decretos reais da Alepe/SEFAZ e os "presumido" são deduzidos (trocar por
ementa real assim que houver uma amostra).
"""

from __future__ import annotations

import pytest

from conftest import TIPO_ESPERADO_DECRETO, texto_decreto
from especies import (
    DECRETO,
    EVIDENCIA_AMOSTRA,
    EVIDENCIA_PRESUMIDO,
    EVIDENCIA_PUBLICADO,
    TIPOS_DECRETO,
)
from identificador import StatusIdentificacao, identificar

# (tipo esperado, ementa)
CASOS = [
    # --- Concessão (amostra: 60929 e os 28 do caderno de 26/06/2026) ---
    ("CONCESSAO", "Concede estímulo previsto na Lei nº 11.675, de 11 de outubro de 1999, que dispõe sobre o PRODEPE, à empresa A M N INDÚSTRIA LTDA."),
    ("CONCESSAO", "CONCEDE ESTÍMULO PREVISTO NA LEI Nº 11.675, À EMPRESA X."),
    ("CONCESSAO", "Dispõe sobre a concessão de estímulo do PRODEPE à empresa X."),  # presumido
    # --- Alteração de empresa (amostra: 60951) ---
    ("ALTERACAO", "Introduz alterações nos Decretos nº 41.447, de 27 de janeiro de 2015, e nº 50.100, que concedem incentivo do PRODEPE à empresa X."),
    ("ALTERACAO", "Introduz alterações no Decreto nº 41.447, de 27 de janeiro de 2015, que concede incentivo do PRODEPE à empresa X."),  # singular (60.134/2025)
    ("ALTERACAO", "Introduz alterações no Decreto nº 22.456, de 20 de julho de 2000."),  # não é decreto de programa
    # --- Alteração da norma do programa (publicados) ---
    ("ALTERACAO_NORMA", "Introduz modificações no Decreto nº 21.959, de 27 de dezembro de 1999, e alterações, que dispõe sobre o Programa de Desenvolvimento do Estado de Pernambuco - PRODEPE."),  # 32.016/2008
    ("ALTERACAO_NORMA", "Introduz modificações no Decreto n° 21.959, de 27 de dezembro de 1999, que regulamenta o PRODEPE."),  # 40.607/2014 (n°)
    ("ALTERACAO_NORMA", "Altera o Decreto n° 21.959, de 27 de dezembro de 1999, que regulamenta o Programa de Desenvolvimento do Estado de Pernambuco - PRODEPE."),  # 23.735/2001
    ("ALTERACAO_NORMA", "Modifica o Decreto nº 44.650, de 30 de junho de 2017, relativamente ao Programa de Estímulo à Atividade Portuária."),  # 59.024/2025
    ("ALTERACAO_NORMA", "Modifica o Decreto nº 28.800, de 04 de janeiro de 2006, que dispõe sobre o controle do montante mínimo de recolhimento do ICMS, relativamente às empresas beneficiárias do Programa de Desenvolvimento do Estado de Pernambuco, bem como o Decreto nº 21.959, de 27 de dezembro de 1999, que regulamenta o PRODEPE."),  # 29.984/2006
    ("ALTERACAO_NORMA", "Altera o Decreto nº 34.560, de 2010, que institui o Programa de Estímulo à Atividade Portuária."),
    ("ALTERACAO_NORMA", "Introduz modiﬁ cações no Decreto nº 21.959, de 27 de dezembro de 1999."),  # ligadura do Diário
    ("ALTERACAO_NORMA", "Modiﬁ ca o Decreto nº 44.650, de 30 de junho de 2017, relativamente ao PROIND."),  # ligadura do Diário
    # --- Prorrogação e renovação (amostra: 60950, 60945) ---
    ("PRORROGACAO", "Dispõe sobre a prorrogação do prazo de fruição de estímulo do PRODEPE concedido pelo Decreto nº 30.722, de 2007, à empresa X."),
    ("PRORROGACAO", "Dispõe sobre as prorrogações dos prazos de fruição de estímulos do PRODEPE concedidos pelos Decretos nº 40.909 e nº 44.231, à empresa X."),  # plural (58.817/2025)
    ("PRORROGACAO", "Dispoe sobre a prorrogacao do prazo de fruicao do PRODEPE."),  # sem acentos
    ("RENOVACAO", "Dispõe sobre a renovação do prazo de fruição de estímulo do PRODEPE concedido pelo Decreto nº 40.909."),
    ("RENOVACAO", "Dispõe sobre as renovações dos prazos de fruição do PRODEPE."),  # plural (presumido)
    # --- Segunda prorrogação / renovação (presumidos) ---
    ("SEGUNDA_PRORROGACAO", "Dispõe sobre a segunda prorrogação do prazo de fruição de estímulo do PRODEPE."),
    ("SEGUNDA_PRORROGACAO", "Dispõe sobre a 2ª prorrogação do prazo de fruição de estímulo do PRODEPE."),
    ("SEGUNDA_RENOVACAO", "Dispõe sobre a segunda renovação do prazo de fruição de estímulo do PRODEPE."),
    ("SEGUNDA_RENOVACAO", "Dispõe sobre a 2ª renovação do prazo de fruição de estímulo do PRODEPE."),
    # --- Transferência (amostra: 60505) ---
    ("TRANSFERENCIA", "Dispõe sobre a transferência do estímulo do PRODEPE concedido a empresa X, pelo Decreto nº 40.909, para a empresa Y."),
    ("TRANSFERENCIA", "Dispõe sobre as transferências dos estímulos do PRODEPE."),  # plural (presumido)
    ("TRANSFERENCIA", "Transfere o estímulo do PRODEPE concedido pelo Decreto nº 40.909."),  # presumido
    # --- PROIND (amostra: 60.903 e 60.913) ---
    ("AUTORIZACAO_PROIND", "Autoriza a utilização do incentivo fiscal previsto no Anexo 33 do Decreto nº 44.650, de 30 de junho de 2017, que dispõe sobre o PROIND pelo contribuinte BLACK JOHN LTDA."),
    ("AUTORIZACAO_PROIND", "Autoriza a utilização do incentivo ﬁ scal previsto no Anexo 33 do Decreto nº 44.650, pelo contribuinte X."),  # ligadura do Diário
    # --- Revogação (publicada: 39.773/2013) ---
    ("REVOGACAO", "Revoga o Decreto nº 39.617, de 22 de julho de 2013."),
    ("REVOGACAO", "Revoga os Decretos nº 39.617 e nº 39.618, que prorrogaram o prazo de fruição do PRODEPE."),  # cita "prorrogação": segue revogação
    ("REVOGACAO", "Torna sem efeito o Decreto nº 39.617, de 22 de julho de 2013."),  # presumido
    # --- Terceirização (amostra: 60233) ---
    ("TERCEIRIZACAO", "Autoriza a terceirização da industrialização dos produtos incentivados pelo Decreto nº 22.478, de 21 de julho de 2000."),
]

# Ementas de decretos fora do escopo da DBF ou sem tipo conhecido: tipo None.
SEM_TIPO = [
    "Declara de utilidade pública, para fins de desapropriação, área de terra, situada no Município de Tabira.",
    "Abre ao Orçamento Fiscal do Estado, relativo ao exercício de 2026, crédito suplementar no valor de R$ 3.003.467,94.",
    "Altera o art. 15 do Decreto nº 39.200, de 18 de março de 2013.",  # 60.135/2025 (FEM): não é decreto de programa
    "Atualiza os valores relativos à Taxa de Fiscalização e Utilização de Serviços Públicos.",
    "Redenomina o cargo em comissão e a função gratificada que indica.",
    "Dispõe sobre a estrutura da Secretaria de Educação.",
    "Estabelece termo final para apresentação de projeto.",  # 32.994/2009: tipo desconhecido, vai para revisão
]


def tipo_da_ementa(ementa: str) -> str | None:
    ident = identificar(texto_decreto("60.001", ementa))
    assert ident.status is StatusIdentificacao.IDENTIFICADO
    return ident.tipo.nome if ident.tipo else None


@pytest.mark.parametrize("esperado, ementa", CASOS)
def test_tipo_pelo_inicio_da_ementa(esperado, ementa):
    assert tipo_da_ementa(ementa) == esperado


@pytest.mark.parametrize("ementa", SEM_TIPO)
def test_ementas_sem_tipo_conhecido(ementa):
    assert tipo_da_ementa(ementa) is None


def test_todo_tipo_tem_caso_de_teste():
    assert {tipo for tipo, _ in CASOS} == {t.nome for t in TIPOS_DECRETO}


def test_nomes_de_tipo_sao_unicos():
    nomes = [t.nome for t in TIPOS_DECRETO]
    assert len(nomes) == len(set(nomes))


def test_evidencia_e_um_valor_conhecido():
    validas = {EVIDENCIA_AMOSTRA, EVIDENCIA_PUBLICADO, EVIDENCIA_PRESUMIDO}
    assert {t.evidencia for t in TIPOS_DECRETO} <= validas


def test_tipos_com_amostra_batem_com_as_amostras_reais():
    # Todo tipo marcado "amostra" e com modelo deve ter a amostra correspondente.
    for tipo in TIPOS_DECRETO:
        if tipo.evidencia == EVIDENCIA_AMOSTRA and tipo.modelo:
            assert TIPO_ESPERADO_DECRETO[tipo.modelo] == tipo.nome


def test_modelo_das_segundas_usa_o_template_da_prorrogacao_e_da_renovacao():
    modelos = {t.nome: t.modelo for t in TIPOS_DECRETO}
    assert modelos["SEGUNDA_PRORROGACAO"] == modelos["PRORROGACAO"] == "60950"
    assert modelos["SEGUNDA_RENOVACAO"] == modelos["RENOVACAO"] == "60945"


def test_ordem_especifico_antes_do_geral():
    nomes = [t.nome for t in TIPOS_DECRETO]
    assert nomes.index("SEGUNDA_PRORROGACAO") < nomes.index("PRORROGACAO")
    assert nomes.index("SEGUNDA_RENOVACAO") < nomes.index("RENOVACAO")
    assert nomes.index("ALTERACAO_NORMA") < nomes.index("ALTERACAO")


def test_cada_ementa_casa_com_um_unico_tipo_na_pratica():
    # Nenhum caso de CASOS pode casar com um tipo anterior ao esperado por acidente:
    # o primeiro tipo que casa é o esperado (já garantido por test_tipo_pelo_inicio),
    # e ementas de revogação que citam "prorrogação" não viram prorrogação.
    assert tipo_da_ementa("Revoga o Decreto nº 1, que prorrogou o prazo.") == "REVOGACAO"


# ---------------------------------------------------------------------------
# Cabeçalho (número do decreto)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cabecalho, numero",
    [
        ("DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026.", "60929"),
        ("DECRETO N° 60929, DE 1º DE JULHO DE 2026.", "60929"),
        ("DECRETO N.º 60.929 DE 25 DE MARÇO DE 2026.", "60929"),  # sem vírgula
        ("DECRETO Nº 1.234, DE 02 DE MAIO DE 2026.", "1234"),
        ("DECRETO  Nº  60.929 ,  DE  25  DE  JUNHO  DE  2026", "60929"),
        ("DECRETO Nº 100.000, DE 25 DE DEZEMBRO DE 2030.", "100000"),
        ("DECRETO Nº 12, DE 3 DE ABRIL DE 1951.", "12"),
    ],
)
def test_cabecalho_variantes(cabecalho, numero):
    ident = identificar(f"{cabecalho}\nConcede estímulo.\nA GOVERNADORA DO ESTADO,")
    assert ident.status is StatusIdentificacao.IDENTIFICADO
    assert ident.numero == numero
    assert DECRETO.nome_arquivo(ident.numero) == f"Decreto {numero}.pdf"


@pytest.mark.parametrize(
    "texto",
    [
        "Decreto nº 21.959, de 27 de dezembro de 1999, que regulamenta o PRODEPE.",  # citação em minúsculas
        "O DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026 foi publicado.",  # no meio do texto
        "DECRETO Nº 60.929 regulamenta algo.",  # sem data
        "DECRETOS Nº 60.929, DE 25 DE JUNHO DE 2026.",
    ],
)
def test_cabecalho_que_nao_e_cabecalho(texto):
    assert identificar(texto).status is not StatusIdentificacao.IDENTIFICADO