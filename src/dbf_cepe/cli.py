"""Linha de comando do pipeline de normalização na fonte (DBF/SEFAZ).

Quatro modos, independentes por enquanto:

  1. Captura (etapa 1): `--baixar --de AAAA-MM-DD [--ate AAAA-MM-DD]` baixa os
     cadernos (PDF completo do dia) para PASTA_CADERNOS e termina. Os cadernos
     ainda NÃO seguem para a etapa 2: falta o passo que separa os atos.
  2. Separação de cadernos: `--separar CADERNO.pdf` (ou uma pasta de cadernos)
     lista os atos (etapa 1b), o tipo (etapa 2), o programa da DBF e se já
     existem na base (etapa 3). Só lê. Com `--gravar`, cada ato de programa
     da DBF que a base não tem vira um PDF em PASTA_TEMP (pendentes), pronto
     para o lote (modo 3).
  3. Lote: processa os PDFs da pasta temporária (PASTA_TEMP), um ato por PDF:
     etapa 2 (identificação) -> etapa 3 (duplicidade) -> etapa 4 (normalização:
     gera o PDF limpo em PASTA_NORMALIZADOS) -> etapa 5 (grava na base da
     DBF e só então descarta o original e a cópia normalizada). Se a gravação
     falhar por E/S, o original fica em pendentes e o programa termina com
     código 1; se o conteúdo gravado não conferir, o original vai para erros.
  4. Catálogo: `--catalogar PASTA` reconhece decretos, editais, portarias, erratas
     e republicações dos cadernos e grava um CSV, um ato por linha (ver
     catalogo.py). Só lê os cadernos; não usa a configuração (.env), a base da DBF
     nem as pastas de saída do pipeline.

Uso:
    python main.py --simular                              # lote, sem mexer em nada
    python main.py                                        # lote, de verdade
    python main.py --baixar --de 2026-10-01 --simular     # mostra o que baixaria
    python main.py --baixar --de 2026-10-01 --ate 2026-10-20
    python main.py --separar "dados/cadernos/20260626_ResumoDiario.pdf"
    python main.py --separar dados/cadernos --gravar     # grava os inéditos em pendentes
    python main.py --separar dados/cadernos --comparar   # compara o texto com o da base
    python main.py --catalogar dados/cadernos             # grava dados/catalogo_atos.csv
    python main.py --conferir [--simular]                 # revisa a pasta "conferir duplicata"
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from dbf_cepe import captura
from dbf_cepe import catalogo
from dbf_cepe.pipeline import conferencia
from dbf_cepe import proxy
from dbf_cepe.config import (
    Configuracao,
    ErroConfiguracao,
    carregar_configuracao,
)
from dbf_cepe.pipeline.duplicidade import PastaDbfIndisponivel, carregar_indice
from dbf_cepe.leitura.separador import ErroSeparacao
from dbf_cepe.pipeline.lote import Destino, executar
from dbf_cepe.pipeline.registro import ErroRegistro
from dbf_cepe.pipeline.relatorio import relatorio_dos_cadernos

log = logging.getLogger("pipeline")


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
    cadernos = captura.FONTES[fonte]
    if cfg.proxy_do_ambiente:
        log.warning(
            "A variável de ambiente HTTPS_PROXY (%s) vale mais que a linha HTTPS_PROXY do .env. "
            "Se o endereço está velho, apague-a com Remove-Item Env:HTTPS_PROXY (PowerShell) "
            "e rode de novo.",
            cfg.proxy_do_ambiente,
        )
    endereco_proxy = cfg.proxy_https
    if endereco_proxy == proxy.VALOR_AUTOMATICO:
        # HTTPS_PROXY=auto: pergunta ao Windows uma vez por captura (a porta muda
        # entre sessões). Na simulação não há rede, então nem se pergunta.
        endereco_proxy = (
            None if simular else proxy.resolver_proxy(endereco_proxy, cadernos[0].url(inicio))
        )
    resultados = captura.baixar_cadernos(
        inicio, fim, cfg.pasta_cadernos, cadernos=cadernos, simular=simular,
        proxy=endereco_proxy, ssl_estrito=cfg.ssl_estrito,
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


def _indice_para_o_catalogo() -> set[str] | None:
    """Só com --com-base: lê o .env e lista a base da DBF (só leitura). Falha vira aviso."""
    try:
        return carregar_indice(carregar_configuracao().pasta_dbf)
    except (ErroConfiguracao, PastaDbfIndisponivel) as erro:
        print(f"Aviso: base da DBF não consultada ({erro}); erratas sem outro sinal ficam a confirmar.",
              file=sys.stderr)
        return None


def catalogar_cadernos(caminho: Path, saida: Path | None = None, com_base: bool = False) -> int:
    """Modo `--catalogar`: grava o CSV e imprime o resumo. Só lê os cadernos. Sem `com_base` não
    toca em configuração nem na base da DBF; com ele, só LÊ a lista de arquivos da base (para o
    escopo das erratas). Nunca grava fora do CSV de saída."""
    destino = saida or catalogo.PASTA_PADRAO_DO_CSV / catalogo.NOME_PADRAO_DO_CSV
    indice = _indice_para_o_catalogo() if com_base else None
    try:
        resultado = catalogo.catalogar(caminho, indice)
    except ErroSeparacao as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 2
    catalogo.gravar_csv(resultado.linhas, destino)
    print(catalogo.resumo_em_texto(resultado, destino))
    return 1 if resultado.cadernos_com_falha else 0


def main(argv: list[str] | None = None) -> int:
    """Interprete os modos de captura, separação, lote e catálogo e retorne seu status.

    O código 0 indica conclusão sem falhas; 1 indica falha de captura/catalogação ou
    ato que não pôde ser gravado; 2 indica argumento inválido, configuração inválida
    ou erro de leitura que impede o modo solicitado.
    """
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
    parser.add_argument(
        "--comparar",
        action="store_true",
        help="com --separar: compara o texto de cada decreto que já existe na DBF com o "
        "PDF da base (só leitura; serve para achar republicações)",
    )
    parser.add_argument(
        "--conferir",
        action="store_true",
        help="revisa os PDFs de 'dados/conferir duplicata': mostra as diferenças para a base e "
        "pergunta se usa o texto do Diário (substitui na base, com cópia) ou mantém a base; "
        "com --simular, só mostra",
    )
    parser.add_argument(
        "--catalogar",
        type=Path,
        metavar="PASTA",
        help="cataloga os atos (decreto, edital, portaria, errata, republicação) de um caderno "
        "ou de todos os PDFs de uma pasta, em CSV (só lê; não usa .env nem a base da DBF)",
    )
    parser.add_argument(
        "--saida",
        type=Path,
        metavar="ARQUIVO.csv",
        help=f"com --catalogar: onde gravar o CSV (padrão: {catalogo.PASTA_PADRAO_DO_CSV / catalogo.NOME_PADRAO_DO_CSV})",
    )
    parser.add_argument(
        "--com-base",
        action="store_true",
        help="com --catalogar: lê a lista de arquivos da base da DBF (.env) para decidir o escopo "
        "das erratas; só leitura",
    )
    args = parser.parse_args(argv)
    if args.com_base and not args.catalogar:
        parser.error("--com-base só vale junto com --catalogar")
    if args.catalogar and (
        args.separar or args.baixar or args.de or args.ate or args.simular
        or args.gravar or args.comparar
    ):
        parser.error("--catalogar não se combina com outros modos nem com suas opções")
    if args.saida and not args.catalogar:
        parser.error("--saida só vale junto com --catalogar")
    if args.conferir and (args.separar or args.baixar or args.de or args.ate or args.gravar or args.comparar):
        parser.error("--conferir só se combina com --simular")
    if args.separar and (args.baixar or args.de or args.ate or args.simular):
        parser.error("--separar não se combina com --baixar, --de, --ate nem --simular")
    if args.gravar and not args.separar:
        parser.error("--gravar só vale junto com --separar")
    if args.comparar and not args.separar:
        parser.error("--comparar só vale junto com --separar")
    if args.baixar and args.de is None:
        parser.error("--baixar exige --de AAAA-MM-DD")
    if not args.baixar and (args.de or args.ate):
        parser.error("--de e --ate só valem junto com --baixar")

    if args.catalogar:
        return catalogar_cadernos(args.catalogar, args.saida, args.com_base)

    try:
        cfg = carregar_configuracao()
        _configurar_log(cfg.pasta_logs)
        if args.conferir:
            conferencia.conferir(cfg, args.simular)
            return 0
        if args.separar:
            resumo = relatorio_dos_cadernos(
                cfg, args.separar, args.gravar, args.permitir_pasta_vazia, args.comparar
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
        resumo_lote = executar(cfg, args.simular, args.permitir_pasta_vazia)
        if resumo_lote[Destino.FALHA_AO_GRAVAR]:
            return 1  # decretos ficaram em pendentes; vale rodar de novo
    except (
        ErroConfiguracao, PastaDbfIndisponivel, captura.ErroCaptura, ErroSeparacao, ErroRegistro
    ) as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrompido pelo usuário (Ctrl+C).", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
