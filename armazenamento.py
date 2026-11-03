"""Etapa 5 — armazenamento e descarte dos PDFs.

`mover_sem_sobrescrever` e `descartar` são usados desde a etapa 2 (PDF de imagem
-> "leitura tesseract", outra espécie -> descarte, erros -> revisão).

`armazenar` grava o PDF normalizado na pasta da DBF (por exemplo, o Drive em G:):

  1. confere o PDF gerado: abre, lê o texto e confirma que ele se identifica como
     o ato esperado (nome final igual ao calculado pela espécie e pelo número);
  2. confere de novo, na hora de gravar, que o nome ainda não existe na pasta da
     DBF (outra pessoa pode ter salvado o mesmo decreto desde a etapa 3);
  3. copia para "<nome>.part" na própria pasta da DBF e compara o SHA-256 do que
     foi gravado com o do PDF gerado;
  4. renomeia para o nome final (no Windows, o renomear recusa um nome que já
     existe, então nunca sobrescreve) e relê o arquivo final;
  5. só então descarta o PDF original (em `dados/pendentes`) e a cópia gerada (em
     `dados/normalizados`).

Qualquer falha antes do passo 5 deixa o original onde estava, para reprocessar, e
não deixa arquivo parcial na pasta da DBF. A pasta é sincronizada com o Drive:
o ".part" é o único arquivo temporário e só existe durante a cópia.

Exceções de `armazenar`:
  - `ArquivoJaExiste`: o nome já existe na DBF (o original deve ser descartado como
    duplicado);
  - `GravacaoDivergente`: o conteúdo gravado não confere (o original deve ir para
    revisão);
  - `ErroArmazenamento`: falha de E/S (pasta indisponível, sem permissão, disco
    cheio); o original deve ficar onde está para uma nova tentativa.
"""

from __future__ import annotations

import gc
import hashlib
import logging
import os
import shutil
from pathlib import Path

from duplicidade import chave_nome
from identificador import StatusIdentificacao, identificar
from reader import StatusLeitura, ler_pdf

log = logging.getLogger("pipeline")

_BLOCO = 1024 * 1024


class ErroArmazenamento(Exception):
    """Não foi possível gravar o PDF na pasta da DBF (falha de E/S; tentar de novo)."""


class ArquivoJaExiste(ErroArmazenamento):
    """O nome final já existe na pasta da DBF; nada foi gravado."""


class GravacaoDivergente(ErroArmazenamento):
    """O PDF gerado ou o arquivo gravado não passou na conferência."""


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


def descartar_se_existir(caminho: Path) -> bool:
    """Apaga o arquivo se existir; nunca levanta exceção. Devolve False se não conseguiu.

    No Windows, um identificador do PyMuPDF ainda aberto pode bloquear a exclusão;
    uma coleta de lixo e uma segunda tentativa resolvem.
    """
    for tentativa in (1, 2):
        try:
            caminho.unlink(missing_ok=True)
            return True
        except OSError:
            if tentativa == 1:
                gc.collect()
    log.warning("Não foi possível apagar %s; apague à mão.", caminho)
    return False


def _sha256(caminho: Path) -> str:
    resumo = hashlib.sha256()
    with open(caminho, "rb") as arquivo:
        while bloco := arquivo.read(_BLOCO):
            resumo.update(bloco)
    return resumo.hexdigest()


def _conferir_se_identifica_como(pdf: Path, nome_final: str, descricao: str) -> None:
    leitura = ler_pdf(pdf)
    if leitura.status is not StatusLeitura.OK:
        raise GravacaoDivergente(
            f"{descricao} não pôde ser lido ({leitura.status.value}): {leitura.detalhe}"
        )
    ident = identificar(leitura.texto)
    if (
        ident.status is not StatusIdentificacao.IDENTIFICADO
        or ident.especie is None
        or chave_nome(ident.especie.nome_arquivo(ident.numero)) != chave_nome(nome_final)
    ):
        raise GravacaoDivergente(
            f"{descricao} não se identifica como {nome_final} ({ident.status.value}: {ident.detalhe})"
        )


def armazenar(
    pdf_gerado: Path,
    nome_final: str,
    pasta_dbf: Path,
    original: Path,
    simular: bool = False,
) -> Path:
    """Grava `pdf_gerado` em `pasta_dbf / nome_final` e então descarta `original`.

    Devolve o caminho gravado. Em `simular`, só confere que o nome ainda está livre.
    """
    destino = pasta_dbf / nome_final
    if destino.exists():
        raise ArquivoJaExiste(f"{nome_final} já existe em {pasta_dbf}")
    if simular:
        return destino

    _conferir_se_identifica_como(pdf_gerado, nome_final, "o PDF gerado")

    parcial = destino.with_name(destino.name + ".part")
    try:
        if not pasta_dbf.is_dir():
            raise ErroArmazenamento(f"pasta da DBF indisponível: {pasta_dbf}")
        esperado = _sha256(pdf_gerado)
        # "wb" substitui a sobra de uma execução interrompida
        with open(pdf_gerado, "rb") as origem, open(parcial, "wb") as saida:
            shutil.copyfileobj(origem, saida, _BLOCO)
            saida.flush()
            os.fsync(saida.fileno())
        if _sha256(parcial) != esperado:
            raise GravacaoDivergente(f"a cópia de {nome_final} na DBF difere do PDF gerado")
        if destino.exists():
            raise ArquivoJaExiste(f"{nome_final} apareceu em {pasta_dbf} durante a gravação")
        try:
            os.rename(parcial, destino)  # no Windows, recusa um nome que já existe
        except FileExistsError as erro:
            raise ArquivoJaExiste(f"{nome_final} apareceu em {pasta_dbf} durante a gravação") from erro
        try:
            _conferir_se_identifica_como(destino, nome_final, f"{nome_final} gravado na DBF")
        except GravacaoDivergente:
            descartar_se_existir(destino)  # fomos nós que o criamos há instantes
            raise
    except ErroArmazenamento:
        descartar_se_existir(parcial)
        raise
    except OSError as erro:
        descartar_se_existir(parcial)
        raise ErroArmazenamento(f"{type(erro).__name__}: {erro}") from erro

    # Gravação confirmada: só agora os PDFs de trabalho podem ir embora.
    descartar_se_existir(original)
    descartar_se_existir(pdf_gerado)
    return destino