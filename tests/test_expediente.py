"""Remoção do expediente do Diário (o quadro com governadora, secretários, preços e endereço
da CEPE) do texto dos atos que dividem a página com ele."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from dbf_cepe.leitura import separador
from dbf_cepe.leitura.separador import separar_caderno

EXPEDIENTE = (
    "GOVERNADORA\nRaquel Teixeira Lyra Lucena\nVICE-GOVERNADORA\nPriscila Krause Branco\n"
    "SECRETÁRIOS DE ESTADO\nSECRETÁRIA DE ADMINISTRAÇÃO\nAna Maraíza de Sousa Silva\n"
    "DIRETOR PRESIDENTE\nJoão Baltar Freire\nCOMPANHIA EDITORA DE PERNAMBUCO\n"
    "CNPJ 10.921.252/0001-07\nOuvidoria - Fone: 3183-2736\nouvidoria@cepe.com.br"
)
DECRETO_1 = (
    "DECRETO Nº 61.563, DE 20 DE OUTUBRO DE 2026.\nAbre crédito suplementar.\n"
    "A GOVERNADORA DO ESTADO, no uso das atribuições,\nDECRETA:\nArt. 1º Fica aberto o crédito."
)
DECRETO_2 = (
    "Art. 2º Este Decreto entra em vigor na data de sua publicação.\n"
    "Palácio do Campo das Princesas, Recife, 20 de outubro do ano de 2026, 210º da Revolução "
    "Republicana Constitucionalista e 204º da Independência do Brasil.\nRAQUEL TEIXEIRA LYRA LUCENA"
)


def pagina_com_expediente(caminho: Path, expediente: str = EXPEDIENTE) -> Path:
    """Decreto em duas colunas e o expediente num quadro entre elas, na ordem do PDF:
    1ª coluna, quadro, 2ª coluna (como nos cadernos reais)."""
    doc = pymupdf.open()
    pagina = doc.new_page()
    pagina.insert_textbox(pymupdf.Rect(30, 40, 210, 400), DECRETO_1, fontsize=7)
    pagina.insert_textbox(pymupdf.Rect(215, 40, 395, 400), expediente, fontsize=7)
    pagina.insert_textbox(pymupdf.Rect(400, 40, 580, 400), DECRETO_2, fontsize=7)
    doc.save(caminho)
    doc.close()
    return caminho


def test_expediente_sai_do_texto_do_decreto(tmp_path):
    atos = separar_caderno(pagina_com_expediente(tmp_path / "c.pdf")).atos
    assert len(atos) == 1
    texto = atos[0].texto
    assert "COMPANHIA EDITORA" not in texto and "Priscila Krause Branco" not in texto
    assert "SECRETÁRIA DE ADMINISTRAÇÃO" not in texto and "ouvidoria" not in texto
    assert "Art. 1º Fica aberto o crédito." in texto and "Independência do Brasil." in texto
    assert not atos[0].avisos  # com o quadro fora, o fecho é achado e o ato termina nele


def test_sem_marcas_suficientes_nada_e_removido(tmp_path):
    # só o nome da CEPE e o cargo (como no rodapé de certificação da capa): não é o quadro
    pouco = "GOVERNADORA\nRaquel Teixeira Lyra Lucena\nCOMPANHIA EDITORA DE PERNAMBUCO\nCNPJ 10.921.252/0001-07"
    texto = separar_caderno(pagina_com_expediente(tmp_path / "c.pdf", pouco)).atos[0].texto
    assert "COMPANHIA EDITORA DE PERNAMBUCO" in texto and "Raquel Teixeira Lyra Lucena" in texto


def test_sem_a_linha_do_cargo_de_governador_nada_e_removido(tmp_path):
    sem_cargo = EXPEDIENTE.replace("GOVERNADORA\nRaquel", "Raquel", 1)
    texto = separar_caderno(pagina_com_expediente(tmp_path / "c.pdf", sem_cargo)).atos[0].texto
    assert "COMPANHIA EDITORA DE PERNAMBUCO" in texto


def test_pagina_sem_expediente_fica_como_estava(tmp_path):
    caminho = tmp_path / "c.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_textbox(pymupdf.Rect(40, 40, 570, 800), DECRETO_1 + "\n" + DECRETO_2, fontsize=8)
    doc.save(caminho)
    with pymupdf.open(caminho) as lido:
        linhas = lido[0].get_text("text").split("\n")[:-1]
        assert separador._linhas_da_pagina(lido[0]) == linhas


def test_posicao_que_nao_confere_com_o_texto_nao_remove_nada(tmp_path, monkeypatch):
    caminho = pagina_com_expediente(tmp_path / "c.pdf")
    with pymupdf.open(caminho) as doc:
        linhas = doc[0].get_text("text").split("\n")[:-1]
        alteradas = [*linhas, "linha que a posição não conhece"]
        assert separador._sem_expediente(doc[0], alteradas) == alteradas


def test_marcas_ficam_no_separador():
    assert set(separador.MARCAS_DO_EXPEDIENTE) == {
        "governador", "vice", "secretarios", "cepe", "contato", "diretoria", "titulo"
    }
    assert separador.MINIMO_DE_MARCAS_DO_EXPEDIENTE == 4


def test_governador_da_assinatura_de_outro_ato_nao_estica_o_quadro(tmp_path):
    """Um "GOVERNADOR" sozinho na linha (assinatura) longe do quadro não é marca: só o par
    GOVERNADOR(A) + VICE-GOVERNADOR(A) conta. Senão o retângulo pegaria o texto entre os dois."""
    caminho = tmp_path / "c.pdf"
    doc = pymupdf.open()
    pagina = doc.new_page()
    pagina.insert_textbox(pymupdf.Rect(30, 40, 210, 120), "PORTARIA Nº 1\nTexto que precisa ficar.\nGOVERNADOR", fontsize=7)
    pagina.insert_textbox(pymupdf.Rect(30, 500, 210, 800), DECRETO_1, fontsize=7)
    pagina.insert_textbox(pymupdf.Rect(215, 500, 395, 800), EXPEDIENTE, fontsize=7)
    pagina.insert_textbox(pymupdf.Rect(400, 500, 580, 800), DECRETO_2, fontsize=7)
    doc.save(caminho)
    doc.close()
    with pymupdf.open(caminho) as lido:
        linhas = separador._linhas_da_pagina(lido[0])
    assert "Texto que precisa ficar." in linhas and "PORTARIA Nº 1" in linhas
    assert "COMPANHIA EDITORA DE PERNAMBUCO" not in linhas and "Art. 1º Fica aberto o crédito." in linhas


def test_preco_ao_lado_das_marcas_curtas_tambem_sai(tmp_path):
    """Como em 2010: as marcas são linhas curtas, mas o bloco delas é largo (tem uma linha
    longa); preços e avisos ao lado, dentro da largura do bloco, são do quadro."""
    caminho = tmp_path / "c.pdf"
    largo = EXPEDIENTE + "\nExemplar do Dia ...................................................... R$ 2,00"
    doc = pymupdf.open()
    pagina = doc.new_page()
    pagina.insert_textbox(pymupdf.Rect(30, 500, 210, 800), DECRETO_1, fontsize=7)
    pagina.insert_textbox(pymupdf.Rect(215, 500, 395, 800), largo, fontsize=7)
    pagina.insert_textbox(pymupdf.Rect(350, 520, 395, 545), "Quaisquer", fontsize=7)
    pagina.insert_textbox(pymupdf.Rect(400, 500, 580, 800), DECRETO_2, fontsize=7)
    doc.save(caminho)
    doc.close()
    with pymupdf.open(caminho) as lido:
        linhas = separador._linhas_da_pagina(lido[0])
    assert "Quaisquer" not in linhas and "Art. 1º Fica aberto o crédito." in linhas


def test_diretora_presidente_conta_como_marca(tmp_path):
    # exatamente 4 tipos de marca: o par governador/vice, a CEPE e a diretoria (no feminino)
    quatro = "GOVERNADOR\nEduardo Campos\nVICE-GOVERNADOR\nJoão Lyra Neto\nDIRETORA PRESIDENTE\nFulana\nCOMPANHIA EDITORA DE PERNAMBUCO"
    texto = separar_caderno(pagina_com_expediente(tmp_path / "c.pdf", quatro)).atos[0].texto
    assert "COMPANHIA EDITORA DE PERNAMBUCO" not in texto and "João Lyra Neto" not in texto
