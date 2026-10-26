"""Etapa 1b (saída) — grava cada decreto separado como um PDF em `dados/pendentes`.

Cada decreto do caderno que a DBF ainda não tem vira um PDF próprio, de um ato
só, que segue o fluxo normal (etapa 2 em diante). Este PDF é INTERMEDIÁRIO: só
carrega o texto do decreto, sem o layout dos modelos da DBF (isso é a etapa 4).

Por que gerar um PDF novo em vez de recortar o do caderno: as páginas do Diário
têm várias colunas e os decretos começam no meio delas, então um recorte da
página traria texto de decretos vizinhos.

Segurança: o PDF é escrito em um arquivo ".part", relido com o mesmo leitor da
etapa 2 e só é renomeado se o texto relido for idêntico ao do decreto
(ignorando quebras de linha) e o número identificado for o esperado. Qualquer
diferença (por exemplo, um caractere que a fonte não tem) vira erro, e nenhum
arquivo fica para trás. Nunca sobrescreve um arquivo existente.

A fonte é a Liberation Sans (licença SIL OFL 1.1), versionada em `fontes/`
junto com o texto da licença.
"""

from __future__ import annotations

import gc
from pathlib import Path

from fpdf import FPDF

from identificador import StatusIdentificacao, identificar, normalizar_espacos
from reader import StatusLeitura, ler_pdf
from separador import AtoSeparado

PASTA_FONTES = Path(__file__).resolve().parent / "fontes"
FONTE_REGULAR = "LiberationSans-Regular.ttf"
FONTE_NEGRITO = "LiberationSans-Bold.ttf"

_MARGEM_MM = 20
_TAMANHO_TITULO = 12
_TAMANHO_TEXTO = 10
_ALTURA_LINHA = 5
_ESPACO_LINHA_VAZIA = 3


class ErroExportacao(Exception):
    """O PDF do decreto não pôde ser gerado ou não passou na conferência."""


def _remover(caminho: Path) -> None:
    try:
        caminho.unlink(missing_ok=True)
    except PermissionError:  # Windows: identificador do PyMuPDF ainda aberto
        gc.collect()
        try:
            caminho.unlink(missing_ok=True)
        except OSError:
            pass


def _montar_pdf(ato: AtoSeparado, pasta_fontes: Path) -> FPDF:
    regular, negrito = pasta_fontes / FONTE_REGULAR, pasta_fontes / FONTE_NEGRITO
    for fonte in (regular, negrito):
        if not fonte.is_file():
            raise ErroExportacao(
                f"fonte não encontrada: {fonte}. A pasta 'fontes/' do projeto precisa "
                f"conter {FONTE_REGULAR} e {FONTE_NEGRITO}."
            )
    pdf = FPDF(format="A4")
    pdf.set_margins(_MARGEM_MM, _MARGEM_MM, _MARGEM_MM)
    pdf.set_auto_page_break(True, _MARGEM_MM)
    pdf.add_font("Liberation", "", str(regular))
    pdf.add_font("Liberation", "B", str(negrito))
    pdf.set_title(f"{ato.especie.nome} {ato.numero}")
    pdf.add_page()
    for posicao, linha in enumerate(ato.texto.split("\n")):
        if not linha.strip():
            pdf.ln(_ESPACO_LINHA_VAZIA)
            continue
        if posicao == 0:
            pdf.set_font("Liberation", "B", _TAMANHO_TITULO)
        else:
            pdf.set_font("Liberation", "", _TAMANHO_TEXTO)
        pdf.multi_cell(0, _ALTURA_LINHA, linha, new_x="LMARGIN", new_y="NEXT")
    return pdf


def _conferir(parcial: Path, ato: AtoSeparado) -> None:
    leitura = ler_pdf(parcial)
    if leitura.status is not StatusLeitura.OK:
        raise ErroExportacao(f"PDF gerado não pôde ser relido ({leitura.status.value}): {leitura.detalhe}")
    ident = identificar(leitura.texto)
    if ident.status is not StatusIdentificacao.IDENTIFICADO or ident.numero != ato.numero:
        raise ErroExportacao(
            f"PDF gerado foi identificado como {ident.numero!r} ({ident.status.value}), "
            f"esperado {ato.numero!r}"
        )
    if normalizar_espacos(leitura.texto) != normalizar_espacos(ato.texto):
        raise ErroExportacao(
            "o texto do PDF gerado difere do texto do decreto (caractere sem glifo na fonte?)"
        )


def gravar_pdf_do_ato(
    ato: AtoSeparado, destino: Path, pasta_fontes: Path = PASTA_FONTES
) -> Path:
    """Grava o decreto em `destino` (um arquivo .pdf que ainda não existe)."""
    if destino.exists():
        raise ErroExportacao(f"{destino.name} já existe; não será sobrescrito")
    parcial = destino.with_name(destino.name + ".part")
    destino.parent.mkdir(parents=True, exist_ok=True)
    try:
        _montar_pdf(ato, pasta_fontes).output(str(parcial))
        _conferir(parcial, ato)
        parcial.replace(destino)
    except ErroExportacao:
        _remover(parcial)
        raise
    except Exception as erro:
        _remover(parcial)
        raise ErroExportacao(f"{type(erro).__name__}: {erro}") from erro
    return destino