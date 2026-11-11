"""Base da DBF com o PDF digitalizado (imagem): sem texto para comparar, a publicação conta
como já existente, a não ser que haja sinal de republicação (decisão de 10/11/2026)."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date
from pathlib import Path

import pymupdf
import pytest

from conftest import criar_pdf_imagem, texto_decreto
from dbf_cepe.config import Configuracao
from dbf_cepe.leitura.separador import AtoSeparado, ResultadoSeparacao, separar_caderno
from dbf_cepe.pipeline import relatorio as modulo_relatorio
from dbf_cepe.pipeline.exportador import gravar_pdf_do_ato
from dbf_cepe.pipeline.lote import Destino, executar
from dbf_cepe.pipeline.publicacao import Publicacao, ler_do_pdf
from dbf_cepe.pipeline.registro import BASE_IMAGEM, CONFERIR, Entrada, Registro
from dbf_cepe.pipeline.relatorio import nome_em_pendentes, relatorio_dos_cadernos
from dbf_cepe.pipeline.republicacao import Acao, planejar
from dbf_cepe.regras.especies import DECRETO

CONCESSAO = "Concede estímulo previsto na Lei nº 11.675, de 11 de outubro de 1999, que dispõe sobre o PRODEPE."


def pub(dia: str, pagina: int = 3, marcada: bool = False) -> Publicacao:
    d = date.fromisoformat(dia)
    return Publicacao(d, f"{d:%Y%m%d}_PoderExecutivo.pdf", pagina, marcada)


def ato(numero: str, publicacao: Publicacao, extra: str = "") -> AtoSeparado:
    return AtoSeparado(
        DECRETO, numero, f"DECRETO Nº {numero}", texto_decreto(f"{numero[:2]}.{numero[2:]}", CONCESSAO) + extra,
        publicacao.pagina, publicacao.pagina, data_publicacao=publicacao.data, caderno=publicacao.caderno,
        marcado_como_republicacao=publicacao.marcada_como_republicacao,
    )


@pytest.fixture
def cfg(tmp_path):
    c = Configuracao(
        pasta_temp=tmp_path / "pendentes", pasta_cadernos=tmp_path / "cadernos", pasta_erros=tmp_path / "erros",
        pasta_ocr=tmp_path / "ocr", pasta_normalizados=tmp_path / "normalizados", pasta_logs=tmp_path / "logs",
        pasta_dbf=tmp_path / "dbf",
    )
    c.pasta_temp.mkdir()
    c.pasta_dbf.mkdir()
    criar_pdf_imagem(c.pasta_dbf / "Decreto 48562.pdf")  # digitalizado, sem texto
    return c


def nomes(pasta: Path) -> list[str]:
    return sorted(p.name for p in pasta.iterdir()) if pasta.exists() else []


def _planejar(registro, publicacao, h="h", outra=False):
    return planejar(DECRETO, "48562", publicacao, h, registro, {"Decreto 48562.pdf"}, lambda nome: None,
                    outra_publicacao_no_lote=outra)


# ----------------------------------------------------------------------------
# Regra (planejar)
# ----------------------------------------------------------------------------


def test_base_imagem_sem_sinal_conta_como_ja_existente(tmp_path):
    plano = _planejar(Registro(tmp_path / "r.json"), pub("2020-01-15"))
    assert plano.acao is Acao.BASE_IMAGEM and "imagem" in plano.motivo


@pytest.mark.parametrize(
    ("publicacao", "outra", "registro_com_outro_texto", "sinal"),
    [
        (pub("2020-01-15", marcada=True), False, False, "marca como republicado"),
        (pub("2020-01-15"), True, False, "outro texto neste lote"),
        (pub("2020-01-15"), False, True, "outra publicação deste número"),
    ],
)
def test_base_imagem_com_sinal_de_republicacao_vai_para_conferir(tmp_path, publicacao, outra, registro_com_outro_texto, sinal):
    registro = Registro(tmp_path / "r.json")
    if registro_com_outro_texto:
        registro.adicionar(Entrada("Decreto", "48562", None, pub("2020-01-10"), "outro", None, BASE_IMAGEM))
    plano = _planejar(registro, publicacao, outra=outra)
    assert plano.acao is Acao.CONFERIR and sinal in plano.motivo


def test_registro_com_o_mesmo_texto_nao_e_sinal(tmp_path):
    registro = Registro(tmp_path / "r.json", [Entrada("Decreto", "48562", None, pub("2020-01-10"), "h", None, BASE_IMAGEM)])
    assert _planejar(registro, pub("2020-01-15"), h="h").acao is Acao.BASE_IMAGEM


def test_base_com_texto_diferente_continua_indo_para_conferir(tmp_path):
    plano = planejar(DECRETO, "48562", pub("2020-01-15"), "h", Registro(tmp_path / "r.json"),
                     {"Decreto 48562.pdf"}, lambda nome: False)
    assert plano.acao is Acao.CONFERIR and "texto diferente" in plano.motivo


def test_um_arquivo_legivel_e_diferente_ao_lado_de_um_em_imagem_vai_para_conferir(tmp_path):
    lidos = {"Decreto 48562.pdf": None, "Decreto 48562 (1).pdf": False}
    plano = planejar(DECRETO, "48562", pub("2020-01-15"), "h", Registro(tmp_path / "r.json"),
                     set(lidos), lambda nome: lidos[nome])
    assert plano.acao is Acao.CONFERIR


# ----------------------------------------------------------------------------
# Lote e --separar --gravar
# ----------------------------------------------------------------------------


def _pendente(cfg, a: AtoSeparado) -> Path:
    return gravar_pdf_do_ato(a, cfg.pasta_temp / nome_em_pendentes("Decreto 48562.pdf", a))


def test_lote_descarta_e_anota_no_registro(cfg):
    _pendente(cfg, ato("48562", pub("2020-01-15")))
    resumo = executar(cfg)
    assert resumo[Destino.DESCARTADO_DUPLICADO] == 1
    assert nomes(cfg.pasta_dbf) == ["Decreto 48562.pdf"] and nomes(cfg.conferir_duplicata) == []
    assert [e.situacao for e in Registro.carregar(cfg.registro).entradas] == [BASE_IMAGEM]


def test_lote_com_duas_publicacoes_diferentes_manda_a_segunda_para_conferir(cfg):
    _pendente(cfg, ato("48562", pub("2020-01-15")))
    _pendente(cfg, ato("48562", pub("2020-01-20"), extra="\nArt. 2º Republicado por incorreção."))
    resumo = executar(cfg)
    assert resumo[Destino.DESCARTADO_DUPLICADO] == 1 and resumo[Destino.ENVIADO_CONFERIR] == 1
    assert [e.situacao for e in Registro.carregar(cfg.registro).entradas] == [BASE_IMAGEM, CONFERIR]


def test_lote_com_a_marca_de_republicacao_manda_para_conferir(cfg):
    _pendente(cfg, ato("48562", pub("2020-01-15", marcada=True)))
    assert executar(cfg)[Destino.ENVIADO_CONFERIR] == 1


def test_marca_viaja_nos_metadados(cfg):
    caminho = _pendente(cfg, ato("48562", pub("2020-01-15", marcada=True)))
    assert ler_do_pdf(caminho).marcada_como_republicacao is True
    sem = _pendente(cfg, ato("48562", pub("2020-01-16")))
    assert ler_do_pdf(sem).marcada_como_republicacao is False


def test_separar_gravar_pula_o_da_base_em_imagem(cfg, tmp_path, monkeypatch, caplog):
    unico = ato("48562", pub("2020-01-15"))
    monkeypatch.setattr(modulo_relatorio, "separar_caderno", lambda caminho: ResultadoSeparacao((unico,), 9))
    caderno = tmp_path / "20200115_PoderExecutivo.pdf"
    caderno.write_bytes(b"%PDF-1.4")
    with caplog.at_level(logging.INFO, logger="pipeline"):
        relatorio_dos_cadernos(cfg, caderno, gravar=True)
    assert "já existe na DBF (PDF da base é imagem; não comparado)" in caplog.text
    assert nomes(cfg.pasta_temp) == []


def test_separar_gravar_com_dois_textos_no_lote_grava_os_dois(cfg, tmp_path, monkeypatch):
    """Com o mesmo número duas vezes no lote (textos diferentes), os dois vão para pendentes:
    o lote decide (o mais antigo conta como já existente; o outro vai para conferir)."""
    a1 = ato("48562", pub("2020-01-15"))
    a2 = ato("48562", pub("2020-01-20", pagina=4), extra="\nArt. 2º Republicado por incorreção.")
    por_caderno = {"20200115_PoderExecutivo.pdf": (a1,), "20200120_PoderExecutivo.pdf": (a2,)}
    monkeypatch.setattr(modulo_relatorio, "separar_caderno", lambda caminho: ResultadoSeparacao(por_caderno[Path(caminho).name], 9))
    pasta = tmp_path / "cad"
    pasta.mkdir()
    for nome in por_caderno:
        (pasta / nome).write_bytes(b"%PDF-1.4")
    relatorio_dos_cadernos(cfg, pasta, gravar=True)
    assert len(nomes(cfg.pasta_temp)) == 2
    resumo = executar(cfg)
    assert resumo[Destino.DESCARTADO_DUPLICADO] == 1 and resumo[Destino.ENVIADO_CONFERIR] == 1


def test_separador_marca_o_decreto_com_o_rodape_de_republicacao(tmp_path):
    texto = (
        "DECRETO Nº 61.055, DE 13 DE JULHO DE 2026.\nAbre crédito suplementar.\n"
        "A GOVERNADORA DO ESTADO, no uso das atribuições,\nDECRETA:\nArt. 1º Fica aberto.\n"
        "Palácio do Campo das Princesas, Recife, 13 de julho do ano de 2026, 210º da Revolução "
        "Republicana Constitucionalista e 204º da Independência do Brasil.\nRAQUEL TEIXEIRA LYRA LUCENA\n"
        "Governadora do Estado\n(REPUBLICADO POR HAVER SAÍDO COM INCORREÇÃO NO ORIGINAL)\n"
        "DECRETO Nº 61.056, DE 13 DE JULHO DE 2026.\nAbre crédito.\nA GOVERNADORA DO ESTADO, no uso,\n"
        "DECRETA:\nArt. 1º Fica aberto.\nPalácio do Campo das Princesas, Recife, 13 de julho do ano de 2026, "
        "210º da Revolução Republicana Constitucionalista e 204º da Independência do Brasil.\n"
    )
    caminho = tmp_path / "20260715_ResumoDiario.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_textbox(pymupdf.Rect(40, 40, 570, 800), texto, fontsize=8)
    doc.save(caminho)
    doc.close()
    marcas = {a.numero: a.marcado_como_republicacao for a in separar_caderno(caminho).atos}
    assert marcas == {"61055": True, "61056": False}


def test_publicacao_sem_a_chave_republicado_continua_valida():
    assert Publicacao.de_dict({"data": "2020-01-15", "caderno": "x.pdf", "pagina": 2}).marcada_como_republicacao is False
    assert replace(pub("2020-01-15"), marcada_como_republicacao=True).chave == pub("2020-01-15").chave
