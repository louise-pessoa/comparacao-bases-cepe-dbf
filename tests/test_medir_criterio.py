"""Ferramenta auxiliar que mede, por ano, os decretos reprovados pelo critério da cascata."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import medir_criterio as mc
from conftest import criar_pdf_texto
from dbf_cepe.leitura import criterio

AMOSTRAS_CADERNOS = Path(__file__).parent / "amostras_cadernos"

CABECALHO = "DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026."
EMENTA = "Concede estímulo previsto na Lei nº 11.675, de 11 de outubro de 1999, que dispõe sobre o PRODEPE, à empresa X."
ABERTURA = "A GOVERNADORA DO ESTADO, no uso das atribuições que lhe são conferidas pelo inciso IV do art. 37,"
CORPO = "DECRETA:\nArt. 1º Fica concedido o estímulo.\nArt. 2º Este Decreto entra em vigor na data de sua publicação."
FECHO = (
    "Palácio do Campo das Princesas, Recife, 25 de junho do ano de 2026, 210º da Revolução Republicana "
    "Constitucionalista e 204º da Independência do Brasil."
)
FECHO_ANTIGO = "PALÁCIO DO CAMPO DAS PRINCESAS, em 18 de março de 2010."
SEM_FECHO = "Recife, 18 de março de 2010."
ASSINATURA = "RAQUEL TEIXEIRA LYRA LUCENA\nGovernadora do Estado"


@pytest.mark.parametrize(
    ("nome", "ano"),
    [("20190322_PoderExecutivo.pdf", "2019"), ("20261021_ResumoDiario.pdf", "2026"), ("caderno.pdf", "sem data")],
)
def test_ano_vem_do_nome_do_caderno(nome, ano):
    assert mc.ano_do_caderno(Path(nome)) == ano


def _caderno(pasta: Path, nome: str, *decretos: str) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    return criar_pdf_texto(pasta / nome, "\n".join(decretos))


def _decreto(numero: str, fecho: str = FECHO) -> str:
    return "\n".join([f"DECRETO Nº {numero}, DE 25 DE JUNHO DE 2026.", EMENTA, ABERTURA, CORPO, fecho, ASSINATURA])


def test_medir_agrupa_por_ano_e_procura_nas_subpastas(tmp_path):
    _caderno(tmp_path / "2010", "20100319_PoderExecutivo.pdf", _decreto("34.716", FECHO_ANTIGO))
    _caderno(tmp_path / "2009", "20090319_PoderExecutivo.pdf", _decreto("33.000", SEM_FECHO))
    _caderno(tmp_path / "2026", "20260626_ResumoDiario.pdf", _decreto("60.929"), _decreto("60.930"))
    anos, falhas = mc.medir([tmp_path])
    assert falhas == []
    assert (anos["2026"].decretos, anos["2026"].reprovados, anos["2026"].recuperados) == (2, 0, 0)
    # fecho antigo: reprova na leitura atual e é recuperado no nível 2
    assert (anos["2010"].recuperados, anos["2010"].reprovados) == (1, 0)
    # sem fecho nenhum: reprova em todos os níveis
    assert (anos["2009"].reprovados, anos["2009"].dbf_reprovados) == (1, 1)
    assert anos["2009"].motivos == {criterio.SEM_FECHO: 1}
    assert anos["2009"].exemplos == ["20090319_PoderExecutivo.pdf p. 1 | Decreto 33000 (DBF) | sem fecho"]


def test_amostras_reais_de_2026(tmp_path):
    for pdf in AMOSTRAS_CADERNOS.glob("*.pdf"):
        shutil.copy(pdf, tmp_path / pdf.name)
    anos, falhas = mc.medir([tmp_path])
    assert falhas == [] and anos["sem data"].decretos == 13
    # só reprova o 60.905, que continua na página 10, fora do recorte da amostra da página 9
    assert anos["sem data"].exemplos == [
        "Caderno 2026-06-26 (pagina 9).pdf p. 1 | Decreto 60905 (DBF) | sem fecho"
    ]


def test_main_imprime_tabela_e_grava_json(tmp_path, capsys):
    _caderno(tmp_path / "c", "20100319_PoderExecutivo.pdf", _decreto("34.716", SEM_FECHO))
    saida = tmp_path / "medida.json"
    assert mc.main([str(tmp_path / "c"), "--json", str(saida)]) == 0
    texto = capsys.readouterr().out
    assert "2010" in texto and "sem fecho 1" in texto
    assert json.loads(saida.read_text(encoding="utf-8"))["2010"]["reprovados"] == 1


def test_main_sem_pasta_ou_sem_pdf_sai_com_2(tmp_path, capsys):
    assert mc.main([str(tmp_path / "nao_existe")]) == 2
    assert mc.main([str(tmp_path)]) == 2


def test_main_com_pdf_ilegivel_avisa_e_sai_com_1(tmp_path, capsys):
    _caderno(tmp_path, "20260626_ResumoDiario.pdf", _decreto("60.929"))
    (tmp_path / "20260627_ResumoDiario.pdf").write_bytes(b"nao e pdf")
    assert mc.main([str(tmp_path)]) == 1
    assert "caderno não lido" in capsys.readouterr().err
