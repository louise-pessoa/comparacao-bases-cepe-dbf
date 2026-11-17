"""Configuração: .env, caminhos relativos, caminho do Windows no Linux e pastas sobrepostas."""

from __future__ import annotations

import os

import pytest

from dbf_cepe.config import (
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
        "HTTPS_PROXY",
        "https_proxy",
        "SSL_ESTRITO",
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


# ----------------------------------------------------------------------------
# Proxy da rede (HTTPS_PROXY)
# ----------------------------------------------------------------------------


def test_proxy_vem_vazio_por_padrao(tmp_path):
    assert carregar_configuracao(tmp_path / "nao_existe.env").proxy_https is None


def test_proxy_e_lido_do_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text("HTTPS_PROXY=http://127.0.0.1:9000\n", encoding="utf-8")
    assert carregar_configuracao(env).proxy_https == "http://127.0.0.1:9000"


def test_proxy_em_branco_no_env_conta_como_sem_proxy(tmp_path):
    env = tmp_path / ".env"
    env.write_text("HTTPS_PROXY=\n", encoding="utf-8")
    assert carregar_configuracao(env).proxy_https is None


def test_variavel_de_ambiente_do_proxy_tem_precedencia(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("HTTPS_PROXY=http://127.0.0.1:9000\n", encoding="utf-8")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8888")
    assert carregar_configuracao(env).proxy_https == "http://127.0.0.1:8888"


@pytest.mark.parametrize("valor", ["auto", "AUTO", " Auto "])
def test_proxy_auto_e_aceito_e_normalizado(tmp_path, valor):
    env = tmp_path / ".env"
    env.write_text(f"HTTPS_PROXY={valor}\n", encoding="utf-8")
    assert carregar_configuracao(env).proxy_https == "auto"


@pytest.mark.parametrize("valor", ["127.0.0.1:9000", "ftp://127.0.0.1:21", "http://", "proxy"])
def test_proxy_em_formato_invalido_e_recusado(tmp_path, valor):
    env = tmp_path / ".env"
    env.write_text(f"HTTPS_PROXY={valor}\n", encoding="utf-8")
    with pytest.raises(ErroConfiguracao, match="HTTPS_PROXY"):
        carregar_configuracao(env)


# ----------------------------------------------------------------------------
# Verificação estrita de certificado (SSL_ESTRITO)
# ----------------------------------------------------------------------------


def test_ssl_estrito_por_padrao(tmp_path):
    assert carregar_configuracao(tmp_path / "nao_existe.env").ssl_estrito is True


@pytest.mark.parametrize("valor", ["nao", "não", "false", "0", "NAO", "False"])
def test_ssl_estrito_pode_ser_desligado(tmp_path, valor):
    env = tmp_path / ".env"
    env.write_text(f"SSL_ESTRITO={valor}\n", encoding="utf-8")
    assert carregar_configuracao(env).ssl_estrito is False


@pytest.mark.parametrize("valor", ["sim", "true", "1", ""])
def test_ssl_estrito_ligado_ou_em_branco(tmp_path, valor):
    env = tmp_path / ".env"
    env.write_text(f"SSL_ESTRITO={valor}\n", encoding="utf-8")
    assert carregar_configuracao(env).ssl_estrito is True


def test_ssl_estrito_em_formato_invalido_e_recusado(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SSL_ESTRITO=talvez\n", encoding="utf-8")
    with pytest.raises(ErroConfiguracao, match="SSL_ESTRITO"):
        carregar_configuracao(env)


# ----------------------------------------------------------------------------
# HTTPS_PROXY do ambiente sobrepondo o do .env
# ----------------------------------------------------------------------------


def test_ambiente_diferente_do_env_e_sinalizado_sem_credenciais(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("HTTPS_PROXY=auto\n", encoding="utf-8")
    monkeypatch.setenv("HTTPS_PROXY", "http://usuario:segredo@127.0.0.1:61843")
    cfg = carregar_configuracao(env)
    assert cfg.proxy_https is not None and cfg.proxy_https.endswith("127.0.0.1:61843")  # o ambiente vence
    assert cfg.proxy_do_ambiente == "127.0.0.1:61843"


def test_ambiente_igual_ao_env_nao_e_sinalizado(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("HTTPS_PROXY=http://127.0.0.1:9000\n", encoding="utf-8")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9000")
    assert carregar_configuracao(env).proxy_do_ambiente is None


def test_ambiente_sem_proxy_no_env_nao_e_sinalizado(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("HTTPS_PROXY=\n", encoding="utf-8")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9000")
    assert carregar_configuracao(env).proxy_do_ambiente is None


def test_sem_variavel_de_ambiente_nao_ha_sinalizacao(tmp_path):
    env = tmp_path / ".env"
    env.write_text("HTTPS_PROXY=auto\n", encoding="utf-8")
    assert carregar_configuracao(env).proxy_do_ambiente is None
"""Proteja a leitura do .env e a trava contra pastas iguais ou aninhadas."""


# ----------------------------------------------------------------------------
# OCR (docs/plano_ocr.md)
# ----------------------------------------------------------------------------

_VARIAVEIS_OCR = (
    "OCR_ATIVO", "TESSERACT_CMD", "OCR_IDIOMA", "OCR_DPI", "OCR_TIMEOUT", "OCR_CONFIANCA_MEDIA",
    "OCR_CONFIANCA_CRITICA", "OCR_SIMILARIDADE_DUPLICATA", "OCR_GRAVAR_NA_BASE", "PASTA_CACHE_OCR", "OCR_TESSDATA",
)


@pytest.fixture
def sem_ocr_no_ambiente(monkeypatch):
    for variavel in _VARIAVEIS_OCR:
        monkeypatch.delenv(variavel, raising=False)


def test_ocr_padroes(tmp_path, sem_ocr_no_ambiente):
    cfg = carregar_configuracao(tmp_path / "nao_existe.env")
    assert cfg.ocr.ativo == "auto" and cfg.ocr.idioma == "por" and cfg.ocr.dpi == 300
    assert cfg.ocr.gravar_na_base is False  # piloto: desligado até ser conferido
    assert cfg.cache_ocr == RAIZ / "dados" / "cache ocr"


def test_ocr_le_o_env(tmp_path, sem_ocr_no_ambiente):
    env = tmp_path / ".env"
    env.write_text(
        "OCR_ATIVO=Não\nOCR_IDIOMA=por+eng\nOCR_DPI=400\nOCR_CONFIANCA_MEDIA=90,5\n"
        "OCR_SIMILARIDADE_DUPLICATA=0.97\nOCR_GRAVAR_NA_BASE=sim\n",
        encoding="utf-8",
    )
    cfg = carregar_configuracao(env).ocr
    assert (cfg.ativo, cfg.idioma, cfg.dpi, cfg.confianca_media) == ("nao", "por+eng", 400, 90.5)
    assert cfg.similaridade_duplicata == 0.97 and cfg.gravar_na_base is True


@pytest.mark.parametrize(
    "linha",
    [
        "OCR_ATIVO=talvez",
        "OCR_DPI=1200",
        "OCR_DPI=abc",
        "OCR_CONFIANCA_MEDIA=101",
        "OCR_SIMILARIDADE_DUPLICATA=nan",
        "OCR_IDIOMA=por --tessdata-dir x",  # nada além do nome do idioma vai para o Tesseract
        "OCR_IDIOMA=../por",
    ],
)
def test_ocr_valores_invalidos(tmp_path, sem_ocr_no_ambiente, linha):
    env = tmp_path / ".env"
    env.write_text(linha + "\n", encoding="utf-8")
    with pytest.raises(ErroConfiguracao):
        carregar_configuracao(env)


def test_cache_do_ocr_nao_pode_ficar_dentro_da_base(tmp_path, sem_ocr_no_ambiente):
    base = tmp_path / "dbf"
    env = tmp_path / ".env"
    env.write_text(f"PASTA_DBF={base}\nPASTA_CACHE_OCR={base / 'cache'}\n", encoding="utf-8")
    with pytest.raises(ErroConfiguracao, match="PASTA_CACHE_OCR"):
        carregar_configuracao(env)


def test_ocr_tessdata_relativo_a_raiz(tmp_path, sem_ocr_no_ambiente):
    env = tmp_path / ".env"
    env.write_text("OCR_TESSDATA=dados/tessdata\n", encoding="utf-8")
    assert carregar_configuracao(env).ocr.tessdata == RAIZ / "dados" / "tessdata"
