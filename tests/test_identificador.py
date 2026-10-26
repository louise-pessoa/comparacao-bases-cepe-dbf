"""Etapa 2 (identificação): número, espécie e tipo do ato."""

from __future__ import annotations

import pytest

from conftest import AMOSTRAS, TIPO_ESPERADO_DECRETO, texto_decreto
from identificador import StatusIdentificacao, identificar, normalizar_espacos
from reader import ler_pdf


@pytest.mark.parametrize("numero, tipo", sorted(TIPO_ESPERADO_DECRETO.items()))
def test_amostras_reais(numero, tipo):
    ident = identificar(ler_pdf(AMOSTRAS / f"Decreto {numero}.pdf").texto)
    assert ident.status is StatusIdentificacao.IDENTIFICADO
    assert ident.numero == numero
    assert ident.tipo.nome == tipo
    assert ident.especie.nome_arquivo(ident.numero) == f"Decreto {numero}.pdf"


@pytest.mark.parametrize(
    "cabecalho, numero",
    [
        ("DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026.", "60929"),
        ("DECRETO N° 60.929, DE 25 DE JUNHO DE 2026.", "60929"),
        ("DECRETO N.º 60.929, DE 25 DE JUNHO DE 2026.", "60929"),
        ("DECRETO Nº 60929, DE 25 DE JUNHO DE 2026.", "60929"),
        ("DECRETO Nº 16.050, DE 1º DE MARÇO DE 2012.", "16050"),
        ("DECRETO Nº 999, DE 5 DE MAIO DE 1990.", "999"),
    ],
)
def test_numero_sem_separador_de_milhar(cabecalho, numero):
    ident = identificar(f"{cabecalho}\nConcede estímulo.\nA GOVERNADORA DO ESTADO,")
    assert ident.status is StatusIdentificacao.IDENTIFICADO
    assert ident.numero == numero


def test_espacos_e_quebras_de_linha_do_texto_justificado():
    texto = (
        "DECRETO Nº 60.233, DE 15 DE JANEIRO DE 2026.\n"
        "Autoriza a terceirização da industrialização dos produtos "
        "incentivados pelo Decreto nº 22.478, de 21\nde \njulho \nde \n2000,\n"
        "A GOVERNADORA DO ESTADO,"
    )
    ident = identificar(texto)
    assert ident.numero == "60233"
    assert ident.tipo.nome == "TERCEIRIZACAO"


def test_numero_vem_do_cabecalho_e_nao_das_citacoes():
    # O corpo cita outros decretos; só vale o cabeçalho, no início.
    texto = texto_decreto(
        "60.950",
        "Dispõe sobre a prorrogação do prazo de fruição concedido pelo "
        "Decreto nº 49.853, de 26 de novembro de 2020, à empresa X.",
    )
    ident = identificar(texto)
    assert ident.numero == "60950"
    assert ident.tipo.nome == "PRORROGACAO"


def test_cabecalho_fora_do_inicio_nao_e_aceito():
    # Texto que apenas MENCIONA um decreto em maiúsculas não é o ato.
    ident = identificar("Parecer sobre o DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026.")
    assert ident.status is StatusIdentificacao.SEM_CORRESPONDENCIA


@pytest.mark.parametrize(
    "ementa, esperado",
    [
        ("Concede estímulo previsto na Lei nº 11.675 à empresa X.", "CONCESSAO"),
        ("Introduz alterações nos Decretos nº 48.564 e nº 50.135.", "ALTERACAO"),
        ("Dispõe sobre a prorrogação do prazo de fruição.", "PRORROGACAO"),
        ("Dispõe sobre a renovação do prazo de fruição.", "RENOVACAO"),
        ("Dispõe sobre a segunda prorrogação do prazo de fruição.", "SEGUNDA_PRORROGACAO"),
        ("Dispõe sobre a segunda renovação do prazo de fruição.", "SEGUNDA_RENOVACAO"),
        ("Dispõe sobre a transferência do estímulo do PRODEPE.", "TRANSFERENCIA"),
        ("Autoriza a terceirização da industrialização dos produtos.", "TERCEIRIZACAO"),
        (
            "Autoriza a utilização do incentivo fiscal previsto no Anexo 33 do Decreto "
            "nº 44.650, de 30 de junho de 2017, que dispõe sobre o PROIND pelo contribuinte X.",
            "AUTORIZACAO_PROIND",
        ),
        # Armadilha: a ementa da revogação cita "prorrogação" no meio do texto.
        (
            "Revoga o Decreto nº 39.617, que dispõe sobre a prorrogação do "
            "prazo de fruição de estímulo do PRODEPE.",
            "REVOGACAO",
        ),
    ],
)
def test_tipo_pelo_inicio_da_ementa(ementa, esperado):
    assert identificar(texto_decreto("60.001", ementa)).tipo.nome == esperado


def test_segunda_prorrogacao_e_renovacao_usam_o_modelo_do_tipo_base():
    prorrogacao = identificar(
        texto_decreto("60.002", "Dispõe sobre a segunda prorrogação do prazo.")
    ).tipo
    renovacao = identificar(
        texto_decreto("60.003", "Dispõe sobre a segunda renovação do prazo.")
    ).tipo
    assert prorrogacao.modelo == "60950"
    assert renovacao.modelo == "60945"


def test_tipo_desconhecido_ainda_identifica_o_numero():
    ident = identificar(texto_decreto("60.004", "Estabelece normas gerais."))
    assert ident.status is StatusIdentificacao.IDENTIFICADO
    assert ident.numero == "60004"
    assert ident.tipo is None


@pytest.mark.parametrize(
    "texto",
    [
        "LEI Nº 11.675, DE 11 DE OUTUBRO DE 1999.\nDispõe sobre o PRODEPE.",
        "LEI COMPLEMENTAR Nº 105, DE 10 DE JANEIRO DE 2001.",
        "PORTARIA SF Nº 030, DE 4 DE MARÇO DE 2010.",
        "EDITAL Nº 12/2026\nConvoca as empresas.",
        "ERRATA\nNo Decreto nº 60.929, de 25 de junho de 2026, onde se lê...",
        "Errata ao Decreto nº 60.929, de 25 de junho de 2026.",
    ],
)
def test_outras_especies_sao_reconhecidas(texto):
    assert identificar(texto).status is StatusIdentificacao.OUTRA_ESPECIE


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "   \n  ",
        "Texto qualquer sem cabeçalho de ato.",
        "DECRETO LEGISLATIVO Nº 100, DE 1º DE MARÇO DE 2020.",
        "DECRETO Nº , DE 25 DE JUNHO DE 2026.",
    ],
)
def test_sem_correspondencia_nao_e_descartado(texto):
    # Sem correspondência = revisão manual, nunca descarte.
    assert identificar(texto).status is StatusIdentificacao.SEM_CORRESPONDENCIA


def test_cabecalho_repetido_do_mesmo_ato_nao_e_multiplos_atos():
    # Cabeçalho corrente repetido a cada página não deve acusar vários atos.
    texto = texto_decreto("60.929", "Concede estímulo.") + "\nDECRETO Nº 60.929, DE 25 DE JUNHO DE 2026."
    assert identificar(texto).status is StatusIdentificacao.IDENTIFICADO


def test_pdf_com_mais_de_um_ato_vai_para_revisao():
    texto = (
        texto_decreto("60.929", "Concede estímulo.")
        + "\n"
        + texto_decreto("60.930", "Concede estímulo.")
    )
    ident = identificar(texto)
    assert ident.status is StatusIdentificacao.MULTIPLOS_ATOS
    assert "60929" in ident.detalhe and "60930" in ident.detalhe


def test_normalizar_espacos():
    assert normalizar_espacos("  a \n\n b\t c ") == "a b c"
