"""Etapa 1b (separação do caderno): um bloco de texto por decreto."""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf
import pytest

from conftest import AMOSTRAS, criar_pdf_imagem, criar_pdf_texto
from especies import DECRETO, Especie
from identificador import StatusIdentificacao, identificar, normalizar_espacos
from separador import (
    ErroSeparacao,
    limpar_ligaduras,
    separar_caderno,
    separar_texto,
)

# Trecho (páginas 24 a 26) do caderno do Poder Executivo de 26/06/2026, do
# Diário Oficial. Contém o decreto 60.929 (PRODEPE) no meio de outros atos.
CADERNO = Path(__file__).parent / "amostras_cadernos" / "Caderno 2026-06-26 (paginas 24 a 26).pdf"

FECHO = (
    "Art. 4º Este Decreto entra em vigor na data de sua publicação.\n"
    "Palácio do Campo das Princesas, Recife, 25 de junho do ano de 2026, 210º da Revolução "
    "Republicana Constitucionalista e 204º da\nIndependência do Brasil.\n"
    "RAQUEL TEIXEIRA LYRA LUCENA\nGovernadora do Estado\nFLÁVIO MARTINS SODRÉ DA MOTA\n"
)


def decreto(numero: str, corpo: str = "Concede estímulo à empresa X.", fecho: str = FECHO) -> str:
    return (
        f"DECRETO Nº {numero}, DE 25 DE JUNHO DE 2026.\n{corpo}\n"
        "A GOVERNADORA DO ESTADO, no uso das atribuições,\nDECRETA:\n"
        f"Art. 1º Fica concedido o estímulo.\n{fecho}"
    )


# ----------------------------------------------------------------------------
# Caderno real
# ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def resultado():
    return separar_caderno(CADERNO)


def test_caderno_real_separa_um_bloco_por_decreto(resultado):
    numeros = [ato.numero for ato in resultado.atos]
    assert numeros == [str(n) for n in range(60928, 60938)]
    assert resultado.paginas == 3
    assert resultado.paginas_digitalizadas == ()
    assert resultado.avisos == ()
    assert all(ato.avisos == () for ato in resultado.atos)


def test_decreto_60929_do_caderno_e_identico_ao_pdf_avulso_da_dbf(resultado):
    # Prova de que a separação por texto reproduz o ato exato: o texto extraído
    # do meio do caderno é o mesmo da amostra "Decreto 60929.pdf".
    ato = next(a for a in resultado.atos if a.numero == "60929")
    with pymupdf.open(AMOSTRAS / "Decreto 60929.pdf") as doc:
        avulso = "".join(pagina.get_text() for pagina in doc)
    assert normalizar_espacos(ato.texto) == normalizar_espacos(avulso)


def test_cada_bloco_comeca_no_cabecalho_e_traz_so_o_seu_decreto(resultado):
    for ato in resultado.atos:
        assert ato.texto.startswith(f"DECRETO Nº {ato.numero[:2]}.{ato.numero[2:]},")
        ident = identificar(ato.texto)
        assert ident.status is StatusIdentificacao.IDENTIFICADO  # um só ato no bloco
        assert ident.numero == ato.numero


def test_blocos_nao_levam_cabecalho_de_pagina_nem_ligaduras(resultado):
    for ato in resultado.atos:
        assert "Diário Oficial do Estado de Pernambuco" not in ato.texto
        assert not re.search(r"^Ano CX", ato.texto, re.M)
        assert not re.search(r"^Recife, \d+ de \w+ de \d{4}\s*$", ato.texto, re.M)
        assert not re.search("[ﬀ-ﬄ]", ato.texto)
    texto_60929 = next(a for a in resultado.atos if a.numero == "60929").texto
    assert "beneficiário" in texto_60929 and "ficam condicionados" in texto_60929


def test_decreto_termina_nas_assinaturas_sem_anexo_e_o_vizinho_nao_vaza(resultado):
    ato = next(a for a in resultado.atos if a.numero == "60929")
    assert ato.texto.rstrip().endswith("BIANCA FERREIRA TEIXEIRA")
    assert "60.930" not in ato.texto


def test_anexo_faz_parte_do_decreto(resultado):
    credito = next(a for a in resultado.atos if a.numero == "60932")
    assert "ANEXO" in credito.texto and "TOTAL" in credito.texto
    assert credito.texto.index("Independência do Brasil.") < credito.texto.index("ANEXO")


def test_so_o_60929_e_concessao_entre_os_decretos_do_trecho(resultado):
    tipos = {a.numero: identificar(a.texto).tipo for a in resultado.atos}
    assert tipos["60928"].nome == tipos["60929"].nome == "CONCESSAO"
    assert all(tipos[str(n)] is None for n in range(60930, 60938))  # utilidade pública e crédito


def test_paginas_inicial_e_final(resultado):
    por_numero = {a.numero: (a.pagina_inicial, a.pagina_final) for a in resultado.atos}
    assert por_numero["60929"] == (1, 1)
    assert por_numero["60930"] == (1, 2)  # começa no fim de uma página e segue na outra


# ----------------------------------------------------------------------------
# Regras, com texto sintético
# ----------------------------------------------------------------------------


def numeros(texto: str, **kw) -> list[str]:
    atos, _ = separar_texto(texto, **kw)
    return [a.numero for a in atos]


def test_citacao_em_minusculas_nao_e_cabecalho():
    texto = decreto("60.001", corpo="Altera o Decreto nº 21.959, de 27 de dezembro de 1999.")
    atos, avisos = separar_texto(texto)
    assert [a.numero for a in atos] == ["60001"]
    assert avisos == []


def test_cabecalho_com_espaco_antes_da_linha_e_aceito():
    # No caderno real: "1.000.000,00\n DECRETO Nº 60.937 ..."
    texto = "TOTAL\n1.000.000,00\n " + decreto("60.937")
    atos, avisos = separar_texto(texto)
    assert [a.numero for a in atos] == ["60937"] and avisos == []


def test_texto_antes_do_primeiro_decreto_e_ignorado():
    assert numeros("fim de outro ato...\nPORTARIA X\n" + decreto("60.002")) == ["60002"]


def test_cabecalho_no_meio_da_linha_vira_aviso_e_nao_e_ato():
    texto = "fim do ato anterior DECRETO Nº 60.003, DE 25 DE JUNHO DE 2026.\n" + decreto("60.004")
    atos, avisos = separar_texto(texto)
    assert [a.numero for a in atos] == ["60004"]
    assert any("meio da linha" in a and "60.003" in a for a in avisos)


def test_depois_das_assinaturas_o_que_nao_e_anexo_fica_de_fora():
    texto = decreto("60.005") + "SECRETARIA DE ADMINISTRAÇÃO\nTexto de outro assunto.\n"
    (ato,), _ = separar_texto(texto)
    assert ato.texto.rstrip().endswith("FLÁVIO MARTINS SODRÉ DA MOTA")
    assert "SECRETARIA" not in ato.texto


def test_anexo_vai_ate_o_proximo_ato():
    texto = (
        decreto("60.006")
        + "ANEXO ÚNICO\nCRÉDITO SUPLEMENTAR\nTOTAL 1.000,00\n"
        + "ATOS DO DIA 25 DE JUNHO DE 2026.\nNº 1 - Nomear Fulano.\n"
    )
    (ato,), _ = separar_texto(texto)
    assert ato.texto.endswith("TOTAL 1.000,00")
    assert "ATOS DO DIA" not in ato.texto


@pytest.mark.parametrize("outro", ["PORTARIA SF Nº 115, DE 25.06.2026.", "LEI Nº 19.256, DE 25 DE JUNHO DE 2026.", "EDITAL DPC Nº 062/2026"])
def test_outras_especies_encerram_o_decreto_com_anexo(outro):
    texto = decreto("60.007") + f"ANEXO I\nTabela\n{outro}\nTexto.\n"
    (ato,), _ = separar_texto(texto)
    assert outro not in ato.texto and ato.texto.endswith("Tabela")


def test_sem_fecho_o_fim_e_estimado_e_ha_aviso():
    texto = decreto("60.008", fecho="") + decreto("60.009")
    atos, _ = separar_texto(texto)
    assert [a.numero for a in atos] == ["60008", "60009"]
    assert any("fecho" in aviso for aviso in atos[0].avisos)
    assert "60.009" not in atos[0].texto
    assert atos[1].avisos == ()


def test_cabecalho_sem_governadora_tem_aviso():
    texto = "DECRETO Nº 60.010, DE 25 DE JUNHO DE 2026.\nTexto qualquer sem o preâmbulo.\n" + FECHO
    (ato,), _ = separar_texto(texto)
    assert any("GOVERNADORA" in aviso for aviso in ato.avisos)


def test_mesmo_numero_duas_vezes_gera_aviso_e_mantem_os_dois():
    texto = decreto("60.011") + decreto("60.011", corpo="Republicado por incorreção.")
    atos, avisos = separar_texto(texto)
    assert [a.numero for a in atos] == ["60011", "60011"]
    assert any("2 vezes" in a for a in avisos)
    assert "Republicado" in atos[1].texto and "Republicado" not in atos[0].texto


def test_numero_sem_separador_de_milhar():
    assert numeros(decreto("39.773")) == ["39773"]
    assert numeros(decreto("5.039")) == ["5039"]


def test_texto_vazio_ou_sem_decretos():
    assert separar_texto("") == ([], [])
    assert separar_texto("PORTARIA X\nNada de decreto aqui.\n") == ([], [])


def test_especie_e_parametrizavel_sem_mexer_no_separador():
    # Como uma futura espécie (ex.: edital) entraria: só uma nova `Especie`.
    outra = Especie(
        nome="Edital",
        cabecalho=re.compile(r"EDITAL\s+N[º°]\s*(?P<numero>\d+)/(?P<ano>\d{4})"),
        padrao_arquivo="{especie} {numero}.pdf",
    )
    texto = "EDITAL Nº 012/2026\nA GOVERNADORA DO ESTADO convoca.\n" + decreto("60.012")
    atos, _ = separar_texto(texto, especies=(outra,))
    assert [(a.especie.nome, a.numero) for a in atos] == [("Edital", "012")]
    atos, _ = separar_texto(texto, especies=(DECRETO,))
    assert [(a.especie.nome, a.numero) for a in atos] == [("Decreto", "60012")]


def test_limpar_ligaduras():
    assert limpar_ligaduras("beneﬁ ciário ﬁ cam ﬂ uxo e ﬃ ") == "beneficiário ficam fluxo e ffi"
    assert limpar_ligaduras("oﬁcial") == "oficial"  # ligadura sem espaço
    assert limpar_ligaduras("fi  texto") == "fi  texto"  # sem ligadura: não mexe
    # Ligadura + espaço verdadeiro: sobra o espaço real.
    assert limpar_ligaduras("ﬁ  de") == "fi de"


# ----------------------------------------------------------------------------
# Arquivos problemáticos
# ----------------------------------------------------------------------------


def test_arquivo_inexistente_ou_nao_pdf(tmp_path):
    with pytest.raises(ErroSeparacao):
        separar_caderno(tmp_path / "nao_existe.pdf")
    falso = tmp_path / "x.pdf"
    falso.write_bytes(b"isto nao e pdf")
    with pytest.raises(ErroSeparacao):
        separar_caderno(falso)


def test_caderno_digitalizado_nao_gera_atos_e_avisa(tmp_path):
    pdf = criar_pdf_imagem(tmp_path / "scan.pdf")
    resultado = separar_caderno(pdf)
    assert resultado.atos == ()
    assert resultado.paginas_digitalizadas == (1,)
    assert any("digitalizadas" in aviso for aviso in resultado.avisos)


def test_caderno_parcialmente_digitalizado_avisa_mas_separa_o_que_tem_texto(tmp_path):
    pdf = criar_pdf_imagem(tmp_path / "misto.pdf", com_pagina_de_texto=True)
    resultado = separar_caderno(pdf)
    assert resultado.paginas_digitalizadas  # a página de imagem foi vista
    assert any("OCR" in aviso for aviso in resultado.avisos)


def test_pdf_de_texto_simples_com_um_decreto(tmp_path):
    pdf = criar_pdf_texto(tmp_path / "um.pdf", decreto("60.013"))
    resultado = separar_caderno(pdf)
    assert [a.numero for a in resultado.atos] == ["60013"]