"""Configuração: .env, caminhos relativos, caminho do Windows no Linux e pastas sobrepostas."""

from __future__ import annotations

import os

import pytest

from config import (
    RAIZ,
    Configuracao,
    ErroConfiguracao,
    carregar_configuracao,
    validar_configuracao,
)


@pytest.fixture(autouse=True)
def sem_variaveis_do_projeto(monkeypatch):
    for variavel in (
        "PASTA_DBF",
        "PASTA_TEMP",
        "PASTA_CADERNOS",
        "PASTA_ERROS",
        "PASTA_OCR",
        "PASTA_NORMALIZADOS",
        "PASTA_LOGS",
    ):
        monkeypatch.delenv(variavel, raising=False)


def test_padroes_sem_env(tmp_path):
    cfg = carregar_configuracao(tmp_path / "nao_existe.env")
    assert cfg.pasta_dbf is None
    assert cfg.pasta_temp == RAIZ / "dados/pendentes"
    assert cfg.pasta_ocr == RAIZ / "dados" / "leitura tesseract"


def test_le_o_env_com_espacos_no_caminho(tmp_path):
    # Caminho montado a partir do tmp_path: funciona no Windows e no Linux.
    pasta = tmp_path / "Meu Drive" / "DBF" / "Decretos"
    env = tmp_path / ".env"
    env.write_text(f"PASTA_DBF={pasta}\n", encoding="utf-8")
    cfg = carregar_configuracao(env)
    assert cfg.pasta_dbf == pasta


def test_caminho_relativo_e_resolvido_a_partir_da_raiz(tmp_path):
    env = tmp_path / ".env"
    env.write_text("PASTA_TEMP=entrada\n", encoding="utf-8")
    assert carregar_configuracao(env).pasta_temp == RAIZ / "entrada"


def test_variavel_de_ambiente_tem_precedencia_sobre_o_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(f"PASTA_DBF={tmp_path / 'do_env'}\n", encoding="utf-8")
    monkeypatch.setenv("PASTA_DBF", str(tmp_path / "do_ambiente"))
    assert carregar_configuracao(env).pasta_dbf == tmp_path / "do_ambiente"


@pytest.mark.skipif(os.name == "nt", reason="o alerta só vale fora do Windows")
def test_caminho_do_windows_no_linux_e_rejeitado(tmp_path):
    env = tmp_path / ".env"
    env.write_text("PASTA_DBF=G:\\Meu Drive\\DBF\n", encoding="utf-8")
    with pytest.raises(ErroConfiguracao, match="/mnt/g"):
        carregar_configuracao(env)


# ----------------------------------------------------------------------------
# Pastas sobrepostas: a DBF e as pastas de trabalho não podem coincidir nem
# ficar uma dentro da outra (o descarte de PDFs é definitivo).
# ----------------------------------------------------------------------------


def _cfg(tmp_path, **mudancas):
    pastas = dict(
        pasta_temp=tmp_path / "pendentes",
        pasta_cadernos=tmp_path / "cadernos",
        pasta_erros=tmp_path / "erros",
        pasta_ocr=tmp_path / "leitura tesseract",
        pasta_normalizados=tmp_path / "normalizados",
        pasta_logs=tmp_path / "logs",
        pasta_dbf=tmp_path / "dbf",
    )
    return Configuracao(**{**pastas, **mudancas})


def test_pastas_distintas_sao_aceitas(tmp_path):
    validar_configuracao(_cfg(tmp_path))


def test_padroes_do_projeto_nao_se_sobrepoem(tmp_path):
    # As pastas padrão (dados/..., logs) precisam coexistir com uma DBF qualquer.
    cfg = carregar_configuracao(tmp_path / "nao_existe.env")
    validar_configuracao(Configuracao(**{**cfg.__dict__, "pasta_dbf": tmp_path / "dbf"}))


def test_pastas_com_nome_parecido_nao_sao_sobreposicao(tmp_path):
    # "dbf" e "dbf2" começam igual como texto, mas são pastas irmãs.
    validar_configuracao(_cfg(tmp_path, pasta_temp=tmp_path / "dbf2"))


def test_sem_pasta_dbf_valida_so_as_de_trabalho(tmp_path):
    validar_configuracao(_cfg(tmp_path, pasta_dbf=None))
    with pytest.raises(ErroConfiguracao, match="PASTA_ERROS"):
        validar_configuracao(
            _cfg(tmp_path, pasta_dbf=None, pasta_erros=tmp_path / "pendentes")
        )


@pytest.mark.parametrize(
    "campo, relativo, citadas",
    [
        # trabalho x DBF: mesma pasta
        ("pasta_temp", "dbf", ("PASTA_TEMP", "PASTA_DBF")),
        ("pasta_normalizados", "dbf", ("PASTA_NORMALIZADOS", "PASTA_DBF")),
        # trabalho dentro da DBF
        ("pasta_temp", "dbf/pendentes", ("PASTA_TEMP", "PASTA_DBF")),
        ("pasta_erros", "dbf/erros", ("PASTA_ERROS", "PASTA_DBF")),
        ("pasta_logs", "dbf/logs", ("PASTA_LOGS", "PASTA_DBF")),
        # DBF dentro de uma pasta de trabalho
        ("pasta_dbf", "pendentes/dbf", ("PASTA_TEMP", "PASTA_DBF")),
        # trabalho x trabalho
        ("pasta_temp", "erros", ("PASTA_TEMP", "PASTA_ERROS")),
        ("pasta_ocr", "erros", ("PASTA_ERROS", "PASTA_OCR")),
        # o mesmo caminho escrito de outro jeito
        ("pasta_temp", "qualquer/../dbf", ("PASTA_TEMP", "PASTA_DBF")),
        ("pasta_temp", "DBF", ("PASTA_TEMP", "PASTA_DBF")),
    ],
)
def test_pastas_sobrepostas_sao_rejeitadas(tmp_path, campo, relativo, citadas):
    cfg = _cfg(tmp_path, **{campo: tmp_path / relativo})
    with pytest.raises(ErroConfiguracao) as erro:
        validar_configuracao(cfg)
    for nome in citadas:
        assert nome in str(erro.value)


@pytest.mark.skipif(os.name == "nt", reason="atalho simbólico exige privilégio no Windows")
def test_atalho_para_a_pasta_da_dbf_e_detectado(tmp_path):
    (tmp_path / "dbf").mkdir()
    (tmp_path / "atalho").symlink_to(tmp_path / "dbf", target_is_directory=True)
    with pytest.raises(ErroConfiguracao, match="PASTA_TEMP"):
        validar_configuracao(_cfg(tmp_path, pasta_temp=tmp_path / "atalho"))


def test_env_com_pastas_sobrepostas_e_rejeitado(tmp_path):
    base = tmp_path / "base"
    env = tmp_path / ".env"
    env.write_text(f"PASTA_DBF={base}\nPASTA_TEMP={base}\n", encoding="utf-8")
    with pytest.raises(ErroConfiguracao, match="PASTA_TEMP"):
        carregar_configuracao(env)


def test_pasta_de_cadernos_tem_padrao_e_aceita_o_env(tmp_path):
    padrao = carregar_configuracao(tmp_path / "nao_existe.env")
    assert padrao.pasta_cadernos == RAIZ / "dados" / "cadernos"

    env = tmp_path / ".env"
    env.write_text("PASTA_CADERNOS=/dados/cadernos_cepe\n", encoding="utf-8")
    assert carregar_configuracao(env).pasta_cadernos.parts[-2:] == ("dados", "cadernos_cepe")


def test_pasta_de_cadernos_nao_pode_ficar_dentro_da_dbf(tmp_path):
    with pytest.raises(ErroConfiguracao, match="PASTA_CADERNOS"):
        validar_configuracao(_cfg(tmp_path, pasta_cadernos=tmp_path / "dbf" / "cadernos"))