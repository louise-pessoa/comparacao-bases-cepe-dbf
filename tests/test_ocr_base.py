"""Fase 5 do plano do OCR: o PDF da base em imagem ou com texto ilegível é lido por OCR.

Duplicata só sem nenhuma diferença que o OCR não explique (decisão de 23/11/2026); com
diferença, conferência; OCR sem texto aproveitável, como antes ("base é imagem"). Nunca grava
nem renomeia na base por causa do OCR. Os testes sem marca usam um OCR simulado; os marcados
`ocr`, o Tesseract instalado."""

from __future__ import annotations

import logging
import shutil

import pytest

from conftest import AMOSTRAS, criar_pdf_imagem, criar_pdf_imagem_de, criar_pdf_texto
from dbf_cepe.config import Configuracao, ConfiguracaoOcr, ErroConfiguracao
from dbf_cepe.leitura.ocr import PaginaOcr, Palavra
from dbf_cepe.leitura.reader import ler_pdf
from dbf_cepe.pipeline import comparacao, conferencia
from dbf_cepe.pipeline import relatorio as modulo_relatorio
from dbf_cepe.pipeline.lote import Destino, executar
from dbf_cepe.pipeline.registro import BASE_IMAGEM, CONFERIR, DUPLICATA_OCR, Registro
from dbf_cepe.pipeline.relatorio import relatorio_dos_cadernos
from dbf_cepe.leitura.separador import ResultadoSeparacao
from test_base_imagem import _pendente, ato, nomes, pub

PRAZO = "\nArt. 2º O estímulo é concedido pelo prazo de 12 anos."


class LeitorFalso:
    """OCR simulado: devolve o mesmo texto para qualquer página, com a confiança dada."""

    versao = "5.3.4"

    def __init__(self, texto: str, confianca: float = 95.0, periodo_ruim: tuple = ()):
        # Sem período ruim por padrão: os testes do lote usam 2020.
        self.cfg = ConfiguracaoOcr(periodo_ruim=periodo_ruim)
        self.texto, self.confianca = texto, confianca
        self.lidos: list[str] = []

    def ler(self, pdf, numero, ajustes=None) -> PaginaOcr:
        self.lidos.append(pdf.name)
        palavras = tuple(Palavra(p, self.confianca, (0, 0, 1, 1), (1, 1, 1)) for p in self.texto.split())
        return PaginaOcr(self.texto, palavras, self.versao)


@pytest.fixture
def cfg(tmp_path):
    """Como em test_base_imagem: a base tem o Decreto 48562 digitalizado."""
    c = Configuracao(
        pasta_temp=tmp_path / "pendentes", pasta_cadernos=tmp_path / "cadernos", pasta_erros=tmp_path / "erros",
        pasta_ocr=tmp_path / "ocr", pasta_normalizados=tmp_path / "normalizados", pasta_logs=tmp_path / "logs",
        pasta_dbf=tmp_path / "dbf",
    )
    c.pasta_temp.mkdir()
    c.pasta_dbf.mkdir()
    criar_pdf_imagem(c.pasta_dbf / "Decreto 48562.pdf")
    return c


@pytest.fixture
def ocr_simulado(monkeypatch):
    def instalar(texto: str, confianca: float = 95.0, periodo_ruim: tuple = ()) -> LeitorFalso:
        leitor = LeitorFalso(texto, confianca, periodo_ruim)
        monkeypatch.setattr(comparacao, "leitor_ocr", lambda _cfg: leitor)
        monkeypatch.setattr(conferencia, "leitor_ocr", lambda _cfg: leitor)
        return leitor

    return instalar


# ----------------------------------------------------------------------------
# Lote
# ----------------------------------------------------------------------------


def test_base_em_imagem_igual_pelo_ocr_e_duplicata_anotada_como_ocr(cfg, ocr_simulado, caplog):
    a = ato("48562", pub("2020-01-15"), PRAZO)
    leitor = ocr_simulado("Recife, 15 de janeiro de 2020 Diário Oficial " + a.texto.replace("estímulo", "estimulo"))
    _pendente(cfg, a)
    with caplog.at_level(logging.INFO, logger="pipeline"):
        resumo = executar(cfg)
    assert resumo[Destino.DESCARTADO_DUPLICADO] == 1
    assert leitor.lidos == ["Decreto 48562.pdf"]
    assert [e.situacao for e in Registro.carregar(cfg.registro).entradas] == [DUPLICATA_OCR]
    assert "base lida por OCR, sem diferença" in caplog.text
    assert "OCR ativo" in caplog.text
    assert nomes(cfg.pasta_dbf) == ["Decreto 48562.pdf"] and nomes(cfg.conferir_duplicata) == []


def test_numero_diferente_lido_com_confianca_vai_para_conferir(cfg, ocr_simulado, caplog):
    a = ato("48562", pub("2020-01-15"), PRAZO)
    antes = (cfg.pasta_dbf / "Decreto 48562.pdf").read_bytes()
    ocr_simulado(a.texto.replace("12 anos", "15 anos"))
    _pendente(cfg, a)
    with caplog.at_level(logging.INFO, logger="pipeline"):
        resumo = executar(cfg)
    assert resumo[Destino.ENVIADO_CONFERIR] == 1
    assert [e.situacao for e in Registro.carregar(cfg.registro).entradas] == [CONFERIR]
    assert "[caderno: 12 | base: 15] número diferente (lido com confiança)" in caplog.text
    assert (cfg.pasta_dbf / "Decreto 48562.pdf").read_bytes() == antes  # a base não muda


def test_ocr_com_confianca_baixa_nunca_decide_duplicata(cfg, ocr_simulado):
    a = ato("48562", pub("2020-01-15"), PRAZO)
    ocr_simulado(a.texto, confianca=70)
    _pendente(cfg, a)
    assert executar(cfg)[Destino.ENVIADO_CONFERIR] == 1


def test_ocr_sem_texto_continua_base_imagem(cfg, ocr_simulado):
    ocr_simulado("borrão ilegível")
    _pendente(cfg, ato("48562", pub("2020-01-15")))
    assert executar(cfg)[Destino.DESCARTADO_DUPLICADO] == 1
    assert [e.situacao for e in Registro.carregar(cfg.registro).entradas] == [BASE_IMAGEM]


# ----------------------------------------------------------------------------
# Período de digitalização ruim (OCR_PERIODO_RUIM; decisão de 26/11/2026)
# ----------------------------------------------------------------------------

RUIM = ((2018, 2020),)


def test_periodo_ruim_so_ruido_fica_nao_comparado(cfg, ocr_simulado):
    a = ato("48562", pub("2020-01-15"), PRAZO)
    ocr_simulado(a.texto.replace("estímulo", "XYZWQK"), periodo_ruim=RUIM)
    _pendente(cfg, a)
    assert executar(cfg)[Destino.DESCARTADO_DUPLICADO] == 1
    assert [e.situacao for e in Registro.carregar(cfg.registro).entradas] == [BASE_IMAGEM]
    assert nomes(cfg.conferir_duplicata) == []


def test_periodo_ruim_numero_lido_com_confianca_vai_para_conferir(cfg, ocr_simulado):
    a = ato("48562", pub("2020-01-15"), PRAZO)
    ocr_simulado(a.texto.replace("12 anos", "15 anos"), periodo_ruim=RUIM)
    _pendente(cfg, a)
    assert executar(cfg)[Destino.ENVIADO_CONFERIR] == 1


def test_periodo_ruim_sem_diferenca_e_duplicata_mas_nao_com_confianca_baixa(cfg, ocr_simulado):
    a = ato("48562", pub("2020-01-15"), PRAZO)
    ocr_simulado(a.texto, periodo_ruim=RUIM)
    _pendente(cfg, a)
    executar(cfg)
    assert [e.situacao for e in Registro.carregar(cfg.registro).entradas] == [DUPLICATA_OCR]
    pdf = cfg.pasta_dbf / "Decreto 48562.pdf"
    baixa = comparacao.comparar_por_ocr(a.texto, pdf, LeitorFalso(a.texto, 70, RUIM), 2020)
    assert not baixa.lido and "período de digitalização ruim (2020)" in baixa.detalhe


def test_fora_do_periodo_ruim_o_ruido_vai_para_conferir(cfg, ocr_simulado):
    a = ato("48562", pub("2021-01-15"), PRAZO)
    ocr_simulado(a.texto.replace("estímulo", "XYZWQK"), periodo_ruim=RUIM)
    _pendente(cfg, a)
    assert executar(cfg)[Destino.ENVIADO_CONFERIR] == 1


def test_conferencia_ignora_o_periodo_ruim(cfg, ocr_simulado, tmp_path):
    a = ato("48562", pub("2020-01-15"), PRAZO)
    ocr_simulado(a.texto.replace("estímulo", "XYZWQK"), periodo_ruim=RUIM)
    pdf = criar_pdf_texto(tmp_path / "Decreto 48562 (conferir duplicata).pdf", a.texto)
    item = conferencia.preparar(pdf, cfg.pasta_dbf, {"Decreto 48562.pdf"}, Registro(cfg.registro), cfg)
    assert item.comparacao is not None and item.comparacao.lido and item.comparacao.total_trechos >= 1


# ----------------------------------------------------------------------------
# --separar --gravar e --conferir
# ----------------------------------------------------------------------------


def test_separar_gravar_mostra_a_duplicata_pelo_ocr(cfg, ocr_simulado, tmp_path, monkeypatch, caplog):
    a = ato("48562", pub("2020-01-15"), PRAZO)
    ocr_simulado(a.texto)
    monkeypatch.setattr(modulo_relatorio, "separar_caderno", lambda caminho: ResultadoSeparacao((a,), 9))
    caderno = tmp_path / "20200115_PoderExecutivo.pdf"
    caderno.write_bytes(b"%PDF-1.4")
    with caplog.at_level(logging.INFO, logger="pipeline"):
        relatorio_dos_cadernos(cfg, caderno, gravar=True)
    assert "já existe na DBF (base lida por OCR, sem diferença)" in caplog.text
    assert nomes(cfg.pasta_temp) == []


def test_conferencia_mostra_so_as_diferencas_que_o_ocr_nao_explica(cfg, ocr_simulado, tmp_path):
    a = ato("48562", pub("2020-01-15"), PRAZO)
    ocr_simulado(a.texto.replace("12 anos", "15 anos").replace("estímulo", "estimulo"))
    pdf = criar_pdf_texto(tmp_path / "Decreto 48562 (conferir duplicata).pdf", a.texto)
    item = conferencia.preparar(pdf, cfg.pasta_dbf, {"Decreto 48562.pdf"}, Registro(cfg.registro), cfg)
    assert item.comparacao is not None and item.comparacao.por_ocr and not item.comparacao.identico
    texto = "\n".join(conferencia.descrever(item))
    assert "1 diferença(s) que o OCR não explica" in texto
    assert "[Diário: 12 | base: 15]" in texto and "estimulo" not in texto


def test_sem_cfg_nada_muda(cfg, ocr_simulado):
    ocr_simulado("qualquer coisa " * 30)
    assert not comparacao.comparar_com_base("texto", cfg.pasta_dbf / "Decreto 48562.pdf").lido


# ----------------------------------------------------------------------------
# Configuração e Tesseract de verdade
# ----------------------------------------------------------------------------


def test_ocr_exigido_e_ausente_e_erro_de_configuracao(cfg, tmp_path):
    from dataclasses import replace

    ruim = replace(cfg, ocr=ConfiguracaoOcr(ativo="sim", tesseract_cmd=tmp_path / "tesseract.exe"))
    with pytest.raises(ErroConfiguracao, match="OCR"):
        comparacao.leitor_ocr(ruim)


@pytest.mark.ocr
@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract não instalado")
def test_decreto_real_digitalizado_e_duplicata_e_outro_nao(cfg, tmp_path):
    texto = ler_pdf(AMOSTRAS / "Decreto 60929.pdf").texto
    criar_pdf_imagem_de(AMOSTRAS / "Decreto 60929.pdf", cfg.pasta_dbf / "Decreto 60929.pdf")
    criar_pdf_imagem_de(AMOSTRAS / "Decreto 60951.pdf", cfg.pasta_dbf / "Decreto 60951.pdf")
    mesmo = comparacao.comparar_com_base(texto, cfg.pasta_dbf / "Decreto 60929.pdf", cfg)
    assert mesmo.por_ocr and mesmo.identico, mesmo.trechos
    outro = comparacao.comparar_com_base(texto, cfg.pasta_dbf / "Decreto 60951.pdf", cfg)
    assert outro.por_ocr and not outro.identico and outro.total_trechos >= 5
