"""Etapa 2 (parte 1) — leitura do PDF com PyMuPDF.

Classifica o arquivo em três situações:
  - OK:         há texto extraível; o texto bruto é devolvido.
  - IMAGEM:     PDF digitalizado (total ou parcialmente); segue para OCR.
  - CORROMPIDO: não abre, está vazio, sem páginas ou protegido por senha.

Detecção de imagem: NÃO basta o PDF conter imagens. Os PDFs da CEPE trazem o
brasão como imagem na primeira página e são 100% texto. Uma página é
considerada digitalizada quando tem pouquíssimo texto E uma imagem que cobre
parte relevante dela (FRACAO_MIN_IMAGEM). A imagem pode ser bem menor que a
página: o Decreto 35945, que é uma imagem, a cobre em 29%.

Limitação conhecida: um download truncado não é detectável aqui. Os PDFs da
CEPE trazem dados após o último "%%EOF" e o PyMuPDF os marca como "reparados"
mesmo quando íntegros, então esses sinais geram falsos positivos. A integridade
do download deve ser conferida na etapa 1 (captura).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pymupdf

# Página com menos caracteres (sem contar espaços) que isto, e com uma imagem
# grande, é tratada como digitalizada. Os decretos têm centenas de caracteres
# por página.
MIN_CARACTERES_POR_PAGINA = 20
# Fração mínima da área da página que a imagem precisa cobrir. O brasão da CEPE
# cobre cerca de 3% (não pode disparar).
FRACAO_MIN_IMAGEM = 0.10

##################################################################################

class StatusLeitura(Enum):
    OK = "ok"
    IMAGEM = "imagem"
    CORROMPIDO = "corrompido"


@dataclass(frozen=True)
class ResultadoLeitura:
    status: StatusLeitura
    texto: str = ""
    paginas: int = 0
    paginas_digitalizadas: tuple[int, ...] = ()  # números das páginas (base 1)
    detalhe: str = ""


##################################################################################

def _pagina_digitalizada(pagina: pymupdf.Page, texto: str) -> bool:
    if len("".join(texto.split())) >= MIN_CARACTERES_POR_PAGINA:
        return False
    area_pagina = pagina.rect.get_area()
    return any(
        pymupdf.Rect(info["bbox"]).get_area() >= FRACAO_MIN_IMAGEM * area_pagina
        for info in pagina.get_image_info()
    )


def ler_pdf(caminho: Path) -> ResultadoLeitura:
    """Abre o PDF e devolve o texto bruto ou o motivo de não haver texto."""
    try:
        with pymupdf.open(caminho) as doc:
            if doc.needs_pass:
                return ResultadoLeitura(
                    StatusLeitura.CORROMPIDO, detalhe="PDF protegido por senha"
                )
            if doc.page_count == 0:
                return ResultadoLeitura(
                    StatusLeitura.CORROMPIDO, detalhe="PDF sem páginas"
                )

            textos: list[str] = []
            digitalizadas: list[int] = []
            for numero, pagina in enumerate(doc, start=1):
                texto = pagina.get_text("text")
                textos.append(texto)
                if _pagina_digitalizada(pagina, texto):
                    digitalizadas.append(numero)
            paginas = doc.page_count
    except Exception as erro:  # inexistente, vazio, não-PDF, estrutura quebrada
        return ResultadoLeitura(
            StatusLeitura.CORROMPIDO, detalhe=f"{type(erro).__name__}: {erro}"
        )

    texto_total = "\n".join(textos)

    if digitalizadas:
        # Parcialmente digitalizado também vai para OCR: normalizar só as
        # páginas com texto geraria um decreto incompleto.
        return ResultadoLeitura(
            StatusLeitura.IMAGEM,
            texto=texto_total,
            paginas=paginas,
            paginas_digitalizadas=tuple(digitalizadas),
            detalhe=f"páginas digitalizadas: {digitalizadas} de {paginas}",
        )
    if not texto_total.strip():
        return ResultadoLeitura(
            StatusLeitura.IMAGEM,
            paginas=paginas,
            detalhe="nenhum texto extraível no PDF",
        )
    return ResultadoLeitura(StatusLeitura.OK, texto=texto_total, paginas=paginas)
