"""Leitura por OCR (leitura/ocr.py): Tesseract, montagem do texto, cache e diagnóstico.

Os testes sem marca simulam o pytesseract e rodam em qualquer máquina. Os marcados `ocr`
usam o Tesseract instalado (com o idioma português) e são pulados sem ele."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from conftest import AMOSTRAS, criar_pdf_imagem_de, criar_pdf_texto
from dbf_cepe.config import OCR_AUTO, OCR_NAO, OCR_SIM, ConfiguracaoOcr
from dbf_cepe.leitura import ocr

TEM_TESSERACT = shutil.which("tesseract") is not None


def _dados(*palavras: tuple[str, float, int, int, int]) -> dict[str, list]:
    """Saída simulada do image_to_data: (texto, confiança, bloco, parágrafo, linha)."""
    colunas: dict[str, list] = {c: [] for c in ("text", "conf", "left", "top", "width", "height",
                                                 "block_num", "par_num", "line_num")}
    for texto, conf, bloco, par, linha in palavras:
        for coluna, valor in zip(colunas, (texto, conf, 0, 0, 10, 10, bloco, par, linha)):
            colunas[coluna].append(valor)
    return colunas


class _Falso:
    """pytesseract simulado: conta as chamadas e devolve `dados`, ou levanta `erro`."""

    class Output:
        DICT = "dict"

    class pytesseract:  # noqa: N801 (o módulo real tem o mesmo nome)
        tesseract_cmd = ""

    def __init__(self, dados=None, erro: Exception | None = None):
        self.dados = dados or _dados(("DECRETO", 95, 1, 1, 1), ("Nº", 90, 1, 1, 1), ("1.234", 80, 1, 1, 1))
        self.erro = erro
        self.chamadas = 0

    def image_to_data(self, imagem, **opcoes):
        self.chamadas += 1
        self.opcoes = opcoes
        if self.erro:
            raise self.erro
        return self.dados

    def get_tesseract_version(self):
        return "5.3.4"


@pytest.fixture
def falso(monkeypatch, tmp_path):
    simulado = _Falso()
    monkeypatch.setattr(ocr, "_pytesseract", lambda executavel: simulado)
    executavel = tmp_path / "tesseract"
    executavel.write_text("")
    simulado.executavel = executavel
    return simulado


# ----------------------------------------------------------------------------
# Montagem do texto
# ----------------------------------------------------------------------------


def test_texto_por_linhas_e_blocos_sem_entradas_de_estrutura():
    dados = _dados(
        ("", -1, 1, 0, 0),  # bloco (estrutura)
        ("DECRETO", 96, 1, 1, 1), ("Nº", 91, 1, 1, 1), ("1.234,", 88, 1, 1, 1),
        ("Concede", 90, 1, 1, 2), ("estímulo.", 70, 1, 1, 2),
        ("  ", 95, 1, 1, 2),  # espaço solto
        ("A", 97, 2, 1, 1), ("GOVERNADORA", 93, 2, 1, 1),
    )
    texto, palavras = ocr.montar_texto(dados)
    assert texto == "DECRETO Nº 1.234,\nConcede estímulo.\n\nA GOVERNADORA\n"
    assert [p.texto for p in palavras][:3] == ["DECRETO", "Nº", "1.234,"]
    assert len(palavras) == 7


def test_confianca_media_e_pagina_vazia():
    _, palavras = ocr.montar_texto(_dados(("a", 90, 1, 1, 1), ("b", 70, 1, 1, 1)))
    assert ocr.PaginaOcr("a b\n", palavras, "5").confianca_media == 80
    assert ocr.PaginaOcr("", (), "5").confianca_media == 0
    assert ocr.montar_texto(_dados()) == ("", ())


def test_ajustes_recusam_dpi_e_psm_fora_do_limite():
    with pytest.raises(ValueError):
        ocr.AjustesOcr(dpi=1200)
    with pytest.raises(ValueError):
        ocr.AjustesOcr(psm=20)
    assert ocr.AjustesOcr(psm=4).opcoes == "--oem 1 --psm 4"


# ----------------------------------------------------------------------------
# Localização do Tesseract
# ----------------------------------------------------------------------------


def test_ocr_desligado_nao_procura_o_tesseract():
    assert ocr.localizar_tesseract(ConfiguracaoOcr(ativo=OCR_NAO)) is None
    assert ocr.criar_leitor(ConfiguracaoOcr(ativo=OCR_NAO), None) is None


def test_tesseract_cmd_tem_de_ser_o_tesseract(tmp_path):
    outro = tmp_path / "cmd.exe"
    outro.write_text("")
    with pytest.raises(ocr.ErroOcr, match="não é o executável"):
        ocr.localizar_tesseract(ConfiguracaoOcr(tesseract_cmd=outro))
    with pytest.raises(ocr.ErroOcr, match="não existe"):
        ocr.localizar_tesseract(ConfiguracaoOcr(tesseract_cmd=tmp_path / "tesseract.exe"))
    certo = tmp_path / "Tesseract.EXE"
    certo.write_text("")
    assert ocr.localizar_tesseract(ConfiguracaoOcr(tesseract_cmd=certo)) == certo


def test_sem_tesseract_no_path_auto_desliga_e_sim_falha(monkeypatch):
    monkeypatch.setattr(ocr.shutil, "which", lambda nome: None)
    assert ocr.localizar_tesseract(ConfiguracaoOcr(ativo=OCR_AUTO)) is None
    with pytest.raises(ocr.ErroOcr, match="OCR_ATIVO=sim"):
        ocr.localizar_tesseract(ConfiguracaoOcr(ativo=OCR_SIM))


# ----------------------------------------------------------------------------
# Leitor e cache
# ----------------------------------------------------------------------------


def _leitor(falso, cache: Path | None) -> ocr.LeitorOcr:
    cfg = ConfiguracaoOcr(tesseract_cmd=falso.executavel)
    return ocr.criar_leitor(cfg, cache)


def test_leitor_usa_o_cache_na_segunda_leitura(falso, tmp_path):
    pdf = criar_pdf_texto(tmp_path / "a.pdf", "qualquer")
    leitor = _leitor(falso, tmp_path / "cache")
    primeira = leitor.ler(pdf, 1)
    segunda = leitor.ler(pdf, 1)
    assert falso.chamadas == 1
    assert primeira == segunda
    assert primeira.texto == "DECRETO Nº 1.234\n" and primeira.versao == "5.3.4"
    assert falso.opcoes["lang"] == "por" and falso.opcoes["config"] == "--oem 1 --psm 3"
    # Outros ajustes são outra leitura.
    leitor.ler(pdf, 1, leitor.ajustes(psm=4))
    assert falso.chamadas == 2


def test_cache_muda_quando_o_pdf_muda(falso, tmp_path):
    pdf = criar_pdf_texto(tmp_path / "a.pdf", "um")
    leitor = _leitor(falso, tmp_path / "cache")
    leitor.ler(pdf, 1)
    criar_pdf_texto(pdf, "outro conteúdo")
    leitor.ler(pdf, 1)
    assert falso.chamadas == 2


def test_cache_ilegivel_e_apagado_e_refeito(falso, tmp_path):
    pdf = criar_pdf_texto(tmp_path / "a.pdf", "um")
    leitor = _leitor(falso, tmp_path / "cache")
    leitor.ler(pdf, 1)
    (arquivo,) = (tmp_path / "cache").glob("*.json")
    arquivo.write_text("{quebrado", encoding="utf-8")
    assert leitor.ler(pdf, 1).texto == "DECRETO Nº 1.234\n"
    assert falso.chamadas == 2
    assert not list((tmp_path / "cache").glob("*.tmp"))


def test_sem_cache_le_sempre(falso, tmp_path):
    pdf = criar_pdf_texto(tmp_path / "a.pdf", "um")
    leitor = _leitor(falso, None)
    leitor.ler(pdf, 1)
    leitor.ler(pdf, 1)
    assert falso.chamadas == 2


def test_pagina_inexistente_e_pdf_quebrado_viram_erro_ocr(falso, tmp_path):
    leitor = _leitor(falso, None)
    pdf = criar_pdf_texto(tmp_path / "a.pdf", "um")
    with pytest.raises(ocr.ErroOcr, match="não há página 2"):
        leitor.ler(pdf, 2)
    quebrado = tmp_path / "b.pdf"
    quebrado.write_bytes(b"isto nao e pdf")
    with pytest.raises(ocr.ErroOcr):
        leitor.ler(quebrado, 1)


@pytest.mark.parametrize(
    ("erro", "mensagem"),
    [(RuntimeError("Tesseract process timeout"), "passou de 120 s"), (RuntimeError("Error opening data file"), "data file")],
)
def test_falha_do_tesseract_vira_erro_ocr(falso, tmp_path, erro, mensagem):
    falso.erro = erro
    pdf = criar_pdf_texto(tmp_path / "a.pdf", "um")
    with pytest.raises(ocr.ErroOcr, match=mensagem):
        _leitor(falso, tmp_path / "cache").ler(pdf, 1)
    assert not (tmp_path / "cache").exists()  # falha não vai para o cache


def test_o_pdf_lido_nao_e_alterado(falso, tmp_path):
    pdf = criar_pdf_texto(tmp_path / "base.pdf", "um")
    antes = pdf.read_bytes()
    _leitor(falso, tmp_path / "cache").ler(pdf, 1)
    assert pdf.read_bytes() == antes


# ----------------------------------------------------------------------------
# Tesseract de verdade
# ----------------------------------------------------------------------------


@pytest.mark.ocr
@pytest.mark.skipif(not TEM_TESSERACT, reason="Tesseract não instalado")
def test_ocr_le_o_cabecalho_de_um_decreto_digitalizado(tmp_path):
    pdf = criar_pdf_imagem_de(AMOSTRAS / "Decreto 60929.pdf", tmp_path / "img.pdf")
    leitor = ocr.criar_leitor(ConfiguracaoOcr(), tmp_path / "cache")
    pagina = leitor.ler(pdf, 1)
    assert "DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026." in pagina.texto
    assert "A GOVERNADORA DO ESTADO" in pagina.texto
    assert pagina.confianca_media > 80


@pytest.mark.ocr
@pytest.mark.skipif(not TEM_TESSERACT, reason="Tesseract não instalado")
def test_verificar_ocr_com_o_tesseract_instalado():
    diagnostico = ocr.verificar(ConfiguracaoOcr())
    assert diagnostico.ok, diagnostico.linhas
    assert any("12.345" in linha for linha in diagnostico.linhas)


def test_verificar_sem_tesseract_explica_o_que_fazer(monkeypatch):
    monkeypatch.setattr(ocr.shutil, "which", lambda nome: None)
    diagnostico = ocr.verificar(ConfiguracaoOcr())
    assert not diagnostico.ok
    assert any("TESSERACT_CMD" in linha for linha in diagnostico.linhas)
    assert ocr.verificar(ConfiguracaoOcr(ativo=OCR_NAO)).ok


def test_linha_de_comando_verificar_ocr(monkeypatch, capsys):
    from dbf_cepe import cli

    monkeypatch.setenv("OCR_ATIVO", "nao")
    assert cli.main(["--verificar-ocr"]) == 0
    assert "OCR desligado" in capsys.readouterr().out
    monkeypatch.setenv("OCR_DPI", "5000")
    assert cli.main(["--verificar-ocr"]) == 2
    with pytest.raises(SystemExit) as saida:
        cli.main(["--verificar-ocr", "--simular"])
    assert saida.value.code == 2


# ----------------------------------------------------------------------------
# OCR_TESSDATA: pasta de idiomas fora da instalação (sem administrador)
# ----------------------------------------------------------------------------


def test_tessdata_inexistente_vira_erro(tmp_path):
    with pytest.raises(ocr.ErroOcr, match="OCR_TESSDATA"):
        ocr.localizar_tesseract(ConfiguracaoOcr(tessdata=tmp_path / "nao"))


def test_tessdata_vale_so_durante_a_chamada(monkeypatch, tmp_path):
    monkeypatch.setenv("TESSDATA_PREFIX", "original")
    with ocr._tessdata(ConfiguracaoOcr(tessdata=tmp_path)):
        assert ocr.os.environ["TESSDATA_PREFIX"] == str(tmp_path)
    assert ocr.os.environ["TESSDATA_PREFIX"] == "original"
    monkeypatch.delenv("TESSDATA_PREFIX")
    with ocr._tessdata(ConfiguracaoOcr(tessdata=tmp_path)):
        pass
    assert "TESSDATA_PREFIX" not in ocr.os.environ


@pytest.mark.ocr
@pytest.mark.skipif(not TEM_TESSERACT, reason="Tesseract não instalado")
def test_tessdata_com_so_o_portugues(tmp_path):
    origem = ocr._pasta_dos_idiomas(Path(shutil.which("tesseract")))
    if origem is None or not (origem / "por.traineddata").is_file():
        pytest.skip("por.traineddata não encontrado")
    pasta = tmp_path / "meus idiomas"
    pasta.mkdir()
    shutil.copy(origem / "por.traineddata", pasta)
    cfg = ConfiguracaoOcr(tessdata=pasta)
    diagnostico = ocr.verificar(cfg)
    assert diagnostico.ok, diagnostico.linhas
    assert "Idiomas instalados: por" in diagnostico.linhas
    leitor = ocr.criar_leitor(cfg, tmp_path / "cache")
    assert set(leitor.modelos) == {"por"} and len(leitor.modelos["por"]) == 64
    pdf = criar_pdf_imagem_de(AMOSTRAS / "Decreto 60929.pdf", tmp_path / "img.pdf")
    assert "60.929" in leitor.ler(pdf, 1).texto


# ----------------------------------------------------------------------------
# Tratamento da imagem antes do OCR (OCR_PRE_PROCESSAMENTO)
# ----------------------------------------------------------------------------


def test_limiar_de_otsu_separa_texto_e_fundo():
    histograma = [0] * 256
    histograma[40] = 300  # texto
    histograma[200] = 3000  # fundo
    limiar = ocr.limiar_de_otsu(histograma)
    assert 40 <= limiar < 200


def test_tratar_imagem_deixa_so_preto_e_branco():
    from PIL import Image

    imagem = Image.new("L", (40, 20), 190)  # fundo cinza-claro
    for x in range(5, 35):
        imagem.putpixel((x, 10), 60)  # um traço escuro
    imagem.putpixel((2, 2), 120)  # um pontinho solto
    assert ocr.tratar_imagem(imagem, "nenhum") is imagem
    binaria = ocr.tratar_imagem(imagem, "binarizar")
    assert {tom for tom, n in enumerate(binaria.histogram()) if n} == {0, 255} and binaria.getpixel((20, 10)) == 0 and binaria.getpixel((30, 3)) == 255
    limpa = ocr.tratar_imagem(imagem, "limpar")
    assert limpa.getpixel((2, 2)) == 255  # a mediana tirou o pontinho


def test_pre_processamento_entra_nos_ajustes_e_no_cache(falso, tmp_path):
    pdf = criar_pdf_texto(tmp_path / "a.pdf", "qualquer")
    leitor = ocr.criar_leitor(ConfiguracaoOcr(tesseract_cmd=falso.executavel, pre_processamento="limpar"), tmp_path / "c")
    assert leitor.ajustes().pre_processamento == "limpar"
    leitor.ler(pdf, 1)
    leitor.ler(pdf, 1, leitor.ajustes(pre_processamento="nenhum"))
    assert falso.chamadas == 2  # outro tratamento, outra leitura
    with pytest.raises(ValueError):
        ocr.AjustesOcr(pre_processamento="inventado")
