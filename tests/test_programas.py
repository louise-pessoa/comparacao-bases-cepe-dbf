"""Critério de escopo: o decreto pertence a um programa de benefício da DBF?"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from conftest import AMOSTRAS, TIPO_ESPERADO_DECRETO
from especies import DECRETO
from programas import (
    EVIDENCIA_AMOSTRA,
    EVIDENCIA_PRESUMIDO,
    EVIDENCIA_PUBLICADO,
    PROGRAMAS_DBF,
    PROGRAMAS_OPCIONAIS,
    cabecalho_do_ato,
    cita_programa_so_no_corpo,
    em_escopo,
    programas_do_ato,
)
from separador import separar_caderno

AMOSTRAS_CADERNOS = Path(__file__).parent / "amostras_cadernos"
TRECHO_24_A_26 = AMOSTRAS_CADERNOS / "Caderno 2026-06-26 (paginas 24 a 26).pdf"
PAGINA_9 = AMOSTRAS_CADERNOS / "Caderno 2026-06-26 (pagina 9).pdf"

TODOS = PROGRAMAS_DBF + PROGRAMAS_OPCIONAIS


def siglas(texto: str, programas=None) -> list[str]:
    return [p.sigla for p in programas_do_ato(texto, DECRETO, programas)]


def decreto(ementa: str, considerandos: str = "", corpo: str = "Art. 1º Fica concedido.") -> str:
    return (
        f"DECRETO Nº 60.001, DE 25 DE JUNHO DE 2026.\n{ementa}\n"
        f"A GOVERNADORA DO ESTADO, no uso das atribuições,\n{considerandos}\nDECRETA:\n{corpo}\n"
    )


# ---------------------------------------------------------------------------
# Amostras reais
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("numero", sorted(TIPO_ESPERADO_DECRETO))
def test_as_seis_amostras_reais_sao_do_prodepe(numero):
    with pymupdf.open(AMOSTRAS / f"Decreto {numero}.pdf") as doc:
        texto = "".join(pagina.get_text() for pagina in doc)
    assert siglas(texto) == ["PRODEPE"]


def test_decreto_60233_so_cita_a_lei_do_programa_nos_considerandos():
    # A ementa da terceirização não diz "PRODEPE"; os considerandos citam a Lei 11.675.
    with pymupdf.open(AMOSTRAS / "Decreto 60233.pdf") as doc:
        texto = "".join(pagina.get_text() for pagina in doc)
    assert "PRODEPE" not in cabecalho_do_ato(texto, DECRETO).split("CONSIDERANDO")[0]
    assert siglas(texto) == ["PRODEPE"]


def test_trecho_real_separa_prodepe_de_utilidade_publica_e_credito():
    por_numero = {a.numero: siglas(a.texto) for a in separar_caderno(TRECHO_24_A_26).atos}
    assert por_numero["60928"] == por_numero["60929"] == ["PRODEPE"]
    for numero in ("60930", "60931", "60932", "60933", "60937"):
        assert por_numero[numero] == []


def test_decreto_proind_do_caderno_real():
    atos = {a.numero: a for a in separar_caderno(PAGINA_9).atos}
    assert siglas(atos["60903"].texto) == ["PROIND"]
    assert siglas(atos["60904"].texto) == ["PRODEPE"]


# ---------------------------------------------------------------------------
# Cada programa, em várias grafias
# ---------------------------------------------------------------------------

CASOS = [
    # PRODEPE
    ("PRODEPE", "Concede estímulo previsto na Lei nº 11.675, de 11 de outubro de 1999, à empresa X."),
    ("PRODEPE", "Dispõe sobre o PRODEPE da empresa X."),
    ("PRODEPE", "Concede estímulo previsto na Lei Estadual n° 11.675, à empresa X."),
    ("PRODEPE", "Concede estímulo previsto na Lei n.º 11675, à empresa X."),
    ("PRODEPE", "Altera o Decreto nº 21.959, de 27 de dezembro de 1999."),
    ("PRODEPE", "Altera o Decreto n° 21959, de 27 de dezembro de 1999."),
    ("PRODEPE", "Introduz modificações no regulamento do Programa de Desenvolvimento do Estado de Pernambuco."),
    ("PRODEPE", "Dispõe sobre a prorrogação do estímulo.", "CONSIDERANDO a decisão do Comitê Diretor do PRODEPE, na 148ª Reunião;"),
    # PROIND
    ("PROIND", "Autoriza a utilização do incentivo previsto no Anexo 33 do Decreto nº 44.650, de 2017, à empresa X."),
    ("PROIND", "Autoriza o incentivo do PROIND ao contribuinte X."),
    ("PROIND", "Autoriza o incentivo previsto no Anexo 33 ao RICMS/PE ao contribuinte X."),
    ("PROIND", "Autoriza o incentivo previsto no Anexo 33 do RICMS ao contribuinte X."),
    # PEAP
    ("PEAP", "Modifica o Decreto nº 44.650 relativamente ao Programa de Estímulo à Atividade Portuária."),
    ("PEAP", "Altera as regras do Peap-II."),
    ("PEAP", "Altera as regras do PEAP II."),
    ("PEAP", "Altera as regras do Peap-I."),
    ("PEAP", "Altera o Decreto nº 34.560, de 2010."),
    ("PEAP", "Altera o Anexo 27 do Decreto nº 44.650, de 30 de junho de 2017."),
    ("PEAP", "Institui o Programa de Estímulo à Atividade Portuária, nos termos da Lei nº 13.942, de 2009."),
    # PRODINPE
    ("PRODINPE", "Concede incentivo do PRODINPE à empresa X."),
    ("PRODINPE", "Dispõe sobre a Indústria Naval e de Mecânica Pesada Associada."),
    ("PRODINPE", "Concede estímulo previsto na Lei nº 12.710, de 2004."),
    ("PRODINPE", "Altera o Anexo 29 do Decreto nº 44.650, de 2017."),
    # PRODEAUTO
    ("PRODEAUTO", "Concede incentivo do Programa de Desenvolvimento do Setor Automotivo à empresa X."),
    ("PRODEAUTO", "Concede incentivo do PRODEAUTO à empresa X."),
    ("PRODEAUTO", "Concede incentivo previsto na Lei nº 13.484, de 29 de junho de 2008."),
    ("PRODEAUTO", "Altera o Anexo 36 do Decreto nº 44.650, de 2017."),
    # Vitivinícola
    ("VITIVINICOLA", "Dispõe sobre o Programa de Desenvolvimento do Setor Vitivinícola."),
    ("VITIVINICOLA", "Altera o Anexo 30 do Decreto nº 44.650, de 2017."),
    # Calçados
    ("CALCADOS", "Dispõe sobre o Programa da Indústria de Calçados, Bolsas, Cintos e Bolas Esportivas."),
    ("CALCADOS", "Altera o Anexo 35 do Decreto nº 44.650, de 2017."),
    # Mais Atacadistas
    ("MAIS_ATACADISTAS", "Inclui a empresa X na sistemática Mais Atacadistas – Pernambuco."),
    ("MAIS_ATACADISTAS", "Altera o Anexo 26 do Decreto nº 44.650, de 2017."),
]


@pytest.mark.parametrize("caso", CASOS, ids=lambda c: f"{c[0]}-{c[1][:40]}")
def test_cada_programa_e_reconhecido_no_cabecalho(caso):
    sigla, ementa, *considerandos = caso
    assert sigla in siglas(decreto(ementa, *considerandos))


def test_todos_os_programas_ativos_tem_caso_de_teste():
    assert {p.sigla for p in PROGRAMAS_DBF} == {caso[0] for caso in CASOS}


def test_programa_aparece_so_quando_o_cabecalho_o_cita():
    # PROIND não pode puxar o PRODEPE só porque o texto tem "Decreto nº 44.650".
    assert siglas(decreto("Autoriza o incentivo do Anexo 33 do Decreto nº 44.650, de 2017.")) == ["PROIND"]


def test_decreto_pode_citar_mais_de_um_programa():
    ementa = "Concede estímulo do PRODEPE e autoriza o incentivo do PROIND à empresa X."
    assert siglas(decreto(ementa)) == ["PRODEPE", "PROIND"]


# ---------------------------------------------------------------------------
# Falsos positivos que os padrões evitam
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ementa, considerandos, nao_esperado",
    [
        # Termos genéricos nos considerandos de um PRODEPE de empresa desse ramo.
        ("Concede estímulo do PRODEPE à empresa X.", "CONSIDERANDO a importância do setor automotivo para o Estado;", "PRODEAUTO"),
        ("Concede estímulo do PRODEPE à empresa X.", "CONSIDERANDO a indústria naval de Suape;", "PRODINPE"),
        ("Concede estímulo do PRODEPE à empresa X.", "CONSIDERANDO a atividade portuária do Estado;", "PEAP"),
        ("Concede estímulo do PRODEPE à empresa X.", "CONSIDERANDO a produção vitivinícola do Vale do São Francisco;", "VITIVINICOLA"),
        # "Anexo N" sem o RICMS ao lado é de outro decreto.
        ("Altera o Anexo 33 do Decreto nº 12.345, de 2020.", "", "PROIND"),
        ("Altera o Anexo 27 deste Decreto.", "", "PEAP"),
        # Número de lei parecido.
        ("Concede estímulo previsto na Lei nº 11.6750, de 1999.", "", "PRODEPE"),
        ("Concede estímulo previsto na Lei nº 111.675, de 1999.", "", "PRODEPE"),
        # Sigla que é parte de outra palavra.
        ("Dispõe sobre o PRODEPEX.", "", "PRODEPE"),
        ("Dispõe sobre o SUPEAP.", "", "PEAP"),
    ],
)
def test_padroes_nao_marcam_programa_por_termo_generico(ementa, considerandos, nao_esperado):
    assert nao_esperado not in siglas(decreto(ementa, considerandos))


@pytest.mark.parametrize(
    "ementa",
    [
        "Abre ao Orçamento Fiscal do Estado, relativo ao exercício de 2026, crédito suplementar.",
        "Declara de utilidade pública, para fins de desapropriação, área de terra.",
        "Dispõe sobre a estrutura da Secretaria de Educação.",
        "Altera o art. 15 do Decreto nº 39.200, de 18 de março de 2013.",
        "Atualiza os valores relativos à Taxa de Fiscalização e Utilização de Serviços Públicos.",
    ],
)
def test_decretos_de_outros_assuntos_ficam_fora_do_escopo(ementa):
    assert siglas(decreto(ementa)) == []


def test_so_o_cabecalho_conta_citacao_no_corpo_nao_basta():
    texto = decreto("Dispõe sobre a estrutura da Secretaria.", corpo="Art. 1º Aplica-se o PRODEPE.")
    assert siglas(texto) == []
    assert cita_programa_so_no_corpo(texto, DECRETO) is True


@pytest.mark.parametrize(
    "corpo",
    [
        "Art. 1º O Anexo 33 do Decreto nº 44.650, de 2017, passa a vigorar com alterações.",
        "Art. 1º Aplica-se o Programa de Estímulo à Atividade Portuária.",
        "Art. 1º Fica incluída a empresa na sistemática Mais Atacadistas.",
    ],
)
def test_aviso_vale_para_qualquer_programa_no_corpo_e_nao_so_para_siglas(corpo):
    texto = decreto("Dispõe sobre a estrutura da Secretaria.", corpo=corpo)
    assert siglas(texto) == []
    assert cita_programa_so_no_corpo(texto, DECRETO) is True


def test_sem_citacao_no_corpo_nao_avisa():
    assert cita_programa_so_no_corpo(decreto("Dispõe sobre a estrutura da Secretaria."), DECRETO) is False
    assert cita_programa_so_no_corpo(decreto("Concede estímulo do PRODEPE.", corpo="Art. 1º PRODEPE."), DECRETO) is False


def test_cabecalho_termina_no_decreta_ou_nos_primeiros_caracteres():
    assert "Art. 1º" not in cabecalho_do_ato(decreto("Ementa."), DECRETO)
    sem_decreta = "DECRETO Nº 1, DE 1 DE JANEIRO DE 2026.\n" + "x" * 10000
    assert len(cabecalho_do_ato(sem_decreta, DECRETO)) == 4000


def test_decreta_com_espaco_antes_dos_dois_pontos():
    texto = "DECRETO Nº 1, DE 1 DE JANEIRO DE 2026.\nEmenta.\nDECRETA :\nArt. 1º PRODEPE."
    assert "Art. 1º" not in cabecalho_do_ato(texto, DECRETO)


# ---------------------------------------------------------------------------
# Programas opcionais (inativos) e metadados
# ---------------------------------------------------------------------------

OPCIONAIS = [
    ("COOPERA", "Altera o Anexo 44 do Decreto nº 44.650, de 2017."),
    ("COOPERA", "Dispõe sobre o COOPERA."),
    ("FIOS_TECIDOS_CONFECCOES", "Altera o Anexo 40 do Decreto nº 44.650, de 2017."),
    ("FIOS_TECIDOS_CONFECCOES", "Dispõe sobre a sistemática de fios, tecidos e confecções."),
    ("MONOFASICA", "Altera o Anexo 41 do Decreto nº 44.650, de 2017."),
    ("MONOFASICA", "Dispõe sobre o regime de tributação monofásica."),
    ("INOVAR_PE", "Dispõe sobre o Inovar-PE."),
    ("INOVAR_PE", "Dispõe sobre o Inovar PE."),
    ("FEEF", "Dispõe sobre o FEEF."),
    ("FEEF", "Dispõe sobre o Fundo Estadual de Equilíbrio Fiscal."),
]


@pytest.mark.parametrize("sigla, ementa", OPCIONAIS)
def test_programas_opcionais_estao_prontos_mas_inativos(sigla, ementa):
    texto = decreto(ementa)
    assert sigla not in siglas(texto)  # fora do escopo atual
    assert sigla in siglas(texto, TODOS)  # basta movê-lo para PROGRAMAS_DBF


def test_todos_os_opcionais_tem_caso_de_teste():
    assert {p.sigla for p in PROGRAMAS_OPCIONAIS} == {s for s, _ in OPCIONAIS}


def test_siglas_sao_unicas_entre_ativos_e_opcionais():
    todas = [p.sigla for p in TODOS]
    assert len(todas) == len(set(todas))


def test_opcional_nao_marca_programa_ativo_e_vice_versa():
    # Anexo 44 é COOPERA e só COOPERA.
    assert siglas(decreto("Altera o Anexo 44 do Decreto nº 44.650."), TODOS) == ["COOPERA"]


def test_evidencia_de_cada_programa_e_conhecida():
    validas = {EVIDENCIA_AMOSTRA, EVIDENCIA_PUBLICADO, EVIDENCIA_PRESUMIDO}
    assert {p.evidencia for p in TODOS} <= validas
    confirmados = {p.sigla for p in PROGRAMAS_DBF if p.evidencia == EVIDENCIA_AMOSTRA}
    assert confirmados == {"PRODEPE", "PROIND"}  # os únicos com decretos da DBF em mãos