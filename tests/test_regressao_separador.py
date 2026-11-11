"""Ferramenta de regressão do separador: o relatório --detalhes, decreto a decreto."""

from __future__ import annotations

import regressao_separador as rs

CADERNO = [[], 3, []]  # avisos do caderno, páginas, páginas digitalizadas


def ato(numero="61563", texto="L1\nL2\nL3", avisos=(), pagina=3, cabecalho="DECRETO Nº 61.563"):
    return [numero, cabecalho, texto, pagina, pagina, list(avisos)]


def test_linhas_removidas():
    linhas, tipos = rs.detalhar([ato(texto="L1\nEXP 1\nEXP 2\nL2"), CADERNO], [ato(texto="L1\nL2"), CADERNO])
    assert tipos == ["só linhas removidas"]
    assert linhas == ["  Decreto 61563 (p. 3): 2 linha(s) saíram (EXP 1 | EXP 2)"]


def test_linhas_acrescentadas_sao_destacadas():
    _, tipos = rs.detalhar([ato(texto="L1\nL2"), CADERNO], [ato(texto="L1\nNOVA\nL2"), CADERNO])
    assert tipos == ["linhas acrescentadas"]


def test_so_avisos():
    linhas, tipos = rs.detalhar([ato(avisos=["sem a abertura"]), CADERNO], [ato(), CADERNO])
    assert tipos == ["só avisos"] and "aviso saiu: sem a abertura" in linhas[0]


def test_decreto_novo_ou_sumido_e_identicos_nao_aparecem():
    antes = [ato(), ato(numero="61564", pagina=4), CADERNO]
    depois = [ato(), ato(numero="61565", pagina=5), CADERNO]
    linhas, tipos = rs.detalhar(antes, depois)
    assert linhas == ["  Decreto 61564 (p. 4): sumiu", "  Decreto 61565 (p. 5): novo"]
    assert tipos == ["decreto novo ou sumido"] * 2


def test_avisos_do_caderno():
    _, tipos = rs.detalhar([ato(), [["aviso"], 3, []]], [ato(), CADERNO])
    assert tipos == ["avisos do caderno"]


def test_amostra_de_linhas_e_limitada():
    linhas, _ = rs.detalhar([ato(texto="A\nB\nC\nD\nF"), CADERNO], [ato(texto="F"), CADERNO])
    assert linhas[0].endswith("(A | B | C | …)")
