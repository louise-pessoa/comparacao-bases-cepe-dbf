"""Ferramenta auxiliar que procura erratas, republicações e decretos repetidos."""

from __future__ import annotations

import pymupdf
import pytest

import localizar_exemplos as lx
from conftest import criar_pdf_texto, texto_decreto

FECHO = "Palácio do Campo das Princesas, Recife, 25 de junho do ano de 2026, 210º da Revolução Republicana Constitucionalista."


def _tipos(achados):
    return sorted(a.tipo for a in achados)


def test_acha_errata_pelo_inicio_da_linha(tmp_path):
    criar_pdf_texto(tmp_path / "c.pdf", "texto qualquer", "ERRATA\nNa publicação do Decreto nº 60.929, onde se lê A leia-se B.")
    achados, avisos = lx.procurar(tmp_path)
    assert [(a.pagina, a.tipo) for a in achados] == [(2, lx.ERRATA)]
    assert "Decreto nº 60.929" in achados[0].trecho
    assert avisos == []


def test_errata_no_meio_da_linha_nao_conta(tmp_path):
    criar_pdf_texto(tmp_path / "c.pdf", "o texto cita uma errata publicada ontem")
    assert lx.procurar(tmp_path)[0] == []


@pytest.mark.parametrize(
    "marca",
    [
        # Fórmulas reais do Diário (cadernos de 26/06 e de 21 e 22/10/2026)
        "(REPUBLICADO POR HAVER SAÍDO COM INCORREÇÃO NO ORIGINAL)",
        "(REPUBLICADOS POR HAVEREM SAÍDO COM INCORREÇÕES NOS ORIGINAIS)",
        "(REPUBLICADAS POR HAVER SAÍDO COM INCORREÇÃO NA ORIGINAL)",
        "Republicada por haver inconsistência na publicação do DOE de 12/09/2026",
        "AVISO DE ABERTURA – 2ª REPUBLICAÇÃO DE EDITAL",
        "AVISO DE ABERTURA – 1ª REPUBLICAÇÃO DE EDITAL",
        "Aviso de republicação",
        "Comunico a republicação do Edital em razão da necessidade de ajuste",
        "(Republicado por incorreção)",
        "AVISO DE ABERTURA – 3ª REPUBLICAÇÃO DO PREGÃO Nº 90590.2025",  # só o ordinal a pega
    ],
)
def test_acha_marca_de_republicacao(tmp_path, marca):
    criar_pdf_texto(tmp_path / "c.pdf", f"DECRETO Nº 60.951\nTexto.\n{marca}")
    achados, _ = lx.procurar(tmp_path)
    assert _tipos(achados) == [lx.REPUBLICACAO]


@pytest.mark.parametrize(
    "texto",
    [
        "considerando o Decreto nº 56.378, republicado no DOE de 06/06/2024, e o Decreto nº 58.716",
        "RETIFICAÇÃO Nº 01 AO EDITAL DE CHAMAMENTO PÚBLICO",
        "EXTRATO DE TERMO DE RETIFICAÇÃO- PL.4386.2025",
    ],
)
def test_citacao_de_decreto_republicado_nao_e_republicacao(tmp_path, texto):
    criar_pdf_texto(tmp_path / "c.pdf", f"Texto.\n{texto}")
    assert lx.REPUBLICACAO not in _tipos(lx.procurar(tmp_path)[0])


def test_retificacao_no_meio_da_linha_nao_conta(tmp_path):
    criar_pdf_texto(tmp_path / "c.pdf", "EXTRATO DE TERMO DE RETIFICAÇÃO- PL.4386.2025")
    assert lx.procurar(tmp_path)[0] == []


def test_retificacao_no_inicio_da_linha_e_separada_da_errata(tmp_path):
    criar_pdf_texto(tmp_path / "c.pdf", "RETIFICAÇÃO Nº 01 AO EDITAL DE CHAMAMENTO\nTexto.", "AVISO DE RETIFICAÇÃO: Processo 013/2026")
    achados, _ = lx.procurar(tmp_path)
    assert [(a.pagina, a.tipo) for a in achados] == [(1, lx.RETIFICACAO), (2, lx.RETIFICACAO)]


@pytest.mark.parametrize(
    ("trecho", "esperado"),
    [
        # Erratas reais de 26/06 e de 21 e 22/10/2026
        ("ERRATA No art. 4º do Decreto nº 61.560, de 19 de outubro de 2026, que redenomina o cargo", "Decreto 61.560"),
        ("ERRATA Na Portaria SDS nº 2508, de 15/04/2026, onde se lê", "Portaria 2508"),
        ("ERRATA Na PORTARIA FUNASE nº 671, datada de 29/09/2026", "Portaria 671"),
        ("ERRATA Na Portaria SJDH nº 126, publicada no Diário Oficial", "Portaria 126"),
        ("Errata ao Edital do Chamamento Público nº 010/2026, Processo nº 047/CPL/2026", "Edital 010/2026"),
        ("ERRATA DO DESPACHO SECOGE DE 18/06//2026, PUBLICADO NO DOE DE 19/06/2026", "Despacho"),
        ("ERRATA: No despacho do dia 19.06.2026, publicado no DOE de 20.06.2026", "Despacho"),
        ("ERRATA Na publicação referente ao Termo de Fomento nº 014/2026, veiculada", "Termo de fomento 014/2026"),
        ("ERRATA Na publicação referente ao CANCELAMENTO DA ARPC.0002.00.2026.GOV.SAD.PE", "?"),
    ],
)
def test_ato_referido_pela_errata(trecho, esperado):
    assert lx.ato_referido(trecho) == esperado


def test_relatorio_conta_as_erratas_por_ato_referido(tmp_path):
    criar_pdf_texto(
        tmp_path / "c.pdf",
        "ERRATA No art. 4º do Decreto nº 61.560, de 19 de outubro de 2026.",
        "ERRATA Na Portaria SDS nº 2508, de 15/04/2026.",
        "ERRATA Na Portaria SJDH nº 126.",
        "ERRATA do despacho de 18/06.",
    )
    achados, avisos = lx.procurar(tmp_path)
    relatorio = lx.montar_relatorio(achados, avisos)
    assert "Erratas por ato referido: Portaria 2, Decreto 1, Despacho 1" in relatorio
    assert "| refere-se a: Decreto 61.560" in relatorio


def test_revolucao_republicana_do_fecho_nao_e_republicacao(tmp_path):
    criar_pdf_texto(tmp_path / "c.pdf", FECHO)
    assert lx.procurar(tmp_path)[0] == []


def test_decreto_repetido_entre_cadernos(tmp_path):
    criar_pdf_texto(tmp_path / "20260626_a.pdf", texto_decreto("60.951", "Altera."))
    criar_pdf_texto(tmp_path / "20260630_b.pdf", texto_decreto("60.951", "Altera."))
    criar_pdf_texto(tmp_path / "20260630_c.pdf", texto_decreto("60.952", "Outro."))
    achados, _ = lx.procurar(tmp_path)
    assert [(a.caderno.name, a.tipo) for a in achados] == [
        ("20260626_a.pdf", "decreto-repetido-60951"),
        ("20260630_b.pdf", "decreto-repetido-60951"),
    ]


def test_decreto_repetido_no_mesmo_caderno(tmp_path):
    criar_pdf_texto(tmp_path / "c.pdf", texto_decreto("60.951", "Altera."), texto_decreto("60.951", "Altera."))
    assert _tipos(lx.procurar(tmp_path)[0]) == ["decreto-repetido-60951"] * 2


def test_decreto_que_atravessa_paginas_nao_e_repetido(tmp_path):
    criar_pdf_texto(tmp_path / "c.pdf", texto_decreto("60.951", "Altera."), "continuação do texto sem cabeçalho")
    assert lx.procurar(tmp_path)[0] == []


def test_citacao_de_decreto_em_minusculas_nao_conta(tmp_path):
    criar_pdf_texto(tmp_path / "c.pdf", "Decreto nº 60.951, de 2026\nDecreto nº 60.951, de 2026")
    assert lx.procurar(tmp_path)[0] == []


def test_arquivo_corrompido_vira_aviso_e_a_busca_continua(tmp_path):
    (tmp_path / "ruim.pdf").write_bytes(b"nao e um pdf")
    criar_pdf_texto(tmp_path / "c.pdf", "ERRATA\nTexto.")
    achados, avisos = lx.procurar(tmp_path)
    assert _tipos(achados) == [lx.ERRATA]
    assert len(avisos) == 1 and "ruim.pdf" in avisos[0]


def test_pasta_sem_pdfs_avisa(tmp_path):
    achados, avisos = lx.procurar(tmp_path)
    assert achados == [] and "nenhum PDF" in avisos[0]


def test_copia_uma_pagina_por_pdf_sem_sobrescrever(tmp_path):
    cadernos = tmp_path / "cadernos"
    cadernos.mkdir()
    criar_pdf_texto(cadernos / "c.pdf", "primeira", "ERRATA\nsegunda página")
    achados, _ = lx.procurar(cadernos)
    saida = tmp_path / "exemplos"
    copiadas = lx.copiar_paginas(achados, saida)
    assert [p.name for p in copiadas] == ["c_p002_errata.pdf"]
    with pymupdf.open(copiadas[0]) as doc:
        assert doc.page_count == 1 and "segunda página" in doc[0].get_text()
    copiadas[0].write_bytes(b"editado pela pessoa")
    assert lx.copiar_paginas(achados, saida) == []
    assert copiadas[0].read_bytes() == b"editado pela pessoa"


def test_mesma_pagina_com_duas_marcas_gera_um_pdf_por_tipo(tmp_path):
    cadernos = tmp_path / "cadernos"
    cadernos.mkdir()
    criar_pdf_texto(cadernos / "c.pdf", "ERRATA\nRepublicado por incorreção. Republicado.")
    achados, _ = lx.procurar(cadernos)
    copiadas = lx.copiar_paginas(achados, tmp_path / "exemplos")
    assert sorted(p.name for p in copiadas) == ["c_p001_errata.pdf", "c_p001_republicacao.pdf"]


def test_main_grava_relatorio_e_paginas(tmp_path, capsys):
    cadernos = tmp_path / "cadernos"
    cadernos.mkdir()
    criar_pdf_texto(cadernos / "c.pdf", "ERRATA\nTexto da errata.")
    saida = tmp_path / "exemplos"
    assert lx.main([str(cadernos), "--saida", str(saida)]) == 0
    assert "Texto da errata" in (saida / "relatorio.txt").read_text(encoding="utf-8")
    assert (saida / "c_p001_errata.pdf").exists()
    assert "1 página(s) copiada(s)" in capsys.readouterr().out


def test_main_sem_paginas_so_grava_o_relatorio(tmp_path):
    cadernos = tmp_path / "cadernos"
    cadernos.mkdir()
    criar_pdf_texto(cadernos / "c.pdf", "ERRATA\nTexto.")
    saida = tmp_path / "exemplos"
    assert lx.main([str(cadernos), "--saida", str(saida), "--sem-paginas"]) == 0
    assert [p.name for p in saida.iterdir()] == ["relatorio.txt"]


def test_main_pasta_inexistente(tmp_path, capsys):
    assert lx.main([str(tmp_path / "nao_existe")]) == 2
    assert "pasta não encontrada" in capsys.readouterr().err


def test_mesmo_cabecalho_duas_vezes_na_mesma_pagina_nao_conta_como_repetido(tmp_path):
    # O critério é "em mais de uma página"; duas linhas na mesma página são ruído da extração.
    criar_pdf_texto(tmp_path / "c.pdf", "DECRETO Nº 60.951\nDECRETO Nº 60.951\nTexto.")
    assert lx.procurar(tmp_path)[0] == []


# --- Levantamento do formato dos cabeçalhos (edital, portaria) ---------------------------


def test_acha_cabecalho_de_edital_e_portaria_no_inicio_da_linha(tmp_path):
    criar_pdf_texto(
        tmp_path / "c.pdf",
        "EDITAL DBF Nº 12/2026\nDispõe sobre o credenciamento.",
        "PORTARIA SEFAZ Nº 45, DE 3 DE JULHO DE 2026\nDispõe sobre prazos.",
    )
    achados, _ = lx.procurar(tmp_path)
    assert [(a.pagina, a.tipo, a.referente) for a in achados] == [
        (1, "cabecalho-edital", "EDITAL DBF Nº N/N"),
        (2, "cabecalho-portaria", "PORTARIA SEFAZ Nº N, DE N DE JULHO DE N"),
    ]


def test_formato_troca_os_numeros_por_n():
    assert lx.formato_do_cabecalho("EDITAL DBF Nº 12/2026") == "EDITAL DBF Nº N/N"
    assert lx.formato_do_cabecalho("PORTARIA Nº 1.234") == "PORTARIA Nº N.N"
    # a ementa da linha seguinte não entra no formato
    assert lx.formato_do_cabecalho("EDITAL DBF Nº 12/2026\nDispõe sobre X") == "EDITAL DBF Nº N/N"


@pytest.mark.parametrize(
    "texto",
    [
        "conforme a Portaria nº 45/2026 da Secretaria",  # citação em minúsculas/meio de frase
        "nos termos do EDITAL DBF Nº 12/2026, de chamamento",  # maiúsculas, mas no meio da linha
        "RETIFICAÇÃO AO EDITAL DBF Nº 12/2026",  # a linha não começa com EDITAL
    ],
)
def test_cabecalho_fora_do_inicio_da_linha_nao_conta(tmp_path, texto):
    criar_pdf_texto(tmp_path / "c.pdf", texto)
    assert [a for a in lx.procurar(tmp_path)[0] if a.tipo.startswith("cabecalho-")] == []


def test_especies_do_cabecalho_sao_parametrizaveis(tmp_path):
    criar_pdf_texto(tmp_path / "c.pdf", "EDITAL DBF Nº 12/2026\nPORTARIA SEFAZ Nº 45\nRESOLUÇÃO Nº 7/2026")
    assert _tipos(lx.procurar(tmp_path, ("RESOLUÇÃO",))[0]) == ["cabecalho-resolução"]
    assert lx.procurar(tmp_path, ())[0] == []


def test_relatorio_agrupa_cabecalhos_por_formato(tmp_path):
    criar_pdf_texto(
        tmp_path / "c.pdf",
        "EDITAL DBF Nº 12/2026\nPORTARIA SEFAZ Nº 45\nEDITAL DBF Nº 13/2026",
    )
    achados, avisos = lx.procurar(tmp_path)
    relatorio = lx.montar_relatorio(achados, avisos)
    assert "Formatos de cabeçalho encontrados" in relatorio
    assert "[cabecalho-edital] 2x  EDITAL DBF Nº N/N" in relatorio
    assert "[cabecalho-portaria] 1x  PORTARIA SEFAZ Nº N" in relatorio


def test_so_as_primeiras_paginas_de_cada_formato_sao_copiadas(tmp_path):
    paginas = [f"EDITAL DBF Nº {n}/2026\nTexto." for n in range(1, 6)] + ["PORTARIA SEFAZ Nº 9\nTexto."]
    criar_pdf_texto(tmp_path / "c.pdf", *paginas)
    achados, _ = lx.procurar(tmp_path)
    escolhidos = lx.selecionar_para_copia(achados, por_formato=2)
    assert [a.pagina for a in escolhidos if a.tipo == "cabecalho-edital"] == [1, 2]
    assert [a.pagina for a in escolhidos if a.tipo == "cabecalho-portaria"] == [6]


def test_erratas_nao_sofrem_o_limite_de_copia(tmp_path):
    criar_pdf_texto(tmp_path / "c.pdf", *[f"ERRATA\nNo Decreto nº {n}" for n in range(1, 6)])
    achados, _ = lx.procurar(tmp_path)
    assert len(lx.selecionar_para_copia(achados, por_formato=1)) == 5


def test_main_copia_poucas_paginas_de_cabecalho(tmp_path):
    entrada, saida = tmp_path / "cad", tmp_path / "ex"
    entrada.mkdir()
    criar_pdf_texto(entrada / "c.pdf", *[f"EDITAL DBF Nº {n}/2026\nTexto." for n in range(1, 6)])
    assert lx.main([str(entrada), "--saida", str(saida), "--por-formato", "2"]) == 0
    assert len(list(saida.glob("*_cabecalho-edital.pdf"))) == 2
    assert "Formatos de cabeçalho" in (saida / "relatorio.txt").read_text(encoding="utf-8")


def test_main_cabecalhos_vazio_desliga_o_levantamento(tmp_path):
    entrada, saida = tmp_path / "cad", tmp_path / "ex"
    entrada.mkdir()
    criar_pdf_texto(entrada / "c.pdf", "EDITAL DBF Nº 12/2026")
    assert lx.main([str(entrada), "--saida", str(saida), "--cabecalhos", ""]) == 0
    assert list(saida.glob("*.pdf")) == []