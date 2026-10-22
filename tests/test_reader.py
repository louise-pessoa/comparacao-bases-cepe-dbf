"""Etapa 2 (leitura): texto, PDF de imagem e arquivos inválidos."""

from __future__ import annotations

import pymupdf
import pytest

from conftest import (
    AMOSTRAS,
    TIPO_ESPERADO,
    criar_pdf_com_imagem_parcial,
    criar_pdf_imagem,
    criar_pdf_texto,
)
from reader import StatusLeitura, ler_pdf


##################################################################################

@pytest.mark.parametrize("numero", sorted(TIPO_ESPERADO))
def test_amostras_reais_sao_lidas_como_texto(numero):
    # Os PDFs da CEPE têm o brasão como imagem na página 1: isso NÃO pode fazê-los
    # parecer digitalizados.
    leitura = ler_pdf(AMOSTRAS / f"Decreto {numero}.pdf")
    assert leitura.status is StatusLeitura.OK
    assert "DECRETO" in leitura.texto
    assert leitura.paginas_digitalizadas == ()


def test_pdf_de_texto_sintetico_e_ok(tmp_path):
    pdf = criar_pdf_texto(tmp_path / "a.pdf", "DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026.")
    leitura = ler_pdf(pdf)
    assert leitura.status is StatusLeitura.OK
    assert "60.929" in leitura.texto


def test_pdf_so_de_imagem_vai_para_ocr(tmp_path):
    leitura = ler_pdf(criar_pdf_imagem(tmp_path / "scan.pdf"))
    assert leitura.status is StatusLeitura.IMAGEM
    assert leitura.paginas_digitalizadas == (1,)


def test_pdf_parcialmente_digitalizado_vai_para_ocr(tmp_path):
    leitura = ler_pdf(criar_pdf_imagem(tmp_path / "parcial.pdf", com_pagina_de_texto=True))
    assert leitura.status is StatusLeitura.IMAGEM
    assert leitura.paginas_digitalizadas == (2,)
    assert leitura.paginas == 2


@pytest.mark.parametrize(
    "fracao, esperado",
    [
        (0.03, StatusLeitura.OK),  # brasão da CEPE (~3% da página)
        (0.05, StatusLeitura.OK),
        (0.12, StatusLeitura.IMAGEM),
        (0.29, StatusLeitura.IMAGEM),  # Decreto 35945 (~29%)
        (0.90, StatusLeitura.IMAGEM),
    ],
)
def test_cobertura_da_imagem_com_quase_nenhum_texto(tmp_path, fracao, esperado):
    # Com 1 caractere de texto, só a imagem decide. O texto não pode ser vazio:
    # sem nenhum texto o PDF iria para OCR qualquer que fosse a imagem.
    pdf = criar_pdf_com_imagem_parcial(tmp_path / "p.pdf", fracao, texto="1")
    assert ler_pdf(pdf).status is esperado


def test_imagem_de_29_por_cento_sem_texto_marca_a_pagina_como_digitalizada(tmp_path):
    # Mesmo caso do Decreto 35945: nenhum texto e imagem cobrindo 29% da página.
    leitura = ler_pdf(criar_pdf_com_imagem_parcial(tmp_path / "p.pdf", 0.29))
    assert leitura.status is StatusLeitura.IMAGEM
    assert leitura.paginas_digitalizadas == (1,)


def test_pagina_em_branco_sem_imagem_nao_conta_como_digitalizada(tmp_path):
    pdf = criar_pdf_texto(tmp_path / "branco.pdf", "DECRETO Nº 1, DE 1º DE JANEIRO DE 2026.", "")
    leitura = ler_pdf(pdf)
    assert leitura.status is StatusLeitura.OK


def test_pdf_sem_nenhum_texto_e_sem_imagem_vai_para_ocr(tmp_path):
    doc = pymupdf.open()
    doc.new_page()
    doc.save(tmp_path / "vazio_visual.pdf")
    leitura = ler_pdf(tmp_path / "vazio_visual.pdf")
    assert leitura.status is StatusLeitura.IMAGEM


@pytest.mark.parametrize(
    "conteudo", [b"isto nao e um pdf", b""], ids=["lixo", "arquivo_vazio"]
)
def test_arquivo_invalido_e_corrompido(tmp_path, conteudo):
    pdf = tmp_path / "ruim.pdf"
    pdf.write_bytes(conteudo)
    leitura = ler_pdf(pdf)
    assert leitura.status is StatusLeitura.CORROMPIDO
    assert leitura.detalhe


def test_arquivo_inexistente_e_corrompido(tmp_path):
    assert ler_pdf(tmp_path / "nao_existe.pdf").status is StatusLeitura.CORROMPIDO


def test_pdf_com_senha_e_corrompido(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "segredo")
    caminho = tmp_path / "senha.pdf"
    doc.save(
        caminho,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="dono",
        user_pw="usuario",
    )
    doc.close()
    leitura = ler_pdf(caminho)
    assert leitura.status is StatusLeitura.CORROMPIDO
    assert "senha" in leitura.detalhe
