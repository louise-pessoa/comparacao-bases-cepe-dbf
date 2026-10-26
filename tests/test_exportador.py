"""PDF intermediário de cada decreto, gravado em dados/pendentes."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

import exportador
from exportador import ErroExportacao, gravar_pdf_do_ato
from identificador import StatusIdentificacao, identificar, normalizar_espacos
from reader import StatusLeitura, ler_pdf
from separador import AtoSeparado, separar_caderno
from conftest import AMOSTRAS
from especies import DECRETO

TRECHO = Path(__file__).parent / "amostras_cadernos" / "Caderno 2026-06-26 (paginas 24 a 26).pdf"


@pytest.fixture(scope="module")
def atos():
    return {ato.numero: ato for ato in separar_caderno(TRECHO).atos}


def ato_sintetico(texto: str, numero: str = "60001") -> AtoSeparado:
    return AtoSeparado(DECRETO, numero, f"DECRETO Nº {numero}", texto, 1, 1)


def test_pdf_gravado_e_lido_como_o_decreto_certo(atos, tmp_path):
    destino = gravar_pdf_do_ato(atos["60929"], tmp_path / "Decreto 60929.pdf")
    leitura = ler_pdf(destino)
    assert leitura.status is StatusLeitura.OK
    ident = identificar(leitura.texto)
    assert ident.status is StatusIdentificacao.IDENTIFICADO
    assert (ident.numero, ident.tipo.nome) == ("60929", "CONCESSAO")


def test_texto_do_pdf_gerado_e_o_mesmo_da_amostra_avulsa_da_dbf(atos, tmp_path):
    destino = gravar_pdf_do_ato(atos["60929"], tmp_path / "Decreto 60929.pdf")
    with pymupdf.open(AMOSTRAS / "Decreto 60929.pdf") as doc:
        avulso = "".join(pagina.get_text() for pagina in doc)
    assert normalizar_espacos(ler_pdf(destino).texto) == normalizar_espacos(avulso)


def test_todos_os_decretos_do_trecho_passam_na_conferencia(atos, tmp_path):
    for numero, ato in atos.items():
        gravar_pdf_do_ato(ato, tmp_path / f"Decreto {numero}.pdf")
    assert len(list(tmp_path.glob("*.pdf"))) == len(atos)
    assert not list(tmp_path.glob("*.part"))


def test_decreto_longo_ocupa_varias_paginas(tmp_path):
    texto = "DECRETO Nº 60.001, DE 25 DE JUNHO DE 2026.\n" + "\n".join(
        f"Art. {i}º Linha do artigo número {i}, com texto suficiente para ocupar espaço." for i in range(1, 400)
    )
    destino = gravar_pdf_do_ato(ato_sintetico(texto), tmp_path / "Decreto 60001.pdf")
    with pymupdf.open(destino) as doc:
        assert doc.page_count > 5


def test_acentos_e_simbolos_do_portugues_sobrevivem(tmp_path):
    texto = (
        "DECRETO Nº 60.001, DE 25 DE JUNHO DE 2026.\n"
        "Art. 1º Fica concedido o benefício, 2ª via – “aspas” e ’apóstrofo’: ç ã õ é ú à ü § % R$ 1,5.\n"
    )
    destino = gravar_pdf_do_ato(ato_sintetico(texto), tmp_path / "Decreto 60001.pdf")
    assert normalizar_espacos(ler_pdf(destino).texto) == normalizar_espacos(texto)


def test_nao_sobrescreve_arquivo_existente(atos, tmp_path):
    existente = tmp_path / "Decreto 60929.pdf"
    existente.write_bytes(b"conteudo antigo")
    with pytest.raises(ErroExportacao, match="não será sobrescrito"):
        gravar_pdf_do_ato(atos["60929"], existente)
    assert existente.read_bytes() == b"conteudo antigo"


def test_cria_a_pasta_de_destino(atos, tmp_path):
    destino = tmp_path / "dados" / "pendentes" / "Decreto 60929.pdf"
    gravar_pdf_do_ato(atos["60929"], destino)
    assert destino.is_file()


def test_fonte_ausente_gera_erro_claro_e_nao_deixa_arquivo(atos, tmp_path):
    with pytest.raises(ErroExportacao, match="fonte não encontrada"):
        gravar_pdf_do_ato(atos["60929"], tmp_path / "saida" / "Decreto 60929.pdf", pasta_fontes=tmp_path / "sem_fontes")
    assert list((tmp_path / "saida").iterdir()) == []


def test_caractere_sem_glifo_na_fonte_reprova_na_conferencia(tmp_path):
    texto = "DECRETO Nº 60.001, DE 25 DE JUNHO DE 2026.\nArt. 1º Texto com ideograma 漢字 que a fonte não tem.\n"
    saida = tmp_path / "saida"
    with pytest.raises(ErroExportacao):
        gravar_pdf_do_ato(ato_sintetico(texto), saida / "Decreto 60001.pdf")
    assert list(saida.iterdir()) == []  # nem .part nem PDF


def test_numero_diferente_do_esperado_reprova(atos, tmp_path):
    errado = AtoSeparado(DECRETO, "99999", "DECRETO Nº 99.999", atos["60929"].texto, 1, 1)
    saida = tmp_path / "saida"
    with pytest.raises(ErroExportacao, match="esperado '99999'"):
        gravar_pdf_do_ato(errado, saida / "Decreto 99999.pdf")
    assert list(saida.iterdir()) == []


def test_fontes_versionadas_e_licenca_presentes():
    assert (exportador.PASTA_FONTES / exportador.FONTE_REGULAR).is_file()
    assert (exportador.PASTA_FONTES / exportador.FONTE_NEGRITO).is_file()
    licenca = (exportador.PASTA_FONTES / "LICENCA-LiberationSans.txt").read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE" in licenca.upper()