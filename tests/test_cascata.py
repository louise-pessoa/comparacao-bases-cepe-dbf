"""Leitura em cascata (leitura/cascata.py): o que passa no critério sai como veio; o que
reprova é relido nos níveis de recuperação, em ordem; o que nenhum nível aprova vai para
revisão manual."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pymupdf

from dbf_cepe.leitura import cascata, criterio
from dbf_cepe.leitura.cascata import Nivel, avaliar, separar_em_cascata
from dbf_cepe.leitura.separador import AtoSeparado, separar_caderno
from dbf_cepe.regras.especies import DECRETO

TRECHO = Path(__file__).parent / "amostras_cadernos" / "Caderno 2026-06-26 (paginas 24 a 26).pdf"
PAGINA_9 = Path(__file__).parent / "amostras_cadernos" / "Caderno 2026-06-26 (pagina 9).pdf"

BOM = (
    "DECRETO Nº 61.000, DE 1 DE JULHO DE 2026.\nAbre crédito suplementar.\n"
    "A GOVERNADORA DO ESTADO, no uso das atribuições,\nDECRETA:\nArt. 1º Fica aberto.\n"
    "Palácio do Campo das Princesas, Recife, 1º de julho do ano de 2026, 210º da Revolução "
    "Republicana Constitucionalista e 204º da Independência do Brasil.\nRAQUEL TEIXEIRA LYRA LUCENA"
)
CORTADO = BOM.split("\nPalácio")[0]  # sem o fecho


def ato(texto: str, numero: str = "61000") -> AtoSeparado:
    return AtoSeparado(DECRETO, numero, f"DECRETO Nº {numero}", texto, 3, 3)


def nivel(numero: int, texto: str | None, chamadas: list[int] | None = None, outro_numero: str | None = None) -> Nivel:
    def reler(caminho: Path, original: AtoSeparado) -> AtoSeparado | None:
        if chamadas is not None:
            chamadas.append(numero)
        if texto is None:
            return None
        return replace(original, texto=texto, numero=outro_numero or original.numero)

    return Nivel(numero, f"teste {numero}", reler)


def test_aprovado_no_nivel_1_sai_identico_e_nenhum_nivel_e_chamado():
    chamadas: list[int] = []
    original = ato(BOM)
    assert avaliar(original, (nivel(2, BOM, chamadas),), Path("c.pdf")) is original
    assert chamadas == []


def test_reprovado_e_recuperado_no_primeiro_nivel_que_aprova():
    chamadas: list[int] = []
    final = avaliar(ato(CORTADO), (nivel(2, CORTADO, chamadas), nivel(3, BOM, chamadas), nivel(4, BOM, chamadas)), Path("c.pdf"))
    assert chamadas == [2, 3]
    assert (final.nivel_de_leitura, final.texto, final.reprovacao) == (3, BOM, ())


def test_nivel_que_nao_acha_o_ato_ou_acha_outro_numero_nao_conta():
    final = avaliar(ato(CORTADO), (nivel(2, None), nivel(3, BOM, outro_numero="61001")), Path("c.pdf"))
    assert final.nivel_de_leitura is None


def test_reprovado_em_todos_os_niveis_vai_para_revisao_com_os_motivos_do_nivel_1():
    final = avaliar(ato(CORTADO), (nivel(2, CORTADO),), Path("c.pdf"))
    assert final.nivel_de_leitura is None
    assert final.reprovacao == (criterio.SEM_FECHO,)
    assert final.texto == CORTADO
    assert final.avisos[-1] == "reprovado no critério de leitura (sem fecho) em todos os níveis; revisão manual"


def test_sem_niveis_de_recuperacao_o_reprovado_so_ganha_o_aviso():
    final = avaliar(ato(CORTADO), (), Path("c.pdf"))
    assert final.nivel_de_leitura is None and final.texto == CORTADO and len(final.avisos) == 1


def test_niveis_de_recuperacao_hoje():
    assert [(n.numero, n.aceita_fechos_anteriores) for n in cascata.NIVEIS] == [(2, True), (3, True)]


def test_caderno_real_de_2026_passa_inteiro_no_nivel_1():
    em_cascata = separar_em_cascata(TRECHO)
    direto = separar_caderno(TRECHO)
    assert em_cascata.atos == direto.atos
    assert all(a.nivel_de_leitura == 1 and not a.reprovacao for a in em_cascata.atos)


def test_decreto_cortado_pelo_fim_da_amostra_reprova():
    atos = {a.numero: a for a in separar_em_cascata(PAGINA_9).atos}
    assert atos["60905"].nivel_de_leitura is None and atos["60905"].reprovacao == (criterio.SEM_FECHO,)
    assert atos["60903"].nivel_de_leitura == 1


def test_niveis_passados_na_chamada_valem_no_lugar_dos_padrao():
    atos = {a.numero: a for a in separar_em_cascata(PAGINA_9, niveis=(nivel(2, None),)).atos}
    assert atos["60905"].nivel_de_leitura is None


# ----------------------------------------------------------------------------
# Níveis reais, com cadernos sintéticos
# ----------------------------------------------------------------------------

ANTIGO = (
    "DECRETO Nº 34.700, DE 5 DE MAIO DE 2010.\nConcede incentivo do PRODEPE à empresa Exemplo Ltda.\n"
    "O GOVERNADOR DO ESTADO, no uso das atribuições,\nDECRETA:\nArt. 1º Fica concedido o incentivo.\n"
    "Art. 2º Este Decreto entra em vigor na data de sua publicação.\n"
    "PALÁCIO DO CAMPO DAS PRINCESAS, em 5 de maio de 2010.\nEDUARDO HENRIQUE ACCIOLY CAMPOS"
)
ESQUERDA = (
    "DECRETO Nº 47.200, DE 3 DE MARÇO DE 2019.\nConcede incentivo do PRODEPE à empresa Exemplo Ltda.\n"
    "O GOVERNADOR DO ESTADO, no uso das atribuições,\nDECRETA:\nArt. 1º Fica concedido o incentivo."
)
DIREITA = (
    "Art. 2º Este Decreto entra em vigor na data de sua publicação.\n"
    "Palácio do Campo das Princesas, Recife, 3 de março do ano de 2019, 203º da Revolução Republicana "
    "Constitucionalista e 197º da Independência do Brasil.\nPAULO HENRIQUE SARAIVA CÂMARA"
)


def _caderno(caminho: Path, *caixas: tuple[tuple[float, float, float, float], str]) -> Path:
    doc = pymupdf.open()
    pagina = doc.new_page()
    for retangulo, texto in caixas:  # a ordem das caixas é a ordem em que o PDF guarda o texto
        pagina.insert_textbox(pymupdf.Rect(*retangulo), texto, fontsize=9)
    doc.save(caminho)
    return caminho


def test_nivel_2_recupera_o_decreto_com_o_fecho_de_ate_2010(tmp_path):
    caminho = _caderno(tmp_path / "2010.pdf", ((40, 40, 560, 800), ANTIGO))
    (lido,) = separar_em_cascata(caminho).atos
    assert lido.nivel_de_leitura == 2 and lido.reprovacao == ()
    assert lido.texto.rstrip().endswith("EDUARDO HENRIQUE ACCIOLY CAMPOS")
    assert lido.avisos[-1].startswith("lido no nível 2 (fecho de até 2010): a leitura atual reprovou (fecho antigo)")


def test_nivel_3_le_por_coluna_a_pagina_guardada_com_a_coluna_da_direita_primeiro(tmp_path):
    caminho = _caderno(tmp_path / "2019.pdf", ((310, 40, 570, 800), DIREITA), ((40, 40, 300, 800), ESQUERDA))
    (direto,) = separar_caderno(caminho).atos
    assert criterio.motivos_de_reprovacao(direto)  # a leitura atual mistura as colunas
    (lido,) = separar_em_cascata(caminho).atos
    assert lido.nivel_de_leitura == 3 and lido.reprovacao == ()
    assert lido.texto.index("Art. 1º") < lido.texto.index("Art. 2º") < lido.texto.index("Palácio")


def test_sem_fecho_em_nenhuma_leitura_vai_para_revisao_manual(tmp_path):
    caminho = _caderno(tmp_path / "2009.pdf", ((40, 40, 560, 800), ANTIGO.split("\nPALÁCIO")[0]))
    (lido,) = separar_em_cascata(caminho).atos
    assert lido.nivel_de_leitura is None and lido.reprovacao == (criterio.SEM_FECHO,)


def test_releitura_de_caderno_que_nao_abre_nao_acha_o_ato(tmp_path):
    falso = tmp_path / "falso.pdf"
    falso.write_bytes(b"nao e pdf")
    assert cascata.NIVEIS[0].reler(falso, ato(CORTADO)) is None
    assert cascata.NIVEIS[1].reler(tmp_path / "nao_existe.pdf", ato(CORTADO)) is None


def test_especie_sem_fecho_anterior_fica_como_esta():
    sem_anterior = replace(DECRETO, fechos_anteriores=())
    assert cascata.com_fechos_anteriores(sem_anterior) is sem_anterior
    assert cascata.com_fechos_anteriores(replace(DECRETO, fecho=None)).fecho is None
    juntos = cascata.com_fechos_anteriores(DECRETO).fecho
    assert juntos.search("PALÁCIO DO CAMPO DAS PRINCESAS, em 5 de maio de 2010.")
