"""Modo lote (etapas 2 a 5): processa os PDFs da pasta temporária (PASTA_TEMP), um ato por
PDF: identificação -> duplicidade -> normalização -> gravação na base da DBF. Chamado por
`main.py` (python main.py [--simular])."""

from __future__ import annotations

import logging
import time
from collections import Counter
from enum import Enum
from pathlib import Path

import armazenamento
import normalizador
from armazenamento import ArquivoJaExiste, ErroArmazenamento, GravacaoDivergente
from config import (
    Configuracao,
    ErroConfiguracao,
    validar_configuracao,
)
from duplicidade import carregar_indice, chave_nome, ja_existe
from identificador import StatusIdentificacao, identificar
from normalizador import ErroNormalizacao
from reader import StatusLeitura, ler_pdf

log = logging.getLogger("pipeline")


class Destino(Enum):
    DESCARTADO_OUTRA_ESPECIE = "descartado (outra espécie)"
    DESCARTADO_DUPLICADO = "descartado (já existe na DBF)"
    ENVIADO_OCR = "enviado para leitura tesseract"
    ENVIADO_ERROS = "enviado para erros (revisão manual)"
    FALHA_AO_GRAVAR = "mantido na pasta temporária (falha ao gravar na DBF)"
    CONCLUIDO = "normalizado e armazenado na DBF"


def _mover(
    pdf: Path, pasta: Path, destino: Destino, motivo: str, simular: bool
) -> Destino:
    novo = armazenamento.mover_sem_sobrescrever(pdf, pasta, simular)
    log.info("%s | %s | %s | %s", pdf.name, destino.value, motivo, novo.name)
    return destino


def _descartar(pdf: Path, destino: Destino, motivo: str, simular: bool) -> Destino:
    armazenamento.descartar(pdf, simular)
    log.info("%s | %s | %s", pdf.name, destino.value, motivo)
    return destino


def processar_arquivo(
    pdf: Path,
    cfg: Configuracao,
    indice_dbf: set[str],
    vistos_no_lote: set[str],
    simular: bool,
) -> Destino:
    """Decide o destino de um PDF. As etapas são marcadas nos comentários."""
    # --- Etapa 2: leitura -------------------------------------------------
    leitura = ler_pdf(pdf)
    if leitura.status is StatusLeitura.CORROMPIDO:
        return _mover(
            pdf, cfg.pasta_erros, Destino.ENVIADO_ERROS, leitura.detalhe, simular
        )
    if leitura.status is StatusLeitura.IMAGEM:
        return _mover(pdf, cfg.pasta_ocr, Destino.ENVIADO_OCR, leitura.detalhe, simular)

    # --- Etapa 2: identificação -------------------------------------------
    ident = identificar(leitura.texto)
    if ident.status is StatusIdentificacao.OUTRA_ESPECIE:
        return _descartar(pdf, Destino.DESCARTADO_OUTRA_ESPECIE, ident.detalhe, simular)
    if ident.status is not StatusIdentificacao.IDENTIFICADO:
        return _mover(
            pdf, cfg.pasta_erros, Destino.ENVIADO_ERROS, ident.detalhe, simular
        )

    nome = ident.especie.nome_arquivo(ident.numero)

    # --- Etapa 3: duplicidade ---------------------------------------------
    if ja_existe(indice_dbf, nome):
        return _descartar(pdf, Destino.DESCARTADO_DUPLICADO, nome, simular)
    if chave_nome(nome) in vistos_no_lote:
        return _mover(
            pdf,
            cfg.pasta_erros,
            Destino.ENVIADO_ERROS,
            f"{nome} aparece mais de uma vez no lote",
            simular,
        )
    if ident.tipo is None:
        return _mover(
            pdf,
            cfg.pasta_erros,
            Destino.ENVIADO_ERROS,
            f"{nome}: tipo do ato não reconhecido ({ident.detalhe})",
            simular,
        )
    vistos_no_lote.add(chave_nome(nome))

    # --- Etapa 4: normalização --------------------------------------------
    try:
        pdf_gerado = normalizador.gerar_pdf_normalizado(
            pdf, ident, cfg.pasta_normalizados, simular
        )
    except ErroNormalizacao as erro:
        return _mover(
            pdf,
            cfg.pasta_erros,
            Destino.ENVIADO_ERROS,
            f"{nome}: normalização falhou ({erro})",
            simular,
        )

    # --- Etapa 5: armazenamento -------------------------------------------
    try:
        armazenamento.armazenar(pdf_gerado, nome, cfg.pasta_dbf, pdf, simular)
    except ArquivoJaExiste:
        # Outra pessoa salvou o mesmo decreto depois da etapa 3.
        armazenamento.descartar_se_existir(pdf_gerado)
        return _descartar(
            pdf, Destino.DESCARTADO_DUPLICADO, f"{nome} (surgiu na DBF durante o lote)", simular
        )
    except GravacaoDivergente as erro:
        return _mover(
            pdf,
            cfg.pasta_erros,
            Destino.ENVIADO_ERROS,
            f"{nome}: gravação na DBF não confere ({erro})",
            simular,
        )
    except ErroArmazenamento as erro:
        # Falha de E/S (drive indisponível, sem permissão...): o original fica em
        # pendentes e o próximo lote tenta de novo.
        log.error("%s | %s | %s: %s", pdf.name, Destino.FALHA_AO_GRAVAR.value, nome, erro)
        return Destino.FALHA_AO_GRAVAR

    log.info("%s | %s | %s (%s)", pdf.name, Destino.CONCLUIDO.value, nome, ident.tipo.nome)
    return Destino.CONCLUIDO


def listar_pdfs(pasta: Path) -> list[Path]:
    if not pasta.is_dir():
        return []
    return sorted(
        p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"
    )


def executar(
    cfg: Configuracao, simular: bool = False, permitir_pasta_vazia: bool = False
) -> Counter[Destino]:
    """Processa o lote e devolve a contagem por destino."""
    if cfg.pasta_dbf is None:
        raise ErroConfiguracao(
            "PASTA_DBF não está definida. Copie .env.example para .env e "
            "informe a pasta da base da DBF."
        )

    # Defesa em profundidade: `executar` também é chamada fora do `main`, e é
    # ela que descarta arquivos.
    validar_configuracao(cfg)

    inicio = time.perf_counter()
    indice_dbf = carregar_indice(cfg.pasta_dbf, permitir_pasta_vazia)
    pdfs = listar_pdfs(cfg.pasta_temp)
    modo = " [SIMULAÇÃO: nada será movido ou apagado]" if simular else ""
    log.info(
        "Lote iniciado%s: %d PDF(s) em %s; %d arquivo(s) na base da DBF.",
        modo,
        len(pdfs),
        cfg.pasta_temp,
        len(indice_dbf),
    )

    resumo: Counter[Destino] = Counter()
    vistos_no_lote: set[str] = set()
    for pdf in pdfs:
        try:
            destino = processar_arquivo(pdf, cfg, indice_dbf, vistos_no_lote, simular)
        except Exception as erro:  # uma falha não interrompe o restante do lote
            log.exception("%s | falha inesperada: %s", pdf.name, erro)
            try:
                destino = _mover(
                    pdf, cfg.pasta_erros, Destino.ENVIADO_ERROS, str(erro), simular
                )
            except Exception:
                log.exception("%s | não foi possível isolar o arquivo", pdf.name)
                continue
        resumo[destino] += 1

    log.info("Lote finalizado em %.2f s.", time.perf_counter() - inicio)
    for destino, quantidade in resumo.most_common():
        log.info("  %3d  %s", quantidade, destino.value)
    return resumo
