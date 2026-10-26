"""`--separar`: relatório de cadernos e gravação dos decretos inéditos em pendentes."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest

import exportador
import main as modulo_main
from config import Configuracao, ErroConfiguracao
from duplicidade import PastaDbfIndisponivel
from main import main, relatorio_dos_cadernos
from separador import ErroSeparacao

AMOSTRAS_CADERNOS = Path(__file__).parent / "amostras_cadernos"
TRECHO = AMOSTRAS_CADERNOS / "Caderno 2026-06-26 (paginas 24 a 26).pdf"  # 60928..60937
PAGINA_9 = AMOSTRAS_CADERNOS / "Caderno 2026-06-26 (pagina 9).pdf"  # 60903..60905


@pytest.fixture
def cfg(tmp_path):
    dbf = tmp_path / "dbf"
    dbf.mkdir()
    (dbf / "Decreto 60929.pdf").write_bytes(b"%PDF-1.4")
    return Configuracao(
        pasta_temp=tmp_path / "pendentes",
        pasta_cadernos=tmp_path / "cadernos",
        pasta_erros=tmp_path / "erros",
        pasta_ocr=tmp_path / "leitura tesseract",
        pasta_normalizados=tmp_path / "normalizados",
        pasta_logs=tmp_path / "logs",
        pasta_dbf=dbf,
    )


def nomes(pasta: Path) -> list[str]:
    return sorted(p.name for p in pasta.iterdir()) if pasta.exists() else []


# ----------------------------------------------------------------------------
# Relatório (só leitura)
# ----------------------------------------------------------------------------


def test_relatorio_mostra_programa_tipo_e_situacao(cfg, caplog):
    with caplog.at_level(logging.INFO, logger="pipeline"):
        resumo = relatorio_dos_cadernos(cfg, TRECHO)
    texto = caplog.text
    assert "10 ato(s) encontrado(s)" in texto
    assert "Decreto 60929.pdf | p. 1 | CONCESSAO | PRODEPE | já existe na DBF" in texto
    assert "Decreto 60928.pdf | p. 1 | CONCESSAO | PRODEPE | INÉDITO" in texto
    assert "Decreto 60930.pdf | p. 1-2 | tipo não reconhecido | - | fora do escopo da DBF" in texto
    assert resumo.situacoes == {"fora do escopo da DBF": 8, "já existe na DBF": 1, "INÉDITO": 1}
    assert resumo.falhas == 0


def test_relatorio_nao_grava_nada(cfg, tmp_path):
    antes = sorted(p.name for p in tmp_path.rglob("*"))
    relatorio_dos_cadernos(cfg, TRECHO)
    assert sorted(p.name for p in tmp_path.rglob("*")) == antes


def test_relatorio_do_decreto_proind(cfg, caplog):
    with caplog.at_level(logging.INFO, logger="pipeline"):
        relatorio_dos_cadernos(cfg, PAGINA_9)
    assert "Decreto 60903.pdf | p. 1 | AUTORIZACAO_PROIND | PROIND | INÉDITO" in caplog.text


def test_relatorio_avisa_do_caderno_cortado(cfg, caplog):
    # A página 9 termina no meio do decreto 60905: sem fecho.
    with caplog.at_level(logging.WARNING, logger="pipeline"):
        resumo = relatorio_dos_cadernos(cfg, PAGINA_9)
    assert "Decreto 60905.pdf | AVISO: fecho" in caplog.text
    assert resumo.avisos == 1


def test_relatorio_sem_pasta_dbf_avisa_e_continua(cfg, caplog):
    sem_dbf = Configuracao(**{**cfg.__dict__, "pasta_dbf": None})
    with caplog.at_level(logging.INFO, logger="pipeline"):
        relatorio_dos_cadernos(sem_dbf, TRECHO)
    assert "PASTA_DBF não definida" in caplog.text
    assert "base da DBF não consultada" in caplog.text


def test_relatorio_com_dbf_inacessivel_avisa_e_continua(cfg, tmp_path, caplog):
    inacessivel = Configuracao(**{**cfg.__dict__, "pasta_dbf": tmp_path / "nao_existe"})
    with caplog.at_level(logging.INFO, logger="pipeline"):
        relatorio_dos_cadernos(inacessivel, TRECHO)
    assert "Base da DBF indisponível" in caplog.text


def test_pasta_de_cadernos_processa_todos_os_pdfs(cfg, tmp_path, caplog):
    pasta = tmp_path / "cadernos"
    pasta.mkdir()
    shutil.copy(TRECHO, pasta / "a.pdf")
    shutil.copy(PAGINA_9, pasta / "b.pdf")
    (pasta / "manifesto.json").write_text("{}", encoding="utf-8")  # não é PDF: ignorado
    with caplog.at_level(logging.INFO, logger="pipeline"):
        resumo = relatorio_dos_cadernos(cfg, pasta)
    assert resumo.cadernos == 2 and resumo.atos == 13


def test_pasta_sem_pdfs_e_erro(cfg, tmp_path):
    vazia = tmp_path / "vazia"
    vazia.mkdir()
    with pytest.raises(ErroSeparacao, match="nenhum PDF"):
        relatorio_dos_cadernos(cfg, vazia)


def test_decreto_que_cita_programa_so_no_corpo_gera_aviso(cfg, tmp_path, caplog):
    import pymupdf

    texto = (
        "DECRETO Nº 60.001, DE 25 DE JUNHO DE 2026.\nDispõe sobre a estrutura da Secretaria.\n"
        "A GOVERNADORA DO ESTADO, no uso das atribuições,\nDECRETA:\n"
        "Art. 1º Aplica-se o PRODEPE.\nPalácio do Campo das Princesas, Recife, 25 de junho de 2026, "
        "210º da Revolução Republicana Constitucionalista e 204º da\nIndependência do Brasil.\n"
        "RAQUEL TEIXEIRA LYRA LUCENA\nGovernadora do Estado\n"
    )
    pdf = tmp_path / "c.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_textbox(pymupdf.Rect(40, 40, 560, 800), texto, fontsize=9)
    doc.save(pdf)
    with caplog.at_level(logging.INFO, logger="pipeline"):
        resumo = relatorio_dos_cadernos(cfg, pdf)
    assert "cita um programa da DBF só no corpo" in caplog.text
    assert resumo.avisos == 1


# ----------------------------------------------------------------------------
# Gravação em pendentes
# ----------------------------------------------------------------------------


def test_gravar_cria_pdf_so_dos_ineditos_da_dbf(cfg):
    resumo = relatorio_dos_cadernos(cfg, TRECHO, gravar=True)
    # 60929 já existe na DBF; 60930..60937 são de outros assuntos; sobra o 60928.
    assert nomes(cfg.pasta_temp) == ["Decreto 60928.pdf"]
    assert resumo.situacoes["INÉDITO"] == 1 and resumo.falhas == 0


def test_gravar_inclui_o_proind_sem_modelo(cfg):
    relatorio_dos_cadernos(cfg, PAGINA_9, gravar=True)
    assert nomes(cfg.pasta_temp) == ["Decreto 60903.pdf", "Decreto 60904.pdf", "Decreto 60905.pdf"]


def test_gravar_de_novo_nao_duplica_nem_sobrescreve(cfg, caplog):
    relatorio_dos_cadernos(cfg, TRECHO, gravar=True)
    antes = (cfg.pasta_temp / "Decreto 60928.pdf").read_bytes()
    with caplog.at_level(logging.INFO, logger="pipeline"):
        relatorio_dos_cadernos(cfg, TRECHO, gravar=True)
    assert "INÉDITO (já está em pendentes)" in caplog.text
    assert (cfg.pasta_temp / "Decreto 60928.pdf").read_bytes() == antes
    assert nomes(cfg.pasta_temp) == ["Decreto 60928.pdf"]


def test_pdfs_gravados_seguem_para_o_lote(cfg):
    # O PDF gravado é aceito pela etapa 2: identifica o número e o tipo.
    from identificador import identificar
    from reader import ler_pdf

    relatorio_dos_cadernos(cfg, TRECHO, gravar=True)
    ident = identificar(ler_pdf(cfg.pasta_temp / "Decreto 60928.pdf").texto)
    assert (ident.numero, ident.tipo.nome) == ("60928", "CONCESSAO")


def test_gravar_exige_a_base_da_dbf(cfg, tmp_path):
    sem_dbf = Configuracao(**{**cfg.__dict__, "pasta_dbf": None})
    with pytest.raises(ErroConfiguracao, match="PASTA_DBF"):
        relatorio_dos_cadernos(sem_dbf, TRECHO, gravar=True)
    inacessivel = Configuracao(**{**cfg.__dict__, "pasta_dbf": tmp_path / "nao_existe"})
    with pytest.raises(PastaDbfIndisponivel):
        relatorio_dos_cadernos(inacessivel, TRECHO, gravar=True)
    assert nomes(cfg.pasta_temp) == []


def test_falha_ao_gravar_e_contada_e_nao_deixa_arquivo(cfg, monkeypatch, caplog):
    def sempre_falha(ato, destino, *a, **k):
        raise exportador.ErroExportacao("falha simulada")

    monkeypatch.setattr(modulo_main, "gravar_pdf_do_ato", sempre_falha)
    with caplog.at_level(logging.INFO, logger="pipeline"):
        resumo = relatorio_dos_cadernos(cfg, TRECHO, gravar=True)
    assert resumo.falhas == 1
    assert "INÉDITO (FALHA ao gravar)" in caplog.text
    assert nomes(cfg.pasta_temp) == []


def test_numero_repetido_no_caderno_grava_so_o_primeiro(cfg, tmp_path, monkeypatch, caplog):
    from especies import DECRETO
    from separador import AtoSeparado, ResultadoSeparacao

    texto = (
        "DECRETO Nº 60.777, DE 25 DE JUNHO DE 2026.\nConcede estímulo previsto na Lei nº 11.675, "
        "de 11 de outubro de 1999, que dispõe sobre o PRODEPE, à empresa X.\n"
        "A GOVERNADORA DO ESTADO, no uso das atribuições,\nDECRETA:\nArt. 1º Fica concedido.\n"
    )
    ato = AtoSeparado(DECRETO, "60777", "DECRETO Nº 60.777", texto, 1, 1)
    monkeypatch.setattr(
        modulo_main, "separar_caderno",
        lambda caminho: ResultadoSeparacao((ato, ato), 1),
    )
    with caplog.at_level(logging.INFO, logger="pipeline"):
        resumo = relatorio_dos_cadernos(cfg, TRECHO, gravar=True)
    assert nomes(cfg.pasta_temp) == ["Decreto 60777.pdf"]
    assert "repetido neste caderno" in caplog.text and resumo.avisos == 1


# ----------------------------------------------------------------------------
# Linha de comando
# ----------------------------------------------------------------------------


def test_cli_separar(cfg, monkeypatch):
    monkeypatch.setattr(modulo_main, "carregar_configuracao", lambda: cfg)
    assert main(["--separar", str(TRECHO)]) == 0
    assert nomes(cfg.pasta_temp) == []


def test_cli_separar_gravar(cfg, monkeypatch):
    monkeypatch.setattr(modulo_main, "carregar_configuracao", lambda: cfg)
    assert main(["--separar", str(TRECHO), "--gravar"]) == 0
    assert nomes(cfg.pasta_temp) == ["Decreto 60928.pdf"]


def test_cli_gravar_com_falha_sai_com_codigo_1(cfg, monkeypatch):
    def sempre_falha(ato, destino, *a, **k):
        raise exportador.ErroExportacao("falha simulada")

    monkeypatch.setattr(modulo_main, "carregar_configuracao", lambda: cfg)
    monkeypatch.setattr(modulo_main, "gravar_pdf_do_ato", sempre_falha)
    assert main(["--separar", str(TRECHO), "--gravar"]) == 1


def test_cli_gravar_sem_dbf_sai_com_codigo_2(cfg, monkeypatch, capsys):
    sem_dbf = Configuracao(**{**cfg.__dict__, "pasta_dbf": None})
    monkeypatch.setattr(modulo_main, "carregar_configuracao", lambda: sem_dbf)
    assert main(["--separar", str(TRECHO), "--gravar"]) == 2
    assert "PASTA_DBF" in capsys.readouterr().err


def test_cli_separar_arquivo_invalido_sai_com_erro_2(cfg, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(modulo_main, "carregar_configuracao", lambda: cfg)
    assert main(["--separar", str(tmp_path / "nao_existe.pdf")]) == 2
    assert "Erro:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argumentos",
    [
        ["--separar", "x.pdf", "--baixar", "--de", "2026-06-26"],
        ["--separar", "x.pdf", "--simular"],
        ["--gravar"],
    ],
)
def test_cli_combinacoes_invalidas(argumentos):
    with pytest.raises(SystemExit) as saida:
        main(argumentos)
    assert saida.value.code == 2