"""Comparação do texto do caderno com o PDF que a DBF já tem (só leitura)."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest

from dbf_cepe.pipeline import comparacao
from dbf_cepe import cli as modulo_main
from dbf_cepe.pipeline import relatorio as modulo_relatorio
from dbf_cepe.pipeline.comparacao import Comparacao, comparar_com_base, comparar_textos, palavras
from conftest import criar_pdf_imagem, criar_pdf_texto
from dbf_cepe.config import Configuracao
from dbf_cepe.pipeline.relatorio import relatorio_dos_cadernos
from dbf_cepe.leitura.separador import separar_caderno

AMOSTRAS = Path(__file__).parent / "amostras"
CADERNO = Path(__file__).parent / "amostras_cadernos" / "Caderno 2026-06-26 (paginas 24 a 26).pdf"


@pytest.fixture
def cfg(tmp_path):
    dbf = tmp_path / "dbf"
    dbf.mkdir()
    return Configuracao(
        pasta_temp=tmp_path / "pendentes",
        pasta_cadernos=tmp_path / "cadernos",
        pasta_erros=tmp_path / "erros",
        pasta_ocr=tmp_path / "leitura tesseract",
        pasta_normalizados=tmp_path / "normalizados",
        pasta_logs=tmp_path / "logs",
        pasta_dbf=dbf,
    )


def _texto_do_60929() -> str:
    return next(a.texto for a in separar_caderno(CADERNO).atos if a.numero == "60929")


# ----------------------------------------------------------------------------
# palavras e comparar_textos
# ----------------------------------------------------------------------------


def test_palavras_separa_palavras_numeros_e_sinais_sem_o_layout():
    assert palavras("A  empresa\nbeneﬁ ciária  foi pré-\nfabricada, art. 3º.") == [
        "A", "empresa", "beneficiária", "foi", "pré", "-", "fabricada", ",", "art", ".", "3º", ".",
    ]


def test_maiusculas_e_pontuacao_contam():
    assert palavras("Art. 1º") != palavras("art. 1º")
    assert comparar_textos("fica concedido.", "fica concedido;").identico is False


@pytest.mark.parametrize(
    ("caderno", "base"),
    [
        # Casos reais de 26/06/2026 (30 decretos que a DBF já tinha; 10 pareciam diferentes só por isto):
        ("Caruaru/ PE, com CNPJ/MF", "Caruaru/PE, com CNPJ/MF"),
        ("CNPJ/MF nº 01.551.272/0001-42 e", "CNPJ/MF nº 01.551.272/0001- 42 e"),
        ("CACEPE nº 1176412-00, o", "CACEPE nº 1176412- 00, o"),
        ("flange fluxo d\u2019água para", "flange fluxo d'água para"),
        ("\u201cpallet\u201d, de batata", '"pallet", de batata'),
        ("estabilizante/ oxidativo", "estabilizante/oxidativo"),
        ("(hfc- 236cb), 1,1,1", "(hfc-236cb), 1,1,1"),
        ("2-(trifluorometil)prop-1-eno", "2- (trifluorometil)prop-1-eno"),
        ("(aminoiminometil) ureia", "(aminoiminometil)ureia"),
        ("frigorias/ hora", "frigorias/hora"),
        ("de \u2013 a\u2014b", "de - a-b"),
        ("duas\u00a0palavras", "duas palavras"),
    ],
)
def test_diferencas_so_de_layout_e_de_aspas_nao_contam(caderno, base):
    assert comparar_textos(caderno, base).identico


@pytest.mark.parametrize(
    ("caderno", "base"),
    [
        ("Dos Santos", "Dossantos"),  # espaço no meio de uma palavra muda o texto
        ("concedido.", "concedido;"),
        ("75% do valor", "85% do valor"),
        ("Art. 1º Fica", "Art. 2º Fica"),
        ("Caruaru/PE", "Caruaru-PE"),
    ],
)
def test_diferencas_de_conteudo_continuam_contando(caderno, base):
    assert not comparar_textos(caderno, base).identico


def test_textos_iguais_com_layout_diferente_sao_identicos():
    resultado = comparar_textos("Art. 1º Fica\nconcedido o estímulo.", "Art.  1º  Fica concedido\no  estímulo.")
    assert resultado.identico and resultado.similaridade == 1.0


def test_palavra_trocada_conta_uma_de_cada_lado():
    resultado = comparar_textos("fica concedido o estímulo de 75%", "fica concedido o estímulo de 85%")
    assert (resultado.so_no_ato, resultado.so_na_base) == (1, 1)
    assert 0.8 < resultado.similaridade < 1.0 and not resultado.identico


def test_palavra_acrescentada_so_de_um_lado():
    resultado = comparar_textos("fica concedido o estímulo", "fica concedido o estímulo fiscal")
    assert (resultado.so_no_ato, resultado.so_na_base) == (0, 1)


def test_textos_vazios_sao_identicos():
    assert comparar_textos("", "  ").identico


def test_texto_totalmente_diferente():
    resultado = comparar_textos("um dois três", "quatro cinco seis")
    assert resultado.similaridade == 0.0 and (resultado.so_no_ato, resultado.so_na_base) == (3, 3)


# ----------------------------------------------------------------------------
# comparar_com_base
# ----------------------------------------------------------------------------


def test_decreto_real_do_caderno_e_identico_ao_pdf_da_dbf():
    assert comparar_com_base(_texto_do_60929(), AMOSTRAS / "Decreto 60929.pdf").identico


def test_decreto_do_caderno_difere_do_pdf_de_outro_decreto():
    resultado = comparar_com_base(_texto_do_60929(), AMOSTRAS / "Decreto 60951.pdf")
    assert resultado.lido and not resultado.identico and resultado.similaridade < 0.9


def test_pdf_da_base_com_uma_palavra_diferente(tmp_path):
    criar_pdf_texto(tmp_path / "Decreto 1.pdf", "Fica concedido o estímulo de 85% ao contribuinte.")
    resultado = comparar_com_base("Fica concedido o estímulo de 75% ao contribuinte.", tmp_path / "Decreto 1.pdf")
    assert (resultado.so_no_ato, resultado.so_na_base) == (1, 1)


def test_pdf_da_base_ilegivel_nao_levanta_excecao(tmp_path):
    (tmp_path / "ruim.pdf").write_bytes(b"nao e um pdf")
    resultado = comparar_com_base("texto", tmp_path / "ruim.pdf")
    assert resultado.lido is False and resultado.identico is False and resultado.detalhe


def test_pdf_da_base_so_com_imagem_nao_e_lido(tmp_path):
    criar_pdf_imagem(tmp_path / "imagem.pdf")
    assert comparar_com_base("texto", tmp_path / "imagem.pdf").lido is False


def test_pdf_da_base_inexistente_nao_levanta_excecao(tmp_path):
    assert comparar_com_base("texto", tmp_path / "nao_existe.pdf").lido is False


# ----------------------------------------------------------------------------
# --comparar no relatório
# ----------------------------------------------------------------------------


def test_relatorio_com_comparar_diz_que_o_texto_e_identico(cfg, caplog):
    shutil.copy(AMOSTRAS / "Decreto 60929.pdf", cfg.pasta_dbf)
    with caplog.at_level(logging.INFO, logger="pipeline"):
        resumo = relatorio_dos_cadernos(cfg, CADERNO, comparar=True)
    assert "já existe na DBF (texto idêntico ao da base)" in caplog.text
    assert "Comparação com a base (1 decreto(s) que já existem):" in caplog.messages
    assert "    1  texto idêntico" in caplog.messages
    assert resumo.situacoes["já existe na DBF"] == 1


def test_relatorio_sem_comparar_nao_le_a_base(cfg, caplog, monkeypatch):
    shutil.copy(AMOSTRAS / "Decreto 60929.pdf", cfg.pasta_dbf)

    def nao_deve_ler(*args, **kwargs):
        raise AssertionError("sem --comparar a base não deve ser lida")

    monkeypatch.setattr(comparacao, "comparar_com_base", nao_deve_ler)
    with caplog.at_level(logging.INFO, logger="pipeline"):
        relatorio_dos_cadernos(cfg, CADERNO)
    assert "texto idêntico" not in caplog.text and "Comparação com a base" not in caplog.text


def test_relatorio_mostra_diferenca_e_resumo_por_faixa(cfg, caplog, monkeypatch):
    (cfg.pasta_dbf / "Decreto 60929.pdf").write_bytes(b"qualquer")
    monkeypatch.setattr(
        comparacao,
        "comparar_com_base",
        lambda texto, pdf: Comparacao(lido=True, similaridade=0.97, so_no_ato=3, so_na_base=4),
    )
    with caplog.at_level(logging.INFO, logger="pipeline"):
        relatorio_dos_cadernos(cfg, CADERNO, comparar=True)
    assert "texto difere da base: similaridade 97.00%, 3 elemento(s) só no caderno, 4 só na base" in caplog.text
    assert "similaridade de 95% a menos de 99%" in caplog.text
    assert "menos parecidos: Decreto 60929.pdf (97.00%)" in caplog.text


def test_relatorio_conta_base_nao_lida(cfg, caplog):
    (cfg.pasta_dbf / "Decreto 60929.pdf").write_bytes(b"%PDF-1.4")
    with caplog.at_level(logging.INFO, logger="pipeline"):
        relatorio_dos_cadernos(cfg, CADERNO, comparar=True)
    assert "já existe na DBF (PDF da base não lido" in caplog.text
    assert "PDF da base não lido" in caplog.text.split("Comparação com a base")[1]


def test_relatorio_com_comparar_nao_grava_nada(cfg, tmp_path):
    shutil.copy(AMOSTRAS / "Decreto 60929.pdf", cfg.pasta_dbf)
    antes = sorted(p.name for p in tmp_path.rglob("*"))
    relatorio_dos_cadernos(cfg, CADERNO, comparar=True)
    assert sorted(p.name for p in tmp_path.rglob("*")) == antes


def test_comparar_sem_separar_e_recusado(capsys):
    with pytest.raises(SystemExit) as saida:
        modulo_main.main(["--comparar"])
    assert saida.value.code == 2
    assert "--comparar só vale junto com --separar" in capsys.readouterr().err


def test_cli_repassa_comparar_ao_relatorio(cfg, monkeypatch):
    recebido = {}

    def falso(cfg_, caminho, gravar, vazia, comparar):
        recebido["comparar"] = comparar
        return modulo_relatorio.ResumoRelatorio()

    monkeypatch.setattr(modulo_main, "carregar_configuracao", lambda: cfg)
    monkeypatch.setattr(modulo_main, "relatorio_dos_cadernos", falso)
    assert modulo_main.main(["--separar", str(CADERNO), "--comparar"]) == 0
    assert recebido == {"comparar": True}


# ----------------------------------------------------------------------------
# Trechos que diferem
# ----------------------------------------------------------------------------


def test_trecho_mostra_a_palavra_trocada_com_contexto():
    resultado = comparar_textos(
        "Fica concedido o estímulo de 75% ao contribuinte, nos termos do art. 3º.",
        "Fica concedido o estímulo de 85% ao contribuinte nos termos do art. 3º.",
    )
    assert resultado.total_trechos == 2
    assert resultado.trechos[0] == "...concedido o estímulo de [caderno: 75 | base: 85] % ao contribuinte ,..."
    assert resultado.trechos[1] == "...75 % ao contribuinte [caderno: , | base: (nada)] nos termos do art..."


def test_trecho_de_palavra_que_so_existe_de_um_lado():
    resultado = comparar_textos("fica concedido o estímulo", "fica concedido o estímulo fiscal")
    assert resultado.trechos == ("...fica concedido o estímulo [caderno: (nada) | base: fiscal] ...",)
    inverso = comparar_textos("fica concedido o estímulo fiscal", "fica concedido o estímulo")
    assert inverso.trechos == ("...fica concedido o estímulo [caderno: fiscal | base: (nada)] ...",)


def test_textos_identicos_nao_tem_trechos():
    resultado = comparar_textos("um dois", "um  dois")
    assert resultado.trechos == () and resultado.total_trechos == 0


def test_numero_de_trechos_e_limitado_mas_o_total_e_contado():
    a = " ".join(f"p{i} x{i}" for i in range(20))
    b = " ".join(f"p{i} y{i}" for i in range(20))
    resultado = comparar_textos(a, b)
    assert len(resultado.trechos) == comparacao.LIMITE_DE_TRECHOS
    assert resultado.total_trechos == 20


def test_relatorio_mostra_as_diferencas_e_quantas_faltam(cfg, caplog, monkeypatch):
    (cfg.pasta_dbf / "Decreto 60929.pdf").write_bytes(b"qualquer")
    monkeypatch.setattr(
        comparacao,
        "comparar_com_base",
        lambda texto, pdf: Comparacao(
            lido=True, similaridade=0.99, so_no_ato=10, so_na_base=10,
            trechos=("...a [caderno: x | base: y] b...",), total_trechos=3,
        ),
    )
    with caplog.at_level(logging.INFO, logger="pipeline"):
        relatorio_dos_cadernos(cfg, CADERNO, comparar=True)
    assert "Decreto 60929.pdf | diferença: ...a [caderno: x | base: y] b..." in caplog.messages
    assert "Decreto 60929.pdf | ... e mais 2 diferença(s)" in caplog.messages


def test_relatorio_sem_diferenca_nao_mostra_trechos(cfg, caplog):
    shutil.copy(AMOSTRAS / "Decreto 60929.pdf", cfg.pasta_dbf)
    with caplog.at_level(logging.INFO, logger="pipeline"):
        relatorio_dos_cadernos(cfg, CADERNO, comparar=True)
    assert "diferença:" not in caplog.text