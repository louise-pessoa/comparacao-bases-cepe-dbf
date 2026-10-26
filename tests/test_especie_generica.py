"""O pipeline não conhece "decreto": tudo vem da `Especie`.

Estes testes usam uma espécie FICTÍCIA ("Parecer"), com cabeçalho, fecho, cargo
de quem assina, abertura do corpo, fim do cabeçalho e programa próprios. Se
alguma etapa ainda dependesse de regra de decreto (fecho "Independência do
Brasil", "A GOVERNADORA", "DECRETA:"), estes testes falhariam. É a garantia de
que incluir editais no futuro é só definir uma `Especie` nova em `especies.py`.
"""

from __future__ import annotations

import re

from especies import DECRETO, Especie, Tipo
from identificador import StatusIdentificacao, identificar
from programas import (
    Programa,
    cabecalho_do_ato,
    cita_programa_so_no_corpo,
    em_escopo,
    programas_do_ato,
)
from separador import separar_texto

ALFA = Programa("ALFA", "Programa Alfa", "norma fictícia", re.compile(r"\bAlfa\b", re.IGNORECASE))

PARECER = Especie(
    nome="Parecer",
    cabecalho=re.compile(r"(?<![A-Za-zÀ-ú])PARECER\s+N[º°]\s*(?P<numero>\d+)/\d{4}\."),
    padrao_arquivo="{especie}-{numero}.pdf",
    tipos=(Tipo("OPINATIVO", re.compile(r"Opina\b", re.IGNORECASE), None),),
    fecho=re.compile(r"É o parecer\."),
    cargo_assinante=re.compile(r"^Procurador-Geral$"),
    abertura_do_corpo=re.compile(r"\bVistos e examinados\b"),
    fim_do_cabecalho=re.compile(r"\bCONCLUSÃO\s*:"),
    programas=(ALFA,),
)

TEXTO = """\
PARECER Nº 12/2026.
Opina sobre o enquadramento da empresa X no programa Alfa.
Vistos e examinados os autos,
CONSIDERANDO a Lei do programa;
CONCLUSÃO:
Pelo deferimento. É o parecer.
JOSE DA SILVA
Procurador-Geral
PARECER Nº 13/2026.
Opina sobre assunto qualquer.
Vistos e examinados os autos,
CONCLUSÃO:
Pelo arquivamento do pedido do programa Alfa. É o parecer.
MARIA SOUZA
Procurador-Geral
Texto solto que não pertence a nenhum ato.
DECRETO Nº 60.001, DE 25 DE JUNHO DE 2026.
Dispõe sobre a estrutura da Secretaria.
A GOVERNADORA DO ESTADO,
DECRETA:
Art. 1º Fica criada a Secretaria.
Palácio do Campo das Princesas, Recife, 25 de junho de 2026; 204º da Revolução Republicana Constitucionalista e 204º da Independência do Brasil.
RAQUEL LYRA
Governadora do Estado
"""


def test_nome_do_arquivo_segue_o_padrao_da_especie():
    assert PARECER.nome_arquivo("12") == "Parecer-12.pdf"
    assert DECRETO.nome_arquivo("60929") == "Decreto 60929.pdf"


def test_separador_usa_fecho_e_assinante_da_especie():
    atos, avisos = separar_texto(TEXTO, especies=(PARECER,))
    assert [a.numero for a in atos] == ["12", "13"]
    assert avisos == []
    assert [a.avisos for a in atos] == [(), ()]
    assert atos[0].texto.endswith("Procurador-Geral")
    assert "PARECER Nº 13" not in atos[0].texto
    # O fecho + o cargo do parecer encerram o ato: o texto solto depois fica de fora.
    assert atos[1].texto.endswith("Procurador-Geral")
    assert "Texto solto" not in atos[1].texto


def test_ato_de_uma_especie_termina_antes_do_cabecalho_de_outra():
    atos, avisos = separar_texto(TEXTO, especies=(PARECER, DECRETO))
    assert [(a.especie.nome, a.numero) for a in atos] == [
        ("Parecer", "12"), ("Parecer", "13"), ("Decreto", "60001"),
    ]
    assert avisos == []
    assert "DECRETO" not in atos[1].texto and "Texto solto" not in atos[1].texto
    assert atos[2].texto.endswith("Governadora do Estado")


def test_regras_do_decreto_nao_vazam_para_outra_especie():
    # Só o decreto pede "Independência do Brasil." e "A GOVERNADORA": o parecer
    # não gera aviso por não tê-los.
    atos, _ = separar_texto(TEXTO, especies=(PARECER,))
    assert all(not a.avisos for a in atos)


def test_aviso_cita_o_fecho_e_a_abertura_da_especie():
    sem_nada = "PARECER Nº 14/2026.\nOpina sobre algo.\nTexto solto.\n"
    atos, _ = separar_texto(sem_nada, especies=(PARECER,))
    avisos = " | ".join(atos[0].avisos)
    assert "É o parecer" in avisos
    assert "Vistos e examinados" in avisos


def test_especie_sem_fecho_nem_abertura_nao_gera_aviso():
    minima = Especie("Nota", re.compile(r"NOTA\s+N[º°]\s*(?P<numero>\d+)\."), "{especie} {numero}.pdf")
    atos, avisos = separar_texto("NOTA Nº 5.\nTexto da nota.\n", especies=(minima,))
    assert [a.numero for a in atos] == ["5"]
    assert atos[0].avisos == () and avisos == []


def test_identificador_usa_a_abertura_da_especie_para_delimitar_a_ementa():
    ident = identificar(TEXTO.split("PARECER Nº 13")[0], (PARECER,))
    assert ident.status is StatusIdentificacao.IDENTIFICADO
    assert ident.numero == "12"
    assert ident.tipo.nome == "OPINATIVO"
    assert "Vistos" not in ident.detalhe


def test_cabecalho_do_ato_termina_onde_a_especie_manda():
    atos, _ = separar_texto(TEXTO, especies=(PARECER,))
    cab = cabecalho_do_ato(atos[0].texto, PARECER)
    assert "CONSIDERANDO" in cab and "Pelo deferimento" not in cab


def test_programa_e_escopo_vem_da_especie():
    atos, _ = separar_texto(TEXTO, especies=(PARECER,))
    primeiro, segundo = atos
    assert [p.sigla for p in programas_do_ato(primeiro.texto, PARECER)] == ["ALFA"]
    assert em_escopo(PARECER, programas_do_ato(primeiro.texto, PARECER))
    # O segundo só cita o programa na conclusão (corpo): fora do escopo, com aviso.
    assert programas_do_ato(segundo.texto, PARECER) == ()
    assert not em_escopo(PARECER, ())
    assert cita_programa_so_no_corpo(segundo.texto, PARECER) is True


def test_especie_sem_lista_de_programas_nao_filtra_por_escopo():
    sem_filtro = Especie("Nota", re.compile(r"NOTA\s+N[º°]\s*(?P<numero>\d+)\."), "{especie} {numero}.pdf")
    assert sem_filtro.programas is None
    assert em_escopo(sem_filtro, ())
    assert programas_do_ato("NOTA Nº 5.\nQualquer texto.", sem_filtro) == ()
    assert cita_programa_so_no_corpo("NOTA Nº 5.\nQualquer texto.", sem_filtro) is False


def test_o_decreto_define_todas_as_regras_da_especie():
    assert DECRETO.fecho and DECRETO.cargo_assinante and DECRETO.abertura_do_corpo
    assert DECRETO.fim_do_cabecalho and DECRETO.programas
    assert DECRETO.fecho.search("e 204º da Independência do Brasil.")
    assert DECRETO.cargo_assinante.match("Governadora do Estado")
    assert DECRETO.cargo_assinante.match("Governador do Estado")
    assert DECRETO.abertura_do_corpo.search("A GOVERNADORA DO ESTADO, no uso")
    assert DECRETO.abertura_do_corpo.search("O GOVERNADOR DO ESTADO, no uso")
    assert DECRETO.fim_do_cabecalho.search("DECRETA:") and DECRETO.fim_do_cabecalho.search("DECRETA :")