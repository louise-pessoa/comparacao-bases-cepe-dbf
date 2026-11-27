"""EDITAL e PORTARIA: definidos em especies.py, mas NÃO ativos; e a sincronia entre
especies.py / separador.py e regras_atos.py (fonte única das regras)."""

from __future__ import annotations

import pytest

from dbf_cepe.regras import especies
from dbf_cepe.regras import regras_atos as ra
from dbf_cepe.leitura import separador
from dbf_cepe.regras.especies import DECRETO, EDITAL, ESPECIES_ATIVAS, PORTARIA, Especie
from dbf_cepe.leitura import criterio
from dbf_cepe.leitura.identificador import StatusIdentificacao, identificar
from dbf_cepe.leitura.separador import separar_texto


def test_edital_e_portaria_nao_estao_ativos():
    assert ESPECIES_ATIVAS == (DECRETO,)
    assert EDITAL not in ESPECIES_ATIVAS and PORTARIA not in ESPECIES_ATIVAS


@pytest.mark.parametrize("especie", [EDITAL, PORTARIA])
def test_cabecalho_tem_o_grupo_numero_exigido_pelo_pipeline(especie: Especie):
    assert "numero" in especie.cabecalho.groupindex


def test_escopo_por_orgao_de_edital_e_portaria():
    assert EDITAL.orgaos_do_escopo == frozenset({"DBF", "DBM"})
    assert PORTARIA.orgaos_do_escopo == frozenset({"SF"})
    assert DECRETO.orgaos_do_escopo is None  # decreto se escopa por programa, não por órgão


def test_dpc_nao_esta_no_escopo():
    assert "DPC" not in EDITAL.orgaos_do_escopo


def test_nome_do_arquivo_depende_so_de_especie_e_numero():
    assert EDITAL.nome_arquivo("064", "2025") == "Edital 064-2025.pdf"  # como na base da DBF
    # "DBM Nº 01/2010" -> 3 dígitos, como na base ("Edital 001-2010 Peap.pdf").
    assert EDITAL.nome_arquivo("01", "2010") == "Edital 001-2010.pdf"
    assert EDITAL.e_da_familia("Edital 001-2010 Peap.pdf", "01", "2010")
    assert not EDITAL.e_da_familia("Edital 001-2011.pdf", "01", "2010")
    with pytest.raises(ValueError, match="ano"):
        EDITAL.nome_arquivo("064")
    assert PORTARIA.nome_arquivo("76") == "Portaria 76.pdf"


def test_tipos_de_edital_nao_tem_modelo_e_tem_evidencia_valida():
    validas = {especies.EVIDENCIA_AMOSTRA, especies.EVIDENCIA_PUBLICADO, especies.EVIDENCIA_PRESUMIDO}
    assert EDITAL.tipos
    assert all(t.modelo is None and t.evidencia in validas for t in EDITAL.tipos)
    assert next(t for t in EDITAL.tipos if t.nome == "DESCREDENCIAMENTO").evidencia == especies.EVIDENCIA_PRESUMIDO


def test_tipo_de_edital_pelo_inicio_do_titulo():
    def tipo(titulo: str):
        return next((t.nome for t in EDITAL.tipos if t.padrao.match(titulo)), None)

    assert tipo("CREDENCIAMENTO PRODEAUTO") == "CREDENCIAMENTO"
    assert tipo("INTIMAÇÃO") == "INTIMACAO"
    assert tipo("A DPC, no uso da competência") is None
    # 2019 e 2020: renovação e prorrogação do credenciamento (o Diário às vezes traz "RENOVAÇÂO").
    assert tipo("RENOVAÇÃO DO CREDENCIAMENTO DE ESTÍMULO À ATIVIDADE PORTUÁRIA") == "RENOVACAO"
    assert tipo("RENOVAÇÂO DO CREDENCIAMENTO DE ESTÍMULO À ATIVIDADE PORTUÁRIA") == "RENOVACAO"
    assert tipo("PRORROGAÇÃO DO CREDENCIAMENTO DE ESTÍMULO À ATIVIDADE PORTUÁRIA") == "PRORROGACAO"


def test_separador_aceita_edital_e_portaria_sem_ativar_nada():
    texto = (
        "EDITAL DBF Nº 063/2026\nCREDENCIAMENTO\nResolve credenciar.\nRecife, 21 de outubro de 2026.\n"
        "PORTARIA SF Nº 76, DE 24.04.2026\nO SECRETÁRIO DA FAZENDA, RESOLVE:\nArt. 1º Designar X.\n"
    )
    atos, avisos = separar_texto(texto, especies=(DECRETO, EDITAL, PORTARIA))
    assert [(a.especie.nome, a.numero) for a in atos] == [("Edital", "063"), ("Portaria", "76")]
    assert not avisos
    # com a configuração atual (só decreto) nada disso é separado
    assert separar_texto(texto)[0] == []


# ---------------------------------------------------------------------------
# Ano e órgão do cabeçalho (o que o pipeline leva adiante) e o cabeçalho em linhas
# ---------------------------------------------------------------------------

DBM_2010 = (
    "EDITAL DE CREDENCIAMENTO DE ESTÍMULO À ATIVIDADE PORTUÁRIA\nDBM Nº 054/2010\n"
    "A Diretoria de Benefícios Fiscais e Relações com os Municípios – DBM, nos termos da Lei nº 13.942,\n"
    "que regulamenta o Programa de Estímulo à Atividade Portuária, resolve credenciar a empresa.\n"
    "Recife, 9 de setembro de 2010.\n"
)


# Editais reais (sem os nomes de empresas): o de 2026 é seguido, na mesma página, de um acórdão
# do TATE; o de 2010, do título da seção seguinte.
EDITAL_2026 = (
    "EDITAL DBF Nº 035/2026\nCREDENCIAMENTO DE ESTÍMULO À ATIVIDADE PORTUÁRIA\n"
    "A Diretoria de Benefícios Fiscais - DBF, nos termos do art. 3º do anexo 27 do Decreto n° 44.650, resolve credenciar\n"
    "os contribuintes a seguir identificados, para fruição dos benefícios fiscais da Lei nº 13.942, de 4.12.2009.\n"
    "Os efeitos deste edital ficam condicionados ao cumprimento dos requisitos previstos no Convênio ICMS nº 190.\n"
    "Recife, 03 de julho de 2026.\nFelipe de Moraes Chaves\nDiretor\n"
    "geradores ocorridos antes de 2024. 4. Entendimento referendado pelo Pleno do TATE nos Acórdãos nºs 95/2025.\n"
)
EDITAL_2010 = (
    "EDITAL DE CREDENCIAMENTO DE ESTÍMULO À ATIVIDADE\nPORTUÁRIA\nDBM Nº 01/2010\n"
    "A Diretoria de Benefícios Fiscais e Relações com os Municípios –\nDBM, nos termos da Lei nº\n"
    "13.942, de 04/12/2009, resolve credenciar o contribuinte.\n"
    "Recife, 18 de março de 2010\nROBERTO DE ABREU E LIMA ALMEIDA\nDIRETOR\n"
    "DIRETORIA GERAL DA RECEITA – I REGIÃO FISCAL NORTE\nDRR I RF NORTE\n"
)


@pytest.mark.parametrize(
    ("texto", "ultimas"),
    [
        (EDITAL_2026, ["Recife, 03 de julho de 2026.", "Felipe de Moraes Chaves", "Diretor"]),
        (EDITAL_2010, ["Recife, 18 de março de 2010", "ROBERTO DE ABREU E LIMA ALMEIDA", "DIRETOR"]),
        # O título da seção seguinte em maiúsculas, sem palavra de título ("DIRETORIA",
        # "SECRETARIA"...), pareceria uma assinatura: o cargo do edital encerra o ato.
        (
            EDITAL_2026.replace("Diretor\ngeradores", "Diretor\nPLANEJAMENTO E GESTÃO\ngeradores"),
            ["Recife, 03 de julho de 2026.", "Felipe de Moraes Chaves", "Diretor"],
        ),
    ],
)
def test_edital_termina_no_cargo_de_quem_assina(texto, ultimas):
    (ato,), avisos = separar_texto(texto, especies=(EDITAL,))
    assert ato.texto.split("\n")[-3:] == ultimas
    assert not avisos and not ato.avisos
    assert criterio.motivos_de_reprovacao(ato) == []


def test_nome_em_caixa_mista_so_conta_logo_acima_do_cargo():
    # Sem o cargo embaixo, a frase em caixa mista depois do fecho não é assinatura.
    texto = "EDITAL DBF Nº 035/2026\nCREDENCIAMENTO\nTexto.\nRecife, 03 de julho de 2026.\nOutro Texto Qualquer\nE mais\n"
    (ato,), _ = separar_texto(texto, especies=(EDITAL,))
    assert ato.texto.endswith("Recife, 03 de julho de 2026.")


def test_tipo_do_edital_pelo_titulo_do_cabecalho():
    # 2010: o tipo está no título, antes do número; o texto depois do cabeçalho começa por "A Diretoria".
    ident = identificar(EDITAL_2010, (EDITAL,))
    assert (ident.numero, ident.orgao, ident.tipo.nome) == ("01", "DBM", "CREDENCIAMENTO")
    # 2026: o título vem na linha de baixo do cabeçalho.
    assert identificar(EDITAL_2026, (EDITAL,)).tipo.nome == "CREDENCIAMENTO"


def test_travessao_depois_do_cabecalho_nao_esconde_o_tipo():
    ident = identificar("EDITAL DE ADITAMENTO DBM Nº 001/2010 - CREDENCIAMENTO DE ESTÍMULO\nA Diretoria\n", (EDITAL,))
    assert ident.tipo.nome == "CREDENCIAMENTO"


def test_separador_leva_ano_e_orgao_de_edital_e_portaria():
    texto = (
        "EDITAL DBF Nº 063/2026\nCREDENCIAMENTO\nResolve credenciar.\n"
        "PORTARIA SF Nº 76, DE 24.04.2026\nO SECRETÁRIO DA FAZENDA, RESOLVE:\nArt. 1º Designar X.\n"
    )
    atos, _ = separar_texto(texto, especies=(DECRETO, EDITAL, PORTARIA))
    assert [(a.especie.nome, a.numero, a.ano, a.orgao, a.nome_padrao) for a in atos] == [
        ("Edital", "063", "2026", "DBF", "Edital 063-2026.pdf"),
        ("Portaria", "76", None, "SF", "Portaria 76.pdf"),
    ]


def test_separador_acha_o_edital_com_o_cabecalho_em_duas_linhas():
    (ato,), avisos = separar_texto(DBM_2010, especies=(EDITAL,))
    assert (ato.numero, ato.ano, ato.orgao, ato.nome_padrao) == ("054", "2010", "DBM", "Edital 054-2010.pdf")
    assert not avisos
    assert criterio.da_dbf(ato)  # DBM e PEAP: no escopo, como no catálogo
    # Com só o decreto ativo, nada disso é separado.
    assert separar_texto(DBM_2010)[0] == []


def test_identificador_le_o_edital_com_o_cabecalho_em_duas_linhas():
    ident = identificar(DBM_2010, (EDITAL,))
    assert ident.status is StatusIdentificacao.IDENTIFICADO
    assert (ident.numero, ident.ano, ident.orgao, ident.nome_padrao) == ("054", "2010", "DBM", "Edital 054-2010.pdf")


def test_edital_sem_ano_no_cabecalho_vai_para_revisao():
    ident = identificar("EDITAL DBF Nº 063\nCREDENCIAMENTO\n", (EDITAL,))
    assert ident.status is StatusIdentificacao.SEM_ANO


@pytest.mark.parametrize(
    ("texto", "orgao", "no_escopo"),
    [
        (DBM_2010, "DBM", True),
        (DBM_2010.replace("DBM Nº", "DPC Nº"), "DPC", False),  # a DPC não faz parte da demanda
    ],
)
def test_escopo_do_edital_no_pipeline_segue_o_orgao(texto, orgao, no_escopo):
    (ato,), _ = separar_texto(texto, especies=(EDITAL,))
    assert ato.orgao == orgao and criterio.da_dbf(ato) is no_escopo


def test_portaria_da_sf_so_entra_no_pipeline_com_programa_da_dbf():
    designacao = "PORTARIA SF Nº 76, DE 24.04.2026\nO SECRETÁRIO DA FAZENDA, RESOLVE:\nArt. 1º Designar X.\n"
    perda = (
        "PORTARIA SF Nº 200, DE 20.10.2026\nO SECRETÁRIO DA FAZENDA, considerando a renúncia ao incentivo do "
        "PRODEPE,\nRESOLVE:\nArt. 1º Declarar a perda dos benefícios.\n"
    )
    (sem_programa,), _ = separar_texto(designacao, especies=(PORTARIA,))
    (com_programa,), _ = separar_texto(perda, especies=(PORTARIA,))
    assert not criterio.da_dbf(sem_programa) and criterio.da_dbf(com_programa)


def test_portaria_nao_tem_arquivo_na_base_e_edital_precisa_do_ano():
    assert PORTARIA.nome_na_base("200", "2026") is None
    assert EDITAL.nome_na_base("064", "2025") == "Edital 064-2025.pdf"
    assert EDITAL.nome_na_base("064") is None
    assert DECRETO.nome_na_base("58509") == "Decreto 58509.pdf"


# ---------------------------------------------------------------------------
# Sincronia com regras_atos.py (uma fonte só)
# ---------------------------------------------------------------------------


def test_decreto_usa_exatamente_os_padroes_de_regras_atos():
    assert DECRETO.cabecalho is ra.CABECALHO_DECRETO
    assert DECRETO.fecho is ra.FECHO_DECRETO
    assert DECRETO.abertura_do_corpo is ra.ABERTURA_DO_CORPO_DECRETO
    assert DECRETO.fechos_anteriores == (ra.FECHO_ANTIGO_DECRETO,)
    assert especies.NOMES_OUTRAS_ESPECIES is ra.NOMES_OUTRAS_ESPECIES


def test_tipos_de_decreto_mantem_ordem_padrao_e_evidencia_de_regras_atos():
    esperados = [(n, p, e) for n, p, e in ra.TIPOS_DECRETO]
    obtidos = [(t.nome, t.padrao, t.evidencia) for t in especies.TIPOS_DECRETO]
    assert obtidos == esperados


def test_todo_tipo_de_decreto_tem_modelo_declarado():
    assert {t.nome for t in especies.TIPOS_DECRETO} == set(especies._MODELO_POR_TIPO)



def test_marcadores_de_ato_do_separador_e_de_regras_atos_coincidem():
    nomes_do_separador = separador._regex_marcadores((DECRETO, EDITAL, PORTARIA))
    texto = "\n".join(
        ["DECRETO Nº 1", "EDITAL X", "PORTARIA Y", "LEI Z", "ERRATA", "ATOS DO DIA", "EXTRATO", "AVISO", "DESPACHO"]
    )
    assert [m.group(0).strip() for m in nomes_do_separador.finditer(texto)] == [
        m.group(0).strip() for m in ra.MARCADOR_DE_ATO.finditer(texto)
    ]
"""Proteja o reconhecimento catalográfico de edital, portaria e seus limites."""
