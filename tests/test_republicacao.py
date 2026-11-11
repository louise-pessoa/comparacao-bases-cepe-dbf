"""Republicação: data do caderno, registro de publicações, regra de nomes e o lote em duas
passagens. Nada aqui toca a base real: as pastas são temporárias."""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import date
from pathlib import Path

import pytest

from conftest import texto_decreto
from dbf_cepe import cli as modulo_cli
from dbf_cepe.config import Configuracao
from dbf_cepe.leitura.separador import AtoSeparado, data_do_caderno, separar_caderno
from dbf_cepe.pipeline import armazenamento
from dbf_cepe.pipeline.exportador import gravar_pdf_do_ato
from dbf_cepe.pipeline.lote import Destino, executar, ordenar_por_publicacao
from dbf_cepe.pipeline.publicacao import Publicacao, hash_do_texto, ler_do_pdf
from dbf_cepe.pipeline.registro import CONFERIR, DUPLICATA, GRAVADO, Entrada, ErroRegistro, Registro
from dbf_cepe.pipeline.relatorio import nome_em_pendentes, relatorio_dos_cadernos
from dbf_cepe.pipeline.republicacao import Acao, planejar
from dbf_cepe.regras.especies import DECRETO, EDITAL

AMOSTRAS_CADERNOS = Path(__file__).parent / "amostras_cadernos"
TRECHO = AMOSTRAS_CADERNOS / "Caderno 2026-06-26 (paginas 24 a 26).pdf"  # 60928..60937

# Sem o fecho, o critério da leitura em cascata reprova o decreto e o --gravar não o grava.
FECHO = (
    "\nPalácio do Campo das Princesas, Recife, 1º de julho do ano de 2026, 210º da Revolução "
    "Republicana Constitucionalista e 204º da Independência do Brasil."
)
CONCESSAO = "Concede estímulo previsto na Lei nº 11.675, de 11 de outubro de 1999, que dispõe sobre o PRODEPE."
ALTERACAO = "Introduz alterações no Decreto nº 50.135, de 2021, que concede estímulo do PRODEPE."


def pub(dia: str, caderno: str = "ResumoDiario", pagina: int = 3) -> Publicacao:
    d = date.fromisoformat(dia)
    return Publicacao(d, f"{d:%Y%m%d}_{caderno}.pdf", pagina)


def ato(numero: str, publicacao: Publicacao, ementa: str = CONCESSAO, extra: str = "") -> AtoSeparado:
    texto = texto_decreto(f"{numero[:2]}.{numero[2:]}", ementa) + extra + FECHO
    return AtoSeparado(
        DECRETO, numero, f"DECRETO Nº {numero}", texto, publicacao.pagina, publicacao.pagina,
        data_publicacao=publicacao.data, caderno=publicacao.caderno,
    )


@pytest.fixture
def cfg(tmp_path):
    c = Configuracao(
        pasta_temp=tmp_path / "pendentes",
        pasta_cadernos=tmp_path / "cadernos",
        pasta_erros=tmp_path / "erros",
        pasta_ocr=tmp_path / "leitura tesseract",
        pasta_normalizados=tmp_path / "normalizados",
        pasta_logs=tmp_path / "logs",
        pasta_dbf=tmp_path / "dbf",
    )
    c.pasta_temp.mkdir()
    c.pasta_dbf.mkdir()
    (c.pasta_dbf / "Decreto 1.pdf").write_bytes(b"outro decreto qualquer")  # a base nunca esta vazia
    return c


def pendente(cfg: Configuracao, a: AtoSeparado) -> Path:
    destino = cfg.pasta_temp / nome_em_pendentes(DECRETO.nome_arquivo(a.numero), a)
    return gravar_pdf_do_ato(a, destino)


def nomes(pasta: Path) -> list[str]:
    return sorted(p.name for p in pasta.iterdir()) if pasta.exists() else []


def registro(cfg: Configuracao) -> Registro:
    return Registro.carregar(cfg.registro)


# ----------------------------------------------------------------------------
# especies.py: nomes parametrizáveis
# ----------------------------------------------------------------------------


def test_nomes_da_publicacao_por_ordem():
    assert DECRETO.nome_da_publicacao("61055", 0) == "Decreto 61055.pdf"
    assert DECRETO.nome_da_publicacao("61055", 1) == "Decreto 61055 (republicação).pdf"
    assert DECRETO.nome_da_publicacao("61055", 2) == "Decreto 61055 (republicação 2).pdf"
    assert DECRETO.nome_da_publicacao("61055", 10) == "Decreto 61055 (republicação 10).pdf"
    assert DECRETO.nome_conferir_duplicata("61055") == "Decreto 61055 (conferir duplicata).pdf"
    assert EDITAL.nome_da_publicacao("064", 1, "2025") == "Edital 064-2025 (republicação).pdf"
    with pytest.raises(ValueError):
        DECRETO.nome_da_publicacao("61055", -1)


@pytest.mark.parametrize(
    ("nome", "ordem"),
    [
        ("Decreto 61055.pdf", 0),
        ("decreto 61055.PDF", 0),
        ("Decreto 61055 (republicação).pdf", 1),
        ("Decreto 61055 (republicação).pdf", 1),  # NFD, como pode vir do Drive
        ("Decreto 61055 (republicação 2).pdf", 2),
        ("Decreto 61055 (republicação 13).pdf", 13),
        ("Decreto 61055 (republicação 1).pdf", None),
        ("Decreto 61055 (republicação 02).pdf", None),
        ("Decreto 61055 (1).pdf", None),
        ("Decreto 610551.pdf", None),
    ],
)
def test_ordem_do_nome(nome, ordem):
    assert DECRETO.ordem_do_nome(nome, "61055") == ordem


@pytest.mark.parametrize(
    ("nome", "da_familia"),
    [
        ("Decreto 61055.pdf", True),
        ("Decreto 61055 (republicação 2).pdf", True),
        ("Decreto 61055 (1).pdf", True),  # fora do padrão, mas do mesmo número
        ("Decreto 61055-A.pdf", True),
        ("Decreto 610551.pdf", False),
        ("Decreto 6105.pdf", False),
        ("Edital 61055.pdf", False),
    ],
)
def test_familia_do_numero(nome, da_familia):
    assert DECRETO.e_da_familia(nome, "61055") is da_familia


def test_sufixos_vem_da_especie():
    from dataclasses import replace

    outra = replace(DECRETO, sufixo_republicacao=" - republicado", sufixo_republicacao_n=" - republicado {n}x")
    assert outra.nome_da_publicacao("7", 1) == "Decreto 7 - republicado.pdf"
    assert outra.nome_da_publicacao("7", 3) == "Decreto 7 - republicado 3x.pdf"
    assert outra.ordem_do_nome("Decreto 7 - republicado 3x.pdf", "7") == 3


# ----------------------------------------------------------------------------
# Data do caderno e metadados
# ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("nome", "data"),
    [
        ("20260715_ResumoDiario.pdf", date(2026, 7, 15)),
        ("20250916_PoderExecutivoExtra.pdf", date(2025, 9, 16)),
        ("20261301_ResumoDiario.pdf", None),
        ("Caderno 2026-06-26 (pagina 9).pdf", None),
        ("2026071_ResumoDiario.pdf", None),
    ],
)
def test_data_do_caderno_vem_do_nome(nome, data):
    assert data_do_caderno(nome) == data


def test_separar_caderno_leva_data_e_caderno(tmp_path):
    copia = tmp_path / "20260626_ResumoDiario.pdf"
    shutil.copy(TRECHO, copia)
    atos = separar_caderno(copia).atos
    assert atos and all(a.data_publicacao == date(2026, 6, 26) for a in atos)
    assert all(a.caderno == "20260626_ResumoDiario.pdf" for a in atos)
    assert all(a.data_publicacao is None for a in separar_caderno(TRECHO).atos)
    assert separar_caderno(str(copia)).atos[0].data_publicacao == date(2026, 6, 26)  # caminho como texto


def test_publicacao_viaja_nos_metadados_do_pdf_de_pendentes(cfg):
    p = pub("2026-07-15", pagina=4)
    caminho = pendente(cfg, ato("61055", p))
    assert caminho.name == "Decreto 61055 - 20260715_ResumoDiario p4.pdf"
    assert ler_do_pdf(caminho) == p


def test_pdf_sem_metadados_nao_tem_publicacao(cfg, tmp_path):
    a = AtoSeparado(DECRETO, "61055", "x", texto_decreto("61.055", CONCESSAO), 1, 1)
    assert ler_do_pdf(gravar_pdf_do_ato(a, tmp_path / "x.pdf")) is None
    (tmp_path / "quebrado.pdf").write_bytes(b"nada")
    assert ler_do_pdf(tmp_path / "quebrado.pdf") is None


def test_ordem_normal_antes_de_extra_e_por_pagina():
    normal, extra = pub("2026-09-30", "ResumoDiario", 9), pub("2026-09-30", "ResumoDiarioExtra", 1)
    assert normal.chave < extra.chave
    assert pub("2026-09-30", pagina=2).chave < pub("2026-09-30", pagina=7).chave
    assert extra.chave < pub("2026-10-01", "ResumoDiario", 1).chave
    # não depende da ordem alfabética: "Extra" viria antes de "ResumoDiario"
    assert pub("2026-10-01", "ResumoDiario", 5).chave < pub("2026-10-01", "Extra", 1).chave
    assert pub("2026-10-01", "PoderExecutivoExtra").extra and not pub("2026-10-01", "PoderExecutivo").extra


def test_hash_ignora_layout_mas_nao_o_texto():
    base = "Art. 1º Fica concedido o estímulo à empresa X, CNPJ 01.234/0001-42."
    assert hash_do_texto(base) == hash_do_texto(base.replace(" ", "\n").replace("0001-42", "0001- 42"))
    assert hash_do_texto(base) != hash_do_texto(base.replace("X", "Y"))


# ----------------------------------------------------------------------------
# Registro
# ----------------------------------------------------------------------------


def _entrada(numero="61055", dia="2026-07-15", arquivo="Decreto 61055.pdf", situacao=GRAVADO, h="h1"):
    return Entrada("Decreto", numero, None, pub(dia), h, arquivo, situacao)


def test_registro_ausente_e_vazio_e_ida_e_volta(tmp_path):
    caminho = tmp_path / "registro.json"
    r = Registro.carregar(caminho)
    assert r.entradas == [] and not caminho.exists()
    r.adicionar(_entrada())
    r.salvar()
    lido = Registro.carregar(caminho)
    assert [e.para_dict() | {"registrado_em": ""} for e in lido.entradas] == [
        _entrada().para_dict() | {"registrado_em": ""}
    ]
    assert lido.entradas[0].registrado_em  # carimbo de quando entrou
    assert not caminho.with_name("registro.json.tmp").exists()


@pytest.mark.parametrize(
    "conteudo",
    [
        "{nao e json",
        "[]",
        json.dumps({"versao": 99, "publicacoes": []}),
        json.dumps({"versao": 1}),
        json.dumps({"versao": 1, "publicacoes": [{"especie": "Decreto"}]}),
        json.dumps({"versao": 1, "publicacoes": [_entrada(situacao="inventada").para_dict()]}),
        json.dumps({"versao": 1, "publicacoes": [_entrada().para_dict() | {"pagina": 0}]}),
        json.dumps({"versao": 1, "publicacoes": [], "operacao_em_andamento": "x"}),
    ],
)
def test_registro_corrompido_aborta_sem_tocar_no_arquivo(tmp_path, conteudo):
    caminho = tmp_path / "registro.json"
    caminho.write_text(conteudo, encoding="utf-8")
    with pytest.raises(ErroRegistro, match="ilegível"):
        Registro.carregar(caminho)
    assert caminho.read_text(encoding="utf-8") == conteudo


def test_registro_salvo_pela_metade_nao_substitui_o_anterior(tmp_path, monkeypatch):
    caminho = tmp_path / "registro.json"
    r = Registro(caminho)
    r.adicionar(_entrada())
    r.salvar()
    antes = caminho.read_bytes()
    r.adicionar(_entrada(dia="2026-07-20", arquivo="Decreto 61055 (republicação).pdf", h="h2"))

    def queda(*args, **kwargs):
        raise OSError("queda de energia")

    monkeypatch.setattr(os, "replace", queda)
    with pytest.raises(ErroRegistro):
        r.salvar()
    assert caminho.read_bytes() == antes
    assert not caminho.with_name("registro.json.tmp").exists()


def test_cli_com_registro_corrompido_sai_com_codigo_2(cfg, monkeypatch, capsys):
    cfg.registro.write_text("{quebrado", encoding="utf-8")
    pendente(cfg, ato("61055", pub("2026-07-15")))
    monkeypatch.setattr(modulo_cli, "carregar_configuracao", lambda: cfg)
    assert modulo_cli.main([]) == 2
    assert "ilegível" in capsys.readouterr().err
    assert nomes(cfg.pasta_dbf) == ["Decreto 1.pdf"]
    assert len(nomes(cfg.pasta_temp)) == 1


# ----------------------------------------------------------------------------
# Regra de nomes (planejar), sem arquivos
# ----------------------------------------------------------------------------


def _planejar(r, base, p, h="novo", identico=lambda nome: False):
    return planejar(DECRETO, "61055", p, h, r, base, identico)


def test_numero_novo_e_gravado_com_o_nome_padrao(tmp_path):
    plano = _planejar(Registro(tmp_path / "r.json"), {"Decreto 1.pdf"}, pub("2026-07-15"))
    assert (plano.acao, plano.nome_final, plano.ordem, plano.renomeacoes) == (Acao.GRAVAR, "Decreto 61055.pdf", 0, ())


def test_publicacao_mais_nova_vira_republicacao(tmp_path):
    r = Registro(tmp_path / "r.json", [_entrada(dia="2026-07-10")])
    plano = _planejar(r, {"Decreto 61055.pdf"}, pub("2026-07-15"))
    assert (plano.acao, plano.nome_final, plano.renomeacoes) == (Acao.GRAVAR, "Decreto 61055 (republicação).pdf", ())
    assert plano.republicacao


def test_segunda_republicacao(tmp_path):
    r = Registro(tmp_path / "r.json", [
        _entrada(dia="2026-07-10"),
        _entrada(dia="2026-07-12", arquivo="Decreto 61055 (republicação).pdf", h="h2"),
    ])
    plano = _planejar(r, {"Decreto 61055.pdf", "Decreto 61055 (republicação).pdf"}, pub("2026-07-15"))
    assert plano.nome_final == "Decreto 61055 (republicação 2).pdf" and plano.ordem == 2


def test_publicacao_mais_antiga_renomeia_as_da_base_na_ordem_segura(tmp_path):
    r = Registro(tmp_path / "r.json", [
        _entrada(dia="2026-07-10"),
        _entrada(dia="2026-07-12", arquivo="Decreto 61055 (republicação).pdf", h="h2"),
    ])
    plano = _planejar(r, {"Decreto 61055.pdf", "Decreto 61055 (republicação).pdf"}, pub("2026-07-01"))
    assert plano.nome_final == "Decreto 61055.pdf" and plano.ordem == 0
    assert plano.renomeacoes == (
        ("Decreto 61055 (republicação).pdf", "Decreto 61055 (republicação 2).pdf"),
        ("Decreto 61055.pdf", "Decreto 61055 (republicação).pdf"),
    )


def test_texto_identico_ao_ja_gravado_e_duplicata_em_qualquer_ordem(tmp_path):
    r = Registro(tmp_path / "r.json", [_entrada(dia="2026-07-10", h="igual")])
    for dia in ("2026-07-15", "2026-07-01"):
        plano = _planejar(r, {"Decreto 61055.pdf"}, pub(dia), h="igual")
        assert plano.acao is Acao.DUPLICATA and "Decreto 61055.pdf" in plano.motivo


def test_mesma_publicacao_ja_registrada_e_ignorada(tmp_path):
    r = Registro(tmp_path / "r.json", [_entrada(dia="2026-07-10", h="x")])
    assert _planejar(r, {"Decreto 61055.pdf"}, pub("2026-07-10"), h="outro").acao is Acao.JA_REGISTRADA


def test_arquivo_da_base_sem_registro(tmp_path):
    r = Registro(tmp_path / "r.json")
    vistos = []

    def igual(nome):
        vistos.append(nome)
        return True

    assert _planejar(r, {"Decreto 61055.pdf"}, pub("2026-07-15"), identico=igual).acao is Acao.DUPLICATA
    assert vistos == ["Decreto 61055.pdf"]
    plano = _planejar(r, {"Decreto 61055.pdf"}, pub("2026-07-15"))
    assert (plano.acao, plano.nome_final) == (Acao.CONFERIR, "Decreto 61055 (conferir duplicata).pdf")


def test_arquivo_da_base_com_nome_fora_do_padrao_vai_para_conferir(tmp_path):
    r = Registro(tmp_path / "r.json")
    plano = _planejar(r, {"Decreto 61055 (1).pdf", "Decreto 610551.pdf"}, pub("2026-07-15"))
    assert plano.acao is Acao.CONFERIR and "Decreto 61055 (1).pdf" in plano.motivo
    assert "610551" not in plano.motivo


def test_registrado_mas_ausente_da_base_gera_aviso(tmp_path):
    r = Registro(tmp_path / "r.json", [_entrada(dia="2026-07-10")])
    plano = _planejar(r, {"Decreto 1.pdf"}, pub("2026-07-15"))
    assert plano.acao is Acao.GRAVAR and plano.nome_final == "Decreto 61055.pdf"
    assert plano.avisos and "não está na base" in plano.avisos[0]


def test_nomes_da_base_fora_da_ordem_do_registro_vao_para_conferir(tmp_path):
    # o registro diz que o arquivo de 10/07 se chama "(republicação)", mas ele é o mais antigo
    # e o nome padrão está tomado por outro registrado: renomear daria ciclo
    r = Registro(tmp_path / "r.json", [
        _entrada(dia="2026-07-10", arquivo="Decreto 61055 (republicação).pdf", h="a"),
        _entrada(dia="2026-07-12", arquivo="Decreto 61055.pdf", h="b"),
    ])
    plano = _planejar(r, {"Decreto 61055.pdf", "Decreto 61055 (republicação).pdf"}, pub("2026-07-15"))
    assert plano.acao is Acao.CONFERIR


# ----------------------------------------------------------------------------
# Lote em duas passagens (pastas temporárias no lugar da base)
# ----------------------------------------------------------------------------


def test_lote_ordena_por_publicacao_e_deixa_os_sem_data_por_ultimo(cfg, tmp_path):
    novo = pendente(cfg, ato("61055", pub("2026-07-15")))
    velho = pendente(cfg, ato("61055", pub("2026-07-10"), extra="\nArt. 2º Diferente."))
    extra = pendente(cfg, ato("61056", pub("2026-07-10", "ResumoDiarioExtra", 1)))
    sem = gravar_pdf_do_ato(
        AtoSeparado(DECRETO, "61057", "x", texto_decreto("61.057", CONCESSAO), 1, 1), cfg.pasta_temp / "A.pdf"
    )
    assert [p for p, _ in ordenar_por_publicacao(sorted(cfg.pasta_temp.iterdir()))] == [velho, extra, novo, sem]


def test_duas_publicacoes_no_mesmo_lote(cfg):
    pendente(cfg, ato("61055", pub("2026-07-15"), extra="\nArt. 2º Republicado por incorreção."))
    pendente(cfg, ato("61055", pub("2026-07-10")))
    resumo = executar(cfg)
    assert resumo[Destino.CONCLUIDO] == 2
    assert nomes(cfg.pasta_dbf) == ["Decreto 1.pdf", "Decreto 61055 (republicação).pdf", "Decreto 61055.pdf"]
    entradas = registro(cfg).entradas
    assert [(e.publicacao.data.day, e.arquivo, e.situacao) for e in entradas] == [
        (10, "Decreto 61055.pdf", GRAVADO),
        (15, "Decreto 61055 (republicação).pdf", GRAVADO),
    ]
    assert registro(cfg).operacao_em_andamento is None
    assert nomes(cfg.pasta_temp) == []


def test_etapa_3_nao_descarta_republicacao_de_lote_anterior(cfg):
    pendente(cfg, ato("61055", pub("2026-07-10")))
    executar(cfg)
    pendente(cfg, ato("61055", pub("2026-07-15"), extra="\nArt. 2º Republicado."))
    resumo = executar(cfg)
    assert resumo[Destino.CONCLUIDO] == 1 and not resumo[Destino.DESCARTADO_DUPLICADO]
    assert "Decreto 61055 (republicação).pdf" in nomes(cfg.pasta_dbf)


def test_publicacao_mais_antiga_que_a_gravada_renomeia_a_da_base(cfg):
    pendente(cfg, ato("61055", pub("2026-07-15"), extra="\nArt. 2º Republicado."))
    executar(cfg)
    gravado_antes = (cfg.pasta_dbf / "Decreto 61055.pdf").read_bytes()
    pendente(cfg, ato("61055", pub("2026-07-10")))
    resumo = executar(cfg)
    assert resumo[Destino.CONCLUIDO] == 1
    assert (cfg.pasta_dbf / "Decreto 61055 (republicação).pdf").read_bytes() == gravado_antes
    assert (cfg.pasta_dbf / "Decreto 61055.pdf").read_bytes() != gravado_antes
    por_arquivo = {e.arquivo: e.publicacao.data.day for e in registro(cfg).entradas}
    assert por_arquivo == {"Decreto 61055.pdf": 10, "Decreto 61055 (republicação).pdf": 15}


def test_terceira_publicacao_e_republicacao_2(cfg):
    for dia, extra in (("2026-07-10", ""), ("2026-07-15", "\nArt. 2º A."), ("2026-07-20", "\nArt. 2º B.")):
        pendente(cfg, ato("61055", pub(dia), extra=extra))
    executar(cfg)
    assert "Decreto 61055 (republicação 2).pdf" in nomes(cfg.pasta_dbf)


def test_textos_identicos_em_datas_diferentes(cfg):
    pendente(cfg, ato("61392", pub("2026-09-30", "ResumoDiarioExtra", 2)))
    pendente(cfg, ato("61392", pub("2026-10-01", "ResumoDiarioExtra", 1)))
    resumo = executar(cfg)
    assert resumo[Destino.CONCLUIDO] == 1 and resumo[Destino.DESCARTADO_DUPLICADO] == 1
    assert nomes(cfg.pasta_dbf) == ["Decreto 1.pdf", "Decreto 61392.pdf"]
    assert [(e.publicacao.data.day, e.situacao) for e in registro(cfg).entradas] == [(30, GRAVADO), (1, DUPLICATA)]


def test_mesmo_numero_no_normal_e_na_extra_do_mesmo_dia(cfg):
    pendente(cfg, ato("61400", pub("2026-10-01", "ResumoDiarioExtra", 1), extra="\nArt. 2º Extra."))
    pendente(cfg, ato("61400", pub("2026-10-01", "ResumoDiario", 9)))
    executar(cfg)
    por_arquivo = {e.arquivo: e.publicacao.caderno for e in registro(cfg).entradas}
    assert por_arquivo == {
        "Decreto 61400.pdf": "20261001_ResumoDiario.pdf",
        "Decreto 61400 (republicação).pdf": "20261001_ResumoDiarioExtra.pdf",
    }


def test_rodar_o_mesmo_lote_de_novo_nao_muda_nada(cfg, tmp_path):
    a = ato("61055", pub("2026-07-10"))
    pendente(cfg, a)
    executar(cfg)
    base, reg = nomes(cfg.pasta_dbf), cfg.registro.read_text(encoding="utf-8")
    pendente(cfg, a)
    resumo = executar(cfg)
    assert resumo[Destino.DESCARTADO_DUPLICADO] == 1
    assert nomes(cfg.pasta_dbf) == base and cfg.registro.read_text(encoding="utf-8") == reg


def test_base_sem_registro_texto_identico_e_descartado(cfg, tmp_path):
    a = ato("60500", pub("2026-07-10"))
    gravar_pdf_do_ato(a, cfg.pasta_dbf / "Decreto 60500.pdf")  # gravado antes do registro existir
    pendente(cfg, a)
    resumo = executar(cfg)
    assert resumo[Destino.DESCARTADO_DUPLICADO] == 1
    assert nomes(cfg.pasta_dbf) == ["Decreto 1.pdf", "Decreto 60500.pdf"]
    assert [e.situacao for e in registro(cfg).entradas] == [DUPLICATA]


def test_base_sem_registro_texto_diferente_vai_para_conferir_e_nunca_para_a_base(cfg):
    gravar_pdf_do_ato(ato("60500", pub("2026-07-10")), cfg.pasta_dbf / "Decreto 60500.pdf")
    base_antes = {p.name: p.read_bytes() for p in cfg.pasta_dbf.iterdir()}
    pendente(cfg, ato("60500", pub("2026-07-15"), extra="\nArt. 2º Republicado."))
    resumo = executar(cfg)
    assert resumo[Destino.ENVIADO_CONFERIR] == 1
    assert {p.name: p.read_bytes() for p in cfg.pasta_dbf.iterdir()} == base_antes
    assert nomes(cfg.conferir_duplicata) == ["Decreto 60500 (conferir duplicata).pdf"]
    assert cfg.conferir_duplicata == cfg.pasta_temp.parent / "conferir duplicata"
    assert [(e.situacao, e.arquivo) for e in registro(cfg).entradas] == [
        (CONFERIR, "Decreto 60500 (conferir duplicata).pdf")
    ]
    assert nomes(cfg.pasta_temp) == []


def test_base_com_nome_fora_do_padrao_vai_para_conferir(cfg):
    gravar_pdf_do_ato(ato("60500", pub("2026-07-10")), cfg.pasta_dbf / "Decreto 60500 (1).pdf")
    pendente(cfg, ato("60500", pub("2026-07-15"), extra="\nArt. 2º Outro."))
    assert executar(cfg)[Destino.ENVIADO_CONFERIR] == 1


def test_simulacao_nao_toca_em_nada_mas_planeja_em_sequencia(cfg, caplog):
    pendente(cfg, ato("61055", pub("2026-07-15"), extra="\nArt. 2º Republicado."))
    pendente(cfg, ato("61055", pub("2026-07-10")))
    with caplog.at_level(logging.INFO, logger="pipeline"):
        resumo = executar(cfg, simular=True)
    assert resumo[Destino.CONCLUIDO] == 2
    assert "Decreto 61055 (republicação).pdf" in caplog.text
    assert nomes(cfg.pasta_dbf) == ["Decreto 1.pdf"] and not cfg.registro.exists()
    assert len(nomes(cfg.pasta_temp)) == 2


def test_pdf_sem_publicacao_segue_a_regra_antiga(cfg):
    gravar_pdf_do_ato(ato("60500", pub("2026-07-10")), cfg.pasta_dbf / "Decreto 60500.pdf")
    a = AtoSeparado(DECRETO, "60500", "x", texto_decreto("60.500", CONCESSAO) + "\nArt. 2º Outro.", 1, 1)
    gravar_pdf_do_ato(a, cfg.pasta_temp / "manual.pdf")
    assert executar(cfg)[Destino.DESCARTADO_DUPLICADO] == 1
    assert not cfg.registro.exists()


# ----------------------------------------------------------------------------
# Falhas no meio da renomeação: desfazer e recuperar
# ----------------------------------------------------------------------------


def _base_com_gravado_em_15(cfg) -> bytes:
    pendente(cfg, ato("61055", pub("2026-07-15"), extra="\nArt. 2º Republicado."))
    executar(cfg)
    pendente(cfg, ato("61055", pub("2026-07-10")))
    return (cfg.pasta_dbf / "Decreto 61055.pdf").read_bytes()


def test_falha_ao_gravar_depois_de_renomear_desfaz_a_renomeacao(cfg, monkeypatch):
    original = _base_com_gravado_em_15(cfg)
    registro_antes = registro(cfg).entradas

    def falha(*args, **kwargs):
        raise armazenamento.ErroArmazenamento("drive desmontado")

    monkeypatch.setattr(armazenamento, "armazenar", falha)
    resumo = executar(cfg)
    assert resumo[Destino.FALHA_AO_GRAVAR] == 1
    assert nomes(cfg.pasta_dbf) == ["Decreto 1.pdf", "Decreto 61055.pdf"]
    assert (cfg.pasta_dbf / "Decreto 61055.pdf").read_bytes() == original
    depois = registro(cfg)
    assert depois.entradas == registro_antes and depois.operacao_em_andamento is None
    assert len(nomes(cfg.pasta_temp)) == 1  # fica para o próximo lote


def test_queda_entre_renomear_e_gravar_e_desfeita_na_proxima_execucao(cfg, monkeypatch):
    original = _base_com_gravado_em_15(cfg)

    def queda(*args, **kwargs):
        raise KeyboardInterrupt  # simula o programa morrendo: nada de desfazer

    monkeypatch.setattr(armazenamento, "armazenar", queda)
    with pytest.raises(KeyboardInterrupt):
        executar(cfg)
    assert nomes(cfg.pasta_dbf) == ["Decreto 1.pdf", "Decreto 61055 (republicação).pdf"]
    assert registro(cfg).operacao_em_andamento is not None
    with pytest.raises(ErroRegistro, match="não terminou"):
        executar(cfg, simular=True)

    monkeypatch.undo()
    resumo = executar(cfg)  # desfaz a operação e processa de novo o pendente
    assert resumo[Destino.CONCLUIDO] == 1
    assert (cfg.pasta_dbf / "Decreto 61055 (republicação).pdf").read_bytes() == original
    assert registro(cfg).operacao_em_andamento is None
    assert sorted(e.arquivo for e in registro(cfg).entradas) == [
        "Decreto 61055 (republicação).pdf", "Decreto 61055.pdf"
    ]


def test_queda_depois_de_gravar_e_antes_de_registrar_e_concluida(cfg, monkeypatch):
    _base_com_gravado_em_15(cfg)
    verdadeiro = Registro.salvar
    chamadas = []

    def cai_no_segundo(self):
        chamadas.append(1)
        if len(chamadas) == 2:  # 1ª = anota a operação; 2ª = registra a conclusão
            raise KeyboardInterrupt
        verdadeiro(self)

    monkeypatch.setattr(Registro, "salvar", cai_no_segundo)
    with pytest.raises(KeyboardInterrupt):
        executar(cfg)
    monkeypatch.undo()
    assert nomes(cfg.pasta_dbf) == ["Decreto 1.pdf", "Decreto 61055 (republicação).pdf", "Decreto 61055.pdf"]
    assert registro(cfg).operacao_em_andamento is not None

    executar(cfg)
    r = registro(cfg)
    assert r.operacao_em_andamento is None
    assert {e.arquivo: e.publicacao.data.day for e in r.entradas} == {
        "Decreto 61055.pdf": 10, "Decreto 61055 (republicação).pdf": 15
    }


def test_renomear_nunca_sobrescreve(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"a")
    (tmp_path / "b.pdf").write_bytes(b"b")
    with pytest.raises(armazenamento.ArquivoJaExiste):
        armazenamento.renomear_na_base(tmp_path, "a.pdf", "b.pdf")
    assert (tmp_path / "b.pdf").read_bytes() == b"b"


def test_desfazer_que_falha_deixa_a_operacao_para_a_proxima_execucao(cfg, monkeypatch):
    _base_com_gravado_em_15(cfg)

    def falha(*args, **kwargs):
        raise armazenamento.ErroArmazenamento("falhou")

    monkeypatch.setattr(armazenamento, "armazenar", falha)
    monkeypatch.setattr(armazenamento, "desfazer_renomeacoes", lambda pasta, feitas: ["sem permissão"])
    assert executar(cfg)[Destino.FALHA_AO_GRAVAR] == 1
    assert registro(cfg).operacao_em_andamento is not None


# ----------------------------------------------------------------------------
# --separar --gravar com cadernos datados
# ----------------------------------------------------------------------------


def test_separar_gravar_entrega_as_duas_ocorrencias_do_mesmo_caderno(cfg, tmp_path, monkeypatch, caplog):
    from dbf_cepe.leitura.separador import ResultadoSeparacao
    from dbf_cepe.pipeline import relatorio as modulo_relatorio

    p1, p2 = pub("2026-07-15", pagina=3), pub("2026-07-15", pagina=8)
    a1, a2 = ato("60777", p1), ato("60777", p2, extra="\nArt. 2º Republicado por incorreção.")
    monkeypatch.setattr(modulo_relatorio, "separar_caderno", lambda caminho: ResultadoSeparacao((a1, a2), 9))
    caderno = tmp_path / "20260715_ResumoDiario.pdf"
    shutil.copy(TRECHO, caderno)
    with caplog.at_level(logging.INFO, logger="pipeline"):
        relatorio_dos_cadernos(cfg, caderno, gravar=True)
    assert nomes(cfg.pasta_temp) == [
        "Decreto 60777 - 20260715_ResumoDiario p3.pdf", "Decreto 60777 - 20260715_ResumoDiario p8.pdf"
    ]
    resumo = executar(cfg)
    assert resumo[Destino.CONCLUIDO] == 2
    assert {"Decreto 60777.pdf", "Decreto 60777 (republicação).pdf"} <= set(nomes(cfg.pasta_dbf))


def test_separar_gravar_pula_o_ja_registrado_e_o_identico_ao_da_base(cfg, tmp_path, monkeypatch, caplog):
    from dbf_cepe.leitura.separador import ResultadoSeparacao
    from dbf_cepe.pipeline import relatorio as modulo_relatorio

    registrado, igual_base = ato("60777", pub("2026-07-15", pagina=3)), ato("60778", pub("2026-07-15", pagina=5))
    pendente(cfg, registrado)
    executar(cfg)
    gravar_pdf_do_ato(igual_base, cfg.pasta_dbf / "Decreto 60778.pdf")
    monkeypatch.setattr(
        modulo_relatorio, "separar_caderno", lambda caminho: ResultadoSeparacao((registrado, igual_base), 9)
    )
    with caplog.at_level(logging.INFO, logger="pipeline"):
        caderno = tmp_path / "20260715_ResumoDiario.pdf"
        shutil.copy(TRECHO, caderno)
        relatorio_dos_cadernos(cfg, caderno, gravar=True)
    assert nomes(cfg.pasta_temp) == []
    assert "já registrado (mesma publicação)" in caplog.text
    assert "já existe na DBF (texto idêntico)" in caplog.text


def test_separar_gravar_com_registro_corrompido_nao_grava(cfg, tmp_path):
    cfg.registro.write_text("{", encoding="utf-8")
    caderno = tmp_path / "20260626_ResumoDiario.pdf"
    shutil.copy(TRECHO, caderno)
    with pytest.raises(ErroRegistro):
        relatorio_dos_cadernos(cfg, caderno, gravar=True)
    assert nomes(cfg.pasta_temp) == []


def test_separar_sem_gravar_nao_le_o_registro(cfg, tmp_path):
    cfg.registro.write_text("{", encoding="utf-8")
    caderno = tmp_path / "20260626_ResumoDiario.pdf"
    shutil.copy(TRECHO, caderno)
    relatorio_dos_cadernos(cfg, caderno)  # só lista: o registro nem é aberto


def _operacao_em_cadeia(tmp_path):
    """Base com N (10/07) e N (republicação) (12/07); chega 01/07: N(rep) -> N(rep 2), N -> N(rep), grava N."""
    pasta = tmp_path / "base"
    pasta.mkdir()
    r = Registro(tmp_path / "r.json", [
        _entrada(dia="2026-07-10", h="a"),
        _entrada(dia="2026-07-12", arquivo="Decreto 61055 (republicação).pdf", h="b"),
    ])
    renomeacoes = [
        ["Decreto 61055 (republicação).pdf", "Decreto 61055 (republicação 2).pdf"],
        ["Decreto 61055.pdf", "Decreto 61055 (republicação).pdf"],
    ]
    r.operacao_em_andamento = {
        "especie": "Decreto", "numero": "61055", "ano": None, "renomeacoes": renomeacoes,
        "gravar": "Decreto 61055.pdf", "nova_entrada": _entrada(dia="2026-07-01", h="c").para_dict(),
    }
    return pasta, r


@pytest.mark.parametrize("feitas", [0, 1, 2])
def test_recuperar_desfaz_qualquer_ponto_da_cadeia(tmp_path, feitas):
    pasta, r = _operacao_em_cadeia(tmp_path)
    (pasta / "Decreto 61055.pdf").write_bytes(b"de 10/07")
    (pasta / "Decreto 61055 (republicação).pdf").write_bytes(b"de 12/07")
    for de, para in r.operacao_em_andamento["renomeacoes"][:feitas]:
        (pasta / de).rename(pasta / para)
    (pasta / "Decreto 61055.pdf.part").write_bytes(b"sobra") if feitas == 2 else None
    mensagem = armazenamento.recuperar_operacao(r, pasta)
    assert "desfeita" in mensagem
    assert {p.name: p.read_bytes() for p in pasta.iterdir()} == {
        "Decreto 61055.pdf": b"de 10/07", "Decreto 61055 (republicação).pdf": b"de 12/07"
    }
    assert r.operacao_em_andamento is None and len(r.entradas) == 2


def test_recuperar_conclui_quando_tudo_foi_feito(tmp_path):
    pasta, r = _operacao_em_cadeia(tmp_path)
    for nome in ("Decreto 61055.pdf", "Decreto 61055 (republicação).pdf", "Decreto 61055 (republicação 2).pdf"):
        (pasta / nome).write_bytes(b"x")
    assert "concluída" in armazenamento.recuperar_operacao(r, pasta)
    assert {e.arquivo: e.publicacao.data.day for e in r.entradas} == {
        "Decreto 61055.pdf": 1, "Decreto 61055 (republicação).pdf": 10, "Decreto 61055 (republicação 2).pdf": 12
    }


def test_recuperar_com_base_que_nao_confere_pede_conferencia_manual(tmp_path):
    pasta, r = _operacao_em_cadeia(tmp_path)
    (pasta / "Decreto 61055 (republicação 2).pdf").write_bytes(b"x")  # só este: nenhum passo explica
    with pytest.raises(ErroRegistro, match="confira à mão"):
        armazenamento.recuperar_operacao(r, pasta)
    assert r.operacao_em_andamento is not None
