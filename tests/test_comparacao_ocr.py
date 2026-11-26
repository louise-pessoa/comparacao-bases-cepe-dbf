"""Comparação do decreto do caderno com o OCR da base: só as diferenças que o OCR não explica.

Os exemplos de ruído vêm da calibração de 19/11/2026 (decretos de 2010 e 2020 com a base em imagem)."""

from __future__ import annotations

from dbf_cepe.pipeline.comparacao_ocr import (
    CONFERIR,
    DUPLICATA,
    NAO_COMPARADO,
    comparar_com_ocr,
    decidir_em_periodo_ruim,
    normalizar,
)

CADERNO = (
    "DECRETO Nº 48.565, DE 23 DE JANEIRO DE 2020.\n"
    "Concede estímulo previsto na Lei nº 11.675, de 11 de outubro de 1999, que dispõe sobre o PRODEPE, "
    "à empresa COMERCIAL DE ALIMENTOS RIO GUAMA LTDA.\n"
    "O GOVERNADOR DO ESTADO, no uso das atribuições que lhe são conferidas pelo inciso IV do art. 37 da "
    "Constituição Estadual, CONSIDERANDO a Ata da 65ª Reunião do referido Comitê,\n"
    "DECRETA:\nArt. 1º Fica concedido o estímulo do PRODEPE pelo prazo de 12 anos."
)


def ocr(texto: str, confianca: float = 95.0, baixas: dict[str, float] | None = None) -> list[tuple[str, float]]:
    """Palavras do OCR com a confiança (as de `baixas` com a confiança dada)."""
    baixas = baixas or {}
    return [(p, baixas.get(p, confianca)) for p in texto.split()]


RUIDO = (
    "Recife, 23 de janeiro de 2020 Diário Oficial do Estado de Pernambuco - Poder Executivo\n"  # cabeçalho da página
    "DECRETO Nº 48 , 565 , DE 23 DE JANEIRO DE 2020 ,\n"
    "Concede estimulo previsto na Lei 6º 11675 , de 11 de outubro de 1395 , que dispõe sobre o PRODEFE , "
    "à empresa GOMERCIAL DE ALIMENTOS RIO GUAMA LTDA .\n"
    "O GOVERNADOR DO ESTADO , nó uso das atribuições que lhe são conferidas pelo inciso [ V do art . 37 da "
    "Constituição Estadual , CONSIDERANDO à Ata da 65º Reunião do referido Comitê ,\n"
    "DECRETA :\nArt . 1º Fica concedido o estímulo do PRODEPE pelo prazo de 12 anos ."
)


def test_mesmo_decreto_com_ruido_do_ocr_nao_tem_diferenca_de_verdade():
    resultado = comparar_com_ocr(CADERNO, ocr(RUIDO, baixas={"6º": 60, "1395": 70}))
    assert resultado.sem_diferenca_de_verdade, [str(d) for d in resultado.nao_explicadas]
    assert resultado.explicadas >= 5


def test_numero_trocado_com_confianca_alta_e_diferenca_de_verdade():
    resultado = comparar_com_ocr(CADERNO, ocr(RUIDO.replace("12 anos", "10 anos"), baixas={"6º": 60, "1395": 70}))
    assert [d.motivo for d in resultado.nao_explicadas] == ["número diferente (lido com confiança)"]
    assert resultado.nao_explicadas[0].caderno == "12" and resultado.nao_explicadas[0].base == "10"


def test_numero_mal_lido_com_confianca_alta_tambem_fica_para_conferir():
    """Na dúvida, conferir: "1999" lido como "1395" com confiança 95 não é explicado."""
    resultado = comparar_com_ocr(CADERNO, ocr(RUIDO, baixas={"6º": 60}))
    assert any(d.caderno == "1999" for d in resultado.nao_explicadas)


def test_outro_decreto_do_mesmo_modelo_tem_diferencas_de_verdade():
    outro = (RUIDO.replace("48 , 565", "48 , 566").replace("GOMERCIAL DE ALIMENTOS RIO GUAMA", "NUTRANE INDUSTRIAL")
             .replace("12 anos", "15 anos"))
    resultado = comparar_com_ocr(CADERNO, ocr(outro, baixas={"6º": 60, "1395": 70}))
    motivos = sorted(d.motivo for d in resultado.nao_explicadas)
    assert len(motivos) >= 3, motivos
    assert "palavra diferente" in motivos


def test_texto_do_caderno_que_falta_na_base():
    sem_artigo = RUIDO.replace("Art . 1º Fica concedido", "Fica concedido")
    resultado = comparar_com_ocr(CADERNO, ocr(sem_artigo, baixas={"6º": 60, "1395": 70}))
    assert [d.motivo for d in resultado.nao_explicadas] == ["falta na base"]
    letra_solta = comparar_com_ocr("A e B LTDA", ocr("A B LTDA"))
    assert letra_solta.sem_diferenca_de_verdade


def test_normalizar():
    assert normalizar("Estímulo") == "estimulo"
    assert normalizar("65ª") == normalizar("65°") == normalizar("65º")
    assert normalizar(",") == ""


def test_inciso_iii_lido_como_hi():
    assert comparar_com_ocr("nos termos do inciso III do art. 2º", ocr("nos termos do inciso HI do art . 2º")).sem_diferenca_de_verdade
    assert comparar_com_ocr("inciso III - produtos", ocr("inciso 111 - produtos")).sem_diferenca_de_verdade


def test_uma_letra_trocada_e_ruido_do_ocr():
    """Calibração de 23/11/2026: os casos que sobraram no mesmo decreto."""
    for caderno, base in [("NBM / SH 5205", "NEM / SH 5205"), ("a Lei", "2 Lei"), ("A GOVERNADORA", "4 GOVERNADORA"),
                          ("inciso V", "inciso W"), ("alínea c", "alínea 0"), ("s / n", "s / h"), ("sobre do", "sobre de")]:
        assert comparar_com_ocr(caderno, ocr(base)).sem_diferenca_de_verdade, (caderno, base)


def test_numero_com_um_algarismo_trocado_continua_diferenca():
    """O número nunca entra na regra de uma letra: "193" -> "199" com confiança é conferir."""
    resultado = comparar_com_ocr("Decreto nº 193", ocr("Decreto nº 199"))
    assert [d.motivo for d in resultado.nao_explicadas] == ["número diferente (lido com confiança)"]
    assert not comparar_com_ocr("ALFA LTDA", ocr("BETA LTDA")).sem_diferenca_de_verdade


def test_regra_do_periodo_ruim():
    """Digitalizações de 2018 a 2020: só o número lido com confiança manda para conferir; o
    ruído que o OCR não explica deixa como "não comparado" (como a base em imagem sem OCR)."""
    assert decidir_em_periodo_ruim(comparar_com_ocr(CADERNO, ocr(CADERNO))) == DUPLICATA
    trocado = CADERNO.replace("12 anos", "15 anos")
    assert decidir_em_periodo_ruim(comparar_com_ocr(CADERNO, ocr(trocado))) == CONFERIR
    assert decidir_em_periodo_ruim(comparar_com_ocr(CADERNO, ocr(CADERNO.replace("12 anos", "80 anos"),
                                                                 baixas={"80": 60.0}))) == NAO_COMPARADO
    palavra = comparar_com_ocr(CADERNO, ocr(CADERNO.replace("GUAMA", "XYZWQ")))
    assert decidir_em_periodo_ruim(palavra) == NAO_COMPARADO
