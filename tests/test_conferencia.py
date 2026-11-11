"""`--conferir`: diferenças entre o PDF da conferência e o da base, e a decisão pelo terminal."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from conftest import criar_pdf_imagem, texto_decreto
from dbf_cepe import cli as modulo_cli
from dbf_cepe.config import Configuracao
from dbf_cepe.leitura.reader import ler_pdf
from dbf_cepe.leitura.separador import AtoSeparado
from dbf_cepe.pipeline import armazenamento, conferencia
from dbf_cepe.pipeline.exportador import gravar_pdf_do_ato
from dbf_cepe.pipeline.lote import Destino, executar
from dbf_cepe.pipeline.registro import CONFERIR, GRAVADO, ErroRegistro, Registro
from dbf_cepe.pipeline.relatorio import nome_em_pendentes
from dbf_cepe.regras.especies import DECRETO

CONCESSAO = "Concede estímulo previsto na Lei nº 11.675, de 11 de outubro de 1999, que dispõe sobre o PRODEPE."
DIARIO = texto_decreto("48.606", CONCESSAO) + "\nArt. 2º nos termos do Decreto nº 21.959, de 27 de dezembro de 1999."
BASE = DIARIO.replace("de 27 de dezembro de 1999.", "de 1999.")


def ato(texto: str, dia: str | None = "2020-01-31") -> AtoSeparado:
    d = date.fromisoformat(dia) if dia else None
    return AtoSeparado(
        DECRETO, "48606", "DECRETO Nº 48.606", texto, 15, 15,
        data_publicacao=d, caderno=f"{d:%Y%m%d}_PoderExecutivo.pdf" if d else None,
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
    return c


def para_conferir(cfg, base: str | None = BASE) -> Path:
    """Base com o arquivo do número (texto `base`, ou imagem se None) e o lote mandando a
    publicação do Diário para a conferência."""
    if base is None:
        criar_pdf_imagem(cfg.pasta_dbf / "Decreto 48606.pdf")
    else:
        gravar_pdf_do_ato(ato(base, None), cfg.pasta_dbf / "Decreto 48606.pdf")
    diario = ato(DIARIO)
    gravar_pdf_do_ato(diario, cfg.pasta_temp / nome_em_pendentes("Decreto 48606.pdf", diario))
    if base is None:  # base em imagem só vai para conferência com sinal de republicação
        registro = Registro.carregar(cfg.registro)
        from dbf_cepe.pipeline.registro import BASE_IMAGEM, Entrada
        from dbf_cepe.pipeline.publicacao import Publicacao

        registro.adicionar(Entrada("Decreto", "48606", None, Publicacao(date(2020, 1, 2), "x.pdf", 1), "outro", None, BASE_IMAGEM))
        registro.salvar()
    assert executar(cfg)[Destino.ENVIADO_CONFERIR] == 1
    return cfg.conferir_duplicata / "Decreto 48606 (conferir duplicata).pdf"


def respostas(*itens: str):
    fila = list(itens)

    def perguntar(_prompt: str) -> str:
        if not fila:
            raise EOFError
        return fila.pop(0)

    return perguntar


def rodar(cfg, *itens, simular=False):
    saida: list[str] = []
    resumo = conferencia.conferir(cfg, simular, respostas(*itens), saida.append)
    return resumo, "\n".join(saida)


def texto_da_base(cfg) -> str:
    return ler_pdf(cfg.pasta_dbf / "Decreto 48606.pdf").texto


def entrada(cfg):
    return next(e for e in Registro.carregar(cfg.registro).entradas if e.situacao in (CONFERIR, GRAVADO))


def test_simular_mostra_a_diferenca_e_nao_mexe_em_nada(cfg):
    pdf = para_conferir(cfg)
    antes = (cfg.pasta_dbf / "Decreto 48606.pdf").read_bytes()
    resumo, saida = rodar(cfg, simular=True)
    assert resumo == {conferencia.PULADO: 1}
    assert "[Diário: 27 de dezembro de | base: (nada)]" in saida
    assert "Diário de 31/01/2020, 20200131_PoderExecutivo.pdf, p. 15" in saida
    assert pdf.exists() and (cfg.pasta_dbf / "Decreto 48606.pdf").read_bytes() == antes
    assert not entrada(cfg).decisao


def test_usar_o_texto_do_diario_substitui_a_base_com_copia(cfg):
    pdf = para_conferir(cfg)
    antes = (cfg.pasta_dbf / "Decreto 48606.pdf").read_bytes()
    resumo, _ = rodar(cfg, "d")
    assert resumo == {conferencia.DIARIO: 1}
    assert "27 de dezembro de 1999" in texto_da_base(cfg)
    copias = list(cfg.substituidos.iterdir())
    assert len(copias) == 1 and copias[0].read_bytes() == antes
    assert copias[0].name.startswith("Decreto 48606 - substituído em ")
    assert not pdf.exists()
    e = entrada(cfg)
    assert (e.situacao, e.arquivo) == (GRAVADO, "Decreto 48606.pdf")
    assert e.decisao.startswith("substituída pelo Diário") and e.decidido_em
    assert rodar(cfg)[1].startswith("Nada para conferir")


def test_republicacao_depois_da_substituicao_segue_a_regra_normal(cfg):
    para_conferir(cfg)
    rodar(cfg, "d")
    nova = ato(DIARIO + "\nArt. 3º Republicado por incorreção.", "2020-02-10")
    gravar_pdf_do_ato(nova, cfg.pasta_temp / nome_em_pendentes("Decreto 48606.pdf", nova))
    assert executar(cfg)[Destino.CONCLUIDO] == 1
    assert (cfg.pasta_dbf / "Decreto 48606 (republicação).pdf").exists()


def test_manter_a_base(cfg):
    pdf = para_conferir(cfg)
    antes = (cfg.pasta_dbf / "Decreto 48606.pdf").read_bytes()
    resumo, _ = rodar(cfg, "b")
    assert resumo == {conferencia.BASE: 1}
    assert (cfg.pasta_dbf / "Decreto 48606.pdf").read_bytes() == antes
    assert not pdf.exists() and not cfg.substituidos.exists()
    e = entrada(cfg)
    assert (e.situacao, e.decisao) == (CONFERIR, conferencia.MANTIDA_A_BASE)


@pytest.mark.parametrize("respostas_dadas", [("p",), ("",), ()])
def test_pular_ou_sem_resposta_nao_mexe_em_nada(cfg, respostas_dadas):
    pdf = para_conferir(cfg)
    resumo, _ = rodar(cfg, *respostas_dadas)
    assert resumo == {conferencia.PULADO: 1} and pdf.exists() and not entrada(cfg).decisao


def test_ver_todas_e_resposta_invalida_perguntam_de_novo(cfg):
    para_conferir(cfg)
    resumo, saida = rodar(cfg, "v", "x", "b")
    assert resumo == {conferencia.BASE: 1}
    assert saida.count("[Diário: 27 de dezembro de | base: (nada)]") == 2
    assert "responda d, b, v, p" in saida


def test_base_em_imagem_avisa_que_nao_ha_texto(cfg):
    para_conferir(cfg, base=None)
    resumo, saida = rodar(cfg, "d")
    assert "o PDF da base não tem texto para comparar" in saida
    assert resumo == {conferencia.DIARIO: 1} and "27 de dezembro de 1999" in texto_da_base(cfg)


def test_sem_arquivo_na_base_nao_oferece_substituir(cfg):
    pdf = para_conferir(cfg)
    (cfg.pasta_dbf / "Decreto 48606.pdf").unlink()
    (cfg.pasta_dbf / "Decreto 1.pdf").write_bytes(b"outro")
    resumo, saida = rodar(cfg, "d", "p")
    assert "a base não tem arquivo deste número" in saida and "(d)" not in saida
    assert resumo == {conferencia.PULADO: 1} and pdf.exists()


def test_falha_na_troca_devolve_o_arquivo_antigo(cfg, monkeypatch):
    pdf = para_conferir(cfg)
    antes = (cfg.pasta_dbf / "Decreto 48606.pdf").read_bytes()
    verdadeira = armazenamento._conferir_se_identifica_como
    chamadas = []

    def falha_depois_da_troca(caminho, nome, descricao):
        chamadas.append(descricao)
        if len(chamadas) == 2:  # 1ª: o PDF novo; 2ª: o arquivo já trocado na base
            raise armazenamento.GravacaoDivergente("releitura não confere")
        verdadeira(caminho, nome, descricao)

    monkeypatch.setattr(armazenamento, "_conferir_se_identifica_como", falha_depois_da_troca)
    resumo, saida = rodar(cfg, "d")
    assert resumo == {conferencia.PULADO: 1} and "ERRO: releitura não confere" in saida
    assert (cfg.pasta_dbf / "Decreto 48606.pdf").read_bytes() == antes
    assert pdf.exists() and not entrada(cfg).decisao
    assert not (cfg.pasta_dbf / "Decreto 48606.pdf.part").exists()


def test_pdf_que_nao_e_ato_e_contado_e_ignorado(cfg):
    para_conferir(cfg)
    (cfg.conferir_duplicata / "lixo.pdf").write_bytes(b"nao e pdf")
    resumo, saida = rodar(cfg, "b")
    assert resumo == {conferencia.ILEGIVEL: 1, conferencia.BASE: 1}
    assert (cfg.conferir_duplicata / "lixo.pdf").exists()


def test_registro_ilegivel_ou_com_operacao_pendente_para_tudo(cfg):
    pdf = para_conferir(cfg)
    registro = Registro.carregar(cfg.registro)
    registro.operacao_em_andamento = {"gravar": "x"}
    registro.salvar()
    with pytest.raises(ErroRegistro, match="não terminou"):
        rodar(cfg, "d")
    cfg.registro.write_text("{", encoding="utf-8")
    with pytest.raises(ErroRegistro):
        rodar(cfg, "d")
    assert pdf.exists()


def test_sem_publicacao_no_registro_avisa(cfg):
    pdf = para_conferir(cfg)
    cfg.registro.unlink()
    resumo, saida = rodar(cfg, "b")
    assert "não está no registro" in saida and resumo == {conferencia.BASE: 1} and not pdf.exists()


def test_pasta_vazia(cfg):
    assert rodar(cfg)[1].startswith("Nada para conferir")


def test_cli(cfg, monkeypatch, capsys):
    para_conferir(cfg)
    monkeypatch.setattr(modulo_cli, "carregar_configuracao", lambda: cfg)
    assert modulo_cli.main(["--conferir", "--simular"]) == 0
    assert "1 PDF(s) para conferir" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        modulo_cli.main(["--conferir", "--separar", "x.pdf"])


def test_pasta_de_copias_padrao(cfg):
    assert cfg.substituidos == cfg.pasta_temp.parent / "substituidos"
    assert replace(cfg, pasta_substituidos=Path("/tmp/x")).substituidos == Path("/tmp/x")


def test_cada_decisao_vai_para_a_publicacao_certa(cfg):
    para_conferir(cfg)
    outro = AtoSeparado(
        DECRETO, "48607", "DECRETO Nº 48.607", DIARIO.replace("48.606", "48.607"), 16, 16,
        data_publicacao=date(2020, 1, 31), caderno="20200131_PoderExecutivo.pdf",
    )
    gravar_pdf_do_ato(replace(outro, texto=BASE.replace("48.606", "48.607"), data_publicacao=None, caderno=None),
                      cfg.pasta_dbf / "Decreto 48607.pdf")
    gravar_pdf_do_ato(outro, cfg.pasta_temp / nome_em_pendentes("Decreto 48607.pdf", outro))
    assert executar(cfg)[Destino.ENVIADO_CONFERIR] == 1
    rodar(cfg, "b", "d")  # 48606: mantém a base; 48607: usa o Diário
    por_numero = {e.numero: e for e in Registro.carregar(cfg.registro).entradas}
    assert (por_numero["48606"].situacao, por_numero["48606"].decisao) == (CONFERIR, conferencia.MANTIDA_A_BASE)
    assert por_numero["48607"].situacao == GRAVADO and por_numero["48607"].decisao.startswith("substituída")
