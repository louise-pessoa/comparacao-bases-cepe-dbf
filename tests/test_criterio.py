"""Critério de aprovação da leitura em cascata (leitura/criterio.py)."""

from __future__ import annotations

import pytest

from dbf_cepe.leitura import criterio
from dbf_cepe.leitura.separador import AtoSeparado
from dbf_cepe.regras.especies import DECRETO, EDITAL

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
    assert criterio.motivos_de_reprovacao(bom(), e_da_dbf=True) == []


@pytest.mark.parametrize(
    ("ajuste", "motivo"),
    [
        ((FECHO, "Recife, 25 de junho de 2026."), criterio.SEM_FECHO),
        ((FECHO, FECHO_ANTIGO), criterio.FECHO_ANTIGO),
        ((FECHO, FECHO + "\n" + FECHO), criterio.FECHO_REPETIDO),
        ((ABERTURA, "no uso das atribuições que lhe são conferidas,"), criterio.SEM_ABERTURA),
        ((CORPO, CORPO + "\nDECRETO Nº 60.930, DE 25 DE JUNHO DE 2026.\nAltera algo."), criterio.OUTRO_CABECALHO),
        ((CORPO, CORPO + "\nSECRETÁRIOS DE ESTADO\nSECRETÁRIA DE ADMINISTRAÇÃO\nFulana"), criterio.EXPEDIENTE),
        ((CORPO, CORPO + "\nCOMPANHIA EDITORA DE PERNAMBUCO\nCNPJ 10.921.252/0001-07"), criterio.EXPEDIENTE),
        ((EMENTA, "Dispõe sobre a fruição de estímulo previsto na Lei nº 11.675, que dispõe sobre o PRODEPE."), criterio.DBF_SEM_TIPO),
    ],
)
def test_cada_motivo_de_reprovacao(ajuste, motivo):
    # e_da_dbf=False isola o motivo: com outro cabeçalho no texto, a identificação também
    # perde o tipo ("mais de um ato"), o que somaria "DBF sem tipo"
    assert criterio.motivos_de_reprovacao(bom(ajuste), e_da_dbf=motivo == criterio.DBF_SEM_TIPO) == [motivo]


def test_outro_cabecalho_num_decreto_da_dbf_tambem_perde_o_tipo():
    colado = (CORPO, CORPO + "\nDECRETO Nº 60.930, DE 25 DE JUNHO DE 2026.\nAltera algo.")
    assert criterio.motivos_de_reprovacao(bom(colado), e_da_dbf=True) == [criterio.OUTRO_CABECALHO, criterio.DBF_SEM_TIPO]


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
    assert criterio.motivos_de_reprovacao(bom((ABERTURA, abertura)), e_da_dbf=True) == []


def test_sem_tipo_so_reprova_decreto_da_dbf():
    sem_tipo = (EMENTA, "Abre ao Orçamento Fiscal crédito suplementar.")
    assert criterio.motivos_de_reprovacao(bom(sem_tipo), e_da_dbf=False) == []
    assert criterio.motivos_de_reprovacao(bom(sem_tipo), e_da_dbf=True) == [criterio.DBF_SEM_TIPO]


def test_cabecalho_citado_no_meio_da_linha_nao_reprova():
    citacao = CORPO + "\nconforme o DECRETO Nº 60.930, DE 25 DE JUNHO DE 2026."
    assert criterio.motivos_de_reprovacao(bom((CORPO, citacao)), e_da_dbf=False) == []


def test_abertura_so_conta_perto_do_cabecalho():
    longe = "x" * 2000 + "\n" + ABERTURA
    assert criterio.motivos_de_reprovacao(bom((ABERTURA, longe)), e_da_dbf=False) == [criterio.SEM_ABERTURA]


def test_varios_motivos_juntos():
    reprovado = bom((FECHO, FECHO_ANTIGO), (ABERTURA, "sem abertura aqui,"))
    assert criterio.motivos_de_reprovacao(reprovado, e_da_dbf=False) == [criterio.FECHO_ANTIGO, criterio.SEM_ABERTURA]


def test_motivo_dbf_calculado_quando_nao_informado():
    assert criterio.da_dbf(bom()) is True
    assert criterio.motivos_de_reprovacao(bom()) == []
    sem_tipo = bom((EMENTA, "Dispõe sobre a fruição de estímulo previsto na Lei nº 11.675, que dispõe sobre o PRODEPE."))
    assert criterio.motivos_de_reprovacao(sem_tipo) == [criterio.DBF_SEM_TIPO]


def test_especie_sem_fecho_nem_abertura_nao_e_cobrada_por_eles():
    edital = AtoSeparado(EDITAL, "063", "EDITAL DBF Nº 063/2026", "EDITAL DBF Nº 063/2026\nCREDENCIAMENTO\nTexto.", 1, 1)
    assert criterio.motivos_de_reprovacao(edital, e_da_dbf=False) == []


@pytest.mark.parametrize(
    "trecho",
    [
        "§ 1º Os membros serão indicados pelos respectivos Secretários de Estado.",
        "percentual alterado por ato específico do Diretor Presidente da EPTI.",
        "Cargo\nDiretor Presidente\nDAS-1",
        "conforme a COMPANHIA EDITORA DE PERNAMBUCO informou",
    ],
)
def test_mencao_no_corpo_do_ato_nao_e_expediente(trecho):
    assert criterio.motivos_de_reprovacao(bom((CORPO, CORPO + "\n" + trecho)), e_da_dbf=False) == []
