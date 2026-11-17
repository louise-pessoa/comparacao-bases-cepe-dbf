"""Ferramenta da fase 2 do OCR: calibração com páginas de texto (gabarito) e pares da base."""

from __future__ import annotations

from pathlib import Path

import pytest

import medir_ocr as mo
from conftest import criar_pdf_imagem, criar_pdf_texto
from dbf_cepe.leitura.ocr import PaginaOcr, Palavra


def _lida(*palavras: tuple[str, float]) -> PaginaOcr:
    return PaginaOcr(
        " ".join(t for t, _ in palavras),
        tuple(Palavra(t, c, (0, 0, 1, 1), (1, 1, 1)) for t, c in palavras),
        "5",
    )


def test_comparar_pagina_nao_depende_da_ordem_e_conta_numeros():
    gabarito = "Art. 1º Fica concedido\n o estímulo de 47,5%."
    lida = _lida(("estímulo", 96), ("de", 97), ("47,6%.", 60), ("Art.", 95), ("1º", 92), ("Fica", 99),
                 ("concedido", 98), ("o", 99))
    medida = mo.comparar_pagina(gabarito, lida, "p. 1")
    assert medida.elementos == len(mo.palavras(gabarito))
    assert medida.erros_por_faixa == {50: 1}  # só "47,6%." errou
    assert medida.palavras_por_faixa[95] == 6 and medida.palavras_por_faixa[90] == 1
    assert 0 < medida.acerto_numeros < 1  # "1º" certo, "47" e "5"/"6" não todos
    assert medida.por_pagina[0][0] == "p. 1"


def test_amostrar_espalha_e_e_sempre_igual():
    itens = list(range(100))
    assert mo.amostrar(itens, 4) == [12, 37, 62, 87] == mo.amostrar(itens, 4)
    assert mo.amostrar(itens[:3], 10) == [0, 1, 2]
    assert mo.amostrar(itens, 0) == []


def _gabarito(por_faixa, erros, paginas):
    g = mo.Gabarito()
    g.palavras_por_faixa.update(por_faixa)
    g.erros_por_faixa.update(erros)
    g.por_pagina = paginas
    return g


def test_sugestoes_de_confianca():
    g = _gabarito({95: 1000, 90: 100, 85: 50, 70: 20}, {95: 3, 90: 2, 85: 5, 70: 4},
                  [("a", 96.0, 0.995), ("b", 94.5, 0.99), ("c", 94.2, 0.95), ("d", 88.0, 0.97)])
    assert mo.sugerir_confianca_critica(g) == 90  # 85 erra 10%
    assert mo.sugerir_confianca_media(g) == 95.0  # logo acima da página ruim de maior confiança (94,2)
    so_boas = _gabarito({}, {}, [("a", 96.0, 0.99), ("b", 91.0, 0.985)])
    assert mo.sugerir_confianca_media(so_boas) == 91.0
    assert mo.sugerir_confianca_media(mo.Gabarito()) is None


# ----------------------------------------------------------------------------
# Pares da base
# ----------------------------------------------------------------------------

EMENTA = "Concede estímulo previsto na Lei nº 11.675, de 11 de outubro de 1999, que dispõe sobre o PRODEPE, à empresa {e}."
ABERTURA = "A GOVERNADORA DO ESTADO, no uso das atribuições que lhe são conferidas pelo inciso IV do art. 37,"
FECHO = (
    "Palácio do Campo das Princesas, Recife, 25 de junho do ano de 2026, 210º da Revolução Republicana "
    "Constitucionalista e 204º da Independência do Brasil."
)


def decreto(numero: str, empresa: str) -> str:
    return (
        f"DECRETO Nº {numero}, DE 25 DE JUNHO DE 2026.\n{EMENTA.format(e=empresa)}\n{ABERTURA}\nDECRETA:\n"
        f"Art. 1º Fica concedido à empresa {empresa} o estímulo do PRODEPE por doze anos.\n"
        "Art. 2º Este Decreto entra em vigor na data de sua publicação.\n"
        f"{FECHO}\nRAQUEL TEIXEIRA LYRA LUCENA\nGovernadora do Estado"
    )


class LeitorFalso:
    """OCR simulado da base: devolve um texto por nome de arquivo."""

    def __init__(self, textos: dict[str, str]):
        self.textos = textos
        self.lidos: list[str] = []

    def ler(self, pdf: Path, numero: int, ajustes=None) -> PaginaOcr:
        self.lidos.append(pdf.name)
        return _lida(*((p, 90.0) for p in self.textos[pdf.name].split()))


def test_pares_da_base_so_com_arquivo_em_imagem_e_controle(tmp_path):
    cadernos = tmp_path / "cad"
    cadernos.mkdir()
    criar_pdf_texto(
        cadernos / "20260626_PoderExecutivo.pdf",
        decreto("60.929", "ALFA LTDA") + "\n" + decreto("60.930", "BETA S.A.") + "\n" + decreto("60.931", "GAMA ME"),
    )
    base = tmp_path / "base"
    base.mkdir()
    criar_pdf_imagem(base / "Decreto 60929.pdf")
    criar_pdf_imagem(base / "Decreto 60930.pdf")
    criar_pdf_texto(base / "Decreto 60931.pdf", decreto("60.931", "GAMA ME"))  # tem texto: fora da calibração
    leitor = LeitorFalso({
        "Decreto 60929.pdf": decreto("60.929", "ALFA LTDA"),
        "Decreto 60930.pdf": decreto("60.930", "BETA S.A.").replace("doze", "dez"),
    })
    falhas: list[str] = []
    pares = mo.pares_da_base([cadernos], base, leitor, None, falhas)
    assert falhas == []
    assert [p.arquivo_base for p in pares] == ["Decreto 60929.pdf", "Decreto 60930.pdf"]
    assert pares[0].similaridade == pytest.approx(1.0)
    assert 0.9 < pares[1].similaridade < 1.0 and any("doze" in t for t in pares[1].trechos)
    assert "Decreto 60931.pdf" not in leitor.lidos
    diferentes = mo.controles(pares)
    assert len(diferentes["similaridade"]) == 2 and max(diferentes["similaridade"]) < pares[1].similaridade
    assert max(diferentes["cobertura"]) < pares[1].cobertura
    limiar = mo.sugerir_limiar(diferentes["cobertura"])
    assert limiar == round(min(max(diferentes["cobertura"]) + mo.MARGEM_DO_LIMIAR, 0.99), 2)
    tabela = mo.tabela_base(pares, diferentes)
    assert "mesmo número (2 pares)" in tabela and "COBERTURA" in tabela and "limiar sugerido" in tabela
    assert mo.pares_da_base([cadernos], base, leitor, 1, []) == pares[:1]


def test_tabela_da_base_sem_pares():
    assert "nenhum decreto da DBF" in mo.tabela_base([], {})


def test_cobertura_ignora_o_que_sobra_na_base():
    """Decretos 34732 e 34746 (2010): a base é um recorte da página do Diário, com o cabeçalho
    da página e o fim do decreto vizinho."""
    ato = decreto("34.746", "ALFA LTDA")
    base = ("Recife, 19 de março de 2010 Diário Oficial do Estado de Pernambuco - Poder Executivo\n"
            "Art. 3º Este Decreto entra em vigor na data de sua publicação.\n" + ato)
    similaridade, cobertura, trechos = mo.medir_par(ato, base)
    assert cobertura == pytest.approx(1.0) and similaridade < 0.9
    assert trechos == ()  # nada do caderno falta na base
    _, cobertura_com_erro, faltam = mo.medir_par(ato, base.replace("doze", "dez"))
    assert cobertura_com_erro < 1.0 and any("doze" in t for t in faltam)


def test_main_pasta_inexistente_e_dpi_invalido(tmp_path):
    assert mo.main([str(tmp_path / "nao")]) == 2
    with pytest.raises(SystemExit):
        mo.main([str(tmp_path), "--dpi", "1200"])
