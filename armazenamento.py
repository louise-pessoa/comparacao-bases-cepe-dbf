"""Etapa 5 — armazenamento e descarte dos PDFs.

Implementado: `mover_sem_sobrescrever` e `descartar`, usados desde a etapa 2
(PDF de imagem -> "leitura tesseract", outra espécie -> descarte, erros ->
revisão).

Pendente: `armazenar` (gravar o PDF normalizado na pasta da DBF).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger("pipeline")

##################################################################################

def mover_sem_sobrescrever(
    origem: Path, pasta_destino: Path, simular: bool = False
) -> Path:
    """Move o arquivo mantendo o nome; se já existir, acrescenta " (2)", " (3)"...

    Nunca sobrescreve: um PDF baixado de novo com o mesmo nome da CEPE não pode
    apagar um anterior que ainda aguarda OCR ou revisão.
    """
    destino = pasta_destino / origem.name
    contador = 2
    while destino.exists():
        destino = pasta_destino / f"{origem.stem} ({contador}){origem.suffix}"
        contador += 1
    if not simular:
        pasta_destino.mkdir(parents=True, exist_ok=True)
        shutil.move(str(origem), str(destino))
    return destino


def descartar(origem: Path, simular: bool = False) -> None:
    """Apaga o PDF temporário. A exclusão é definitiva (sem lixeira)."""
    if not simular:
        origem.unlink()


def armazenar(
    pdf_gerado: Path,
    nome_final: str,
    pasta_dbf: Path,
    original: Path,
    simular: bool = False,
) -> Path:
    """ETAPA 5 — NÃO IMPLEMENTADA.

    Contrato: grava `pdf_gerado` em `pasta_dbf / nome_final` (ex.:
    "Decreto 60929.pdf") e só então descarta `original`.

    Pontos a tratar ao implementar:
      - validar o PDF gerado (abrir com PyMuPDF e conferir que tem texto e o
        número esperado) ANTES de copiar para a pasta da DBF;
      - não sobrescrever se o nome já existir (checar de novo na hora de gravar:
        outra pessoa pode ter salvado o mesmo decreto desde a etapa 3);
      - o PDF original só é descartado depois de a gravação ser confirmada; em
        qualquer falha ele permanece na pasta temporária para reprocessamento;
      - Drive sincronizado: evitar deixar arquivos parciais/.tmp na pasta
        sincronizada (gerar e validar na pasta local antes de copiar).
    """
    raise NotImplementedError("Etapa 5 (armazenamento) ainda não implementada.")
