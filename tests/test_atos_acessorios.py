"""Testes de atos_acessorios.py (errata e republicação) e da fonte única das RegEx."""

from __future__ import annotations

import atos_acessorios as aa
import localizar_exemplos as lx
import regras_atos as ra
from especies import DECRETO, EDITAL, PORTARIA


def test_localizar_exemplos_usa_as_mesmas_regex_de_regras_atos():
    # Uma fonte só: o levantamento e o reconhecimento não podem divergir.
    assert lx._ERRATA is ra.ERRATA_LINHA is aa.ERRATA_LINHA
    assert lx._REPUBLICACAO is ra.REPUBLICACAO_MARCADOR is aa.REPUBLICACAO_MARCADOR
    assert lx._RETIFICACAO is ra.RETIFICACAO_LINHA is aa.RETIFICACAO_LINHA


def test_errata_destino_vira_a_especie_do_pipeline():
    texto = (
        "ERRATA No art. 4º do Decreto nº 61.560, de 19 de outubro de 2026\n"
        "ERRATA Na Portaria SJDH nº 126, publicada em 30 de setembro de 2026\n"
        "Errata ao Edital do Chamamento Público nº 010/2026\n"
        "ERRATA DO DESPACHO SECOGE DE 18/06/2026\n"
    )
    erratas = aa.achar_erratas(texto)
    assert [e.destino for e in erratas] == ["decreto", "portaria", "edital", "outro"]
    assert [e.especie_destino for e in erratas] == [DECRETO, PORTARIA, EDITAL, None]


def test_errata_traz_numero_e_ano_do_ato_referido():
    (errata,) = aa.achar_erratas("ERRATA No art. 4º do Decreto nº 61.560, de 19 de outubro de 2026\n")
    assert (errata.referido.especie, errata.referido.numero, errata.referido.ano) == ("Decreto", "61560", "2026")
    assert errata.referido.rotulo == "Decreto 61.560"


def test_errata_so_no_inicio_da_linha_e_com_posicao_correta():
    texto = "Texto sobre a ERRATA de ontem\n  ERRATA Na Portaria nº 5\n"
    (errata,) = aa.achar_erratas(texto)
    assert texto[errata.posicao :].startswith("ERRATA Na Portaria")


def test_ato_referido_devolve_o_rotulo_usado_pelo_levantamento():
    assert aa.ato_referido("ERRATA Na Portaria SDS nº 2508, de 15/04/2026") == "Portaria 2508"
    assert aa.ato_referido("ERRATA A PUBLICAÇÃO DO TERMO") == "?"


def test_marcadores_de_republicacao_com_contexto():
    texto = "Art. 3º Este Decreto entra em vigor.\n(REPUBLICADO POR HAVER SAÍDO COM INCORREÇÃO NO ORIGINAL)\nANEXO I\n"
    (marcador,) = aa.achar_marcadores_de_republicacao(texto)
    assert marcador.marcador.upper().startswith("REPUBLICADO POR HAVER")
    assert "ANEXO I" in marcador.contexto and "\n" not in marcador.contexto


def test_citacao_de_decreto_republicado_nao_e_marcador():
    assert aa.achar_marcadores_de_republicacao("o Decreto nº 56.378, republicado no DOE de 06/06/2024") == []
