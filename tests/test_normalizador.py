"""Etapa 4: normalização do ato em um PDF limpo (fpdf2).

Três frentes:
  - a ESTRUTURA recuperada do texto (parágrafos, ementa, citação, assinaturas)
    tem de coincidir com a que o Word registrou nos seis decretos-modelo da DBF
    (as linhas em branco do PDF original);
  - o LAYOUT do PDF gerado (fonte, justificado, centralizados, recuos) tem de
    coincidir com o dos modelos, medido na página;
  - o TEXTO do ato não pode mudar: só espaço em branco.
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf
import pytest

import normalizador
from conftest import AMOSTRAS, TIPO_ESPERADO_DECRETO, criar_pdf_imagem, criar_pdf_texto, texto_decreto
from especies import DECRETO, Especie
from exportador import gravar_pdf_do_ato
from identificador import StatusIdentificacao, identificar, normalizar_espacos
from normalizador import Bloco, ErroNormalizacao, Estilo, estruturar, gerar_pdf_normalizado
from reader import ler_pdf
from separador import separar_caderno

PASTA_CADERNOS = Path(__file__).parent / "amostras_cadernos"
NUMEROS = sorted(TIPO_ESPERADO_DECRETO)


def _amostra(numero: str) -> Path:
    return AMOSTRAS / f"Decreto {numero}.pdf"


def _gerar(tmp_path: Path, numero: str) -> Path:
    amostra = _amostra(numero)
    ident = identificar(ler_pdf(amostra).texto)
    return gerar_pdf_normalizado(amostra, ident, tmp_path)


# ----------------------------------------------------------------------------
# Estrutura: igual à do Word nos seis modelos
# ----------------------------------------------------------------------------


def _paragrafos_do_word(caminho: Path) -> list[str]:
    """Parágrafos do modelo, separados pelas linhas em branco que o Word gravou.
    Uma quebra de página também aparece como linha em branco; por isso o
    chamador junta os pedaços de um mesmo parágrafo."""
    paragrafos: list[list[str]] = []
    atual: list[str] = []
    for pagina in pymupdf.open(caminho):
        for linha in pagina.get_text().split("\n"):
            if linha.strip():
                atual.append(" ".join(linha.split()))
            elif atual:
                paragrafos.append(atual)
                atual = []
    if atual:
        paragrafos.append(atual)
    return [" ".join(p) for p in paragrafos]


def _blocos_como_o_word(blocos: tuple[Bloco, ...]) -> list[str]:
    """No Word, as assinaturas seguidas formam um parágrafo só (sem linha em
    branco entre elas); aqui cada linha é um bloco centralizado."""
    saida: list[str] = []
    for bloco in blocos:
        texto = normalizar_espacos(bloco.texto)
        if bloco.estilo is Estilo.ASSINATURA and bloco.colado and saida:
            saida[-1] += " " + texto
        else:
            saida.append(texto)
    return saida


def _casar(meus: list[str], word: list[str]) -> None:
    """Cada bloco meu é igual a um ou mais parágrafos consecutivos do Word
    (vários, quando uma quebra de página partiu o parágrafo)."""
    posicao = 0
    for numero, bloco in enumerate(meus):
        acumulado = ""
        while normalizar_espacos(acumulado) != bloco:
            assert posicao < len(word), f"bloco {numero} sem correspondência: {bloco[:80]!r}"
            acumulado += " " + word[posicao]
            posicao += 1
            assert bloco.startswith(normalizar_espacos(acumulado)), (
                f"bloco {numero} diverge do Word:\n  meu : {bloco[:100]!r}\n  word: {acumulado.strip()[:100]!r}"
            )
    assert posicao == len(word), f"sobraram parágrafos do Word: {word[posicao:][:2]}"


@pytest.mark.parametrize("numero", NUMEROS)
def test_estrutura_igual_a_do_word_nos_modelos(numero):
    texto = ler_pdf(_amostra(numero)).texto
    documento = estruturar(texto, DECRETO)
    assert documento.avisos == ()
    _casar(_blocos_como_o_word(documento.blocos), _paragrafos_do_word(_amostra(numero)))


@pytest.mark.parametrize("numero", NUMEROS)
def test_estilos_dos_blocos_nos_modelos(numero):
    blocos = estruturar(ler_pdf(_amostra(numero)).texto, DECRETO).blocos
    estilos = [b.estilo for b in blocos]
    assert estilos[0] is Estilo.TITULO and blocos[0].texto.startswith("DECRETO Nº ")
    assert blocos[0].texto.endswith("2026.")
    assert estilos[1] is Estilo.EMENTA
    assert blocos[2].texto.startswith("A GOVERNADORA DO ESTADO")
    assert any(b.texto == "DECRETA:" for b in blocos)
    # Depois do fecho, só assinaturas; a linha em branco vem depois do cargo.
    fecho = next(i for i, b in enumerate(blocos) if b.texto.startswith("Palácio do Campo"))
    assert blocos[fecho].estilo is Estilo.DISPOSITIVO
    assinaturas = blocos[fecho + 1 :]
    assert assinaturas and all(b.estilo is Estilo.ASSINATURA for b in assinaturas)
    assert assinaturas[1].texto == "Governadora do Estado"
    assert [b.colado for b in assinaturas] == [False, True, False, True, True]


@pytest.mark.parametrize("numero", ["60505", "60945", "60950", "60951"])
def test_trecho_citado_dos_decretos_de_alteracao(numero):
    blocos = estruturar(ler_pdf(_amostra(numero)).texto, DECRETO).blocos
    citados = [b for b in blocos if b.estilo is Estilo.CITACAO]
    assert citados and citados[0].texto.startswith("“Art. 1º")
    assert citados[-1].texto.endswith("”")
    # A citação fecha antes do artigo seguinte do decreto.
    seguinte = blocos[blocos.index(citados[-1]) + 1]
    assert seguinte.estilo is Estilo.DISPOSITIVO and seguinte.texto.startswith("Art. ")


# ----------------------------------------------------------------------------
# Layout do PDF gerado
# ----------------------------------------------------------------------------

_CENTRO_DA_AREA = (normalizador._MARGEM_ESQUERDA + 595.32 - normalizador._MARGEM_DIREITA) / 2
_NEGRITO, _ITALICO = 16, 2


def _linhas(caminho: Path) -> list[tuple[float, float, str]]:
    saida = []
    for pagina in pymupdf.open(caminho):
        for bloco in pagina.get_text("dict")["blocks"]:
            for linha in bloco.get("lines", []):
                texto = "".join(s["text"] for s in linha["spans"]).strip()
                if texto:
                    saida.append((linha["bbox"][0], linha["bbox"][2], texto))
    return saida


@pytest.mark.parametrize("numero", NUMEROS)
def test_fonte_sem_negrito_italico_ou_link(numero, tmp_path):
    pdf = pymupdf.open(_gerar(tmp_path, numero))
    nomes, tamanhos = set(), set()
    for pagina in pdf:
        assert pagina.get_links() == []
        assert list(pagina.annots()) == []
        for bloco in pagina.get_text("dict")["blocks"]:
            for linha in bloco.get("lines", []):
                for span in linha["spans"]:
                    assert not span["flags"] & (_NEGRITO | _ITALICO), span["text"]
                    nomes.add(span["font"])
                    if not re.fullmatch(r"[.”\s]+", span["text"]):  # pontilhado pode encolher
                        tamanhos.add(round(span["size"], 1))
    assert all("LiberationSerif" in nome.replace(" ", "") for nome in nomes), nomes
    assert tamanhos == {12.0}


@pytest.mark.parametrize("numero", NUMEROS)
def test_titulo_e_assinaturas_centralizados(numero, tmp_path):
    linhas = _linhas(_gerar(tmp_path, numero))
    titulo = next(l for l in linhas if l[2].startswith("DECRETO Nº"))
    fecho = next(i for i, l in enumerate(linhas) if l[2].endswith("Independência do Brasil."))
    assinaturas = linhas[fecho + 1 :]
    assert len(assinaturas) == 5
    for x0, x1, texto in [titulo, *assinaturas]:
        assert (x0 + x1) / 2 == pytest.approx(_CENTRO_DA_AREA, abs=3), texto


@pytest.mark.parametrize("numero", NUMEROS)
def test_recuos_iguais_aos_dos_modelos(numero, tmp_path):
    """Os x iniciais usados (85 = margem; 113 = recuo da 1ª linha; 128 = citação;
    ~284 = ementa) são os mesmos do decreto-modelo."""
    def inicios(caminho: Path) -> set[int]:
        return {round(x0 / 5) * 5 for x0, x1, texto in _linhas(caminho) if x0 < 160 or x0 > 270}

    gerado = {x for x in inicios(_gerar(tmp_path, numero)) if x in (85, 115, 130, 285)}
    modelo = {x for x in inicios(_amostra(numero)) if x in (85, 115, 130, 285)}
    assert gerado == modelo
    assert {85, 115, 285} <= gerado


@pytest.mark.parametrize("numero", NUMEROS)
def test_texto_justificado(numero, tmp_path):
    """Linhas de parágrafo que não são a última chegam à margem direita."""
    linhas = _linhas(_gerar(tmp_path, numero))
    margem_direita = 595.32 - normalizador._MARGEM_DIREITA
    corridas = [l for l in linhas if l[0] < 120 and len(l[2]) > 60]
    cheias = [l for l in corridas if l[1] >= margem_direita - 2]
    assert len(corridas) > 10 and len(cheias) / len(corridas) > 0.7


@pytest.mark.parametrize("numero", NUMEROS)
def test_paginas_e_linhas_proximas_do_modelo(numero, tmp_path):
    gerado = _gerar(tmp_path, numero)
    assert len(pymupdf.open(gerado)) == len(pymupdf.open(_amostra(numero)))
    assert abs(len(_linhas(gerado)) - len(_linhas(_amostra(numero)))) <= 4


@pytest.mark.parametrize("numero", NUMEROS)
def test_texto_do_modelo_preservado(numero, tmp_path):
    gerado = ler_pdf(_gerar(tmp_path, numero)).texto
    original = ler_pdf(_amostra(numero)).texto
    assert normalizar_espacos(re.sub(r"(?<=\w)-\s+(?=[a-zà-ÿ])", "-", gerado)) == normalizar_espacos(
        re.sub(r"(?<=\w)-\s+(?=[a-zà-ÿ])", "-", original)
    )
    ident = identificar(gerado)
    assert ident.status is StatusIdentificacao.IDENTIFICADO
    assert (ident.numero, ident.tipo.nome) == (numero, TIPO_ESPERADO_DECRETO[numero])


def test_linha_de_pontos_do_trecho_citado_cabe_inteira(tmp_path):
    """No 60505 a linha tem 122 pontos, de margem a margem: não pode quebrar."""
    pontilhadas = [t for _, _, t in _linhas(_gerar(tmp_path, "60505")) if t.startswith("......")]
    assert pontilhadas
    original = [t for _, _, t in _linhas(_amostra("60505")) if t.startswith("......")]
    assert [len(t) for t in pontilhadas] == [len(t) for t in original]


def test_linha_de_pontos_mais_larga_que_a_pagina_encolhe_em_vez_de_quebrar(tmp_path):
    """Um decreto com 150 pontos na citação (mais que cabe a 12 pt) não pode
    gerar uma linha de pontos partida em duas: só o corpo dessa linha encolhe."""
    pontos = "." * 150
    corpo = (
        "Art. 2º Passa a vigorar com a seguinte alteração:\n"
        f"“Art. 1º {pontos}\n{pontos}\n"
        "IV - prazo: (NR)\n"
        f"{pontos}”\n"
        "Art. 3º Este Decreto entra em vigor na data de sua publicação."
    )
    texto = _decreto(corpo)
    destino = tmp_path / "saida" / "Decreto 60001.pdf"
    normalizador._gerar(texto, identificar(texto), destino, normalizador.PASTA_FONTES)
    pontilhadas = [
        (span["size"], span["text"])
        for pagina in pymupdf.open(destino)
        for bloco in pagina.get_text("dict")["blocks"]
        for linha in bloco.get("lines", [])
        for span in linha["spans"]
        if "......" in span["text"]
    ]
    assert pontilhadas and all(tamanho < 12 for tamanho, _ in pontilhadas)
    assert all(trecho.count(".") >= 150 for _, trecho in pontilhadas)


# ----------------------------------------------------------------------------
# Do caderno ao PDF normalizado (caminho real do pipeline)
# ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "caderno", sorted(PASTA_CADERNOS.glob("*.pdf")), ids=lambda c: c.name
)
def test_todos_os_atos_do_caderno_normalizam(caderno, tmp_path):
    atos = separar_caderno(caderno).atos
    assert atos
    for ato in atos:
        pendente = gravar_pdf_do_ato(ato, tmp_path / "pendentes" / f"Decreto {ato.numero}.pdf")
        ident = identificar(ler_pdf(pendente).texto)
        gerado = gerar_pdf_normalizado(pendente, ident, tmp_path / "normalizados")
        assert gerado.name == f"Decreto {ato.numero}.pdf" and gerado.is_file()
        assert normalizar_espacos(ler_pdf(gerado).texto).startswith(normalizar_espacos(ato.cabecalho))
    assert sorted(p.name for p in (tmp_path / "normalizados").iterdir()) == sorted(
        f"Decreto {a.numero}.pdf" for a in atos
    )


# ----------------------------------------------------------------------------
# Regras de estrutura (texto sintético)
# ----------------------------------------------------------------------------


def _decreto(corpo: str, ementa: str = "Concede estímulo previsto na Lei nº 11.675.") -> str:
    return (
        "DECRETO Nº 60.001, DE 25 DE JUNHO DE 2026.\n"
        f"{ementa}\n"
        "A GOVERNADORA DO ESTADO, no uso das atribuições,\n"
        "DECRETA:\n"
        f"{corpo}\n"
        "Palácio do Campo das Princesas, Recife, 25 de junho do ano de 2026, 210º da\n"
        "Revolução Republicana Constitucionalista e 204º da Independência do Brasil.\n"
        "RAQUEL TEIXEIRA LYRA LUCENA\nGovernadora do Estado\nBIANCA FERREIRA TEIXEIRA"
    )


def _textos(documento, estilo: Estilo) -> list[str]:
    return [b.texto for b in documento.blocos if b.estilo is estilo]


def test_linhas_do_diario_viram_paragrafos():
    corpo = (
        "Art. 1º Fica concedido o estímulo à empresa X, com CNPJ\n"
        "nº 11.111.111/0001-11, observadas as condições:\n"
        "I - natureza do projeto: implantação;\n"
        "II - prazo: até 31 de dezembro de 2032; e\n"
        "III - benefício: crédito presumido.\n"
        "Parágrafo único. O disposto neste artigo vale\n"
        "para o contribuinte.\n"
        "Art. 2º Este Decreto entra em vigor na data de sua publicação."
    )
    doc = estruturar(_decreto(corpo), DECRETO)
    assert doc.avisos == ()
    assert _textos(doc, Estilo.DISPOSITIVO)[:7] == [
        "Art. 1º Fica concedido o estímulo à empresa X, com CNPJ nº 11.111.111/0001-11, observadas as condições:",
        "I - natureza do projeto: implantação;",
        "II - prazo: até 31 de dezembro de 2032; e",
        "III - benefício: crédito presumido.",
        "Parágrafo único. O disposto neste artigo vale para o contribuinte.",
        "Art. 2º Este Decreto entra em vigor na data de sua publicação.",
        "Palácio do Campo das Princesas, Recife, 25 de junho do ano de 2026, 210º da "
        "Revolução Republicana Constitucionalista e 204º da Independência do Brasil.",
    ]


def test_item_so_abre_paragrafo_se_a_linha_anterior_terminou_a_frase():
    """"Art. 5º" no começo de uma linha quebrada no meio da frase é citação."""
    corpo = "Art. 1º O estímulo concedido na forma do\nArt. 5º do Decreto nº 21.959, de 1999, vale até 2032."
    doc = estruturar(_decreto(corpo), DECRETO)
    assert _textos(doc, Estilo.DISPOSITIVO)[0] == (
        "Art. 1º O estímulo concedido na forma do Art. 5º do Decreto nº 21.959, de 1999, vale até 2032."
    )


def test_item_depois_de_ponto_e_virgula_e_e_ou_abre_paragrafo():
    corpo = "Art. 1º Fica concedido:\nI - um; e\nII - dois; ou\nIII - três."
    doc = estruturar(_decreto(corpo), DECRETO)
    assert _textos(doc, Estilo.DISPOSITIVO)[:4] == [
        "Art. 1º Fica concedido:", "I - um; e", "II - dois; ou", "III - três.",
    ]


def test_hifen_de_quebra_de_linha_e_junto_sem_espaco():
    corpo = "Art. 1º Aprova o sistema construtivo pré-\nfabricado e o item A -\nNCM 1234."
    doc = estruturar(_decreto(corpo), DECRETO)
    assert _textos(doc, Estilo.DISPOSITIVO)[0] == (
        "Art. 1º Aprova o sistema construtivo pré-fabricado e o item A - NCM 1234."
    )


def test_ligadura_do_diario_e_limpa():
    doc = estruturar(_decreto("Art. 1º Os benefícios ficam em vigor, com beneﬁ ciário e ﬁ scal."), DECRETO)
    assert _textos(doc, Estilo.DISPOSITIVO)[0] == "Art. 1º Os benefícios ficam em vigor, com beneficiário e fiscal."


def test_citacao_com_pontilhado_em_linha_propria():
    corpo = (
        "Art. 2º O Decreto nº 49.853, de 2020, passa a vigorar com a seguinte alteração:\n"
        "“Art. 1º ...............................\n"
        "..............................\n"
        "IV - prazos de fruição: (NR)\n"
        "a) de 1º de dezembro de 2020 a 30 de novembro de 2027; e (AC)\n"
        "b) de 1º de dezembro de 2027 a 31 de dezembro de 2032. (AC)\n"
        "..............................”\n"
        "Art. 3º Este Decreto entra em vigor na data de sua publicação."
    )
    doc = estruturar(_decreto(corpo), DECRETO)
    assert doc.avisos == ()
    citacao = _textos(doc, Estilo.CITACAO)
    assert len(citacao) == 4
    assert citacao[0].startswith("“Art. 1º ....") and "\n" in citacao[0]
    assert citacao[3].endswith("(AC)\n..............................”")
    assert _textos(doc, Estilo.DISPOSITIVO)[1].startswith("Art. 3º")


def test_citacao_sem_aspas_de_fechamento_avisa():
    corpo = "Art. 2º Passa a vigorar com a seguinte redação:\n“Art. 1º Texto citado;\nII - outro item."
    doc = estruturar(_decreto(corpo), DECRETO)
    assert any("sem aspas de fechamento" in a for a in doc.avisos)


def test_citacao_fecha_mesmo_com_marca_de_alteracao_depois_das_aspas():
    for fim in ("Texto novo.” (NR)", "Texto novo.”;", "Texto novo.”."):
        corpo = f"Art. 1º Passa a vigorar assim:\n“Art. 9º {fim}\nArt. 2º Vale desde já."
        doc = estruturar(_decreto(corpo), DECRETO)
        assert doc.avisos == (), fim
        assert "Art. 2º Vale desde já." in _textos(doc, Estilo.DISPOSITIVO), fim


def test_citacao_de_uma_linha_so_nao_contamina_o_resto():
    corpo = "Art. 1º Fica revogado:\n“Art. 9º Texto antigo.”\nArt. 2º Vale desde já."
    doc = estruturar(_decreto(corpo), DECRETO)
    assert doc.avisos == ()
    assert _textos(doc, Estilo.CITACAO) == ["“Art. 9º Texto antigo.”"]
    assert "Art. 2º Vale desde já." in _textos(doc, Estilo.DISPOSITIVO)


def test_assinaturas_e_linha_em_branco_depois_do_cargo():
    doc = estruturar(_decreto("Art. 1º Fica concedido."), DECRETO)
    assinaturas = [b for b in doc.blocos if b.estilo is Estilo.ASSINATURA]
    assert [(b.texto, b.colado) for b in assinaturas] == [
        ("RAQUEL TEIXEIRA LYRA LUCENA", False),
        ("Governadora do Estado", True),
        ("BIANCA FERREIRA TEIXEIRA", False),
    ]


def test_anexo_fica_linha_a_linha_e_avisa():
    texto = _decreto("Art. 1º Fica concedido.") + "\nANEXO ÚNICO\nMEMORIAL DESCRITIVO\nÁrea: 1.424,72 m²\nPerímetro: 200 m"
    doc = estruturar(texto, DECRETO)
    assert [b.texto for b in doc.blocos if b.estilo is Estilo.ANEXO_TITULO] == ["ANEXO ÚNICO"]
    assert _textos(doc, Estilo.ANEXO_LINHA) == ["MEMORIAL DESCRITIVO", "Área: 1.424,72 m²", "Perímetro: 200 m"]
    assert any("anexo" in a for a in doc.avisos)


def test_ementa_em_um_so_bloco_e_titulo_ate_o_primeiro_ponto():
    texto = _decreto("Art. 1º Fica concedido.", ementa="Concede estímulo previsto na Lei nº 11.675,\nde 11 de outubro de 1999, à empresa X.")
    doc = estruturar(texto, DECRETO)
    assert doc.blocos[0] == Bloco(Estilo.TITULO, "DECRETO Nº 60.001, DE 25 DE JUNHO DE 2026.")
    assert doc.blocos[1] == Bloco(
        Estilo.EMENTA, "Concede estímulo previsto na Lei nº 11.675, de 11 de outubro de 1999, à empresa X."
    )


def test_titulo_quebrado_em_duas_linhas():
    texto = _decreto("Art. 1º Fica concedido.").replace("DE 25 DE JUNHO DE 2026.", "DE 25 DE JUNHO\nDE 2026.", 1)
    assert estruturar(texto, DECRETO).blocos[0].texto == "DECRETO Nº 60.001, DE 25 DE JUNHO DE 2026."


def test_sem_abertura_do_corpo_nem_fecho_avisa_mas_nao_perde_texto():
    texto = "DECRETO Nº 60.002, DE 25 DE JUNHO DE 2026.\nDispõe sobre algo.\nArt. 1º Fica criado o item."
    doc = estruturar(texto, DECRETO)
    assert any("abertura do corpo" in a for a in doc.avisos)
    assert any("fecho" in a for a in doc.avisos)
    assert normalizar_espacos(" ".join(b.texto for b in doc.blocos)) == normalizar_espacos(texto)


def test_texto_antes_do_cabecalho_e_mantido_com_aviso():
    doc = estruturar("Lixo de página.\n" + _decreto("Art. 1º Fica concedido."), DECRETO)
    assert any("antes do cabeçalho" in a for a in doc.avisos)
    assert doc.blocos[0].texto == "Lixo de página."


def test_sem_cabecalho_e_erro():
    with pytest.raises(ErroNormalizacao, match="cabeçalho"):
        estruturar("Texto sem cabeçalho de ato.", DECRETO)


def test_nenhuma_estrutura_perde_ou_muda_palavras():
    """Para qualquer decreto bem formado, a concatenação dos blocos tem as mesmas
    palavras do texto de entrada (só o espaço em branco muda)."""
    texto = _decreto("Art. 1º Fica concedido:\nI - um;\nII - dois.\nParágrafo único. Vale.")
    doc = estruturar(texto, DECRETO)
    assert normalizar_espacos(" ".join(b.texto for b in doc.blocos)) == normalizar_espacos(texto)


# ----------------------------------------------------------------------------
# Espécie sem regras de estrutura: o normalizador não depende de "decreto"
# ----------------------------------------------------------------------------


def test_especie_sem_regras_de_estrutura_gera_texto_corrido(tmp_path):
    parecer = Especie(
        nome="Parecer",
        cabecalho=re.compile(r"PARECER\s+N[º°]\s*(?P<numero>\d+)/\d{4}\."),
        padrao_arquivo="{especie}-{numero}.pdf",
        inicios_com_recuo=re.compile(r"Item\s+\d"),
    )
    texto = "PARECER Nº 12/2026.\nOpina sobre o caso.\nItem 1 Primeiro ponto;\nItem 2 Segundo\nponto."
    doc = estruturar(texto, parecer)
    assert [b.estilo for b in doc.blocos] == [Estilo.TITULO, Estilo.CORPO, Estilo.DISPOSITIVO, Estilo.DISPOSITIVO]
    assert doc.blocos[1].texto == "Opina sobre o caso."
    assert doc.blocos[3].texto == "Item 2 Segundo ponto."
    assert len(doc.avisos) == 2  # sem abertura do corpo e sem fecho


# ----------------------------------------------------------------------------
# gerar_pdf_normalizado: erros, simulação e sobras
# ----------------------------------------------------------------------------


def _pendente(tmp_path: Path, texto: str | None = None) -> tuple[Path, object]:
    pdf = criar_pdf_texto(tmp_path / "pend.pdf", texto or texto_decreto("60.929", "Concede estímulo."))
    return pdf, identificar(ler_pdf(pdf).texto)


def test_gera_com_o_nome_da_especie(tmp_path):
    pdf, ident = _pendente(tmp_path)
    gerado = gerar_pdf_normalizado(pdf, ident, tmp_path / "saida")
    assert gerado == tmp_path / "saida" / "Decreto 60929.pdf" and gerado.is_file()
    assert [p.name for p in (tmp_path / "saida").iterdir()] == ["Decreto 60929.pdf"]


def test_simulacao_confere_mas_nao_grava_nada(tmp_path):
    pdf, ident = _pendente(tmp_path)
    caminho = gerar_pdf_normalizado(pdf, ident, tmp_path / "saida", simular=True)
    assert caminho == tmp_path / "saida" / "Decreto 60929.pdf"
    assert not (tmp_path / "saida").exists()


def test_simulacao_tambem_detecta_falha(tmp_path):
    pdf, ident = _pendente(tmp_path)
    with pytest.raises(ErroNormalizacao, match="fonte não encontrada"):
        gerar_pdf_normalizado(pdf, ident, tmp_path / "saida", simular=True, pasta_fontes=tmp_path / "sem_fontes")
    assert not (tmp_path / "saida").exists()


def test_sobra_de_execucao_anterior_e_substituida(tmp_path):
    pdf, ident = _pendente(tmp_path)
    saida = tmp_path / "saida"
    saida.mkdir()
    (saida / "Decreto 60929.pdf").write_bytes(b"sobra")
    gerado = gerar_pdf_normalizado(pdf, ident, saida)
    assert ler_pdf(gerado).texto.startswith("DECRETO Nº 60.929")
    assert [p.name for p in saida.iterdir()] == ["Decreto 60929.pdf"]


def test_caractere_sem_glifo_na_fonte_e_erro_e_nao_deixa_arquivo(tmp_path):
    texto = _decreto("Art. 1º Fica concedido 中文.")  # a Liberation Serif não tem ideogramas
    destino = tmp_path / "saida" / "Decreto 60001.pdf"
    with pytest.raises(ErroNormalizacao, match="difere"):
        normalizador._gerar(texto, identificar(texto), destino, normalizador.PASTA_FONTES)
    assert list((tmp_path / "saida").iterdir()) == []


def test_pdf_de_imagem_nao_e_normalizado(tmp_path):
    imagem = criar_pdf_imagem(tmp_path / "img.pdf")
    _, ident = _pendente(tmp_path)
    with pytest.raises(ErroNormalizacao, match="imagem"):
        gerar_pdf_normalizado(imagem, ident, tmp_path / "saida")


def test_ato_nao_identificado_nao_e_normalizado(tmp_path):
    pdf = criar_pdf_texto(tmp_path / "x.pdf", "Texto qualquer sem cabeçalho de ato.")
    with pytest.raises(ErroNormalizacao, match="não identificado"):
        gerar_pdf_normalizado(pdf, identificar(ler_pdf(pdf).texto), tmp_path / "saida")


def test_fonte_ausente_da_mensagem_clara(tmp_path):
    pdf, ident = _pendente(tmp_path)
    with pytest.raises(ErroNormalizacao, match="LiberationSerif-Regular.ttf"):
        gerar_pdf_normalizado(pdf, ident, tmp_path / "saida", pasta_fontes=tmp_path / "sem_fontes")
    assert not list((tmp_path / "saida").glob("*"))


def test_fonte_e_licenca_versionadas():
    assert (normalizador.PASTA_FONTES / normalizador.FONTE_TEXTO).is_file()
    licenca = (normalizador.PASTA_FONTES / "LICENCA-LiberationSerif.txt").read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE" in licenca.upper()


def test_decreto_longo_quebra_de_pagina(tmp_path):
    itens = "\n".join(f"Art. {n}º Fica concedido o estímulo número {n} à empresa X, nos termos do regulamento." for n in range(1, 60))
    texto = _decreto(itens)
    pdf = criar_pdf_texto(tmp_path / "longo.pdf", *[texto[i : i + 3500] for i in range(0, len(texto), 3500)])
    ident = identificar(ler_pdf(pdf).texto)
    gerado = gerar_pdf_normalizado(pdf, ident, tmp_path / "saida")
    assert len(pymupdf.open(gerado)) >= 3