"""Etapa 1 — captura dos cadernos (PDF completo do dia) publicados pela CEPE.

Decisão do projeto: os decretos sempre estão no PDF completo do dia; editais,
portarias e erratas podem aparecer também como arquivos isolados por órgão.
Por isso baixamos APENAS os cadernos completos, o que evita o mesmo decreto
chegar duas vezes por caminhos diferentes.

Os cadernos são arquivos públicos com endereço previsível, em duas "fontes":

  resumo (padrão; dias recentes, a confirmar na rede da DBF):
    https://diariooficial.cepe.com.br/1/arquivos/resumoDiario/{aaaa-mm-dd}/
        {aaaa-mm-dd}.pdf     (Poder Executivo do dia)
        {aaaa-mm-dd}E.pdf    (edição extra)

  s3 (histórico; respondeu 403 para 19 a 22/10/2026):
    https://cepebr-prod.s3.amazonaws.com/1/cadernos/{ano}/{aaaammdd}/
        1-PoderExecutivo/PoderExecutivo({aaaammdd}).pdf
        1-PoderExecutivoExtra/PoderExecutivoExtra({aaaammdd}).pdf
        6-PoderLegislativo/PoderLegislativo({aaaammdd}).pdf

Padrão do S3 conferido em endereços publicados: Executivo em 2026 (janeiro a
maio), Extra e Legislativo até 2025. Padrão "resumo" visto em endereços
publicados em julho, setembro e outubro de 2026. Se `baixar_cadernos` não
encontrar nenhuma edição num período com dias úteis, ele avisa no log: abra um
caderno recente no site da CEPE, copie o endereço do PDF e compare com
`Caderno.modelo_url`. As fontes não devem ser misturadas no mesmo período: o
mesmo dia existiria duas vezes (com nomes de arquivo diferentes).

Cadernos NÃO são entregues à etapa 2 (que espera um ato por PDF). Ficam em
`pasta_cadernos`, aguardando o passo de separação dos decretos (ainda não
implementado).

Cuidados com o download (o leitor de PDF não detecta arquivo truncado):
  - só https e só o host permitido; redirecionamentos são recusados;
  - tamanho conferido contra o Content-Length e limite máximo por arquivo;
  - cabeçalho "%PDF-" e abertura pelo PyMuPDF antes de entregar o arquivo;
  - grava em ".part" e só então renomeia; nunca deixa arquivo pela metade;
  - manifesto JSON: o que já foi baixado não é baixado de novo;
  - nome do arquivo montado só a partir da data e do nome do caderno (nada
    vindo do servidor entra no caminho).

403 e 404 são lidos como "não houve edição nesse dia": o S3 responde 403 para
objeto inexistente quando o bucket não permite listagem. Não há como distinguir
isso de um bloqueio real; daí o aviso do log quando nada é encontrado.
"""

from __future__ import annotations

import gc
import hashlib
import http.client
import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pymupdf

log = logging.getLogger("pipeline")

HOSTS_PERMITIDOS = frozenset({"cepebr-prod.s3.amazonaws.com", "diariooficial.cepe.com.br"})
USER_AGENT = "comparacao-bases-CEPE-DBF (DBF/SEFAZ-PE)"
NOME_MANIFESTO = "manifesto.json"

TIMEOUT_SEGUNDOS = 30
TENTATIVAS = 3
# Sem conexão (timeout, DNS, firewall): repetir muito só atrasa o aviso.
TENTATIVAS_SEM_CONEXAO = 2
# Cadernos seguidos sem conexão que fazem o lote desistir do resto do período.
LIMITE_SEM_CONEXAO = 2
ESPERA_ENTRE_TENTATIVAS = 2.0  # multiplicada pelo número da tentativa
PAUSA_ENTRE_REQUISICOES = 0.5  # cortesia com o servidor da CEPE
MAX_BYTES = 300 * 1024 * 1024  # limite por caderno (proteção de disco)
MAX_DIAS = 366  # proteção contra período digitado errado
TAMANHO_BLOCO = 1024 * 1024


class ErroCaptura(Exception):
    """Pedido de captura inválido ou manifesto ilegível."""


@dataclass(frozen=True)
class Caderno:
    """Um tipo de caderno publicado todos os dias (ou quando houver edição)."""

    nome: str  # usado no nome do arquivo salvo, ex.: "PoderExecutivo"
    modelo_url: str  # aceita {ano}, {data} (aaaammdd) e {data_iso} (aaaa-mm-dd)

    def url(self, dia: date) -> str:
        return self.modelo_url.format(
            ano=dia.year, data=f"{dia:%Y%m%d}", data_iso=dia.isoformat()
        )

    def nome_arquivo(self, dia: date) -> str:
        return f"{dia:%Y%m%d}_{self.nome}.pdf"


_BASE = "https://cepebr-prod.s3.amazonaws.com/1/cadernos/{ano}/{data}"

PODER_EXECUTIVO = Caderno(
    "PoderExecutivo", _BASE + "/1-PoderExecutivo/PoderExecutivo({data}).pdf"
)
PODER_EXECUTIVO_EXTRA = Caderno(
    "PoderExecutivoExtra",
    _BASE + "/1-PoderExecutivoExtra/PoderExecutivoExtra({data}).pdf",
)

PODER_LEGISLATIVO = Caderno(
    "PoderLegislativo", _BASE + "/6-PoderLegislativo/PoderLegislativo({data}).pdf"
)

_BASE_RESUMO = "https://diariooficial.cepe.com.br/1/arquivos/resumoDiario/{data_iso}"

RESUMO_DIARIO = Caderno("ResumoDiario", _BASE_RESUMO + "/{data_iso}.pdf")
RESUMO_DIARIO_EXTRA = Caderno("ResumoDiarioExtra", _BASE_RESUMO + "/{data_iso}E.pdf")

FONTES: dict[str, tuple[Caderno, ...]] = {
    "resumo": (RESUMO_DIARIO, RESUMO_DIARIO_EXTRA),
    "s3": (PODER_EXECUTIVO, PODER_EXECUTIVO_EXTRA, PODER_LEGISLATIVO),
}
FONTE_PADRAO = "resumo"
CADERNOS_ATIVOS: tuple[Caderno, ...] = FONTES[FONTE_PADRAO]


class StatusDownload(Enum):
    BAIXADO = "baixado"
    JA_BAIXADO = "já baixado (manifesto)"
    SEM_EDICAO = "sem edição nesse dia (403/404)"
    SEM_CONEXAO = "sem conexão com o servidor"
    FALHA = "falha"
    SIMULADO = "seria baixado (simulação)"


@dataclass(frozen=True)
class ResultadoDownload:
    caderno: Caderno
    dia: date
    status: StatusDownload
    caminho: Path | None = None
    detalhe: str = ""


class _Falha(Exception):
    def __init__(self, motivo: str, repetir: bool) -> None:
        super().__init__(motivo)
        self.repetir = repetir


# Abridor: recebe a URL e devolve algo que se comporta como a resposta do
# urllib (gerenciador de contexto com .status, .headers e .read(n)). É
# injetável para os testes não dependerem de rede.
Abridor = Callable[[str], AbstractContextManager[Any]]


class _SemRedirecionamento(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise urllib.error.HTTPError(
            req.full_url, code, f"redirecionamento recusado para {newurl}", headers, fp
        )


def _contexto_ssl(estrito: bool) -> ssl.SSLContext:
    """Contexto TLS padrão (usa as autoridades de certificação do Windows).

    A partir do Python 3.13 a verificação é "estrita" (RFC 5280) e recusa
    certificados da cadeia sem Authority Key Identifier, como os emitidos por
    alguns proxies de inspeção de HTTPS. Com `estrito=False` só essa checagem
    de conformidade é desligada: a cadeia continua validada contra as
    autoridades confiáveis e o nome do servidor continua conferido.
    """
    contexto = ssl.create_default_context()
    if not estrito:
        contexto.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return contexto


def _criar_opener(proxy: str | None, ssl_estrito: bool) -> urllib.request.OpenerDirector:
    manipuladores: list[Any] = [
        _SemRedirecionamento(),
        urllib.request.HTTPSHandler(context=_contexto_ssl(ssl_estrito)),
    ]
    if proxy:
        manipuladores.insert(0, urllib.request.ProxyHandler({"https": proxy, "http": proxy}))
    return urllib.request.build_opener(*manipuladores)


def criar_abridor(
    timeout: float = TIMEOUT_SEGUNDOS,
    proxy: str | None = None,
    ssl_estrito: bool = True,
) -> Abridor:
    """Abridor de URLs. Sem `proxy`, o urllib usa os proxies do ambiente
    (variáveis HTTPS_PROXY/NO_PROXY e configurações manuais do Windows). Não lê
    proxy automático (PAC/WPAD): nesse caso informe o endereço em `proxy`."""
    opener = _criar_opener(proxy, ssl_estrito)

    def abrir(url: str) -> AbstractContextManager[Any]:
        pedido = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/pdf"}
        )
        return opener.open(pedido, timeout=timeout)

    return abrir


# ----------------------------------------------------------------------------
# Manifesto
# ----------------------------------------------------------------------------


def _ler_manifesto(caminho: Path) -> dict[str, dict[str, Any]]:
    if not caminho.exists():
        return {}
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        arquivos = dados["arquivos"]
        if not isinstance(arquivos, dict):
            raise ValueError("'arquivos' não é um objeto")
        return arquivos
    except (OSError, ValueError, KeyError, TypeError) as erro:
        # Baixar de novo é inofensivo; o arquivo ruim é guardado para análise.
        guardado = caminho.with_name(caminho.name + ".invalido")
        os.replace(caminho, guardado)
        log.warning(
            "Manifesto ilegível (%s); guardado como %s. Será criado um novo.",
            erro,
            guardado.name,
        )
        return {}


def _gravar_manifesto(caminho: Path, arquivos: dict[str, dict[str, Any]]) -> None:
    temporario = caminho.with_name(caminho.name + ".tmp")
    temporario.write_text(
        json.dumps({"versao": 1, "arquivos": arquivos}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporario, caminho)


# ----------------------------------------------------------------------------
# Download de um caderno
# ----------------------------------------------------------------------------


def _validar_url(url: str) -> None:
    partes = urlsplit(url)
    if partes.scheme != "https" or partes.hostname not in HOSTS_PERMITIDOS:
        raise ErroCaptura(f"URL fora da lista permitida (https + host): {url}")


def _receber(url: str, destino: Path, abrir: Abridor, max_bytes: int) -> tuple[int, str]:
    """Baixa para `destino` e devolve (bytes, sha256). Levanta _Falha ou erro de rede."""
    with abrir(url) as resposta:
        if resposta.status != 200:
            raise _Falha(f"HTTP {resposta.status}", repetir=False)
        bruto = resposta.headers.get("Content-Length")
        try:
            esperado = int(bruto) if bruto is not None else None
        except ValueError:
            esperado = None
        if esperado is None:
            raise _Falha(
                "resposta sem Content-Length válido (integridade não verificável)",
                repetir=False,
            )
        if esperado > max_bytes:
            raise _Falha(
                f"arquivo de {esperado} bytes excede o limite de {max_bytes}",
                repetir=False,
            )
        resumo = hashlib.sha256()
        recebido = 0
        with destino.open("wb") as saida:
            while bloco := resposta.read(TAMANHO_BLOCO):
                recebido += len(bloco)
                if recebido > max_bytes:
                    raise _Falha("recebeu mais bytes que o limite", repetir=False)
                resumo.update(bloco)
                saida.write(bloco)
    if recebido != esperado:
        raise _Falha(
            f"download incompleto: {recebido} de {esperado} bytes", repetir=True
        )
    return recebido, resumo.hexdigest()


def _validar_pdf(caminho: Path) -> None:
    """Confere cabeçalho e abertura. NÃO detecta truncamento (ver módulo)."""
    with caminho.open("rb") as arquivo:
        if arquivo.read(5) != b"%PDF-":
            raise _Falha("o conteúdo não é um PDF", repetir=False)
    problema = ""
    try:
        with pymupdf.open(caminho) as doc:
            if doc.needs_pass:
                problema = "PDF protegido por senha"
            elif doc.page_count == 0:
                problema = "PDF sem páginas"
    except Exception as erro:
        problema = f"PDF não abre: {type(erro).__name__}: {erro}"
    # Levantado FORA do except: no Windows, a exceção do PyMuPDF segura o
    # arquivo aberto enquanto estiver viva, e isso impediria apagar o ".part".
    if problema:
        raise _Falha(problema, repetir=False)


def _remover(caminho: Path) -> None:
    """Apaga o arquivo parcial. No Windows, um identificador ainda aberto
    (WinError 32) é liberado por uma coleta de lixo; se mesmo assim falhar, o
    ".part" fica e será sobrescrito na próxima tentativa."""
    try:
        caminho.unlink(missing_ok=True)
    except PermissionError:
        gc.collect()
        try:
            caminho.unlink(missing_ok=True)
        except OSError as erro:
            log.warning("%s | não foi possível apagar o arquivo parcial: %s", caminho.name, erro)


def _baixar_um(
    caderno: Caderno,
    dia: date,
    pasta: Path,
    manifesto: dict[str, dict[str, Any]],
    abrir: Abridor,
    esperar: Callable[[float], None],
    tentativas: int,
    max_bytes: int,
) -> ResultadoDownload:
    url = caderno.url(dia)
    _validar_url(url)
    nome = caderno.nome_arquivo(dia)
    final = pasta / nome
    parcial = pasta / (nome + ".part")

    def resultado(status: StatusDownload, detalhe: str = "") -> ResultadoDownload:
        caminho = final if status is StatusDownload.BAIXADO else None
        return ResultadoDownload(caderno, dia, status, caminho, detalhe)

    for tentativa in range(1, tentativas + 1):
        ultima = tentativa == tentativas
        try:
            tamanho, sha256 = _receber(url, parcial, abrir, max_bytes)
            _validar_pdf(parcial)
        except urllib.error.HTTPError as erro:
            _remover(parcial)
            if erro.code in (403, 404):
                return resultado(StatusDownload.SEM_EDICAO, f"HTTP {erro.code}")
            if erro.code >= 500 and not ultima:
                esperar(ESPERA_ENTRE_TENTATIVAS * tentativa)
                continue
            return resultado(StatusDownload.FALHA, f"HTTP {erro.code}: {erro.reason}")
        except _Falha as falha:
            _remover(parcial)
            if falha.repetir and not ultima:
                esperar(ESPERA_ENTRE_TENTATIVAS * tentativa)
                continue
            return resultado(StatusDownload.FALHA, str(falha))
        except urllib.error.URLError as erro:  # não conectou: DNS, timeout, firewall, SSL
            _remover(parcial)
            if tentativa < min(tentativas, TENTATIVAS_SEM_CONEXAO):
                esperar(ESPERA_ENTRE_TENTATIVAS * tentativa)
                continue
            return resultado(
                StatusDownload.SEM_CONEXAO, f"{type(erro.reason).__name__}: {erro.reason}"
            )
        except KeyboardInterrupt:
            _remover(parcial)  # Ctrl+C não deixa .part para trás
            raise
        except (OSError, http.client.HTTPException) as erro:  # queda no meio do download, disco
            _remover(parcial)
            if not ultima:
                esperar(ESPERA_ENTRE_TENTATIVAS * tentativa)
                continue
            return resultado(StatusDownload.FALHA, f"{type(erro).__name__}: {erro}")
        os.replace(parcial, final)
        manifesto[nome] = {
            "url": url,
            "bytes": tamanho,
            "sha256": sha256,
            "baixado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        return resultado(StatusDownload.BAIXADO)
    return resultado(StatusDownload.FALHA, "sem tentativas")  # inalcançável com tentativas >= 1


# ----------------------------------------------------------------------------
# Período
# ----------------------------------------------------------------------------


def dias_do_periodo(inicio: date, fim: date) -> list[date]:
    if inicio > fim:
        raise ErroCaptura(f"período invertido: {inicio} é posterior a {fim}")
    total = (fim - inicio).days + 1
    if total > MAX_DIAS:
        raise ErroCaptura(
            f"período de {total} dias excede o máximo de {MAX_DIAS}; divida em partes"
        )
    return [inicio + timedelta(days=i) for i in range(total)]


def baixar_cadernos(
    inicio: date,
    fim: date,
    pasta_cadernos: Path,
    *,
    cadernos: tuple[Caderno, ...] = CADERNOS_ATIVOS,
    simular: bool = False,
    abrir: Abridor | None = None,
    esperar: Callable[[float], None] | None = None,
    tentativas: int = TENTATIVAS,
    max_bytes: int = MAX_BYTES,
    pausa: float = PAUSA_ENTRE_REQUISICOES,
    proxy: str | None = None,
    ssl_estrito: bool = True,
) -> list[ResultadoDownload]:
    """Baixa os cadernos do período (inclusive) para `pasta_cadernos`.

    Em simulação não há rede nem escrita: apenas indica o que seria baixado.
    Falhas de um caderno não interrompem os demais, exceto a falta de conexão:
    depois de LIMITE_SEM_CONEXAO cadernos seguidos sem conexão, o restante do
    período é cancelado (senão cada caderno custaria minutos de espera).
    """
    dias = dias_do_periodo(inicio, fim)
    tentativas = max(1, tentativas)
    esperar = esperar or time.sleep  # resolvido na chamada (facilita testar)
    caminho_manifesto = pasta_cadernos / NOME_MANIFESTO
    manifesto = _ler_manifesto(caminho_manifesto)
    if not simular:
        pasta_cadernos.mkdir(parents=True, exist_ok=True)
        if abrir is None:
            abrir = criar_abridor(proxy=proxy, ssl_estrito=ssl_estrito)
            if not ssl_estrito:
                log.warning(
                    "SSL_ESTRITO desligado: certificados sem Authority Key Identifier "
                    "são aceitos. A cadeia e o nome do servidor continuam verificados."
                )
            if proxy:
                destino = urlsplit(proxy)
                log.info("Usando o proxy %s:%s", destino.hostname, destino.port or "(porta padrão)")

    resultados: list[ResultadoDownload] = []
    houve_requisicao = False
    sem_conexao_seguidos = 0
    for dia in dias:
        if sem_conexao_seguidos >= LIMITE_SEM_CONEXAO:
            break
        for caderno in cadernos:
            if sem_conexao_seguidos >= LIMITE_SEM_CONEXAO:
                break
            nome = caderno.nome_arquivo(dia)
            if nome in manifesto:
                res = ResultadoDownload(caderno, dia, StatusDownload.JA_BAIXADO)
            elif simular:
                _validar_url(caderno.url(dia))
                res = ResultadoDownload(
                    caderno, dia, StatusDownload.SIMULADO, detalhe=caderno.url(dia)
                )
            else:
                if houve_requisicao:
                    esperar(pausa)
                houve_requisicao = True
                res = _baixar_um(
                    caderno, dia, pasta_cadernos, manifesto, abrir,  # type: ignore[arg-type]
                    esperar, tentativas, max_bytes,
                )
                if res.status is StatusDownload.BAIXADO:
                    _gravar_manifesto(caminho_manifesto, manifesto)
                if res.status is StatusDownload.SEM_CONEXAO:
                    sem_conexao_seguidos += 1
                else:
                    sem_conexao_seguidos = 0
            log.info("%s | %s | %s", nome, res.status.value, res.detalhe or caderno.url(dia))
            resultados.append(res)

    if sem_conexao_seguidos >= LIMITE_SEM_CONEXAO:
        log.error(
            "Sem conexão com %s em %d cadernos seguidos: o restante do período foi "
            "cancelado. Se o navegador abre o link, a rede provavelmente exige proxy "
            "para o Python (informe-o em HTTPS_PROXY no .env) ou bloqueia o python.exe. "
            "O que já foi baixado continua no manifesto.",
            urlsplit(resultados[-1].caderno.url(resultados[-1].dia)).hostname,
            sem_conexao_seguidos,
        )
    _avisar_se_nada_encontrado(resultados)
    return resultados


def _avisar_se_nada_encontrado(resultados: list[ResultadoDownload]) -> None:
    sem_edicao = sum(r.status is StatusDownload.SEM_EDICAO for r in resultados)
    encontrados = sum(
        r.status in (StatusDownload.BAIXADO, StatusDownload.JA_BAIXADO) for r in resultados
    )
    # 5 ou mais respostas "sem edição" e nenhum caderno encontrado: provável
    # padrão de URL errado ou bloqueio, não ausência real de publicações.
    if encontrados == 0 and sem_edicao >= 5:
        log.warning(
            "Nenhum caderno encontrado em %d consultas (403/404). Confira o padrão "
            "da URL em captura.py com um caderno recente do site da CEPE.",
            sem_edicao,
        )