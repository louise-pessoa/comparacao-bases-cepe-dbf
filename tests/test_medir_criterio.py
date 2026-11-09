"""Ferramenta auxiliar que mede, por ano, os decretos reprovados pelo critério da cascata."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import medir_criterio as mc
from conftest import criar_pdf_texto
from dbf_cepe.leitura.separador import AtoSeparado
from dbf_cepe.regras.especies import DECRETO

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
ASSINATURA = "RAQUEL TEIXEIRA LYRA LUCENA\nGovernadora do Estado"


def ato(*partes: str) -> AtoSeparado:
    return AtoSeparado(DECRETO, "60929", CABECALHO, "\n".join(partes), 1, 1)


def bom(*substituir: tuple[str, str]) -> AtoSeparado:
    partes = [CABECALHO, EMENTA, ABERTURA, CORPO, FECHO, ASSINATURA]
    for velho, novo in substituir:
        partes = [novo if p == velho else p for p in partes]
    return ato(*partes)


def test_decreto_completo_e_aprovado():
    assert mc.motivos(bom(), da_dbf=True) == []


@pytest.mark.parametrize(
    ("ajuste", "motivo"),
    [
        ((FECHO, "Recife, 25 de junho de 2026."), mc.SEM_FECHO),
        ((FECHO, FECHO_ANTIGO), mc.FECHO_ANTIGO),
        ((FECHO, FECHO + "\n" + FECHO), mc.FECHO_REPETIDO),
        ((ABERTURA, "no uso das atribuições que lhe são conferidas,"), mc.SEM_ABERTURA),
        ((CORPO, CORPO + "\nDECRETO Nº 60.930, DE 25 DE JUNHO DE 2026.\nAltera algo."), mc.OUTRO_CABECALHO),
        ((CORPO, CORPO + "\nSECRETÁRIOS DE ESTADO\nSECRETÁRIA DE ADMINISTRAÇÃO\nFulana"), mc.EXPEDIENTE),
        ((CORPO, CORPO + "\nCOMPANHIA EDITORA DE PERNAMBUCO\nCNPJ 10.921.252/0001-07"), mc.EXPEDIENTE),
        ((EMENTA, "Dispõe sobre a fruição de estímulo previsto na Lei nº 11.675, que dispõe sobre o PRODEPE."), mc.DBF_SEM_TIPO),
    ],
)
def test_cada_motivo_de_reprovacao(ajuste, motivo):
    # da_dbf=False isola o motivo: com outro cabeçalho no texto, a identificação também
    # perde o tipo ("mais de um ato"), o que somaria "DBF sem tipo"
    assert mc.motivos(bom(ajuste), da_dbf=motivo == mc.DBF_SEM_TIPO) == [motivo]


def test_outro_cabecalho_num_decreto_da_dbf_tambem_perde_o_tipo():
    colado = (CORPO, CORPO + "\nDECRETO Nº 60.930, DE 25 DE JUNHO DE 2026.\nAltera algo.")
    assert mc.motivos(bom(colado), da_dbf=True) == [mc.OUTRO_CABECALHO, mc.DBF_SEM_TIPO]


@pytest.mark.parametrize(
    "abertura",
    [
        "A VICE GOVERNADORA, NO EXERCÍCIO DO CARGO DE GOVERNADORA DO ESTADO, no uso das atribuições,",
        "A VICE-GOVERNADORA, NO EXERCÍCIO DO CARGO DE GOVERNADORA DO ESTADO, no uso das atribuições,",
        "O PRESIDENTE DA ASSEMBLÉIA LEGISLATIVA DO ESTADO DE PERNAMBUCO, NO EXERCÍCIO DO CARGO DE\n"
        "GOVERNADOR DO ESTADO, no uso das atribuições,",
        "O GOVERNADOR DO ESTADO, no uso das atribuições,",
    ],
)
def test_abertura_de_quem_substitui_o_titular_e_aceita(abertura):
    assert mc.motivos(bom((ABERTURA, abertura)), da_dbf=True) == []


def test_sem_tipo_so_reprova_decreto_da_dbf():
    sem_tipo = (EMENTA, "Abre ao Orçamento Fiscal crédito suplementar.")
    assert mc.motivos(bom(sem_tipo), da_dbf=False) == []
    assert mc.motivos(bom(sem_tipo), da_dbf=True) == [mc.DBF_SEM_TIPO]


def test_cabecalho_citado_no_meio_da_linha_nao_reprova():
    citacao = CORPO + "\nconforme o DECRETO Nº 60.930, DE 25 DE JUNHO DE 2026."
    assert mc.motivos(bom((CORPO, citacao)), da_dbf=False) == []


def test_abertura_so_conta_perto_do_cabecalho():
    longe = "x" * 2000 + "\n" + ABERTURA
    assert mc.motivos(bom((ABERTURA, longe)), da_dbf=False) == [mc.SEM_ABERTURA]


def test_varios_motivos_juntos():
    reprovado = bom((FECHO, FECHO_ANTIGO), (ABERTURA, "sem abertura aqui,"))
    assert mc.motivos(reprovado, da_dbf=False) == [mc.FECHO_ANTIGO, mc.SEM_ABERTURA]


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
    _caderno(tmp_path / "2026", "20260626_ResumoDiario.pdf", _decreto("60.929"), _decreto("60.930"))
    anos, falhas = mc.medir([tmp_path])
    assert falhas == []
    assert (anos["2026"].decretos, anos["2026"].reprovados, anos["2026"].dbf) == (2, 0, 2)
    assert (anos["2010"].reprovados, anos["2010"].dbf_reprovados) == (1, 1)
    assert anos["2010"].motivos == {mc.FECHO_ANTIGO: 1}
    assert anos["2010"].exemplos == ["20100319_PoderExecutivo.pdf p. 1 | Decreto 34716 (DBF) | fecho antigo"]


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
    _caderno(tmp_path / "c", "20100319_PoderExecutivo.pdf", _decreto("34.716", FECHO_ANTIGO))
    saida = tmp_path / "medida.json"
    assert mc.main([str(tmp_path / "c"), "--json", str(saida)]) == 0
    texto = capsys.readouterr().out
    assert "2010" in texto and "fecho antigo 1" in texto
    assert json.loads(saida.read_text(encoding="utf-8"))["2010"]["reprovados"] == 1


def test_main_sem_pasta_ou_sem_pdf_sai_com_2(tmp_path, capsys):
    assert mc.main([str(tmp_path / "nao_existe")]) == 2
    assert mc.main([str(tmp_path)]) == 2


def test_main_com_pdf_ilegivel_avisa_e_sai_com_1(tmp_path, capsys):
    _caderno(tmp_path, "20260626_ResumoDiario.pdf", _decreto("60.929"))
    (tmp_path / "20260627_ResumoDiario.pdf").write_bytes(b"nao e pdf")
    assert mc.main([str(tmp_path)]) == 1
    assert "caderno não lido" in capsys.readouterr().err
