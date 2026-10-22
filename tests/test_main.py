"""Integração do lote (etapas 2 e 3 reais; 4 e 5 ainda pendentes).

Usa só PDFs sintéticos, então roda mesmo sem as amostras reais.
"""

from __future__ import annotations

import pytest

from config import Configuracao, ErroConfiguracao
from conftest import criar_pdf_imagem, criar_pdf_texto, texto_decreto
from duplicidade import PastaDbfIndisponivel
from main import Destino, executar


@pytest.fixture
def ambiente(tmp_path):
    cfg = Configuracao(
        pasta_temp=tmp_path / "pendentes",
        pasta_cadernos=tmp_path / "cadernos",
        pasta_erros=tmp_path / "erros",
        pasta_ocr=tmp_path / "leitura tesseract",
        pasta_normalizados=tmp_path / "normalizados",
        pasta_logs=tmp_path / "logs",
        pasta_dbf=tmp_path / "dbf",
    )
    cfg.pasta_temp.mkdir()
    cfg.pasta_dbf.mkdir()
    (cfg.pasta_dbf / "Decreto 60929.pdf").write_bytes(b"ja existe")

    p = cfg.pasta_temp
    criar_pdf_texto(p / "duplicado.pdf", texto_decreto("60.929", "Concede estímulo."))
    criar_pdf_texto(p / "inedito.pdf", texto_decreto("60.951", "Introduz alterações no Decreto nº 50.135."))
    criar_pdf_texto(p / "sem_tipo.pdf", texto_decreto("60.960", "Estabelece normas."))
    criar_pdf_texto(p / "lei.pdf", "LEI Nº 11.675, DE 11 DE OUTUBRO DE 1999.\nDispõe sobre o PRODEPE.")
    criar_pdf_texto(p / "sem_cabecalho.pdf", "Texto qualquer sem cabeçalho de ato.")
    criar_pdf_imagem(p / "CEPE_2026-06-26_0001.pdf")
    (p / "quebrado.pdf").write_bytes(b"nao e um pdf")
    (p / "leia-me.txt").write_text("não é PDF; deve ser ignorado")
    return cfg


def _estado(cfg):
    return {
        pasta.name: sorted(f.name for f in pasta.iterdir()) if pasta.exists() else None
        for pasta in (cfg.pasta_temp, cfg.pasta_erros, cfg.pasta_ocr)
    }


def test_destino_de_cada_arquivo(ambiente):
    cfg = ambiente
    resumo = executar(cfg)

    assert resumo == {
        Destino.DESCARTADO_DUPLICADO: 1,
        Destino.AGUARDANDO_ETAPAS_4_5: 1,
        Destino.DESCARTADO_OUTRA_ESPECIE: 1,
        Destino.ENVIADO_OCR: 1,
        Destino.ENVIADO_ERROS: 3,  # sem_tipo, sem_cabecalho, quebrado
    }
    # Duplicado e lei foram descartados; o inédito permanece (etapas 4 e 5 pendentes).
    assert _estado(cfg)["pendentes"] == ["inedito.pdf", "leia-me.txt"]
    # PDF de imagem mantém o nome original da CEPE.
    assert _estado(cfg)["leitura tesseract"] == ["CEPE_2026-06-26_0001.pdf"]
    assert _estado(cfg)["erros"] == ["quebrado.pdf", "sem_cabecalho.pdf", "sem_tipo.pdf"]


def test_simulacao_nao_altera_nada_e_preve_o_mesmo_resultado(ambiente):
    cfg = ambiente
    antes = _estado(cfg)
    resumo_simulado = executar(cfg, simular=True)
    assert _estado(cfg) == antes
    assert not cfg.pasta_ocr.exists() and not cfg.pasta_erros.exists()
    assert resumo_simulado == executar(cfg)


def test_mesmo_numero_duas_vezes_no_lote_envia_o_segundo_para_revisao(ambiente):
    cfg = ambiente
    criar_pdf_texto(
        cfg.pasta_temp / "inedito_b.pdf",
        texto_decreto("60.951", "Introduz alterações no Decreto nº 50.135."),
    )
    resumo = executar(cfg)
    assert resumo[Destino.AGUARDANDO_ETAPAS_4_5] == 1
    assert resumo[Destino.ENVIADO_ERROS] == 4
    assert (cfg.pasta_erros / "inedito_b.pdf").exists()


def test_nunca_sobrescreve_arquivo_ja_presente_no_destino(ambiente):
    cfg = ambiente
    cfg.pasta_ocr.mkdir()
    (cfg.pasta_ocr / "CEPE_2026-06-26_0001.pdf").write_bytes(b"anterior")
    executar(cfg)
    assert (cfg.pasta_ocr / "CEPE_2026-06-26_0001.pdf").read_bytes() == b"anterior"
    assert (cfg.pasta_ocr / "CEPE_2026-06-26_0001 (2).pdf").exists()


def test_pasta_dbf_nao_definida(ambiente):
    cfg = Configuracao(**{**ambiente.__dict__, "pasta_dbf": None})
    with pytest.raises(ErroConfiguracao, match="PASTA_DBF"):
        executar(cfg)


def test_pasta_dbf_vazia_aborta_antes_de_mexer_em_qualquer_arquivo(ambiente):
    cfg = ambiente
    (cfg.pasta_dbf / "Decreto 60929.pdf").unlink()
    antes = _estado(cfg)
    with pytest.raises(PastaDbfIndisponivel):
        executar(cfg)
    assert _estado(cfg) == antes


def test_pasta_temporaria_inexistente_nao_e_erro(ambiente):
    cfg = Configuracao(**{**ambiente.__dict__, "pasta_temp": ambiente.pasta_temp / "nao_existe"})
    assert executar(cfg) == {}


def test_pasta_temporaria_igual_a_da_dbf_nao_apaga_nada(ambiente):
    # Configuração equivocada: com PASTA_TEMP == PASTA_DBF, cada decreto da base
    # seria visto como "já existe na DBF" e apagado.
    cfg = ambiente
    base = cfg.pasta_dbf
    criar_pdf_texto(
        base / "Decreto 60951.pdf",
        texto_decreto("60.951", "Introduz alterações no Decreto nº 50.135."),
    )
    antes = sorted(arquivo.name for arquivo in base.iterdir())

    equivocada = Configuracao(**{**cfg.__dict__, "pasta_temp": base})
    with pytest.raises(ErroConfiguracao, match="PASTA_TEMP"):
        executar(equivocada)

    assert sorted(arquivo.name for arquivo in base.iterdir()) == antes
    assert not cfg.pasta_erros.exists()