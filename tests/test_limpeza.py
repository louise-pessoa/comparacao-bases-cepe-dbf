"""Limpeza dos cadernos já processados (pipeline/limpeza.py): só apaga o caderno sem
nenhuma pendência, depois de uma simulação com a mesma lista, e deixa o registro no
manifesto."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import date
from pathlib import Path

import pymupdf
import pytest

from dbf_cepe import captura
from dbf_cepe.cli import main
from dbf_cepe.config import Configuracao
from dbf_cepe.pipeline import limpeza
from dbf_cepe.pipeline.limpeza import ErroLimpeza, avaliar, limpar, registrar_processamento
from dbf_cepe.pipeline.lote import executar
from dbf_cepe.pipeline.publicacao import Publicacao, metadados
from dbf_cepe.pipeline.conferencia import MANTIDA_A_BASE
from dbf_cepe.pipeline.registro import CONFERIR, DUPLICATA, Entrada, Registro
from dbf_cepe.pipeline.relatorio import relatorio_dos_cadernos

AMOSTRAS = Path(__file__).parent / "amostras_cadernos"
HOJE = date(2026, 11, 13)
CADERNO = "20200310_PoderExecutivo.pdf"


@pytest.fixture
def cfg(tmp_path):
    c = Configuracao(
        pasta_temp=tmp_path / "pendentes", pasta_cadernos=tmp_path / "cadernos", pasta_erros=tmp_path / "erros",
        pasta_ocr=tmp_path / "ocr", pasta_normalizados=tmp_path / "normalizados", pasta_logs=tmp_path / "logs",
        pasta_dbf=tmp_path / "dbf",
    )
    for pasta in (c.pasta_temp, c.pasta_dbf, c.pasta_cadernos):
        pasta.mkdir()
    return c


def _caderno(cfg: Configuracao, nome: str = CADERNO, decretos_dbf=(), **processamento) -> Path:
    """Caderno falso no manifesto, já processado (sem `processado` = False: não processado)."""
    caminho = cfg.pasta_cadernos / nome
    caminho.write_bytes(b"%PDF-1.4 caderno")
    arquivos = captura.ler_manifesto(cfg.pasta_cadernos)
    arquivos[nome] = {"url": "https://exemplo/" + nome, "bytes": 16, "sha256": "x", "baixado_em": "2026-11-01"}
    captura.gravar_manifesto(cfg.pasta_cadernos, arquivos)
    if processamento.pop("processado", True):
        registrar_processamento(
            caminho, len(decretos_dbf), [dict(d) for d in decretos_dbf], processamento.get("falhas", 0),
            tuple(processamento.get("paginas_digitalizadas", ())),
        )
    return caminho


def _registrar(
    cfg: Configuracao, numero: str, pagina: int, situacao: str = DUPLICATA, decisao: str = "", caderno: str = CADERNO
) -> None:
    registro = Registro.carregar(cfg.registro)
    publicacao = Publicacao(date(2020, 3, 10), caderno, pagina)
    registro.adicionar(Entrada("Decreto", numero, None, publicacao, "h", "Decreto 1.pdf", situacao, decisao=decisao))
    registro.salvar()


def _pdf_com_metadados(pasta: Path, nome: str, caderno: str = CADERNO) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), "DECRETO")
    doc.set_metadata({"keywords": metadados(Publicacao(date(2020, 3, 10), caderno, 3))})
    doc.save(pasta / nome)


def motivos(cfg: Configuracao) -> list[str]:
    (a,) = avaliar(cfg, cfg.pasta_cadernos, hoje=HOJE)
    return a.motivos


INEDITO = {"numero": "48700", "pagina": 3, "situacao": "INÉDITO"}


# ----------------------------------------------------------------------------
# Condições
# ----------------------------------------------------------------------------


def test_caderno_sem_pendencia_pode_ser_apagado(cfg):
    _caderno(cfg, decretos_dbf=[INEDITO, {"numero": "48701", "pagina": 4, "situacao": "já existe na DBF"}])
    _registrar(cfg, "48700", 3)
    assert motivos(cfg) == []


@pytest.mark.parametrize(
    ("ajuste", "trecho"),
    [
        ({"processado": False}, "não foi processado"),
        ({"falhas": 1}, "não puderam ser gravados"),
        ({"paginas_digitalizadas": (5,)}, "páginas digitalizadas"),
        ({"decretos_dbf": [{"numero": "48702", "pagina": 2, "situacao": "REVISÃO MANUAL"}]}, "Decreto 48702 em revisão manual"),
        ({"decretos_dbf": [{"numero": "48703", "pagina": 2, "situacao": "REPUBLICAÇÃO"}]}, "Decreto 48703 não está no registro"),
    ],
)
def test_cada_condicao_segura_o_caderno(cfg, ajuste, trecho):
    _caderno(cfg, **ajuste)
    assert any(trecho in m for m in motivos(cfg)), motivos(cfg)


@pytest.mark.parametrize(("pagina", "caderno"), [(9, CADERNO), (3, "20200311_PoderExecutivo.pdf")])
def test_registro_de_outra_pagina_ou_outro_caderno_nao_vale(cfg, pagina, caderno):
    _caderno(cfg, decretos_dbf=[INEDITO])
    _registrar(cfg, "48700", pagina, caderno=caderno)
    assert motivos(cfg) == ["Decreto 48700 não está no registro de publicações (o lote ainda não o processou)"]


def test_conferir_duplicata_so_libera_depois_da_decisao(cfg):
    _caderno(cfg, decretos_dbf=[{**INEDITO, "situacao": "CONFERIR DUPLICATA"}])
    _registrar(cfg, "48700", 3, CONFERIR)
    assert motivos(cfg) == ["Decreto 48700 em conferir duplicata, sem decisão (rode --conferir)"]
    registro = Registro.carregar(cfg.registro)
    # como o --conferir grava o "mantida a base": a situação continua CONFERIR, com a decisão
    registro.decidir(registro.entradas[0], CONFERIR, "Decreto 1.pdf", MANTIDA_A_BASE)
    registro.salvar()
    assert motivos(cfg) == []


@pytest.mark.parametrize("pasta", ["pendentes", "erros", "ocr", "conferir duplicata"])
def test_pdf_do_caderno_em_outra_pasta_segura_o_caderno(cfg, pasta):
    _caderno(cfg)
    destino = {"pendentes": cfg.pasta_temp, "erros": cfg.pasta_erros, "ocr": cfg.pasta_ocr,
               "conferir duplicata": cfg.conferir_duplicata}[pasta]
    _pdf_com_metadados(destino, "Decreto 48700.pdf")
    _pdf_com_metadados(destino, "Decreto 9.pdf", caderno="20200311_PoderExecutivo.pdf")  # de outro caderno
    (m,) = motivos(cfg)
    assert m.startswith("Decreto 48700.pdf em ")


def test_mes_corrente_sem_data_ou_fora_do_manifesto_ficam(cfg):
    _caderno(cfg, nome="20261105_PoderExecutivo.pdf")
    (cfg.pasta_cadernos / "solto.pdf").write_bytes(b"%PDF")
    (cfg.pasta_cadernos / "20200311_PoderExecutivo.pdf").write_bytes(b"%PDF")
    resultado = {a.caderno: a.motivos for a in avaliar(cfg, cfg.pasta_cadernos, hoje=HOJE)}
    assert resultado["20261105_PoderExecutivo.pdf"] == ["o mês ainda não acabou"]
    assert "sem data no nome (AAAAMMDD_...)" in resultado["solto.pdf"]
    assert resultado["20200311_PoderExecutivo.pdf"] == ["não está no manifesto da pasta"]


def test_mes_filtra_os_cadernos(cfg):
    _caderno(cfg)
    _caderno(cfg, nome="20200410_PoderExecutivo.pdf")
    assert [a.caderno for a in avaliar(cfg, cfg.pasta_cadernos, date(2020, 4, 1), HOJE)] == ["20200410_PoderExecutivo.pdf"]


def test_pasta_dentro_da_base_e_recusada(cfg):
    dentro = cfg.pasta_dbf / "sub"
    dentro.mkdir()
    with pytest.raises(ErroLimpeza, match="base da DBF"):
        avaliar(cfg, dentro, hoje=HOJE)


def test_caderno_fora_do_manifesto_nao_e_anotado(cfg):
    solto = cfg.pasta_cadernos / CADERNO
    solto.write_bytes(b"%PDF")
    assert registrar_processamento(solto, 0, [], 0, ()) is False
    assert captura.ler_manifesto(cfg.pasta_cadernos) == {}


def test_manifesto_ilegivel_nao_e_substituido(cfg):
    (cfg.pasta_cadernos / captura.NOME_MANIFESTO).write_text("{", encoding="utf-8")
    with pytest.raises(captura.ErroCaptura):
        avaliar(cfg, cfg.pasta_cadernos, hoje=HOJE)
    assert registrar_processamento(cfg.pasta_cadernos / CADERNO, 0, [], 0, ()) is False
    assert (cfg.pasta_cadernos / captura.NOME_MANIFESTO).read_text(encoding="utf-8") == "{"


# ----------------------------------------------------------------------------
# Simulação e exclusão
# ----------------------------------------------------------------------------


def _dois_cadernos(cfg):
    _caderno(cfg)  # sem decretos da DBF: apagável
    _caderno(cfg, nome="20200311_PoderExecutivo.pdf", falhas=1)  # fica


def test_simular_nao_apaga_e_grava_a_lista(cfg):
    _dois_cadernos(cfg)
    r = limpar(cfg, cfg.pasta_cadernos, simular=True, hoje=HOJE)
    assert r.apagados == [CADERNO] and [a.caderno for a in r.mantidos] == ["20200311_PoderExecutivo.pdf"]
    assert (cfg.pasta_cadernos / CADERNO).exists()
    simulacao = json.loads((cfg.pasta_cadernos / limpeza.NOME_SIMULACAO).read_text(encoding="utf-8"))
    assert simulacao["apagaveis"] == [CADERNO] and simulacao["mes"] is None


def test_sem_simulacao_ou_com_lista_diferente_nao_apaga(cfg):
    _dois_cadernos(cfg)
    with pytest.raises(ErroLimpeza, match="--simular"):
        limpar(cfg, cfg.pasta_cadernos, hoje=HOJE)
    limpar(cfg, cfg.pasta_cadernos, simular=True, hoje=HOJE)
    _caderno(cfg, nome="20200312_PoderExecutivo.pdf")  # um apagável novo depois da simulação
    with pytest.raises(ErroLimpeza, match="mudou"):
        limpar(cfg, cfg.pasta_cadernos, hoje=HOJE)
    assert (cfg.pasta_cadernos / CADERNO).exists()


def test_simulacao_de_outro_mes_nao_vale(cfg):
    _dois_cadernos(cfg)
    limpar(cfg, cfg.pasta_cadernos, simular=True, hoje=HOJE)  # a pasta inteira: a mesma lista do mês 2020-03
    with pytest.raises(ErroLimpeza, match="mudou"):
        limpar(cfg, cfg.pasta_cadernos, date(2020, 3, 1), hoje=HOJE)
    assert (cfg.pasta_cadernos / CADERNO).exists()


def test_simulacao_ilegivel(cfg):
    _dois_cadernos(cfg)
    (cfg.pasta_cadernos / limpeza.NOME_SIMULACAO).write_text("{", encoding="utf-8")
    with pytest.raises(ErroLimpeza, match="ilegível"):
        limpar(cfg, cfg.pasta_cadernos, hoje=HOJE)


def test_apaga_a_mesma_lista_e_deixa_o_registro_no_manifesto(cfg):
    _dois_cadernos(cfg)
    limpar(cfg, cfg.pasta_cadernos, simular=True, hoje=HOJE)
    r = limpar(cfg, cfg.pasta_cadernos, hoje=HOJE)
    assert r.apagados == [CADERNO] and r.falhas == []
    assert not (cfg.pasta_cadernos / CADERNO).exists()
    assert (cfg.pasta_cadernos / "20200311_PoderExecutivo.pdf").exists()
    assert not (cfg.pasta_cadernos / limpeza.NOME_SIMULACAO).exists()
    entrada = captura.ler_manifesto(cfg.pasta_cadernos)[CADERNO]
    assert {"url", "sha256", "baixado_em", "processado_em", "processamento", "apagado_em"} <= set(entrada)
    # apagado, sai da avaliação; o manifesto continua impedindo que seja baixado de novo
    assert [a.caderno for a in avaliar(cfg, cfg.pasta_cadernos, hoje=HOJE)] == ["20200311_PoderExecutivo.pdf"]


def test_lote_historico_apaga_sem_arquivo_de_simulacao(cfg):
    _dois_cadernos(cfg)
    r = limpar(cfg, cfg.pasta_cadernos, date(2020, 3, 1), exigir_simulacao=False, hoje=HOJE)
    assert r.apagados == [CADERNO] and not (cfg.pasta_cadernos / CADERNO).exists()


def test_falha_ao_apagar_desfaz_a_anotacao(cfg, monkeypatch):
    _caderno(cfg)
    original = Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name == CADERNO:
            raise PermissionError("em uso")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    r = limpar(cfg, cfg.pasta_cadernos, exigir_simulacao=False, hoje=HOJE)
    assert r.falhas == [CADERNO] and r.apagados == []
    assert "apagado_em" not in captura.ler_manifesto(cfg.pasta_cadernos)[CADERNO]


# ----------------------------------------------------------------------------
# Fluxo completo com um caderno real: --separar --gravar, lote, limpeza
# ----------------------------------------------------------------------------


def _caderno_real(cfg: Configuracao, amostra: str, nome: str) -> Path:
    destino = cfg.pasta_cadernos / nome
    shutil.copy(AMOSTRAS / amostra, destino)
    arquivos = captura.ler_manifesto(cfg.pasta_cadernos)
    arquivos[nome] = {"url": "https://exemplo/" + nome, "bytes": destino.stat().st_size, "sha256": "x",
                      "baixado_em": "2026-07-01"}
    captura.gravar_manifesto(cfg.pasta_cadernos, arquivos)
    return destino


def test_fluxo_completo_apaga_o_caderno_processado(cfg):
    (cfg.pasta_dbf / "Decreto 60929.pdf").write_bytes(b"%PDF-1.4")
    _caderno_real(cfg, "Caderno 2026-06-26 (paginas 24 a 26).pdf", "20260626_PoderExecutivo.pdf")
    relatorio_dos_cadernos(cfg, cfg.pasta_cadernos, gravar=True)
    processamento = captura.ler_manifesto(cfg.pasta_cadernos)["20260626_PoderExecutivo.pdf"]["processamento"]
    assert {(d["numero"], d["situacao"]) for d in processamento["decretos_dbf"]} == {
        ("60928", "INÉDITO"), ("60929", "já existe na DBF")
    }
    assert processamento["falhas"] == 0 and processamento["decretos"] == 10
    (antes,) = avaliar(cfg, cfg.pasta_cadernos, hoje=HOJE)
    assert any("pendentes" in m for m in antes.motivos)  # o lote ainda não rodou
    executar(cfg)
    assert limpar(cfg, cfg.pasta_cadernos, simular=True, hoje=HOJE).apagados == ["20260626_PoderExecutivo.pdf"]
    assert limpar(cfg, cfg.pasta_cadernos, hoje=HOJE).apagados == ["20260626_PoderExecutivo.pdf"]


def test_fluxo_com_decreto_em_revisao_manual_guarda_o_caderno(cfg):
    (cfg.pasta_dbf / "Decreto 1.pdf").write_bytes(b"%PDF-1.4")
    _caderno_real(cfg, "Caderno 2026-06-26 (pagina 9).pdf", "20260626_PoderExecutivo.pdf")
    relatorio_dos_cadernos(cfg, cfg.pasta_cadernos, gravar=True)
    executar(cfg)
    (a,) = avaliar(cfg, cfg.pasta_cadernos, hoje=HOJE)
    assert a.motivos == ["Decreto 60905 em revisão manual"]


def test_falha_ao_gravar_e_pagina_digitalizada_ficam_no_caderno_certo(cfg, monkeypatch):
    from dbf_cepe.pipeline import relatorio as modulo_relatorio
    from dbf_cepe.pipeline.exportador import ErroExportacao

    (cfg.pasta_dbf / "Decreto 1.pdf").write_bytes(b"%PDF-1.4")
    _caderno_real(cfg, "Caderno 2026-06-26 (paginas 24 a 26).pdf", "20260626_PoderExecutivo.pdf")
    _caderno_real(cfg, "Caderno 2026-06-26 (pagina 9).pdf", "20260627_PoderExecutivo.pdf")
    original = modulo_relatorio.separar_caderno

    def separar(caminho):
        resultado = original(caminho)
        if Path(caminho).name.startswith("20260627"):
            return replace(resultado, paginas_digitalizadas=(2,))
        return resultado

    def gravar(ato, destino):
        raise ErroExportacao("disco cheio")

    monkeypatch.setattr(modulo_relatorio, "separar_caderno", separar)
    monkeypatch.setattr(modulo_relatorio, "gravar_pdf_do_ato", gravar)
    relatorio_dos_cadernos(cfg, cfg.pasta_cadernos, gravar=True)
    arquivos = captura.ler_manifesto(cfg.pasta_cadernos)
    primeiro = arquivos["20260626_PoderExecutivo.pdf"]["processamento"]
    segundo = arquivos["20260627_PoderExecutivo.pdf"]["processamento"]
    assert (primeiro["falhas"], primeiro["paginas_digitalizadas"]) == (2, [])  # 60928 e 60929
    assert (segundo["falhas"], segundo["paginas_digitalizadas"]) == (2, [2])  # 60903 e 60904


def test_relatorio_sem_gravar_nao_anota(cfg):
    (cfg.pasta_dbf / "Decreto 1.pdf").write_bytes(b"%PDF-1.4")
    _caderno_real(cfg, "Caderno 2026-06-26 (pagina 9).pdf", "20260626_PoderExecutivo.pdf")
    relatorio_dos_cadernos(cfg, cfg.pasta_cadernos)
    assert "processado_em" not in captura.ler_manifesto(cfg.pasta_cadernos)["20260626_PoderExecutivo.pdf"]


# ----------------------------------------------------------------------------
# Linha de comando
# ----------------------------------------------------------------------------


def test_cli_simula_e_depois_apaga(cfg, monkeypatch):
    _dois_cadernos(cfg)
    monkeypatch.setattr("dbf_cepe.cli.carregar_configuracao", lambda: cfg)
    assert main(["--limpar-cadernos", str(cfg.pasta_cadernos), "--mes", "2020-03", "--simular"]) == 0
    assert (cfg.pasta_cadernos / CADERNO).exists()
    assert main(["--limpar-cadernos", str(cfg.pasta_cadernos), "--mes", "2020-03"]) == 0
    assert not (cfg.pasta_cadernos / CADERNO).exists()


def test_cli_sem_simulacao_e_erro(cfg, monkeypatch, capsys):
    _dois_cadernos(cfg)
    monkeypatch.setattr("dbf_cepe.cli.carregar_configuracao", lambda: cfg)
    assert main(["--limpar-cadernos", str(cfg.pasta_cadernos)]) == 2
    assert "--simular" in capsys.readouterr().err


def test_cli_falha_ao_apagar_sai_com_1(cfg, monkeypatch):
    monkeypatch.setattr("dbf_cepe.cli.carregar_configuracao", lambda: cfg)
    monkeypatch.setattr(limpeza, "limpar", lambda *a, **k: limpeza.ResultadoLimpeza(falhas=["x.pdf"]))
    assert main(["--limpar-cadernos", str(cfg.pasta_cadernos)]) == 1


@pytest.mark.parametrize(
    "argv",
    [
        ["--limpar-cadernos", "x", "--separar", "y"],
        ["--limpar-cadernos", "x", "--conferir"],
        ["--mes", "2020-03"],
        ["--limpar-cadernos", "x", "--mes", "2020-13"],
    ],
)
def test_cli_combinacoes_invalidas(argv):
    with pytest.raises(SystemExit):
        main(argv)
