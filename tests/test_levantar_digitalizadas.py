"""Ferramenta da fase 0 do OCR: páginas digitalizadas dos cadernos e PDFs em imagem da base."""

from __future__ import annotations

import json

import levantar_digitalizadas as ld
from conftest import AMOSTRAS, criar_pdf_imagem, criar_pdf_texto


def test_conta_por_ano_as_paginas_digitalizadas(tmp_path):
    pasta = tmp_path / "2010-03"
    pasta.mkdir()
    criar_pdf_imagem(pasta / "20100318_PoderExecutivo.pdf", com_pagina_de_texto=True)
    criar_pdf_texto(pasta / "20100319_PoderExecutivo.pdf", "DECRETO Nº 1, texto normal do caderno.")
    criar_pdf_texto(tmp_path / "20260626_ResumoDiario.pdf", "texto")
    anos, falhas = ld.levantar_cadernos([tmp_path])
    assert falhas == []
    assert anos["2010"].cadernos == 2 and anos["2010"].paginas == 3
    assert anos["2010"].cadernos_com_digitalizadas == 1 and anos["2010"].digitalizadas == 1
    assert "digitalizadas [2]" in anos["2010"].exemplos[0]
    assert anos["2026"].cadernos_com_digitalizadas == 0


def test_caderno_quebrado_vira_aviso(tmp_path):
    (tmp_path / "20100318_X.pdf").write_bytes(b"quebrado")
    anos, falhas = ld.levantar_cadernos([tmp_path])
    assert anos == {} and len(falhas) == 1


def test_base_separa_imagem_de_texto(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    criar_pdf_imagem(base / "Decreto 35945.pdf")
    (base / "Decreto 60929.pdf").write_bytes((AMOSTRAS / "Decreto 60929.pdf").read_bytes())
    resultado = ld.levantar_base(base)
    assert resultado.total == 2
    assert resultado.situacoes == {"imagem (digitalizado)": 1, "ok": 1}
    assert resultado.imagem[0].startswith("Decreto 35945.pdf")


def test_main_grava_json_e_nao_altera_os_pdfs(tmp_path, capsys):
    caderno = criar_pdf_imagem(tmp_path / "20100318_PoderExecutivo.pdf")
    antes = caderno.read_bytes()
    saida = tmp_path / "levantamento.json"
    assert ld.main([str(tmp_path), "--json", str(saida)]) == 0
    assert "2010" in capsys.readouterr().out
    assert json.loads(saida.read_text(encoding="utf-8"))["cadernos"]["2010"]["digitalizadas"] == 1
    assert caderno.read_bytes() == antes


def test_main_pasta_inexistente(tmp_path):
    assert ld.main([str(tmp_path / "nao")]) == 2


def test_base_por_faixa_de_numero(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    criar_pdf_imagem(base / "Decreto 16176.pdf")
    criar_pdf_imagem(base / "Decreto 34529 (republicação).pdf")
    (base / "Decreto 34999.pdf").write_bytes((AMOSTRAS / "Decreto 60929.pdf").read_bytes())
    (base / "Decreto 60929.pdf").write_bytes((AMOSTRAS / "Decreto 60929.pdf").read_bytes())
    resultado = ld.levantar_base(base)
    assert resultado.faixas["34000-34999"] == {"imagem (digitalizado)": 1, "ok": 1}
    assert ld.faixa_do_arquivo("Edital.pdf") == "outros"
    assert ld.faixa_do_arquivo("Edital 2020-15.pdf") == "outros"  # o ano não é número de decreto
    tabela = ld.tabela_da_base(resultado)
    assert "16000-16999" in tabela and "34000-34999" in tabela
    assert "60000-60999" not in tabela  # só faixas com PDF sem texto


def _ato(numero: str, inicial: int, final: int):
    from dbf_cepe.leitura.separador import AtoSeparado
    from dbf_cepe.regras.especies import ESPECIES_ATIVAS

    return AtoSeparado(ESPECIES_ATIVAS[0], numero, f"DECRETO Nº {numero}", "texto", inicial, final)


def test_detalhe_da_pagina_diz_a_quem_ela_pode_pertencer():
    from dbf_cepe.leitura.ocr import PaginaOcr, montar_texto

    dados = {c: [] for c in ("text", "conf", "left", "top", "width", "height", "block_num", "par_num", "line_num")}
    for palavra in ("ANEXO", "ÚNICO", "DO", "DECRETO", "Nº", "48.900"):
        for coluna, valor in zip(dados, (palavra, 90, 0, 0, 1, 1, 1, 1, 1)):
            dados[coluna].append(valor)
    texto, palavras = montar_texto(dados)
    lida = PaginaOcr(texto, palavras, "5")
    linha = ld.detalhar_pagina(12, lida, (_ato("48890", 9, 11),))
    assert "6 palavras, confiança 90" in linha
    assert "cita ANEXO, Decreto 48900" in linha
    assert "depois de Decreto 48890 (termina na p. 11)" in linha
    assert "dentro de Decreto 48890" in ld.detalhar_pagina(10, lida, (_ato("48890", 9, 11),))
    vazia = ld.detalhar_pagina(12, PaginaOcr("", (), "5"), ())
    assert "OCR sem texto" in vazia and "nenhum decreto perto" in vazia


def test_so_a_base_sem_pasta_de_cadernos(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)  # sem dados/cadernos aqui
    base = tmp_path / "base"
    base.mkdir()
    criar_pdf_imagem(base / "Decreto 34529.pdf")
    assert ld.main(["--base", str(base)]) == 0
    saida = capsys.readouterr().out
    assert "34000-34999" in saida and "ano" not in saida.split("Base da DBF")[0]
