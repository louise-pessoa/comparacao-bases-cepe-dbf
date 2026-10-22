"""Etapa 3: comparação com a pasta da DBF e nomenclatura de arquivo."""

from __future__ import annotations

import pytest

from duplicidade import PastaDbfIndisponivel, carregar_indice, ja_existe
from especies import DECRETO


def test_nome_do_arquivo_segue_o_padrao():
    assert DECRETO.nome_arquivo("60929") == "Decreto 60929.pdf"
    assert DECRETO.nome_arquivo("16050") == "Decreto 16050.pdf"


def test_existe_e_nao_existe(tmp_path):
    (tmp_path / "Decreto 60929.pdf").write_bytes(b"x")
    indice = carregar_indice(tmp_path)
    assert ja_existe(indice, "Decreto 60929.pdf")
    assert not ja_existe(indice, "Decreto 60930.pdf")


def test_comparacao_e_estrita_quanto_a_nomenclatura(tmp_path):
    # "Decreto 60.929.pdf" (com separador de milhar) NÃO é o mesmo nome.
    (tmp_path / "Decreto 60.929.pdf").write_bytes(b"x")
    indice = carregar_indice(tmp_path)
    assert not ja_existe(indice, "Decreto 60929.pdf")


def test_maiusculas_nao_diferenciam_arquivos(tmp_path):
    (tmp_path / "decreto 60929.PDF").write_bytes(b"x")
    assert ja_existe(carregar_indice(tmp_path), "Decreto 60929.pdf")


def test_subpastas_nao_entram_no_indice(tmp_path):
    (tmp_path / "Decreto 1.pdf").mkdir()  # pasta com nome de arquivo
    (tmp_path / "Decreto 2.pdf").write_bytes(b"x")
    indice = carregar_indice(tmp_path)
    assert not ja_existe(indice, "Decreto 1.pdf")
    assert ja_existe(indice, "Decreto 2.pdf")


def test_pasta_inexistente_aborta(tmp_path):
    with pytest.raises(PastaDbfIndisponivel, match="não encontrada"):
        carregar_indice(tmp_path / "nao_montado")


def test_pasta_vazia_aborta_por_padrao(tmp_path):
    with pytest.raises(PastaDbfIndisponivel, match="vazia"):
        carregar_indice(tmp_path)


def test_pasta_vazia_permitida_explicitamente(tmp_path):
    assert carregar_indice(tmp_path, permitir_vazia=True) == set()
