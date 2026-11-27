"""Formato da saída: blocos, avisos recuados, bloco de avisos do fim e o pipeline.log."""

from __future__ import annotations

import logging
import re

import pytest

from dbf_cepe import saida


@pytest.fixture
def log(tmp_path):
    """O logger de teste; cada teste liga o terminal e o arquivo com `_ligar` (o stdout que o
    capsys captura só vale dentro do teste)."""
    logger = logging.getLogger("teste_saida")
    logger.propagate = False
    yield logger
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def _ligar(log, tmp_path) -> None:
    saida.configurar_log(log, tmp_path, "teste.log")


def _terminal(capsys) -> list[str]:
    return capsys.readouterr().out.splitlines()


def _arquivo(tmp_path) -> str:
    return (tmp_path / "teste.log").read_text(encoding="utf-8")


def test_titulos():
    assert saida.subtitulo("RESUMO") == "---- RESUMO ----"
    titulo = saida.titulo("RESUMO")
    assert len(titulo) == saida.LARGURA and titulo.strip("-") == " RESUMO "
    assert saida.titulo("X" * 100).startswith("---- X")  # longo: ao menos 4 traços de cada lado


def test_terminal_so_mensagem_e_arquivo_com_data_e_hora(log, tmp_path, capsys):
    _ligar(log, tmp_path)
    log.info("linha comum")
    log.warning("cuidado")
    log.error("quebrou")
    assert _terminal(capsys) == ["linha comum", "[AVISO] cuidado", "[ERRO]  quebrou"]
    linhas = _arquivo(tmp_path).splitlines()
    assert [re.sub(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \| ", "", linha) for linha in linhas] == [
        "linha comum", "[AVISO] cuidado", "[ERRO]  quebrou",
    ]
    assert all(re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \| ", linha) for linha in linhas)


def test_rodada_com_blocos_avisos_recuados_e_resumo(log, tmp_path, capsys):
    _ligar(log, tmp_path)
    with saida.rodada(log, "SEPARAR CADERNOS"):
        log.info("2 caderno(s)")
        saida.bloco(log, "c1.pdf", chave="c1.pdf")
        with saida.item("Decreto 1.pdf"):
            saida.aviso(log, "sem fecho")  # antes da linha do item: espera por ela
            saida.linha_do_item(log, "Decreto 1.pdf | INÉDITO")
            saida.detalhe(log, "diferença: x")
        with saida.item("Decreto 2.pdf"):
            saida.linha_do_item(log, "Decreto 2.pdf | já existe")
        saida.fechar(log)
        log.info("Total: 2")
    terminal = _terminal(capsys)
    assert re.fullmatch(r"-+ SEPARAR CADERNOS — \d\d/\d\d/\d{4} \d\d:\d\d:\d\d -+", terminal[0])
    assert terminal[1:9] == [
        "2 caderno(s)",
        "",
        "---- c1.pdf ----",
        "Decreto 1.pdf | INÉDITO",
        "    [AVISO] sem fecho",
        "    diferença: x",
        "Decreto 2.pdf | já existe",
        "",
    ]
    assert terminal[9] == saida.titulo("AVISOS E ERROS (1)")
    assert terminal[10] == "[AVISO] c1.pdf | Decreto 1.pdf | sem fecho"
    assert terminal[11:14] == ["", saida.titulo("RESUMO"), "Total: 2"]
    assert terminal[14].startswith("Duração: ") and terminal[15] == ""
    # O .log tem as mesmas linhas; títulos e linhas em branco sem a data, e a rodada
    # começa depois de duas linhas em branco.
    arquivo = _arquivo(tmp_path)
    assert arquivo.startswith("\n\n" + terminal[0] + "\n")
    sem_data = [re.sub(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \| ", "", linha) for linha in arquivo.splitlines()[2:]]
    assert sem_data == terminal


def test_item_sem_linha_propria_solta_os_avisos_no_fim(log, tmp_path, capsys):
    _ligar(log, tmp_path)
    with saida.item("Decreto 3.pdf"):
        saida.aviso(log, "aviso solto")
    assert _terminal(capsys) == ["    [AVISO] aviso solto"]


def test_fora_de_item_o_aviso_vai_em_linha_propria(log, tmp_path, capsys):
    _ligar(log, tmp_path)
    saida.aviso(log, "sem item")
    saida.erro(log, "erro sem item")
    assert _terminal(capsys) == ["[AVISO] sem item", "[ERRO]  erro sem item"]


def test_rodada_aninhada_leva_o_contexto_de_fora_para_o_bloco_final(log, tmp_path, capsys):
    _ligar(log, tmp_path)
    with saida.rodada(log, "LOTE HISTÓRICO"):
        saida.bloco(log, "MÊS 2020-03", chave="2020-03")
        with saida.rodada(log, "SEPARAR CADERNOS"):
            saida.bloco(log, "c1.pdf", chave="c1.pdf")
            log.warning("de dentro")
            saida.fechar(log)
        log.warning("de fora")
        saida.fechar(log)
    terminal = _terminal(capsys)
    inicio = terminal.index(saida.titulo("AVISOS E ERROS (2)"))
    assert terminal[inicio + 1 : inicio + 3] == [
        "[AVISO] 2020-03 | c1.pdf | de dentro",
        "[AVISO] 2020-03 | de fora",
    ]


def test_sem_avisos_nao_ha_bloco_de_avisos(log, tmp_path, capsys):
    _ligar(log, tmp_path)
    with saida.rodada(log, "LOTE"):
        log.info("tudo certo")
        saida.fechar(log)
    assert not any("AVISOS E ERROS" in linha for linha in _terminal(capsys))


def test_configurar_de_novo_nao_duplica_linhas(log, tmp_path, capsys):
    _ligar(log, tmp_path)
    saida.configurar_log(log, tmp_path, "teste.log")
    log.info("uma vez")
    assert _terminal(capsys) == ["uma vez"]


def test_erro_fatal_vai_ao_stderr_e_ao_arquivo_uma_vez(log, tmp_path, capsys):
    _ligar(log, tmp_path)
    saida.erro_fatal(log, "pasta sumiu")
    capturado = capsys.readouterr()
    assert capturado.err == "[ERRO]  pasta sumiu\n" and capturado.out == ""
    assert _arquivo(tmp_path).endswith("| [ERRO]  pasta sumiu\n")


def test_cores_so_no_terminal(monkeypatch):
    assert saida.marcar("x", logging.WARNING, cores=True) == "\033[33m[AVISO] x\033[0m"
    assert saida.marcar("x", logging.ERROR, cores=True) == "\033[31m[ERRO]  x\033[0m"
    assert saida.marcar("x", logging.INFO, cores=True) == "x"

    class Terminal:
        def isatty(self):
            return True

    monkeypatch.setattr(saida.sys, "platform", "linux")
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert saida.usa_cores(Terminal())
    monkeypatch.setenv("NO_COLOR", "1")
    assert not saida.usa_cores(Terminal())
