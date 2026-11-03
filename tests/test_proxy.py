"""Descoberta do proxy do Windows (HTTPS_PROXY=auto). O PowerShell é sempre simulado."""

from __future__ import annotations

import logging
import subprocess
from datetime import date

import pytest

import captura
import main
import proxy
from config import Configuracao

ALVO = "https://diariooficial.cepe.com.br/1/arquivos/resumoDiario/2026-10-21/2026-10-21.pdf"


def _executor(saida="", codigo=0, erro=None):
    chamadas = []

    def executar(comando, **opcoes):
        chamadas.append((comando, opcoes))
        if erro:
            raise erro
        return subprocess.CompletedProcess(comando, codigo, stdout=saida, stderr="")

    executar.chamadas = chamadas
    return executar


def _descobrir(saida="", **kw):
    return proxy.proxy_do_sistema(ALVO, executar=_executor(saida, **kw), plataforma="win32")


def test_devolve_o_proxy_indicado_pelo_windows():
    assert _descobrir("http://127.0.0.1:49771/\r\n") == "http://127.0.0.1:49771"


def test_normaliza_o_endereco_sem_barra_final_nem_caminho():
    assert _descobrir("http://proxy.local:3128/pac/x\n") == "http://proxy.local:3128"


def test_proxy_sem_porta_explicita_fica_sem_porta():
    assert _descobrir("http://proxy.local/\n") == "http://proxy.local"


def test_usa_a_ultima_linha_nao_vazia():
    assert _descobrir("ruído do perfil\n\nhttp://127.0.0.1:1234/\n\n") == "http://127.0.0.1:1234"


def test_sem_proxy_quando_o_windows_devolve_o_proprio_endereco(caplog):
    with caplog.at_level(logging.INFO, logger="pipeline"):
        assert _descobrir(ALVO + "\n") is None
    assert "não indica proxy" in caplog.text


@pytest.mark.parametrize("saida", ["", "   \n", "127.0.0.1:9000", "ftp://127.0.0.1:21/", "http://"])
def test_resposta_inesperada_vira_aviso_e_sem_proxy(saida, caplog):
    with caplog.at_level(logging.WARNING, logger="pipeline"):
        assert _descobrir(saida) is None
    assert "inesperada" in caplog.text


def test_falha_do_powershell_vira_aviso_e_sem_proxy(caplog):
    with caplog.at_level(logging.WARNING, logger="pipeline"):
        assert _descobrir("http://127.0.0.1:1/", codigo=1) is None
    assert "código 1" in caplog.text


@pytest.mark.parametrize(
    "erro", [FileNotFoundError("powershell"), subprocess.TimeoutExpired("powershell", 15)]
)
def test_powershell_ausente_ou_lento_vira_aviso_e_sem_proxy(erro, caplog):
    with caplog.at_level(logging.WARNING, logger="pipeline"):
        assert _descobrir(erro=erro) is None
    assert "sem proxy explícito" in caplog.text


def test_fora_do_windows_nao_chama_o_powershell(caplog):
    executor = _executor("http://127.0.0.1:1/")
    with caplog.at_level(logging.WARNING, logger="pipeline"):
        assert proxy.proxy_do_sistema(ALVO, executar=executor, plataforma="linux") is None
    assert executor.chamadas == []
    assert "só funciona no Windows" in caplog.text


def test_endereco_consultado_vai_por_variavel_de_ambiente_e_nao_no_comando():
    executor = _executor("http://127.0.0.1:1/")
    proxy.proxy_do_sistema(ALVO, executar=executor, plataforma="win32")
    comando, opcoes = executor.chamadas[0]
    assert comando[0] == "powershell" and "-NoProfile" in comando
    assert ALVO not in " ".join(comando)
    assert opcoes["env"]["URL_ALVO"] == ALVO
    assert opcoes["timeout"] == proxy._TEMPO_LIMITE_S
    assert opcoes["check"] is False


@pytest.mark.parametrize("configurado", [None, "", "http://127.0.0.1:9000"])
def test_resolver_devolve_o_valor_comum_sem_consultar_o_windows(configurado):
    executor = _executor("http://127.0.0.1:1/")
    assert proxy.resolver_proxy(configurado, ALVO, executar=executor, plataforma="win32") == configurado
    assert executor.chamadas == []


@pytest.mark.parametrize("valor", ["auto", "AUTO", "Auto"])
def test_resolver_auto_consulta_o_windows(valor):
    executor = _executor("http://127.0.0.1:49771/")
    assert proxy.resolver_proxy(valor, ALVO, executar=executor, plataforma="win32") == "http://127.0.0.1:49771"
    assert len(executor.chamadas) == 1


# ----------------------------------------------------------------------------
# Ligação com a captura (main.capturar)
# ----------------------------------------------------------------------------


@pytest.fixture
def cfg_auto(tmp_path):
    return Configuracao(
        pasta_temp=tmp_path / "p",
        pasta_cadernos=tmp_path / "c",
        pasta_erros=tmp_path / "e",
        pasta_ocr=tmp_path / "o",
        pasta_normalizados=tmp_path / "n",
        pasta_logs=tmp_path / "l",
        pasta_dbf=tmp_path / "d",
        proxy_https="auto",
    )


@pytest.fixture
def capturas(monkeypatch):
    vistos = {}

    def falso(inicio, fim, pasta, **opcoes):
        vistos.update(opcoes)
        return []

    monkeypatch.setattr(captura, "baixar_cadernos", falso)
    return vistos


def test_capturar_com_auto_repassa_o_proxy_descoberto(cfg_auto, capturas, monkeypatch):
    pedidos = []

    def resolver(configurado, url):
        pedidos.append((configurado, url))
        return "http://127.0.0.1:49771"

    monkeypatch.setattr(proxy, "resolver_proxy", resolver)
    main.capturar(cfg_auto, date(2026, 10, 21), date(2026, 10, 22))

    assert capturas["proxy"] == "http://127.0.0.1:49771"
    assert pedidos == [("auto", captura.FONTES[captura.FONTE_PADRAO][0].url(date(2026, 10, 21)))]


def test_capturar_com_auto_sem_proxy_no_windows_segue_sem_proxy(cfg_auto, capturas, monkeypatch):
    monkeypatch.setattr(proxy, "resolver_proxy", lambda *a, **k: None)
    main.capturar(cfg_auto, date(2026, 10, 21), date(2026, 10, 21))
    assert capturas["proxy"] is None


def test_simulacao_nao_pergunta_nada_ao_windows(cfg_auto, capturas, monkeypatch):
    def nao_deve_chamar(*a, **k):
        raise AssertionError("a simulação não pode consultar o Windows")

    monkeypatch.setattr(proxy, "resolver_proxy", nao_deve_chamar)
    main.capturar(cfg_auto, date(2026, 10, 21), date(2026, 10, 21), simular=True)
    assert capturas["proxy"] is None


def test_capturar_com_endereco_fixo_nao_consulta_o_windows(cfg_auto, capturas, monkeypatch):
    def nao_deve_chamar(*a, **k):
        raise AssertionError("endereço fixo não precisa de consulta")

    monkeypatch.setattr(proxy, "resolver_proxy", nao_deve_chamar)
    cfg = Configuracao(**{**cfg_auto.__dict__, "proxy_https": "http://127.0.0.1:9000"})
    main.capturar(cfg, date(2026, 10, 21), date(2026, 10, 21))
    assert capturas["proxy"] == "http://127.0.0.1:9000"


def test_capturar_avisa_quando_o_ambiente_sobrepoe_o_env(cfg_auto, capturas, monkeypatch, caplog):
    cfg = Configuracao(**{**cfg_auto.__dict__, "proxy_https": "http://127.0.0.1:61843", "proxy_do_ambiente": "127.0.0.1:61843"})
    with caplog.at_level(logging.WARNING, logger="pipeline"):
        main.capturar(cfg, date(2026, 10, 21), date(2026, 10, 21))
    assert "127.0.0.1:61843" in caplog.text and "Remove-Item Env:HTTPS_PROXY" in caplog.text


def test_capturar_nao_avisa_quando_nao_ha_sobreposicao(cfg_auto, capturas, monkeypatch, caplog):
    monkeypatch.setattr(proxy, "resolver_proxy", lambda *a, **k: None)
    with caplog.at_level(logging.WARNING, logger="pipeline"):
        main.capturar(cfg_auto, date(2026, 10, 21), date(2026, 10, 21))
    assert "Remove-Item" not in caplog.text