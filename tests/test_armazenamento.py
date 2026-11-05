"""Etapa 5: gravação do PDF normalizado na pasta da DBF e descarte dos PDFs de trabalho."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest

from dbf_cepe.pipeline import armazenamento
from dbf_cepe.pipeline import normalizador
from dbf_cepe.pipeline.armazenamento import (
    ArquivoJaExiste,
    ErroArmazenamento,
    GravacaoDivergente,
    armazenar,
    descartar_se_existir,
)
from conftest import criar_pdf_texto, texto_decreto
from dbf_cepe.leitura.identificador import identificar
from dbf_cepe.leitura.reader import ler_pdf

NOME = "Decreto 60951.pdf"


@pytest.fixture
def ambiente(tmp_path):
    dbf = tmp_path / "dbf"
    dbf.mkdir()
    (dbf / "Decreto 60929.pdf").write_bytes(b"ja existe")
    pendentes = tmp_path / "pendentes"
    pendentes.mkdir()
    original = pendentes / "inedito.pdf"
    criar_pdf_texto(original, texto_decreto("60.951", "Introduz alterações no Decreto nº 50.135."))
    ident = identificar(ler_pdf(original).texto)
    gerado = normalizador.gerar_pdf_normalizado(original, ident, tmp_path / "normalizados")
    return dbf, original, gerado


def _nomes(pasta: Path):
    return sorted(p.name for p in pasta.iterdir())


def test_grava_na_dbf_com_o_nome_final_e_descarta_os_pdfs_de_trabalho(ambiente):
    dbf, original, gerado = ambiente
    conteudo = gerado.read_bytes()
    destino = armazenar(gerado, NOME, dbf, original)
    assert destino == dbf / NOME
    assert destino.read_bytes() == conteudo
    assert _nomes(dbf) == ["Decreto 60929.pdf", NOME]  # sem .part
    assert not original.exists() and not gerado.exists()


def test_o_arquivo_gravado_pode_ser_lido_como_o_decreto(ambiente):
    dbf, original, gerado = ambiente
    armazenar(gerado, NOME, dbf, original)
    ident = identificar(ler_pdf(dbf / NOME).texto)
    assert ident.especie.nome_arquivo(ident.numero) == NOME


def test_simulacao_nao_grava_nem_apaga_nada(ambiente):
    dbf, original, gerado = ambiente
    assert armazenar(gerado, NOME, dbf, original, simular=True) == dbf / NOME
    assert _nomes(dbf) == ["Decreto 60929.pdf"]
    assert original.exists() and gerado.exists()


def test_simulacao_aceita_pdf_gerado_inexistente(ambiente):
    # Em --simular a etapa 4 devolve um caminho que não foi gravado.
    dbf, original, gerado = ambiente
    armazenar(dbf.parent / "nao_existe.pdf", NOME, dbf, original, simular=True)


def test_simulacao_avisa_que_o_nome_ja_existe(ambiente):
    dbf, original, gerado = ambiente
    with pytest.raises(ArquivoJaExiste):
        armazenar(gerado, "Decreto 60929.pdf", dbf, original, simular=True)


def test_nunca_sobrescreve_um_decreto_que_ja_existe(ambiente):
    dbf, original, gerado = ambiente
    (dbf / NOME).write_bytes(b"salvo por outra pessoa")
    with pytest.raises(ArquivoJaExiste):
        armazenar(gerado, NOME, dbf, original)
    assert (dbf / NOME).read_bytes() == b"salvo por outra pessoa"
    assert _nomes(dbf) == ["Decreto 60929.pdf", NOME]
    assert original.exists() and gerado.exists()


def test_pdf_gerado_de_outro_decreto_e_recusado(ambiente):
    dbf, original, gerado = ambiente
    with pytest.raises(GravacaoDivergente, match="não se identifica"):
        armazenar(gerado, "Decreto 60999.pdf", dbf, original)
    assert _nomes(dbf) == ["Decreto 60929.pdf"]
    assert original.exists() and gerado.exists()


def test_pdf_gerado_corrompido_e_recusado(ambiente):
    dbf, original, gerado = ambiente
    gerado.write_bytes(b"nao e um pdf")
    with pytest.raises(GravacaoDivergente, match="não pôde ser lido"):
        armazenar(gerado, NOME, dbf, original)
    assert _nomes(dbf) == ["Decreto 60929.pdf"]
    assert original.exists()


def test_pdf_gerado_ausente_e_recusado(ambiente):
    dbf, original, gerado = ambiente
    gerado.unlink()
    with pytest.raises(GravacaoDivergente):
        armazenar(gerado, NOME, dbf, original)
    assert original.exists()


def test_pasta_da_dbf_indisponivel_mantem_o_original(ambiente):
    dbf, original, gerado = ambiente
    sumiu = dbf.parent / "g_desmontado"
    with pytest.raises(ErroArmazenamento, match="indisponível") as erro:
        armazenar(gerado, NOME, sumiu, original)
    assert not isinstance(erro.value, (ArquivoJaExiste, GravacaoDivergente))
    assert original.exists() and gerado.exists()


def test_sobra_de_part_de_execucao_interrompida_e_substituida(ambiente):
    dbf, original, gerado = ambiente
    (dbf / (NOME + ".part")).write_bytes(b"lixo de uma execucao interrompida")
    armazenar(gerado, NOME, dbf, original)
    assert _nomes(dbf) == ["Decreto 60929.pdf", NOME]


def test_erro_de_e_s_na_copia_nao_deixa_part_e_mantem_o_original(ambiente, monkeypatch):
    dbf, original, gerado = ambiente

    def disco_cheio(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(shutil, "copyfileobj", disco_cheio)
    with pytest.raises(ErroArmazenamento, match="OSError") as erro:
        armazenar(gerado, NOME, dbf, original)
    assert not isinstance(erro.value, (ArquivoJaExiste, GravacaoDivergente))
    assert _nomes(dbf) == ["Decreto 60929.pdf"]
    assert original.exists() and gerado.exists()


def test_copia_truncada_e_detectada_pelo_hash(ambiente, monkeypatch):
    dbf, original, gerado = ambiente

    def copia_truncada(origem, saida, tamanho=0):
        saida.write(origem.read()[:100])

    monkeypatch.setattr(shutil, "copyfileobj", copia_truncada)
    with pytest.raises(GravacaoDivergente, match="difere"):
        armazenar(gerado, NOME, dbf, original)
    assert _nomes(dbf) == ["Decreto 60929.pdf"]
    assert original.exists() and gerado.exists()


def test_nome_que_aparece_durante_a_copia_nao_e_sobrescrito(ambiente, monkeypatch):
    dbf, original, gerado = ambiente
    sha = armazenamento._sha256
    chamadas = []

    def sha_e_alguem_salva(caminho):
        chamadas.append(caminho.name)
        resultado = sha(caminho)
        if len(chamadas) == 2:  # conferência do .part, logo antes de renomear
            (dbf / NOME).write_bytes(b"salvo no meio do caminho")
        return resultado

    monkeypatch.setattr(armazenamento, "_sha256", sha_e_alguem_salva)
    with pytest.raises(ArquivoJaExiste, match="durante"):
        armazenar(gerado, NOME, dbf, original)
    assert (dbf / NOME).read_bytes() == b"salvo no meio do caminho"
    assert _nomes(dbf) == ["Decreto 60929.pdf", NOME]  # sem .part
    assert original.exists() and gerado.exists()


def test_nome_que_aparece_na_hora_de_renomear_nao_e_sobrescrito(ambiente, monkeypatch):
    dbf, original, gerado = ambiente

    def renomear_recusa(origem, destino):
        raise FileExistsError(destino)

    monkeypatch.setattr(armazenamento.os, "rename", renomear_recusa)
    with pytest.raises(ArquivoJaExiste, match="durante"):
        armazenar(gerado, NOME, dbf, original)
    assert _nomes(dbf) == ["Decreto 60929.pdf"]  # o .part foi removido
    assert original.exists() and gerado.exists()


def test_arquivo_final_que_nao_confere_e_removido(ambiente, monkeypatch):
    dbf, original, gerado = ambiente
    conferir = armazenamento._conferir_se_identifica_como
    chamadas = []

    def falha_na_segunda(pdf, nome, descricao):
        chamadas.append(descricao)
        if len(chamadas) == 2:  # releitura do arquivo final
            raise GravacaoDivergente("ilegível na DBF")
        conferir(pdf, nome, descricao)

    monkeypatch.setattr(armazenamento, "_conferir_se_identifica_como", falha_na_segunda)
    with pytest.raises(GravacaoDivergente, match="ilegível"):
        armazenar(gerado, NOME, dbf, original)
    assert _nomes(dbf) == ["Decreto 60929.pdf"]
    assert original.exists() and gerado.exists()


def test_falha_ao_apagar_o_original_nao_desfaz_a_gravacao(ambiente, monkeypatch, caplog):
    dbf, original, gerado = ambiente
    unlink = Path.unlink

    def unlink_bloqueado(self, missing_ok=False):
        if self == original:
            raise PermissionError("arquivo em uso")
        return unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink_bloqueado)
    with caplog.at_level(logging.WARNING, logger="pipeline"):
        destino = armazenar(gerado, NOME, dbf, original)
    assert destino.exists()
    assert original.exists()  # o próximo lote o verá como duplicado e o descartará
    assert not gerado.exists()
    assert "apague à mão" in caplog.text


def test_descartar_se_existir(tmp_path, monkeypatch, caplog):
    arquivo = tmp_path / "a.pdf"
    arquivo.write_bytes(b"x")
    assert descartar_se_existir(arquivo) is True and not arquivo.exists()
    assert descartar_se_existir(arquivo) is True  # já não existe: nada a fazer

    def bloqueado(self, missing_ok=False):
        raise PermissionError("em uso")

    arquivo.write_bytes(b"x")
    monkeypatch.setattr(Path, "unlink", bloqueado)
    with caplog.at_level(logging.WARNING, logger="pipeline"):
        assert descartar_se_existir(arquivo) is False
    assert str(arquivo) in caplog.text


def test_descartar_se_existir_tenta_de_novo_depois_de_liberar_identificadores(tmp_path, monkeypatch):
    arquivo = tmp_path / "a.pdf"
    arquivo.write_bytes(b"x")
    unlink = Path.unlink
    tentativas = []

    def primeira_falha(self, missing_ok=False):
        tentativas.append(1)
        if len(tentativas) == 1:
            raise PermissionError("em uso")
        return unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", primeira_falha)
    assert descartar_se_existir(arquivo) is True
    assert len(tentativas) == 2 and not arquivo.exists()