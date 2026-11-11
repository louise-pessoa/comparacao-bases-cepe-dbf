"""Ferramenta auxiliar: roda o lote histórico de vários meses em sequência, um mês por vez,
e para no primeiro sinal de problema.

Para cada mês, em ordem cronológica, faz o mesmo que o passo a passo de docs/uso.md
("Lote histórico"):

    1. baixa os cadernos em dados/historico/AAAA-MM (--fonte s3 até 05/2026, resumo depois);
    2. --separar --comparar (só lê);
    3. --separar --gravar (grava em dados/pendentes);
    4. lote simulado;
    5. lote de verdade (só com --executar).

Para ANTES de gravar qualquer coisa do mês quando:
  - o download não trouxe nenhum caderno, ou houve falha / falta de conexão;
  - algum decreto da DBF foi reprovado no critério da leitura em cascata (texto cortado ou
    misturado: revisão manual);
  - algum decreto da DBF que a base já tem ficou com similaridade abaixo de 95%;
  - algum decreto não pôde ser gravado em pendentes;
  - a simulação do lote mandaria algo para erros ou falharia ao gravar.

E não começa se `dados/pendentes` já tiver PDFs (sobra de uma rodada anterior).

O que vai para "conferir duplicata" NÃO para a sequência: é anotado no resumo, e a
conferência fica para o fim (`python main.py --conferir`).

Uso (da raiz do repositório, com o .env apontando para a base certa):
    python -m ferramentas.lote_historico --de 2020-03 --ate 2020-06              # só simula
    python -m ferramentas.lote_historico --de 2020-03 --ate 2020-06 --executar   # grava na base

Sem --executar, cada mês é baixado, separado e simulado, mas nada vai para a base e
`dados/pendentes` é esvaziado no fim do mês (os PDFs gerados ali só serviam à simulação).
Sai com código 0 se todos os meses terminaram, 1 se parou num mês, 2 em erro de uso.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path

from dbf_cepe import captura
from dbf_cepe.cli import _configurar_log, capturar
from dbf_cepe.config import Configuracao, ErroConfiguracao, carregar_configuracao
from dbf_cepe.pipeline.lote import Destino, executar, listar_pdfs
from dbf_cepe.pipeline.registro import ErroRegistro
from dbf_cepe.pipeline.relatorio import ResumoRelatorio, relatorio_dos_cadernos

log = logging.getLogger("pipeline")

PASTA_HISTORICO = Path("dados/historico")
# Até este mês (inclusive) o histórico vem do S3; depois, do site atual da CEPE.
ULTIMO_MES_DO_S3 = date(2026, 5, 1)
SIMILARIDADE_MINIMA = 0.95


class ErroDeUso(Exception):
    pass


@dataclass
class Mes:
    inicio: date
    cadernos_baixados: int = 0
    resumo_separacao: ResumoRelatorio | None = None
    resumo_gravacao: ResumoRelatorio | None = None
    lote: Counter[Destino] = field(default_factory=Counter)
    parada: str = ""  # motivo da parada; vazio = mês concluído

    @property
    def nome(self) -> str:
        return f"{self.inicio:%Y-%m}"

    @property
    def fim(self) -> date:
        proximo = (self.inicio.replace(day=28) + timedelta(days=4)).replace(day=1)
        return proximo - timedelta(days=1)

    @property
    def fonte(self) -> str:
        return "s3" if self.inicio <= ULTIMO_MES_DO_S3 else "resumo"


def meses(de: date, ate: date) -> list[date]:
    if ate < de:
        raise ErroDeUso("--ate é anterior a --de")
    lista, atual = [], de.replace(day=1)
    while atual <= ate:
        lista.append(atual)
        atual = (atual.replace(day=28) + timedelta(days=4)).replace(day=1)
    return lista


def _mes_iso(texto: str) -> date:
    try:
        return date.fromisoformat(texto + "-01")
    except ValueError:
        raise argparse.ArgumentTypeError(f"mês inválido: {texto!r} (use AAAA-MM)")


def motivos_de_parada_na_separacao(resumo: ResumoRelatorio) -> list[str]:
    motivos = [f"decreto da DBF reprovado no critério de leitura: {r}" for r in resumo.reprovados_dbf]
    for nome, comparado in resumo.comparacoes:
        if comparado.lido and not comparado.identico and comparado.similaridade < SIMILARIDADE_MINIMA:
            motivos.append(f"{nome}: similaridade {comparado.similaridade:.2%} com o arquivo da base")
    return motivos


def motivos_de_parada_no_lote(simulacao: Counter[Destino]) -> list[str]:
    motivos = []
    for destino in (Destino.ENVIADO_ERROS, Destino.ENVIADO_OCR, Destino.FALHA_AO_GRAVAR):
        if simulacao[destino]:
            motivos.append(f"{simulacao[destino]} PDF(s) com destino \"{destino.value}\" na simulação")
    return motivos


def rodar_mes(cfg: Configuracao, mes: Mes, executar_de_verdade: bool) -> Mes:
    pasta = PASTA_HISTORICO / mes.nome
    log.info("===== %s (fonte %s) =====", mes.nome, mes.fonte)

    downloads = capturar(replace(cfg, pasta_cadernos=pasta), mes.inicio, mes.fim, False, mes.fonte)
    mes.cadernos_baixados = downloads[captura.StatusDownload.BAIXADO] + downloads[captura.StatusDownload.JA_BAIXADO]
    if downloads[captura.StatusDownload.FALHA] or downloads[captura.StatusDownload.SEM_CONEXAO]:
        mes.parada = "download com falha ou sem conexão (rode de novo; o manifesto não baixa o que já veio)"
        return mes
    if not mes.cadernos_baixados:
        mes.parada = "nenhum caderno no mês: confira a rede, o proxy e a fonte"
        return mes

    mes.resumo_separacao = relatorio_dos_cadernos(cfg, pasta, comparar=True)
    motivos = motivos_de_parada_na_separacao(mes.resumo_separacao)
    if motivos:
        mes.parada = "; ".join(motivos)
        return mes

    mes.resumo_gravacao = relatorio_dos_cadernos(cfg, pasta, gravar=True)
    if mes.resumo_gravacao.falhas:
        mes.parada = f"{mes.resumo_gravacao.falhas} decreto(s) não puderam ser gravados em pendentes"
        return mes

    simulacao = executar(cfg, simular=True)
    motivos = motivos_de_parada_no_lote(simulacao)
    if motivos:
        mes.parada = "; ".join(motivos)
        return mes
    if executar_de_verdade:
        mes.lote = executar(cfg)
        if mes.lote[Destino.FALHA_AO_GRAVAR] or mes.lote[Destino.ENVIADO_ERROS]:
            mes.parada = "o lote de verdade teve falha ao gravar ou mandou PDF para erros"
    else:
        mes.lote = simulacao
        for pdf in listar_pdfs(cfg.pasta_temp):  # só serviam à simulação
            pdf.unlink()
    return mes


def resumo_do_mes(mes: Mes, executado: bool = True) -> str:
    s = mes.resumo_gravacao or mes.resumo_separacao
    situacoes = s.situacoes if s else Counter()
    partes = [
        f"{mes.cadernos_baixados} caderno(s)",
        f"{s.atos if s else 0} decreto(s)",
        f"inéditos {situacoes['INÉDITO'] + situacoes['REPUBLICAÇÃO'] + situacoes['ANTERIOR À DA BASE']}",
        f"já existem {situacoes['já existe na DBF'] + situacoes['já registrado']}",
        f"conferir {situacoes['CONFERIR DUPLICATA']}",
        f"{'gravados na base' if executado else 'iriam para a base'} {mes.lote[Destino.CONCLUIDO]}",
    ]
    estado = f"PAROU: {mes.parada}" if mes.parada else "ok"
    return f"{mes.nome}: {', '.join(partes)} | {estado}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--de", type=_mes_iso, required=True, metavar="AAAA-MM", help="primeiro mês")
    parser.add_argument("--ate", type=_mes_iso, required=True, metavar="AAAA-MM", help="último mês, inclusive")
    parser.add_argument("--executar", action="store_true",
                        help="grava na base da DBF (sem isto, cada mês só é baixado, separado e simulado)")
    args = parser.parse_args(argv)
    try:
        lista = meses(args.de, args.ate)
        cfg = carregar_configuracao()
        if cfg.pasta_dbf is None:
            raise ErroConfiguracao("PASTA_DBF não está definida no .env")
        _configurar_log(cfg.pasta_logs)
        sobra = listar_pdfs(cfg.pasta_temp)
        if sobra:
            raise ErroDeUso(
                f"{cfg.pasta_temp} tem {len(sobra)} PDF(s) de uma rodada anterior; rode o lote "
                "(python main.py) ou esvazie a pasta antes"
            )
    except (ErroDeUso, ErroConfiguracao) as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 2
    modo = "GRAVANDO NA BASE" if args.executar else "SIMULAÇÃO (nada vai para a base)"
    log.info("Lote histórico de %s a %s: %d mês(es), %s; base: %s",
             lista[0].strftime("%Y-%m"), lista[-1].strftime("%Y-%m"), len(lista), modo, cfg.pasta_dbf)
    feitos: list[Mes] = []
    try:
        for inicio in lista:
            mes = rodar_mes(cfg, Mes(inicio), args.executar)
            feitos.append(mes)
            log.info("%s", resumo_do_mes(mes, args.executar))
            if mes.parada:
                break
    except (ErroRegistro, ErroConfiguracao, captura.ErroCaptura) as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrompido (Ctrl+C). O mês em andamento pode ter PDFs em dados/pendentes.", file=sys.stderr)
        return 130
    print("\nResumo do lote histórico:")
    for mes in feitos:
        print(f"  {resumo_do_mes(mes, args.executar)}")
    conferir = sum((m.resumo_gravacao.situacoes["CONFERIR DUPLICATA"] if m.resumo_gravacao else 0) for m in feitos)
    if conferir and args.executar:
        print(f"\n{conferir} decreto(s) foram para 'conferir duplicata': rode python main.py --conferir")
    if feitos and feitos[-1].parada:
        print(f"\nParou em {feitos[-1].nome}. Resolva e rode de novo a partir de --de {feitos[-1].nome}.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
