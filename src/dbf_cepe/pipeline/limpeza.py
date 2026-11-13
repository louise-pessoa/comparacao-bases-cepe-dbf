"""Limpeza dos cadernos já processados: apaga o PDF do caderno e deixa no manifesto o
registro do que foi feito com ele.

Em que etapa entra: depois do lote (etapas 2 a 5) de um mês encerrado. O `--separar
--gravar` anota no manifesto da pasta (`registrar_processamento`) que o caderno foi
processado até o fim, com os decretos da DBF dele (número, página e situação). A limpeza
confere essa anotação contra o disco e o registro de publicações; não relê o caderno.

Um caderno só é apagado se tudo isto for verdade (senão fica, e o motivo é mostrado):
  1. o mês dele já acabou (e, com `mes`, é o mês pedido);
  2. o `--separar --gravar` rodou nele até o fim, sem falha ao gravar em pendentes;
  3. não tem página digitalizada (que precisaria de OCR);
  4. nenhum decreto da DBF dele ficou em revisão manual;
  5. nenhum PDF dele está em pendentes, erros, leitura tesseract ou conferir duplicata;
  6. todo decreto da DBF dele que foi para pendentes está no registro de publicações (o
     lote já o gravou, descartou ou mandou para conferir), e um "conferir duplicata" já foi
     decidido no `--conferir`.

O que fica no manifesto, no lugar do arquivo: o endereço, o tamanho, o SHA-256 e a data
do download (da captura), mais `processado_em`, `processamento` (decretos, decretos da
DBF e a situação de cada um) e `apagado_em`. O caderno apagado continua no manifesto, então
a captura não o baixa de novo; para rebaixá-lo, apague a entrada dele.

Segurança: o comando `--limpar-cadernos` só apaga depois de uma simulação com a mesma
lista (`limpeza_simulada.json`, na pasta dos cadernos). Nunca toca na base da DBF: recusa
uma pasta dentro dela. Só apaga arquivos `AAAAMMDD_*.pdf` que estão no manifesto. O
manifesto é gravado ANTES de cada arquivo ser apagado.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dbf_cepe import captura
from dbf_cepe.config import Configuracao
from dbf_cepe.leitura.separador import data_do_caderno
from dbf_cepe.pipeline.lote import listar_pdfs
from dbf_cepe.pipeline.publicacao import ler_do_pdf
from dbf_cepe.pipeline.registro import CONFERIR, Registro

log = logging.getLogger("pipeline")

NOME_SIMULACAO = "limpeza_simulada.json"
# Situações do relatório (`--separar --gravar`) em que o decreto foi para pendentes e, depois
# do lote, tem de estar no registro de publicações.
SITUACOES_PARA_PENDENTES = ("INÉDITO", "REPUBLICAÇÃO", "ANTERIOR À DA BASE", "CONFERIR DUPLICATA")
REVISAO_MANUAL = "REVISÃO MANUAL"


class ErroLimpeza(Exception):
    """Pedido de limpeza inválido (sem simulação, lista diferente, pasta da base...)."""


@dataclass
class Avaliacao:
    caderno: str
    motivos: list[str] = field(default_factory=list)  # por que fica; vazio = pode apagar

    @property
    def apagavel(self) -> bool:
        return not self.motivos


@dataclass
class ResultadoLimpeza:
    apagados: list[str] = field(default_factory=list)
    mantidos: list[Avaliacao] = field(default_factory=list)
    falhas: list[str] = field(default_factory=list)  # não puderam ser apagados (erro de disco)


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ----------------------------------------------------------------------------
# Anotação do processamento (chamada pelo --separar --gravar)
# ----------------------------------------------------------------------------


def registrar_processamento(
    caderno: Path, decretos: int, decretos_dbf: list[dict[str, Any]], falhas: int,
    paginas_digitalizadas: tuple[int, ...],
) -> bool:
    """Anota no manifesto da pasta do caderno que o `--separar --gravar` o processou até o
    fim. `decretos_dbf`: [{"numero", "pagina", "situacao"}]. Caderno fora do manifesto
    (não veio da captura) não é anotado e devolve False."""
    try:
        arquivos = captura.ler_manifesto(caderno.parent)
    except captura.ErroCaptura as erro:
        log.warning("%s: processamento não anotado (%s)", caderno.name, erro)
        return False
    entrada = arquivos.get(caderno.name)
    if entrada is None:
        return False
    entrada["processado_em"] = _agora()
    entrada["processamento"] = {
        "decretos": decretos,
        "decretos_dbf": decretos_dbf,
        "falhas": falhas,
        "paginas_digitalizadas": list(paginas_digitalizadas),
    }
    captura.gravar_manifesto(caderno.parent, arquivos)
    return True


# ----------------------------------------------------------------------------
# Avaliação
# ----------------------------------------------------------------------------


def _pendencias_em_pastas(cfg: Configuracao) -> dict[str, list[str]]:
    """Caderno -> onde há PDF dele ainda esperando alguma coisa (pelos metadados do PDF)."""
    pastas = {
        "pendentes": cfg.pasta_temp,
        "erros": cfg.pasta_erros,
        "leitura tesseract": cfg.pasta_ocr,
        "conferir duplicata": cfg.conferir_duplicata,
    }
    achados: dict[str, list[str]] = {}
    for rotulo, pasta in pastas.items():
        for pdf in listar_pdfs(pasta) if pasta.exists() else []:
            publicacao = ler_do_pdf(pdf)
            if publicacao is not None:
                achados.setdefault(publicacao.caderno, []).append(f"{pdf.name} em {rotulo}")
    return achados


def _motivos_do_registro(nome: str, decretos_dbf: list[dict[str, Any]], registro: Registro) -> list[str]:
    motivos = []
    for decreto in decretos_dbf:
        numero, pagina, situacao = decreto["numero"], decreto["pagina"], decreto["situacao"]
        if situacao == REVISAO_MANUAL:
            motivos.append(f"Decreto {numero} em revisão manual")
            continue
        if situacao not in SITUACOES_PARA_PENDENTES:
            continue  # já existia na base ou no registro: nada a esperar do lote
        entradas = [
            e for e in registro.entradas
            if e.numero == numero and e.publicacao.caderno == nome and e.publicacao.pagina == pagina
        ]
        if not entradas:
            motivos.append(f"Decreto {numero} não está no registro de publicações (o lote ainda não o processou)")
        elif any(e.situacao == CONFERIR and not e.decisao for e in entradas):  # "mantida a base" fica CONFERIR
            motivos.append(f"Decreto {numero} em conferir duplicata, sem decisão (rode --conferir)")
    return motivos


def _mes_do_caderno(nome: str) -> date | None:
    data = data_do_caderno(nome)
    return data.replace(day=1) if data else None


def avaliar(cfg: Configuracao, pasta: Path, mes: date | None = None, hoje: date | None = None) -> list[Avaliacao]:
    """Avalia cada caderno da pasta (só os do mês pedido, se houver). Só lê."""
    pasta = Path(pasta)
    if cfg.pasta_dbf is not None and pasta.resolve().is_relative_to(Path(cfg.pasta_dbf).resolve()):
        raise ErroLimpeza(f"{pasta} fica dentro da base da DBF: a limpeza nunca mexe na base")
    arquivos = captura.ler_manifesto(pasta)
    registro = Registro.carregar(cfg.registro)
    pendencias = _pendencias_em_pastas(cfg)
    mes_atual = (hoje or date.today()).replace(day=1)
    avaliacoes = []
    for caderno in sorted(p for p in pasta.glob("*.pdf") if p.is_file()):
        mes_dele = _mes_do_caderno(caderno.name)
        if mes is not None and mes_dele != mes:
            continue
        avaliacao = Avaliacao(caderno.name)
        entrada = arquivos.get(caderno.name)
        if mes_dele is None:
            avaliacao.motivos.append("sem data no nome (AAAAMMDD_...)")
        elif mes_dele >= mes_atual:
            avaliacao.motivos.append("o mês ainda não acabou")
        if entrada is None:
            avaliacao.motivos.append("não está no manifesto da pasta")
        elif "processado_em" not in entrada:
            avaliacao.motivos.append("não foi processado pelo --separar --gravar")
        else:
            processamento = entrada.get("processamento", {})
            if processamento.get("falhas"):
                avaliacao.motivos.append(f"{processamento['falhas']} decreto(s) não puderam ser gravados em pendentes")
            if processamento.get("paginas_digitalizadas"):
                avaliacao.motivos.append(f"páginas digitalizadas: {processamento['paginas_digitalizadas']}")
            avaliacao.motivos += _motivos_do_registro(caderno.name, processamento.get("decretos_dbf", []), registro)
        avaliacao.motivos += pendencias.get(caderno.name, [])
        avaliacoes.append(avaliacao)
    return avaliacoes


# ----------------------------------------------------------------------------
# Limpeza
# ----------------------------------------------------------------------------


def _ler_simulacao(pasta: Path) -> dict[str, Any] | None:
    caminho = pasta / NOME_SIMULACAO
    if not caminho.exists():
        return None
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError) as erro:
        raise ErroLimpeza(f"simulação ilegível em {caminho}: {erro}; rode de novo com --simular") from erro


def _apagar(pasta: Path, nomes: list[str], resultado: ResultadoLimpeza) -> None:
    arquivos = captura.ler_manifesto(pasta)
    for nome in nomes:
        entrada = arquivos[nome]
        entrada["apagado_em"] = _agora()
        captura.gravar_manifesto(pasta, arquivos)  # antes de apagar: o registro nunca se perde
        try:
            (pasta / nome).unlink()
        except OSError as erro:
            del entrada["apagado_em"]
            captura.gravar_manifesto(pasta, arquivos)
            log.error("%s: não foi possível apagar (%s)", nome, erro)
            resultado.falhas.append(nome)
            continue
        resultado.apagados.append(nome)


def limpar(
    cfg: Configuracao,
    pasta: Path,
    mes: date | None = None,
    simular: bool = False,
    exigir_simulacao: bool = True,
    hoje: date | None = None,
) -> ResultadoLimpeza:
    """Apaga os cadernos da pasta que cumprem todas as condições.

    Com `simular`, só mostra e grava a lista em `limpeza_simulada.json`. Sem `simular` e
    com `exigir_simulacao` (o comando `--limpar-cadernos`), só apaga se a simulação existir
    e tiver a mesma lista; senão levanta `ErroLimpeza` sem apagar nada. O lote histórico
    passa `exigir_simulacao=False`: mostra a lista no log e apaga em seguida."""
    pasta = Path(pasta)
    avaliacoes = avaliar(cfg, pasta, mes, hoje)
    apagaveis = [a.caderno for a in avaliacoes if a.apagavel]
    resultado = ResultadoLimpeza(mantidos=[a for a in avaliacoes if not a.apagavel])
    rotulo = "seria apagado" if simular else "apagar"
    for nome in apagaveis:
        log.info("%s | limpeza: %s", nome, rotulo)
    for avaliacao in resultado.mantidos:
        log.info("%s | limpeza: fica (%s)", avaliacao.caderno, "; ".join(avaliacao.motivos))
    chave_mes = f"{mes:%Y-%m}" if mes else None
    if simular:
        (pasta / NOME_SIMULACAO).write_text(
            json.dumps({"mes": chave_mes, "simulado_em": _agora(), "apagaveis": apagaveis}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        resultado.apagados = apagaveis
        return resultado
    if exigir_simulacao:
        simulacao = _ler_simulacao(pasta)
        if simulacao is None:
            raise ErroLimpeza("rode antes com --simular e confira a lista")
        if simulacao.get("mes") != chave_mes or simulacao.get("apagaveis") != apagaveis:
            raise ErroLimpeza(
                "a lista mudou desde a simulação (ou o mês é outro); rode de novo com --simular e confira"
            )
    _apagar(pasta, apagaveis, resultado)
    if exigir_simulacao:
        (pasta / NOME_SIMULACAO).unlink(missing_ok=True)
    log.info(
        "Limpeza de %s: %d caderno(s) apagado(s), %d mantido(s)%s.",
        pasta, len(resultado.apagados), len(resultado.mantidos),
        f", {len(resultado.falhas)} com falha" if resultado.falhas else "",
    )
    return resultado
