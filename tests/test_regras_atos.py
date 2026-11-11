"""Testes de regras_atos.py: reconhecimento de decreto, edital, portaria, errata e
republicação. As frases são curtas e vêm dos cadernos reais de 25/04, 26/06, 15/07,
21/10 e 22/10/2026 (sem nomes de pessoas e sem trechos extensos)."""

from __future__ import annotations


import pytest

from dbf_cepe.regras import regras_atos as ra
from dbf_cepe.regras.regras_atos import identificar


def _unico(texto: str, especie: str):
    achados = [a for a in identificar(texto) if a.especie == especie]
    assert len(achados) == 1, achados
    return achados[0]


# ---------------------------------------------------------------------------
# Tabela de exemplos (entrada -> resultado esperado)
# ---------------------------------------------------------------------------

# Cada item: (texto curto, (campos esperados do 1º achado, do 2º achado, ...)). O texto
# tem de gerar EXATAMENTE tantos achados quantos dicionários houver; só os campos citados
# são conferidos. Trechos de cadernos de 25/04, 26/06, 15/07, 21/10 e 22/10/2026.
ASSINATURAS_DO_DECRETO = (
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
    # Edital sem órgão no cabeçalho: o órgão vem do título em maiúsculas acima (pista de baixa
    # confiança, em `orgao_do_titulo`; `orgao` continua vazio). Outro órgão = fora do escopo.
    (
        "SECRETARIA DE ADMINISTRAÇÃO\nAVISO DE ABERTURA – 2ª REPUBLICAÇÃO DE EDITAL\n"
        "EDITAL DE CREDENCIAMENTO N° 0014.2026.PNE\nPROCESSO DE INEXIGIBILIDADE\n",
        (
            {"especie": "Edital", "numero": "0014.2026.PNE", "orgao": None,
             "orgao_do_titulo": "SECRETARIA DE ADMINISTRAÇÃO", "sefaz": False, "republicacao": "sim"},
        ),
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
        "A GOVERNADORA DO ESTADO\n" + ASSINATURAS_DO_DECRETO
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


@pytest.mark.parametrize(("texto", "esperados"), EXEMPLOS, ids=[f"exemplo {i}" for i in range(1, len(EXEMPLOS) + 1)])
def test_tabela_de_exemplos(texto, esperados):
    achados = ra.identificar(texto)
    assert len(achados) == len(esperados), achados
    for achado, esperado in zip(achados, esperados):
        assert {campo: getattr(achado, campo) for campo in esperado} == esperado


# ---------------------------------------------------------------------------
# Decreto
# ---------------------------------------------------------------------------

DECRETO = (
    "DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026.\n"
    "Concede estímulo previsto na Lei nº 11.675, de 11 de outubro de 1999.\n"
    "A GOVERNADORA DO ESTADO, no uso das atribuições.\n"
)


def test_decreto_numero_ano_data_e_tipo():
    a = _unico(DECRETO, "Decreto")
    assert (a.numero, a.ano, a.data_do_ato, a.tipo) == ("60929", "2026", "2026-06-25", "CONCESSAO")
    assert a.orgao is None and a.sefaz is None


def test_decreto_citado_no_corpo_nao_conta():
    texto = "Considerando o Decreto nº 21.959, de 27 de dezembro de 1999, e DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026 citado."
    assert identificar(texto) == []


def test_decreto_no_meio_da_linha_nao_conta():
    assert identificar("texto antes DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026.") == []


def test_numero_do_decreto_com_e_sem_separador_de_milhar():
    com = _unico("DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026.\nA GOVERNADORA", "Decreto")
    sem = _unico("DECRETO Nº 60929, DE 25 DE JUNHO DE 2026.\nA GOVERNADORA", "Decreto")
    assert com.numero == sem.numero == "60929"


def test_decreto_sem_abertura_do_corpo_avisa():
    a = _unico("DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026.\nConcede estímulo.\n", "Decreto")
    assert any("abertura do corpo" in aviso for aviso in a.avisos)


@pytest.mark.parametrize(
    ("ementa", "esperado"),
    [
        ("Dispõe sobre a segunda prorrogação dos prazos", "SEGUNDA_PRORROGACAO"),
        ("Dispõe sobre a prorrogação dos prazos", "PRORROGACAO"),
        ("Introduz alterações no Decreto nº 41.447", "ALTERACAO"),
        ("Altera o Decreto nº 21.959, de 27 de dezembro de 1999", "ALTERACAO_NORMA"),
        ("Autoriza a terceirização da industrialização", "TERCEIRIZACAO"),
        ("Abre ao Orçamento Fiscal do Estado crédito suplementar", None),
    ],
)
def test_tipo_do_decreto_pelo_inicio_da_ementa(ementa, esperado):
    a = _unico(f"DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026.\n{ementa}\nA GOVERNADORA DO ESTADO\n", "Decreto")
    assert a.tipo == esperado


def test_ordem_dos_tipos_de_decreto_e_preservada():
    nomes = [n for n, _, _ in ra.TIPOS_DECRETO]
    assert nomes.index("SEGUNDA_PRORROGACAO") < nomes.index("PRORROGACAO")
    assert nomes.index("SEGUNDA_RENOVACAO") < nomes.index("RENOVACAO")
    assert nomes.index("ALTERACAO_NORMA") < nomes.index("ALTERACAO")


# ---------------------------------------------------------------------------
# Edital
# ---------------------------------------------------------------------------

EDITAL_DBF = (
    "EDITAL DBF Nº 063/2026\n"
    "CREDENCIAMENTO RELATIVO À CONCESSÃO DE BENEFÍCIO FISCAL DO ICMS\n"
    "A Diretoria de Benefícios Fiscais - DBF, considerando a Lei nº 15.706, resolve credenciar.\n"
    "Recife, 21 de outubro de 2026.\n"
    "Diretor\n"
)


def test_edital_da_dbf_numero_ano_orgao_tipo_e_data_do_fecho():
    a = _unico(EDITAL_DBF, "Edital")
    assert (a.numero, a.ano, a.orgao, a.tipo) == ("063", "2026", "DBF", "CREDENCIAMENTO")
    assert a.sefaz is True
    assert a.data_do_ato == "2026-10-21"
    assert any("Recife" in aviso for aviso in a.avisos)  # data não veio do cabeçalho: aviso


@pytest.mark.parametrize(
    "cabecalho",
    ["EDITAL DBF Nº 017 /2026", "EDITAL DBF Nº 017/2026", "EDITAL DBF N° 017/2026", "EDITAL DBF Nº. 017/2026"],
)
def test_edital_dbf_tolera_espaco_antes_da_barra_e_variantes_do_ordinal(cabecalho):
    a = _unico(f"{cabecalho}\nCREDENCIAMENTO PRODEAUTO\n", "Edital")
    assert (a.numero, a.ano, a.orgao) == ("017", "2026", "DBF")


def test_edital_dbm_e_aceito_como_nome_da_dbf():
    a = _unico("EDITAL DBM Nº 001/2026\nCREDENCIAMENTO\n", "Edital")
    assert a.orgao == "DBM" and a.sefaz is True


def test_edital_de_outro_orgao_nao_e_sefaz():
    a = _unico("EDITAL FACEPE/SECTI n°25/2026 - Projetos Orientados a Missões\n", "Edital")
    assert (a.numero, a.ano, a.orgao, a.sefaz) == ("25", "2026", "FACEPE/SECTI", False)


def test_edital_dpc_e_reconhecido_mas_fora_do_escopo():
    a = _unico("EDITAL DPC Nº 062/2026\nA DPC, no uso da competência\n", "Edital")
    assert (a.orgao, a.sefaz) == ("DPC", False)


def test_edital_sem_orgao_no_cabecalho_fica_a_confirmar():
    a = _unico("EDITAL Nº 43/2026\nCREDENCIAMENTO PARA FRUIÇÃO DO BENEFÍCIO\n", "Edital")
    assert (a.numero, a.orgao, a.sefaz, a.tipo) == ("43", None, None, "CREDENCIAMENTO")
    assert any("órgão não consta" in aviso for aviso in a.avisos)


def test_edital_de_tipo_no_cabecalho():
    a = _unico("EDITAL DE INTIMAÇÃO Nº 005/2026\nCIÊNCIA DO LANÇAMENTO\n", "Edital")
    assert (a.numero, a.ano, a.orgao, a.tipo) == ("005", "2026", None, "INTIMACAO")


@pytest.mark.parametrize(
    ("cabecalho", "numero", "ano"),
    [
        ("EDITAL DE CREDENCIAMENTO Nº. 4050.2025.0021.SES. Objeto", "4050.2025.0021.SES", "2025"),
        ("EDITAL DE CREDENCIAMENTO N° 0014.2026.PNE", "0014.2026.PNE", "2026"),
    ],
)
def test_edital_com_codigo_composto_nao_perde_o_numero(cabecalho, numero, ano):
    a = _unico(cabecalho, "Edital")
    assert (a.numero, a.ano, a.tipo) == (numero, ano, "CREDENCIAMENTO")


def test_edital_com_numero_em_linha_separada():
    a = _unico("EDITAL \nNº. \n4052.2025.0023.IASSEPE. \nOBJETO: Credenciamento", "Edital")
    assert a.numero == "4052.2025.0023.IASSEPE"


@pytest.mark.parametrize(
    ("texto", "tipo"),
    [
        ("EDITAL DE NÃO CONCORRÊNCIA\nA empresa", "NAO_CONCORRENCIA"),
        ("EDITAL DE CONVOCAÇÃO\nSINDICATO", "CONVOCACAO"),
        ("EDITAL DE DIVULGAÇÃO\nA AGÊNCIA", "DIVULGACAO"),
        ("EDITAL DE CONCURSO PÚBLICO Nº xxx/202x - UPE\nABERTURA", "CONCURSO_PUBLICO"),
    ],
)
def test_edital_sem_numero_nao_some_e_avisa(texto, tipo):
    a = _unico(texto, "Edital")
    assert a.numero is None and a.tipo == tipo
    assert any("sem número" in aviso for aviso in a.avisos)


def test_edital_em_minusculas_ou_no_meio_da_linha_nao_conta():
    assert identificar("Edital DBF Nº 017/2026\nTexto\n") == []
    assert identificar("conforme o EDITAL DBF Nº 017/2026 publicado") == []


# ---------------------------------------------------------------------------
# Órgão lido do título da seção (quando o cabeçalho não o traz)
# ---------------------------------------------------------------------------


def test_orgao_do_titulo_acima_do_edital_sem_sigla():
    texto = (
        "SECRETARIA DE ADMINISTRAÇÃO\nAVISO DE ABERTURA – 2ª REPUBLICAÇÃO DE EDITAL\n"
        "EDITAL DE CREDENCIAMENTO N° 0014.2026.PNE\nPROCESSO\n"
    )
    a = _unico(texto, "Edital")
    assert a.orgao is None  # o cabeçalho não traz órgão: `orgao` nunca recebe a pista do título
    assert a.orgao_do_titulo == "SECRETARIA DE ADMINISTRAÇÃO"
    assert a.sefaz is False
    assert any("lido do título da seção acima" in aviso for aviso in a.avisos)


@pytest.mark.parametrize(
    ("acima", "titulo"),
    [
        ("FUND DO PATRIMÔNIO HISTÓRICO E\nARTÍSTICO DE PE-FUNDARPE\nCREDENCIAMENTO DE PARECERISTAS – FUNCULTURA 2025",
         "FUND DO PATRIMÔNIO HISTÓRICO E ARTÍSTICO DE PE-FUNDARPE"),
        ("AGÊNCIA ESTADUAL DE MEIO AMBIENTE -\nCPRH", "AGÊNCIA ESTADUAL DE MEIO AMBIENTE - CPRH"),
        ("AGÊNCIA DE DEFESA E FISCALIZAÇÃO\nAGROPECUÁRIA DE PERNAMBUCO-\nADAGRO",
         "AGÊNCIA DE DEFESA E FISCALIZAÇÃO AGROPECUÁRIA DE PERNAMBUCO- ADAGRO"),
        ("UNIVERSIDADE DE PERNAMBUCO - UPE /\nREITORIA\nA Reitora assinou a\nseguinte Portaria:",
         "UNIVERSIDADE DE PERNAMBUCO - UPE / REITORIA"),
        ("GOVERNO DO ESTADO DE PERNAMBUCO\nSECRETARIA DE ADMINISTRAÇÃO – SAD/PE\nUNIVERSIDADE DE PERNAMBUCO - UPE",
         "UNIVERSIDADE DE PERNAMBUCO - UPE"),  # vale o título mais próximo do cabeçalho
    ],
)
def test_titulo_da_secao_quebrado_em_varias_linhas(acima, titulo):
    a = _unico(f"{acima}\nPORTARIA Nº 937/2026 de 22.04.2026\nI - Exonerar\n", "Portaria")
    assert a.orgao_do_titulo == titulo


def test_titulo_que_so_tem_a_palavra_fiscalizacao_nao_e_orgao_da_fazenda():
    a = _unico("AGÊNCIA DE DEFESA E FISCALIZAÇÃO AGROPECUÁRIA - ADAGRO\nPORTARIA Nº 73, DE 14/07/2026.\n", "Portaria")
    assert a.sefaz is False  # "FISCALIZAÇÃO" não é a palavra "FISCAL"


def test_titulo_da_dbf_entra_no_escopo_e_o_da_dpc_nao():
    dbf = _unico("DIRETORIA DE BENEFÍCIOS FISCAIS - DBF\nEDITAL Nº 5/2026\nCREDENCIAMENTO\n", "Edital")
    assert dbf.sefaz is True
    dpc = _unico("DIRETORIA GERAL DE PLANEJAMENTO E CONTROLE DA AÇÃO FISCAL – DPC\nEDITAL Nº 43/2026\n", "Edital")
    assert dpc.sefaz is False


def test_titulo_da_fazenda_sem_sigla_do_escopo_fica_a_confirmar():
    a = _unico("DIRETORIA GERAL DA II REGIÃO FISCAL\nEDITAL DE INTIMAÇÃO Nº 005/2026\n", "Edital")
    assert (a.sefaz, a.orgao_do_titulo) == (None, "DIRETORIA GERAL DA II REGIÃO FISCAL")


def test_cabecalho_com_orgao_nao_usa_o_titulo():
    a = _unico("SECRETARIA DE ADMINISTRAÇÃO\nEDITAL DBF Nº 5/2026\nCREDENCIAMENTO\n", "Edital")
    assert (a.orgao, a.orgao_do_titulo, a.sefaz) == ("DBF", None, True)


def test_sem_titulo_por_perto_nao_inventa_orgao():
    assert _unico("texto solto de outra publicação\nEDITAL Nº 43/2026\nCREDENCIAMENTO\n", "Edital").orgao_do_titulo is None
    longe = "SECRETARIA DE ADMINISTRAÇÃO\n" + "linha de texto comum, sem título\n" * 12 + "EDITAL Nº 43/2026\nCREDENCIAMENTO\n"
    assert _unico(longe, "Edital").orgao_do_titulo is None  # mais de 8 linhas acima


def test_titulo_nao_vira_orgao_de_decreto_nem_de_errata():
    for texto, especie in (
        ("SECRETARIA DE ADMINISTRAÇÃO\nDECRETO Nº 60.929, DE 25 DE JUNHO DE 2026.\nA GOVERNADORA\n", "Decreto"),
        ("SECRETARIA DE ADMINISTRAÇÃO\nERRATA\nNa Portaria nº 5\n", "Errata"),
    ):
        assert _unico(texto, especie).orgao_do_titulo is None


def test_titulo_com_assunto_em_maiusculas_nao_engole_o_assunto():
    # "PROGRAMA DE DESENVOLVIMENTO ... PRODEPE" não é órgão: não abre título
    texto = "PROGRAMA DE DESENVOLVIMENTO DE\nPERNAMBUCO - PRODEPE\nEDITAL DE NÃO CONCORRÊNCIA\nA empresa X\n"
    a = _unico(texto, "Edital")
    assert a.orgao_do_titulo is None and a.sefaz is None


# ---------------------------------------------------------------------------
# Portaria
# ---------------------------------------------------------------------------

PORTARIA_SF = (
    "PORTARIA SF Nº 76, DE 24.04.2026\n"
    "O SECRETÁRIO DA FAZENDA, no uso de suas atribuições, RESOLVE:\n"
    "Art. 1º Dispensar o servidor X da função de Gestor de Telemática.\n"
    "Art. 2º Designar o servidor Y para exercer a referida função.\n"
    "Art. 3º Esta Portaria entra em vigor na data de sua publicação.\n"
)


def test_portaria_da_sefaz_pela_sigla_sf():
    a = _unico(PORTARIA_SF, "Portaria")
    assert (a.numero, a.ano, a.orgao, a.sefaz) == ("76", "2026", "SF", True)
    assert a.data_do_ato == "2026-04-24"
    assert a.tipo == "DISPENSA"  # primeiro verbo operativo


def test_portaria_de_outro_orgao_nao_e_sefaz_mesmo_citando_a_fazenda():
    a = _unico("PORTARIA SES/PE N° 609, de 21 de Outubro de 2026\nCONSIDERANDO o Secretário da Fazenda\n", "Portaria")
    assert (a.numero, a.orgao, a.sefaz, a.data_do_ato) == ("609", "SES/PE", False, "2026-10-21")


@pytest.mark.parametrize(
    ("cabecalho", "numero", "ano", "orgao", "data"),
    [
        ("PORTARIA SEE Nº 3669DE 25 DE JUNHO DE 2026", "3669", "2026", "SEE", "2026-06-25"),
        ("PORTARIA FUNASE Nº 396/2026, de 25 de junho de 2026.", "396", "2026", "FUNASE", "2026-06-25"),
        ("PORTARIA Nº 937/2026 de 22.04.2026", "937", "2026", None, "2026-04-22"),
        ("PORTARIA N° 42, DE 25/06/2026. O Diretor Presidente", "42", "2026", None, "2026-06-25"),
        ("PORTARIA Nº. 55 DE22 DE ABRIL DE 2026", "55", "2026", None, "2026-04-22"),
        ("PORTARIA SAD Nº 1.341 DO DIA 24 DE ABRIL DE 2026", "1341", "2026", "SAD", "2026-04-24"),
        ("PORTARIA CONJUNTA SAD/UPE Nº 126 DO DIA 24 DE ABRIL DE 2026", "126", "2026", "CONJUNTA SAD/UPE", "2026-04-24"),
        ("PORTARIA JUCEPE N°53/2026 DE 14 DE JULHO DE 2026.", "53", "2026", "JUCEPE", "2026-07-14"),
        ("PORTARIA SECULT-PENº 017, DE 24 DE ABRIL DE 2026.", "017", "2026", "SECULT-PE", "2026-04-24"),
        ("PORTARIA DO COMANDO-GERAL\nNº 340/DGP3, de 22 de abril de 2026.", "340", "2026", "COMANDO-GERAL", "2026-04-22"),
        ("PORTARIA NCO Nº 212. A SECRETARIA EXECUTIVA", "212", None, "NCO", None),
    ],
)
def test_portaria_formatos_reais_dos_cadernos(cabecalho, numero, ano, orgao, data):
    a = _unico(cabecalho + "\nO SECRETÁRIO, RESOLVE:\n", "Portaria")
    assert (a.numero, a.orgao, a.data_do_ato) == (numero, orgao, data)
    assert a.ano == ano


def test_portaria_administrativa_usa_a_sigla_do_orgao():
    a = _unico("PORTARIA ADMINISTRATIVA nº 56 / 2026 - CBMPE - DGP - DA, DE 19 de junho de 2026.\nEMENTA: DESLIGA\n", "Portaria")
    assert (a.numero, a.ano, a.orgao, a.data_do_ato) == ("56", "2026", "CBMPE", "2026-06-19")


def test_portaria_sem_numero_nao_some_e_avisa():
    a = _unico("PORTARIA SEE/SEGP DE 20 DE OUTUBRO DE 2026. A SECRETARIA EXECUTIVA\n", "Portaria")
    assert (a.numero, a.orgao, a.data_do_ato) == (None, "SEE/SEGP", "2026-10-20")
    assert any("sem número" in aviso for aviso in a.avisos)


def test_portaria_citada_no_corpo_em_minusculas_nao_conta():
    assert identificar("designada pela Portaria nº 2224/2026, de 29 de julho de 2026\n") == []


def test_portaria_em_maiusculas_que_comeca_a_linha_depois_de_frase_cortada_avisa():
    texto = (
        "designada, por meio da\n"
        "PORTARIA Nº 2224/2026, de 29 de julho de 2026, para compor, como Representante\n"
    )
    a = _unico(texto, "Portaria")
    assert any("citação" in aviso for aviso in a.avisos)


def test_portaria_depois_de_assinatura_nao_e_marcada_como_citacao():
    texto = "Secretário da Fazenda\nPORTARIA SF Nº 77, DE 24.04.2026\nO SECRETÁRIO DA FAZENDA\n"
    assert not any("citação" in aviso for aviso in _unico(texto, "Portaria").avisos)


@pytest.mark.parametrize(
    ("corpo", "tipo"),
    [
        ("Art. 1º Designar AMOM para responder pela atividade", "DESIGNACAO"),
        ("Art. 1º Proceder à substituição da função de Ordenador de Despesa", "SUBSTITUICAO"),
        ("Art. 1º A Portaria SF nº 002, de 12.1.2023, passa a vigorar com as seguintes modificações", "ALTERACAO"),
        ("Art. 1º Declarar a perda por renúncia ao incentivo do PRODEPE", "PERDA_INCENTIVO"),
        ("Art. 1º Prorrogar o afastamento da servidora", "PRORROGACAO"),
        ("Art. 1º Aprovar o regimento", None),
    ],
)
def test_tipo_da_portaria_pelo_primeiro_verbo_operativo(corpo, tipo):
    a = _unico(f"PORTARIA SF Nº 1, DE 1.1.2026\nO SECRETÁRIO DA FAZENDA, RESOLVE:\n{corpo}\n", "Portaria")
    assert a.tipo == tipo


def test_verbo_antes_do_resolve_nao_define_o_tipo():
    texto = (
        "PORTARIA SF Nº 1, DE 1.1.2026\n"
        "O SECRETÁRIO, considerando o servidor que passa a vigorar na função, RESOLVE:\n"
        "Art. 1º Designar X.\n"
    )
    assert _unico(texto, "Portaria").tipo == "DESIGNACAO"


# ---------------------------------------------------------------------------
# Errata
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("trecho", "rotulo", "destino", "numero", "ano"),
    [
        ("ERRATA No art. 4º do Decreto nº 61.560, de 19 de outubro de 2026, que redenomina", "Decreto 61.560", "decreto", "61560", "2026"),
        ("ERRATA Na Portaria SDS nº 2508, de 15/04/2026, onde se lê", "Portaria 2508", "portaria", "2508", "2026"),
        ("ERRATA Na PORTARIA FUNASE nº 671, datada de 29/09/2026 publicada", "Portaria 671", "portaria", "671", "2026"),
        ("Errata ao Edital do Chamamento Público nº 010/2026, Processo nº 047", "Edital 010/2026", "edital", "010", "2026"),
        ("ERRATA DO DESPACHO SECOGE DE 18/06//2026, PUBLICADO NO DOE", "Despacho", "outro", None, None),
        ("ERRATA Na publicação referente ao Termo de Fomento nº 014/2026, veiculada", "Termo de fomento 014/2026", "outro", "014", "2026"),
        ("ERRATA Na publicação referente ao CANCELAMENTO DA ARPC.0002.00.2026", "?", "outro", None, None),
    ],
)
def test_errata_ato_referido_e_destino(trecho, rotulo, destino, numero, ano):
    a = _unico(trecho + "\nOnde se lê: X\n", "Errata")
    assert (a.ato_referido, a.destino, a.ref_numero, a.ref_ano) == (rotulo, destino, numero, ano)
    assert a.numero is None  # errata não tem número próprio


def test_errata_com_o_ato_referido_em_outra_linha():
    a = _unico("ERRATA\nNa \nPortaria \nSJDH \nnº 126, publicada em 30 de setembro de 2026\n", "Errata")
    assert (a.destino, a.ref_numero, a.ref_ano) == ("portaria", "126", "2026")


def test_errata_sem_ato_conhecido_avisa():
    a = _unico("ERRATA A PUBLICAÇÃO DO TERMO DE EXECUÇÃO DESCENTRALIZADA\n", "Errata")
    assert a.ato_referido == "?" and any("não identificado" in aviso for aviso in a.avisos)


def test_errata_no_meio_da_linha_nao_conta():
    assert identificar("conforme consta na ERRATA publicada ontem\n") == []


def test_errata_nao_corta_o_ato_anterior_no_trecho_errado():
    texto = PORTARIA_SF + "ERRATA Na Portaria SF nº 76, de 24.04.2026, onde se lê\n"
    portaria = _unico(texto, "Portaria")
    errata = _unico(texto, "Errata")
    assert portaria.fim == errata.posicao


# ---------------------------------------------------------------------------
# Republicação
# ---------------------------------------------------------------------------

ASSINATURAS = (
    "Palácio do Campo das Princesas, Recife, 13 de julho do ano de 2026, 210º da Revolução Republicana\n"
    "Constitucionalista e 204º da \nIndependência do Brasil.\n"
    "RAQUEL TEIXEIRA LYRA LUCENA\nGovernadora do Estado\nPEDRO HENRIQUE NEVES DE HOLANDA\n"
)


def test_republicacao_logo_depois_do_fecho_liga_ao_decreto():
    texto = (
        "DECRETO Nº 61.055, DE 13 DE JULHO DE 2026.\nAbre crédito suplementar\nA GOVERNADORA DO ESTADO\n"
        + ASSINATURAS
        + "(REPUBLICADO POR HAVER SAÍDO COM INCORREÇÃO NO ORIGINAL)\nANEXO I\n"
    )
    achados = identificar(texto)
    assert [(a.especie, a.republicacao) for a in achados] == [("Decreto", "sim")]


def test_republicacao_no_plural_e_no_feminino():
    for marca in (
        "(REPUBLICADOS POR HAVEREM SAÍDO COM INCORREÇÕES NOS ORIGINAIS)",
        "(REPUBLICADAS POR HAVER SAÍDO COM INCORREÇÃO NA ORIGINAL)",
    ):
        texto = "DECRETO Nº 61.055, DE 13 DE JULHO DE 2026.\nA GOVERNADORA\n" + ASSINATURAS + marca + "\n"
        assert _unico(texto, "Decreto").republicacao == "sim", marca


def test_republicacao_de_portaria_depois_das_assinaturas():
    texto = (
        "PORTARIA SF Nº 80, DE 24.04.2026\nO SECRETÁRIO DA FAZENDA, RESOLVE:\n"
        "Art. 2º Esta Portaria entra em vigor na data de sua publicação.\n"
        "Secretário da Fazenda\n(REPUBLICADO POR HAVER SAÍDO COM INCORREÇÃO NO ORIGINAL)\n"
        "PORTARIA SF Nº 81, DE 24.04.2026\nO SECRETÁRIO DA FAZENDA, RESOLVE:\n"
    )
    achados = {a.numero: a.republicacao for a in identificar(texto) if a.especie == "Portaria"}
    assert achados == {"80": "sim", "81": ""}  # o rodapé é da portaria ANTERIOR


def test_republicacao_em_titulo_liga_ao_edital_seguinte():
    texto = (
        "SECRETARIA DE ADMINISTRAÇÃO\n"
        "AVISO DE ABERTURA – 2ª REPUBLICAÇÃO DE EDITAL\n"
        "EDITAL DE CREDENCIAMENTO N° 0014.2026.PNE\nPROCESSO DE INEXIGIBILIDADE\n"
    )
    edital = _unico(texto, "Edital")
    assert edital.republicacao == "sim"
    assert [a.especie for a in identificar(texto)] == ["Edital"]  # nada órfão


def test_republicacao_em_lista_de_atos_de_outra_especie_fica_orfa():
    texto = (
        "ATO DO DIA 16 DE OUTUBRO DE 2026.\n"
        "Nº 5538 - Nomear X para exercer o cargo em comissão.\n"
        "(REPUBLICADO POR HAVER SAÍDO COM INCORREÇÃO NO ORIGINAL).\n"
    )
    achados = identificar(texto)
    assert [a.especie for a in achados] == ["Republicação"]
    assert any("sem decreto, edital ou portaria" in aviso for aviso in achados[0].avisos)


def test_republicacao_nao_se_liga_a_portaria_de_antes_de_instrucao_normativa():
    # O rodapé é da Instrução Normativa (outra espécie), não da portaria que a antecede.
    texto = (
        "PORTARIA SEE Nº 3680 DE 25 DE JUNHO DE 2026.\nO SECRETÁRIO, RESOLVE:\nArt. 1º Designar X.\n"
        "INSTRUÇÃO NORMATIVA SEE/PE Nº 3/2026\nFixa normas.\n"
        "Secretário de Educação\n(REPUBLICADO POR HAVER SAÍDO COM INCORREÇÃO NO ORIGINAL)\n"
    )
    assert _unico(texto, "Portaria").republicacao == ""
    assert [a.especie for a in identificar(texto)] == ["Portaria", "Republicação"]


@pytest.mark.parametrize(
    "citacao",
    [
        "considerando o Decreto nº 56.378, republicado no DOE de 06/06/2024, e o Decreto nº 58.716",
        "210º da Revolução Republicana Constitucionalista e 204º da Independência do Brasil.",
        "RETIFICAÇÃO Nº 01 AO EDITAL DE CHAMAMENTO PÚBLICO",
    ],
)
def test_citacao_nao_e_republicacao(citacao):
    assert ra.REPUBLICACAO_MARCADOR.search(citacao) is None


def test_republicacao_provavel_quando_nao_esta_logo_apos_o_fecho():
    texto = (
        "EDITAL DE CREDENCIAMENTO N° 0014.2026.PNE\nPROCESSO\n"
        "Comunico a republicação do Edital em razão da necessidade de ajuste.\nRecife, 1 de junho de 2026.\n"
    )
    assert _unico(texto, "Edital").republicacao == "provavel"


# ---------------------------------------------------------------------------
# Datas, páginas e limpeza
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "iso"),
    [
        ("DE 24.04.2026", "2026-04-24"),
        ("de 13.4.2016", "2016-04-13"),
        ("DE 25/06/2026", "2026-06-25"),
        ("DE 25 DE JUNHO DE 2026", "2026-06-25"),
        ("de 1º de julho de 2026", "2026-07-01"),
        ("DE22 DE ABRIL DE 2026", "2026-04-22"),
        ("DO DIA 24 DE ABRIL DE 2026", "2026-04-24"),
        ("Nº 4050.2025.0021.SES", None),
        ("DE 31 DE FEVEREIRO DE 2026", None),
        ("sem data", None),
    ],
)
def test_data_no_texto(texto, iso):
    assert ra.data_no_texto(texto) == iso


def test_pagina_vem_das_posicoes_de_inicio_de_pagina():
    pagina1 = "texto da primeira página\n"
    texto = pagina1 + PORTARIA_SF
    a = identificar(texto, [0, len(pagina1)])[0]
    assert a.pagina == 2
    assert identificar(texto)[0].pagina == 1


def test_ligaduras_com_espaco_espurio_sao_convertidas():
    assert ra.limpar_texto("beneﬁ ciário e ﬁ scal") == "beneficiário e fiscal"


def test_limpar_texto_e_idempotente_e_preserva_posicoes():
    texto = PORTARIA_SF
    assert ra.limpar_texto(texto) == texto
    assert len(ra.limpar_texto("a b")) == 3


def test_decreto_com_ligadura_no_cabecalho_ainda_e_reconhecido():
    a = _unico("DECRETO Nº 60.951, DE 25 DE JUNHO DE 2026.\nIntroduz alterações\nA GOVERNADORA\n", "Decreto")
    assert a.tipo == "ALTERACAO"
    a2 = _unico("DECRETO Nº 60.951, DE 25 DE JUNHO DE 2026.\nModiﬁ ca o Decreto nº 44.650\nA GOVERNADORA\n", "Decreto")
    assert a2.tipo == "ALTERACAO_NORMA"


def test_texto_vazio_ou_sem_atos_devolve_lista_vazia():
    assert identificar("") == []
    assert identificar("apenas texto corrido, sem cabeçalho de ato\n") == []


# ---------------------------------------------------------------------------
# Título vale para os atos seguintes do mesmo bloco (decisão de 04/11/2026)
# ---------------------------------------------------------------------------

_CORPO = "linha do corpo da portaria, sem título\n" * 10  # mais de 8 linhas: o título fica longe
UPE_BLOCO = (
    "UNIVERSIDADE DE PERNAMBUCO - UPE / REITORIA\n"
    "PORTARIA Nº 1578/2026 de 17.06.2026\nI - Designar X.\n" + _CORPO
    + "PORTARIA Nº 1579/2026 de 17.06.2026\nI - Dispensar Y.\n" + _CORPO
    + "PORTARIA Nº 1580/2026 de 17.06.2026\nI - Designar Z.\n" + _CORPO
)


def test_portarias_seguintes_do_bloco_herdam_o_titulo():
    achados = [a for a in identificar(UPE_BLOCO) if a.especie == "Portaria"]
    assert [a.orgao_do_titulo for a in achados] == ["UNIVERSIDADE DE PERNAMBUCO - UPE / REITORIA"] * 3
    assert all(a.sefaz is False and a.orgao is None for a in achados)
    assert any("herdado do título do ato anterior" in aviso for aviso in achados[2].avisos)


def test_heranca_atravessa_citacao_curta_entre_dois_atos():
    texto = UPE_BLOCO + "RESOLUÇÃO CONSUN Nº 020/2026, de 30/04/2026.\nPORTARIA Nº 1631/2026 de 25.06.2026\nI - Exonerar.\n"
    ultima = [a for a in identificar(texto) if a.especie == "Portaria"][-1]
    assert ultima.orgao_do_titulo == "UNIVERSIDADE DE PERNAMBUCO - UPE / REITORIA"


def test_titulo_no_fim_do_trecho_do_ato_anterior_vale_mesmo_longe():
    texto = (
        "PORTARIA FUNASE Nº 415/2026, de 25 de junho de 2026.\nO Diretor RESOLVE: Designar.\n"
        "UNIVERSIDADE DE PERNAMBUCO - UPE / REITORIA\n" + "linha comum do texto\n" * 20
        + "PORTARIA Nº 1579/2026 de 17.06.2026\nI - Dispensar.\n"
    )
    upe = [a for a in identificar(texto) if a.numero == "1579"][0]
    assert upe.orgao_do_titulo == "UNIVERSIDADE DE PERNAMBUCO - UPE / REITORIA"
    assert any("fim do trecho do ato anterior" in aviso for aviso in upe.avisos)


def test_sem_titulo_em_lugar_nenhum_nao_herda_orgao_de_cabecalho():
    texto = "PORTARIA FUNASE Nº 415/2026\nO Diretor RESOLVE: Designar.\nPORTARIA Nº 1579/2026 de 17.06.2026\nI - Dispensar.\n"
    upe = [a for a in identificar(texto) if a.numero == "1579"][0]
    assert (upe.orgao, upe.orgao_do_titulo, upe.sefaz) == (None, None, None)


def test_heranca_nao_atravessa_sobra_longa():
    # Um AVISO (outro marcador) com texto longo entre os dois atos: não é mais o mesmo bloco.
    texto = UPE_BLOCO + "AVISO DE LICITAÇÃO\n" + "texto de outra publicação. " * 20 + "\nPORTARIA Nº 1631/2026 de 25.06.2026\nI - Exonerar.\n"
    ultima = [a for a in identificar(texto) if a.especie == "Portaria"][-1]
    assert ultima.orgao_do_titulo is None


# ---------------------------------------------------------------------------
# Uma fonte só: a ferramenta de levantamento usa as mesmas RegEx
# (vindo do antigo tests/test_atos_acessorios.py)
# ---------------------------------------------------------------------------


def test_localizar_exemplos_usa_as_mesmas_regex_de_regras_atos():
    import localizar_exemplos as lx

    assert lx._ERRATA is ra.ERRATA_LINHA
    assert lx._REPUBLICACAO is ra.REPUBLICACAO_MARCADOR
    assert lx._RETIFICACAO is ra.RETIFICACAO_LINHA
    assert lx.ato_referido("ERRATA Na Portaria SDS nº 2508, de 15/04/2026") == "Portaria 2508"
    assert lx.ato_referido("ERRATA A PUBLICAÇÃO DO TERMO") == "?"


def test_ato_referido_da_errata_traz_numero_ano_e_destino():
    ref = ra.ato_referido_da_errata("ERRATA No art. 4º do Decreto nº 61.560, de 19 de outubro de 2026")
    assert (ref.especie, ref.numero, ref.ano, ref.destino, ref.rotulo) == ("Decreto", "61560", "2026", "decreto", "Decreto 61.560")
