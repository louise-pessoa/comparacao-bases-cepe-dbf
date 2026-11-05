"""Etapa 1 (rede) — descoberta do proxy do Windows.

A rede da DBF entrega o proxy por um arquivo de configuração automática (PAC):
o Windows resolve, para cada endereço, um proxy local como `http://127.0.0.1:49771`,
e a porta muda de uma sessão para outra. O Python (urllib) não avalia PAC, então
uma porta fixa no `.env` envelhece e a captura volta a dar timeout.

Com `HTTPS_PROXY=auto` no `.env`, o proxy é perguntado ao próprio Windows a cada
captura, com o mesmo comando que o navegador usa:

    ([System.Net.WebRequest]::GetSystemWebProxy().GetProxy([uri]"https://...")).AbsoluteUri

Se o resultado for o próprio endereço consultado, não há proxy para ele. Falha na
descoberta (fora do Windows, PowerShell ausente ou lento, resposta estranha) vira
aviso no log e a captura segue sem proxy explícito, como antes de existir o `auto`.

Atenção: variáveis de ambiente reais têm precedência sobre o `.env` (ver
`config.carregar_configuracao`). Um `HTTPS_PROXY` definido na janela do PowerShell
com uma porta velha vale mais que o `auto` do `.env` e também engana o Git e o curl;
apague-o com `Remove-Item Env:HTTPS_PROXY`.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Callable
from urllib.parse import urlsplit

log = logging.getLogger("pipeline")

VALOR_AUTOMATICO = "auto"
_TEMPO_LIMITE_S = 15
# O endereço consultado vai por variável de ambiente, não dentro do comando:
# nunca vira código PowerShell.
_COMANDO = "([System.Net.WebRequest]::GetSystemWebProxy().GetProxy([uri]$env:URL_ALVO)).AbsoluteUri"

Executor = Callable[..., "subprocess.CompletedProcess[str]"]


def proxy_do_sistema(
    url: str,
    *,
    executar: Executor = subprocess.run,
    plataforma: str | None = None,
) -> str | None:
    """Proxy que o Windows usaria para `url`, como `http://127.0.0.1:49771`;
    None se não houver proxy ou se a descoberta falhar (com aviso no log)."""
    if (plataforma or sys.platform) != "win32":
        log.warning(
            "HTTPS_PROXY=auto só funciona no Windows; seguindo sem proxy explícito. "
            "Informe o endereço em HTTPS_PROXY, se a rede exigir proxy."
        )
        return None
    try:
        resultado = executar(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _COMANDO],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_TEMPO_LIMITE_S,
            check=False,
            env={**os.environ, "URL_ALVO": url},
        )
    except (OSError, subprocess.SubprocessError) as erro:
        log.warning("Não foi possível perguntar o proxy ao Windows (%s); seguindo sem proxy explícito.", erro)
        return None
    if resultado.returncode != 0:
        log.warning(
            "O PowerShell falhou ao descobrir o proxy (código %s); seguindo sem proxy explícito.",
            resultado.returncode,
        )
        return None

    linhas = [linha.strip() for linha in (resultado.stdout or "").splitlines() if linha.strip()]
    partes = urlsplit(linhas[-1]) if linhas else None
    if not partes or partes.scheme not in ("http", "https") or not partes.hostname:
        log.warning("Resposta inesperada ao descobrir o proxy (%r); seguindo sem proxy explícito.", linhas[-1:])
        return None
    if partes.hostname == urlsplit(url).hostname:
        log.info("O Windows não indica proxy para %s.", urlsplit(url).hostname)
        return None
    porta = f":{partes.port}" if partes.port else ""
    proxy = f"{partes.scheme}://{partes.hostname}{porta}"
    log.info("Proxy descoberto no Windows: %s%s", partes.hostname, porta)
    return proxy


def resolver_proxy(
    configurado: str | None,
    url_alvo: str,
    *,
    executar: Executor = subprocess.run,
    plataforma: str | None = None,
) -> str | None:
    """Traduz o valor de `HTTPS_PROXY`: vazio = nenhum (o urllib usa o ambiente),
    `auto` = perguntar ao Windows, qualquer outro valor = o próprio endereço."""
    if configurado and configurado.casefold() == VALOR_AUTOMATICO:
        return proxy_do_sistema(url_alvo, executar=executar, plataforma=plataforma)
    return configurado