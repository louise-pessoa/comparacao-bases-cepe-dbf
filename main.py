"""Orquestração do pipeline de normalização na fonte (DBF/SEFAZ).

Dois modos, independentes por enquanto:

  1. Captura (etapa 1): `--baixar --de AAAA-MM-DD [--ate AAAA-MM-DD]` baixa os
     cadernos (PDF completo do dia) para PASTA_CADERNOS e termina. Os cadernos
     ainda NÃO seguem para a etapa 2: falta o passo que separa os decretos.
  2. Lote: processa os PDFs da pasta temporária (PASTA_TEMP), um ato por PDF:
     etapa 2 (identificação) -> etapa 3 (duplicidade) -> etapas 4 e 5.
     As etapas 4 e 5 ainda são esqueletos: um decreto inédito permanece na
     pasta temporária (nunca é descartado antes de a etapa 5 gravar o resultado).

Uso:
    python main.py --simular                              # lote, sem mexer em nada
    python main.py                                        # lote, de verdade
    python main.py --baixar --de 2026-10-01 --simular     # mostra o que baixaria
    python main.py --baixar --de 2026-10-01 --ate 2026-10-20
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from datetime import date
from enum import Enum
from pathlib import Path

import armazenamento
import captura
import normalizador
from config import (
    Configuracao,
    ErroConfiguracao,
    carregar_configuracao,
    validar_configuracao,
)
from duplicidade import PastaDbfIndisponivel, carregar_indice, chave_nome, ja_existe
from identificador import StatusIdentificacao, identificar
from reader import StatusLeitura, ler_pdf

log = logging.getLogger("pipeline")


class Destino(Enum):
    DESCARTADO_OUTRA_ESPECIE = "descartado (outra espécie)"
    DESCARTADO_DUPLICADO = "descartado (já existe na DBF)"
    ENVIADO_OCR = "enviado para leitura tesseract"
    ENVIADO_ERROS = "enviado para erros (revisão manual)"
    AGUARDANDO_ETAPAS_4_5 = "mantido na pasta temporária (etapas 4 e 5 pendentes)"
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
            f"{nome}: tipo do decreto não reconhecido ({ident.detalhe})",
            simular,
        )
    vistos_no_lote.add(chave_nome(nome))

    # --- Etapas 4 e 5: normalização e armazenamento -----------------------
    try:
        pdf_gerado = normalizador.gerar_pdf_normalizado(
            pdf, ident, cfg.pasta_normalizados
        )
        armazenamento.armazenar(pdf_gerado, nome, cfg.pasta_dbf, pdf, simular)
    except NotImplementedError as pendente:
        log.info(
            "%s | %s | %s (%s)",
            pdf.name,
            Destino.AGUARDANDO_ETAPAS_4_5.value,
            nome,
            ident.tipo.nome,
        )
        log.debug("pendente: %s", pendente)
        return Destino.AGUARDANDO_ETAPAS_4_5

    log.info("%s | %s | %s", pdf.name, Destino.CONCLUIDO.value, nome)
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


def capturar(
    cfg: Configuracao, inicio: date, fim: date, simular: bool = False
) -> Counter[captura.StatusDownload]:
    """Etapa 1: baixa os cadernos do período e devolve a contagem por situação."""
    modo = " [SIMULAÇÃO: sem rede e sem gravar nada]" if simular else ""
    log.info("Captura iniciada%s: de %s a %s em %s.", modo, inicio, fim, cfg.pasta_cadernos)
    resultados = captura.baixar_cadernos(
        inicio, fim, cfg.pasta_cadernos, simular=simular,
        proxy=cfg.proxy_https, ssl_estrito=cfg.ssl_estrito,
    )
    resumo: Counter[captura.StatusDownload] = Counter(r.status for r in resultados)
    for status, quantidade in resumo.most_common():
        log.info("  %3d  %s", quantidade, status.value)
    if resumo[captura.StatusDownload.FALHA]:
        log.warning("Há downloads com falha: rode de novo para tentar outra vez.")
    return resumo


def _configurar_log(pasta_logs: Path) -> None:
    pasta_logs.mkdir(parents=True, exist_ok=True)
    formato = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    destinos = (
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(pasta_logs / "pipeline.log", encoding="utf-8"),
    )
    log.setLevel(logging.INFO)
    for destino in destinos:
        destino.setFormatter(formato)
        log.addHandler(destino)


def _data_iso(texto: str) -> date:
    try:
        return date.fromisoformat(texto)
    except ValueError:
        raise argparse.ArgumentTypeError(f"data inválida: {texto!r} (use AAAA-MM-DD)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Normalização na Fonte dos decretos da CEPE (DBF/SEFAZ)."
    )
    parser.add_argument(
        "--simular",
        action="store_true",
        help="mostra o que seria feito sem mover, apagar ou gravar nada",
    )
    parser.add_argument(
        "--permitir-pasta-vazia",
        action="store_true",
        help="aceita uma pasta da DBF vazia (por padrão, é tratada como erro)",
    )
    parser.add_argument(
        "--baixar",
        action="store_true",
        help="etapa 1: baixa os cadernos do período (exige --de); não roda o lote",
    )
    parser.add_argument(
        "--de", type=_data_iso, metavar="AAAA-MM-DD", help="primeiro dia (com --baixar)"
    )
    parser.add_argument(
        "--ate",
        type=_data_iso,
        metavar="AAAA-MM-DD",
        help="último dia, inclusive (com --baixar; padrão: hoje)",
    )
    args = parser.parse_args(argv)
    if args.baixar and args.de is None:
        parser.error("--baixar exige --de AAAA-MM-DD")
    if not args.baixar and (args.de or args.ate):
        parser.error("--de e --ate só valem junto com --baixar")

    try:
        cfg = carregar_configuracao()
        _configurar_log(cfg.pasta_logs)
        if args.baixar:
            resumo = capturar(cfg, args.de, args.ate or date.today(), args.simular)
            problemas = resumo[captura.StatusDownload.FALHA] + resumo[
                captura.StatusDownload.SEM_CONEXAO
            ]
            return 1 if problemas else 0
        
        if not args.simular:
            for pasta in (cfg.pasta_temp, cfg.pasta_erros, cfg.pasta_ocr):
                pasta.mkdir(parents=True, exist_ok=True)
        executar(cfg, args.simular, args.permitir_pasta_vazia)
    
    except (ErroConfiguracao, PastaDbfIndisponivel, captura.ErroCaptura) as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 2
         
    except KeyboardInterrupt:
        print("Interrompido pelo usuário (Ctrl+C).", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())