"""Utilitários de teste: geração de PDFs sintéticos e acesso às amostras reais."""

from __future__ import annotations

from pathlib import Path

import pymupdf

AMOSTRAS = Path(__file__).parent / "amostras"

# Amostras reais da CEPE: número -> tipo esperado
TIPO_ESPERADO_DECRETO = {
    "60929": "CONCESSAO",
    "60951": "ALTERACAO",
    "60950": "PRORROGACAO",
    "60945": "RENOVACAO",
    "60505": "TRANSFERENCIA",
    "60233": "TERCEIRIZACAO",
}

# As amostras são decretos já publicados pela CEPE e ficam versionadas em
# tests/amostras/. Não há skip: se faltarem, os testes devem reprovar
# (ver test_amostras.py), para não dar falsa segurança.


def criar_pdf_texto(caminho: Path, *paginas: str) -> Path:
    """PDF sintético com uma página por texto recebido."""
    doc = pymupdf.open()
    for texto in paginas:
        pagina = doc.new_page()
        pagina.insert_textbox(pymupdf.Rect(50, 50, 545, 790), texto, fontsize=10)
    doc.save(caminho)
    doc.close()
    return caminho


def criar_pdf_imagem(caminho: Path, *, com_pagina_de_texto: bool = False) -> Path:
    """PDF 'digitalizado': página inteira ocupada por uma imagem, sem texto.

    Com `com_pagina_de_texto`, a primeira página tem texto e a segunda é a
    imagem (PDF parcialmente digitalizado).
    """
    doc = pymupdf.open()
    if com_pagina_de_texto:
        pagina = doc.new_page()
        pagina.insert_textbox(
            pymupdf.Rect(50, 50, 545, 790),
            "DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026. Concede estímulo.",
            fontsize=10,
        )
    pagina = doc.new_page()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 140), False)
    pixmap.clear_with(200)
    pagina.insert_image(pagina.rect, pixmap=pixmap)
    doc.save(caminho)
    doc.close()
    return caminho


def criar_pdf_com_imagem_parcial(
    caminho: Path, fracao_da_pagina: float, texto: str = ""
) -> Path:
    """PDF de uma página com uma imagem cobrindo `fracao_da_pagina` da área.

    O `texto` opcional (curto) vai no canto superior esquerdo. Serve para testar
    o limite de cobertura da imagem na detecção de páginas digitalizadas.
    """
    doc = pymupdf.open()
    pagina = doc.new_page()
    escala = fracao_da_pagina**0.5
    largura, altura = pagina.rect.width * escala, pagina.rect.height * escala
    x0 = (pagina.rect.width - largura) / 2
    y0 = (pagina.rect.height - altura) / 2
    if texto:
        pagina.insert_text((30, 30), texto, fontsize=10)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 140), False)
    pixmap.clear_with(200)
    pagina.insert_image(
        pymupdf.Rect(x0, y0, x0 + largura, y0 + altura), pixmap=pixmap
    )
    doc.save(caminho)
    doc.close()
    return caminho


def texto_decreto(numero: str, ementa: str, data: str = "26 DE JUNHO DE 2026") -> str:
    """Texto mínimo de um decreto, no formato do Diário Oficial."""
    return (
        f"DECRETO Nº {numero}, DE {data}.\n"
        f"{ementa}\n"
        "A GOVERNADORA DO ESTADO, no uso das atribuições que lhe são "
        "conferidas pelo inciso IV do art. 37 da Constituição Estadual,\n"
        "CONSIDERANDO a Lei nº 11.675, de 11 de outubro de 1999, e o "
        "Decreto nº 21.959, de 27 de dezembro de 1999;\n"
        "DECRETA:\nArt. 1º Fica concedido o estímulo."
    )