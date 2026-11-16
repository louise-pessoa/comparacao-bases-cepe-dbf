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
