"""Orquestração do pipeline de normalização na fonte (DBF/SEFAZ).

Três modos, independentes por enquanto:

  1. Captura (etapa 1): `--baixar --de AAAA-MM-DD [--ate AAAA-MM-DD]` baixa os
     cadernos (PDF completo do dia) para PASTA_CADERNOS e termina. Os cadernos
     ainda NÃO seguem para a etapa 2: falta o passo que separa os atos.
  2. Separação de cadernos: `--separar CADERNO.pdf` (ou uma pasta de cadernos)
     lista os atos (etapa 1b), o tipo (etapa 2), o programa da DBF e se já
     existem na base (etapa 3). Só lê. Com `--gravar`, cada ato de programa
     da DBF que a base não tem vira um PDF em PASTA_TEMP (pendentes), pronto
     para o lote (modo 3).
  3. Lote: processa os PDFs da pasta temporária (PASTA_TEMP), um ato por PDF:
     etapa 2 (identificação) -> etapa 3 (duplicidade) -> etapas 4 e 5.
     As etapas 4 e 5 ainda são esqueletos: um ato inédito permanece na
     pasta temporária (nunca é descartado antes de a etapa 5 gravar o resultado).

Uso:
    python main.py --simular                              # lote, sem mexer em nada
    python main.py                                        # lote, de verdade
    python main.py --baixar --de 2026-10-01 --simular     # mostra o que baixaria
    python main.py --baixar --de 2026-10-01 --ate 2026-10-20
    python main.py --separar "dados/cadernos/20260626_ResumoDiario.pdf"
    python main.py --separar dados/cadernos --gravar     # grava os inéditos em pendentes
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
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
from exportador import ErroExportacao, gravar_pdf_do_ato
from identificador import StatusIdentificacao, identificar
from programas import cita_programa_so_no_corpo, em_escopo, programas_do_ato
from reader import StatusLeitura, ler_pdf
from separador import AtoSeparado, ErroSeparacao, separar_caderno

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
            f"{nome}: tipo do ato não reconhecido ({ident.detalhe})",
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
    cfg: Configuracao,
    inicio: date,
    fim: date,
    simular: bool = False,
    fonte: str = captura.FONTE_PADRAO,
) -> Counter[captura.StatusDownload]:
    """Etapa 1: baixa os cadernos do período e devolve a contagem por situação."""
    modo = " [SIMULAÇÃO: sem rede e sem gravar nada]" if simular else ""
    log.info(
        "Captura iniciada%s: de %s a %s em %s (fonte: %s).",
        modo, inicio, fim, cfg.pasta_cadernos, fonte,
    )
    resultados = captura.baixar_cadernos(
        inicio, fim, cfg.pasta_cadernos, cadernos=captura.FONTES[fonte], simular=simular,
        proxy=cfg.proxy_https, ssl_estrito=cfg.ssl_estrito,
    )
    resumo: Counter[captura.StatusDownload] = Counter(r.status for r in resultados)
    for status, quantidade in resumo.most_common():
        log.info("  %3d  %s", quantidade, status.value)
    if resumo[captura.StatusDownload.FALHA]:
        log.warning("Há downloads com falha: rode de novo para tentar outra vez.")
    return resumo


@dataclass
class ResumoRelatorio:
    cadernos: int = 0
    atos: int = 0
    avisos: int = 0  # pontos de atenção (cabeçalho suspeito, fecho ausente, repetido...)
    falhas: int = 0  # atos que não puderam ser gravados em dados/pendentes
    situacoes: Counter[str] = field(default_factory=Counter)


def _carregar_indice_do_relatorio(
    cfg: Configuracao, gravar: bool, permitir_pasta_vazia: bool = False
) -> set[str] | None:
    """Índice da base da DBF. No relatório puro, a falta dela só gera aviso; ao
    gravar é erro, porque sem ela um ato que a DBF já tem seria gravado."""
    if cfg.pasta_dbf is None:
        if gravar:
            raise ErroConfiguracao(
                "PASTA_DBF não está definida: sem a base da DBF não dá para saber "
                "quais atos já existem, então nada será gravado."
            )
        log.warning("PASTA_DBF não definida; não dá para dizer o que já existe.")
        return None
    try:
        return carregar_indice(cfg.pasta_dbf, permitir_pasta_vazia)
    except PastaDbfIndisponivel as erro:
        if gravar:
            raise
        log.warning("Base da DBF indisponível; não dá para dizer o que já existe: %s", erro)
        return None


def _relatar_caderno(
    cfg: Configuracao,
    caderno: Path,
    indice: set[str] | None,
    gravar: bool,
    resumo: ResumoRelatorio,
) -> None:
    resultado = separar_caderno(caderno)
    log.info(
        "%s: %d página(s), %d ato(s) encontrado(s).",
        caderno.name, resultado.paginas, len(resultado.atos),
    )
    resumo.cadernos += 1
    resumo.atos += len(resultado.atos)
    gravados: set[str] = set()
    for ato in resultado.atos:
        nome = ato.especie.nome_arquivo(ato.numero)
        ident = identificar(ato.texto)
        tipo = ident.tipo.nome if ident.tipo else "tipo não reconhecido"
        programas = programas_do_ato(ato.texto, ato.especie)
        paginas = (
            f"p. {ato.pagina_inicial}"
            if ato.pagina_inicial == ato.pagina_final
            else f"p. {ato.pagina_inicial}-{ato.pagina_final}"
        )
        if not em_escopo(ato.especie, programas):
            situacao = "fora do escopo da DBF"
            if cita_programa_so_no_corpo(ato.texto, ato.especie):
                log.warning(
                    "%s | AVISO: cita um programa da DBF só no corpo do texto; "
                    "confira se não é um ato da DBF", nome,
                )
                resumo.avisos += 1
        elif indice is not None and ja_existe(indice, nome):
            situacao = "já existe na DBF"
        elif indice is None:
            situacao = "base da DBF não consultada"
        elif gravar:
            situacao = _gravar_inedito(cfg, ato, nome, gravados, resumo)
        else:
            situacao = "INÉDITO"
        resumo.situacoes[situacao.split(" (")[0]] += 1
        programa = "+".join(p.sigla for p in programas) or "-"
        log.info("%s | %s | %s | %s | %s", nome, paginas, tipo, programa, situacao)
        for aviso in ato.avisos:
            log.warning("%s | AVISO: %s", nome, aviso)
        resumo.avisos += bool(ato.avisos)
    for aviso in resultado.avisos:
        log.warning("AVISO: %s", aviso)
    resumo.avisos += len(resultado.avisos)


def _gravar_inedito(
    cfg: Configuracao, ato: AtoSeparado, nome: str, gravados: set[str], resumo: ResumoRelatorio
) -> str:
    destino = cfg.pasta_temp / nome
    if chave_nome(nome) in gravados:
        log.warning("%s | AVISO: repetido neste caderno; só a primeira ocorrência foi gravada", nome)
        resumo.avisos += 1
        return "INÉDITO (repetido no caderno, não gravado)"
    if destino.exists():
        return "INÉDITO (já está em pendentes)"
    try:
        gravar_pdf_do_ato(ato, destino)
    except ErroExportacao as erro:
        log.error("%s | falha ao gravar em pendentes: %s", nome, erro)
        resumo.falhas += 1
        return "INÉDITO (FALHA ao gravar)"
    gravados.add(chave_nome(nome))
    return "INÉDITO (gravado em pendentes)"


def relatorio_dos_cadernos(
    cfg: Configuracao,
    caminho: Path,
    gravar: bool = False,
    permitir_pasta_vazia: bool = False,
) -> ResumoRelatorio:
    """Lista os atos de um caderno (ou de todos os PDFs de uma pasta) e o
    que o pipeline faz com cada um.

    Sem `gravar`, só lê: nada é gravado, movido ou apagado. Com `gravar`, cada
    ato de programa da DBF que a base ainda não tem vira um PDF em
    `cfg.pasta_temp` (a pasta pendentes), sem sobrescrever nada.
    """
    if caminho.is_dir():
        cadernos = sorted(p for p in caminho.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
        if not cadernos:
            raise ErroSeparacao(f"nenhum PDF na pasta {caminho}")
    else:
        cadernos = [caminho]
    indice = _carregar_indice_do_relatorio(cfg, gravar, permitir_pasta_vazia)
    resumo = ResumoRelatorio()
    for caderno in cadernos:
        _relatar_caderno(cfg, caderno, indice, gravar, resumo)
    log.info("Total: %d caderno(s), %d ato(s).", resumo.cadernos, resumo.atos)
    for situacao, quantidade in resumo.situacoes.most_common():
        log.info("  %3d  %s", quantidade, situacao)
    if resumo.falhas:
        log.error("%d ato(s) não puderam ser gravados em pendentes.", resumo.falhas)
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
        description="Normalização na Fonte dos atos da CEPE (DBF/SEFAZ); hoje, decretos."
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
    parser.add_argument(
        "--fonte",
        choices=sorted(captura.FONTES),
        default=captura.FONTE_PADRAO,
        help="de onde baixar (com --baixar): resumo = site atual da CEPE (padrão); "
        "s3 = endereços históricos",
    )
    parser.add_argument(
        "--separar",
        type=Path,
        metavar="CADERNO.pdf",
        help="lista os atos de um caderno (ou de todos os PDFs de uma pasta) e "
        "se já existem na DBF",
    )
    parser.add_argument(
        "--gravar",
        action="store_true",
        help="com --separar: grava em PASTA_TEMP um PDF por ato inédito da DBF",
    )
    args = parser.parse_args(argv)
    if args.separar and (args.baixar or args.de or args.ate or args.simular):
        parser.error("--separar não se combina com --baixar, --de, --ate nem --simular")
    if args.gravar and not args.separar:
        parser.error("--gravar só vale junto com --separar")
    if args.baixar and args.de is None:
        parser.error("--baixar exige --de AAAA-MM-DD")
    if not args.baixar and (args.de or args.ate):
        parser.error("--de e --ate só valem junto com --baixar")

    try:
        cfg = carregar_configuracao()
        _configurar_log(cfg.pasta_logs)
        if args.separar:
            resumo = relatorio_dos_cadernos(
                cfg, args.separar, args.gravar, args.permitir_pasta_vazia
            )
            return 1 if resumo.falhas else 0
        if args.baixar:
            resumo = capturar(cfg, args.de, args.ate or date.today(), args.simular, args.fonte)
            problemas = resumo[captura.StatusDownload.FALHA] + resumo[
                captura.StatusDownload.SEM_CONEXAO
            ]
            return 1 if problemas else 0
        if not args.simular:
            for pasta in (cfg.pasta_temp, cfg.pasta_erros, cfg.pasta_ocr):
                pasta.mkdir(parents=True, exist_ok=True)
        executar(cfg, args.simular, args.permitir_pasta_vazia)
    except (
        ErroConfiguracao, PastaDbfIndisponivel, captura.ErroCaptura, ErroSeparacao
    ) as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrompido pelo usuário (Ctrl+C).", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())