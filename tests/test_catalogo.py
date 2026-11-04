"""Catálogo somente leitura dos atos (catalogo.py e `main.py --catalogar`)."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest

import catalogo
import main
from conftest import criar_pdf_imagem, criar_pdf_texto, texto_decreto
from regras_atos import identificar

ASSINATURAS = "Independência do Brasil.\nRAQUEL TEIXEIRA LYRA LUCENA\nGovernadora do Estado\n"

PAGINA_ATOS = "\n".join(
    [
        texto_decreto("60.929", "Concede estímulo previsto na Lei nº 11.675."),
        ASSINATURAS,
        texto_decreto("61.055", "Abre ao Orçamento Fiscal crédito suplementar.").replace(
            "Lei nº 11.675, de 11 de outubro de 1999, e o Decreto nº 21.959, de 27 de dezembro de 1999",
            "Lei nº 19.127",
        ),
        ASSINATURAS + "(REPUBLICADO POR HAVER SAÍDO COM INCORREÇÃO NO ORIGINAL)",
        "EDITAL DBF Nº 063/2026\nCREDENCIAMENTO PRODEAUTO\nA Diretoria de Benefícios Fiscais - DBF, resolve credenciar.\n"
        "Recife, 21 de outubro de 2026.\nDiretor",
        "PORTARIA SF Nº 76, DE 24.04.2026\nO SECRETÁRIO DA FAZENDA, RESOLVE:\nArt. 1º Designar X.\n"
        "Art. 2º Esta Portaria entra em vigor.\nSecretário da Fazenda",
        "PORTARIA SES/PE N° 609, de 21 de Outubro de 2026\nInstitui o Núcleo.",
        "ERRATA\nNo art. 4º do Decreto nº 61.560, de 19 de outubro de 2026\nOnde se lê: X",
    ]
)


@pytest.fixture
def caderno(tmp_path: Path) -> Path:
    pasta = tmp_path / "cadernos"
    pasta.mkdir()
    return criar_pdf_texto(pasta / "20261022_ResumoDiario.pdf", PAGINA_ATOS)


def _linhas(csv_: Path) -> list[dict[str, str]]:
    with csv_.open(encoding="utf-8-sig", newline="") as arquivo:
        return list(csv.DictReader(arquivo, delimiter=";"))


def test_data_de_publicacao_vem_do_nome_do_caderno():
    assert catalogo.data_de_publicacao(Path("20260425_PoderExecutivo.pdf")) == "2026-04-25"
    assert catalogo.data_de_publicacao(Path("20260626_ResumoDiario.pdf")) == "2026-06-26"
    assert catalogo.data_de_publicacao(Path("Caderno 2026-06-26.pdf")) == ""
    assert catalogo.data_de_publicacao(Path("20261345_x.pdf")) == ""  # mês 13


def test_csv_tem_as_colunas_pedidas_na_ordem(caderno: Path, tmp_path: Path):
    saida = tmp_path / "saida" / "catalogo.csv"
    resultado = catalogo.catalogar(caderno)
    catalogo.gravar_csv(resultado.linhas, saida)
    primeira_linha = saida.read_text(encoding="utf-8-sig").splitlines()[0]
    assert primeira_linha == (
        "especie;numero;ano;orgao;tipo;data_do_ato;data_de_publicacao;caderno;pagina;republicacao;situacao"
    )
    assert saida.read_bytes().startswith(b"\xef\xbb\xbf")  # UTF-8 com BOM (Excel)


def test_linhas_do_catalogo_por_especie(caderno: Path, tmp_path: Path):
    saida = tmp_path / "c.csv"
    catalogo.gravar_csv(catalogo.catalogar(caderno).linhas, saida)
    linhas = {(l["especie"], l["numero"]): l for l in _linhas(saida)}

    concessao = linhas[("Decreto", "60929")]
    assert (concessao["ano"], concessao["tipo"], concessao["data_do_ato"]) == ("2026", "CONCESSAO", "2026-06-26")
    assert concessao["data_de_publicacao"] == "2026-10-22"
    assert concessao["caderno"] == "20261022_ResumoDiario.pdf" and concessao["pagina"] == "1"
    assert concessao["situacao"] == "no_escopo: PRODEPE" and concessao["republicacao"] == "nao"

    credito = linhas[("Decreto", "61055")]
    assert credito["republicacao"] == "sim"
    assert credito["situacao"].startswith("fora_do_escopo")

    edital = linhas[("Edital", "063")]
    assert (edital["orgao"], edital["tipo"], edital["data_do_ato"]) == ("DBF", "CREDENCIAMENTO", "2026-10-21")
    assert edital["situacao"] == "no_escopo: órgão DBF"

    sf = linhas[("Portaria", "76")]
    # SF sem programa da DBF (a designação de um servidor não é de estímulo fiscal): fora do escopo
    assert (sf["orgao"], sf["tipo"], sf["data_do_ato"], sf["situacao"]) == (
        "SF", "DESIGNACAO", "2026-04-24", "fora_do_escopo: órgão SF sem programa da DBF"
    )
    assert linhas[("Portaria", "609")]["situacao"] == "fora_do_escopo: órgão SES/PE"


def test_errata_vira_linha_com_o_ato_referido_e_fica_a_confirmar(caderno: Path, tmp_path: Path):
    saida = tmp_path / "c.csv"
    catalogo.gravar_csv(catalogo.catalogar(caderno).linhas, saida)
    (errata,) = [l for l in _linhas(saida) if l["especie"] == "Errata"]
    assert (errata["numero"], errata["ano"], errata["tipo"], errata["republicacao"]) == ("61560", "2026", "decreto", "")
    assert errata["situacao"].startswith("a_confirmar: errata de Decreto 61.560")


def test_errata_de_despacho_fica_fora_do_escopo(tmp_path: Path):
    criar_pdf_texto(tmp_path / "20261022_x.pdf", "ERRATA No despacho do dia 19.06.2026\nOnde se lê: X")
    (linha,) = catalogo.catalogar(tmp_path / "20261022_x.pdf").linhas
    assert (linha.tipo, linha.situacao) == ("outro", "fora_do_escopo: errata de Despacho")


def test_decreto_com_programa_so_no_corpo_fica_a_confirmar(tmp_path: Path):
    texto = (
        "DECRETO Nº 60.100, DE 26 DE JUNHO DE 2026.\nDispõe sobre a prorrogação de prazos.\n"
        "A GOVERNADORA DO ESTADO, no uso das atribuições,\nDECRETA:\n"
        "Art. 1º Fica prorrogado o prazo do PRODEPE conforme a Lei nº 11.675."
    )
    criar_pdf_texto(tmp_path / "20260626_x.pdf", texto)
    (linha,) = catalogo.catalogar(tmp_path / "20260626_x.pdf").linhas
    assert linha.situacao.startswith("a_confirmar: programa da DBF citado só no corpo")


def test_republicacao_sem_ato_vira_linha_e_entra_na_lista_de_nao_reconhecidos(tmp_path: Path):
    criar_pdf_texto(
        tmp_path / "20261021_x.pdf",
        "ATO DO DIA 16 DE OUTUBRO DE 2026.\nNº 5538 - Nomear X.\n(REPUBLICADO POR HAVER SAÍDO COM INCORREÇÃO NO ORIGINAL).",
    )
    resultado = catalogo.catalogar(tmp_path / "20261021_x.pdf")
    (linha,) = resultado.linhas
    assert linha.especie == "Republicação" and linha.situacao.startswith("a_confirmar")
    assert resultado.nao_reconhecidos == [linha]
    texto = catalogo.resumo_em_texto(resultado)
    assert "20261021_x.pdf, página 1 [Republicação]" in texto and "REPUBLICADO" in texto


def test_edital_sem_numero_nao_some_e_vai_para_a_lista(tmp_path: Path):
    criar_pdf_texto(tmp_path / "20260715_x.pdf", "EDITAL DE CONVOCAÇÃO\nSINDICATO DOS FISIOTERAPEUTAS")
    resultado = catalogo.catalogar(tmp_path / "20260715_x.pdf")
    (linha,) = resultado.linhas
    assert (linha.especie, linha.numero, linha.tipo) == ("Edital", "", "CONVOCACAO")
    assert linha.situacao.startswith("a_confirmar: cabeçalho de edital sem número")
    assert "sem número identificável" in catalogo.resumo_em_texto(resultado)


def test_resumo_traz_contagem_por_especie_e_por_tipo(caderno: Path):
    texto = catalogo.resumo_em_texto(catalogo.catalogar(caderno))
    assert "Por espécie: Decreto 2, Edital 1, Portaria 2, Errata 1" in texto
    assert "Decreto: CONCESSAO 1, (sem tipo: a confirmar) 1" in texto
    assert "Errata (destino): decreto 1" in texto
    assert "Republicações ligadas a um ato: sim 1" in texto


def test_pagina_digitalizada_vira_aviso(tmp_path: Path):
    criar_pdf_imagem(tmp_path / "20260626_scan.pdf")
    resultado = catalogo.catalogar(tmp_path / "20260626_scan.pdf")
    assert resultado.linhas == []
    assert any("digitalizadas" in aviso and "OCR" in aviso for aviso in resultado.avisos)


def test_nome_sem_data_avisa_e_deixa_a_publicacao_em_branco(tmp_path: Path):
    criar_pdf_texto(tmp_path / "caderno.pdf", "EDITAL DBF Nº 001/2026\nCREDENCIAMENTO")
    resultado = catalogo.catalogar(tmp_path / "caderno.pdf")
    assert resultado.linhas[0].data_de_publicacao == ""
    assert any("AAAAMMDD" in aviso for aviso in resultado.avisos)


def test_caderno_ilegivel_vira_aviso_e_os_outros_continuam(tmp_path: Path):
    pasta = tmp_path / "cadernos"
    pasta.mkdir()
    (pasta / "20260101_quebrado.pdf").write_bytes(b"isto nao e um pdf")
    criar_pdf_texto(pasta / "20260102_bom.pdf", "EDITAL DBF Nº 001/2026\nCREDENCIAMENTO")
    resultado = catalogo.catalogar(pasta)
    assert (resultado.cadernos, resultado.cadernos_com_falha, len(resultado.linhas)) == (2, 1, 1)
    assert any("20260101_quebrado.pdf" in aviso and "não foi possível ler" in aviso for aviso in resultado.avisos)


def test_pasta_sem_pdf_e_erro(tmp_path: Path):
    with pytest.raises(catalogo.ErroSeparacao, match="nenhum PDF"):
        catalogo.catalogar(tmp_path)


# ---------------------------------------------------------------------------
# main.py --catalogar
# ---------------------------------------------------------------------------


def _sha(pasta: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(pasta.iterdir())}


def test_catalogar_so_le_os_cadernos_e_grava_so_o_csv(caderno: Path, tmp_path: Path, capsys, monkeypatch):
    pasta = caderno.parent
    antes = _sha(pasta)
    saida = tmp_path / "fora" / "catalogo_atos.csv"

    def sem_configuracao(*_a, **_k):
        raise AssertionError("--catalogar não pode carregar a configuração (.env) nem a base da DBF")

    monkeypatch.setattr(main, "carregar_configuracao", sem_configuracao)
    monkeypatch.chdir(tmp_path)
    codigo = main.main(["--catalogar", str(pasta), "--saida", str(saida)])

    assert codigo == 0
    assert _sha(pasta) == antes  # cadernos intactos
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cadernos", "fora"]  # nada além do CSV
    assert [p.name for p in saida.parent.iterdir()] == ["catalogo_atos.csv"]
    saida_do_programa = capsys.readouterr().out
    assert "Por espécie:" in saida_do_programa and str(saida) in saida_do_programa


def test_catalogar_grava_em_dados_por_padrao(caderno: Path, tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main.main(["--catalogar", str(caderno.parent)]) == 0
    assert (tmp_path / "dados" / "catalogo_atos.csv").is_file()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cadernos", "dados"]
    capsys.readouterr()


def test_catalogar_com_um_caderno_so(caderno: Path, tmp_path: Path, capsys):
    assert main.main(["--catalogar", str(caderno), "--saida", str(tmp_path / "c.csv")]) == 0
    assert len(_linhas(tmp_path / "c.csv")) >= 5
    capsys.readouterr()


def test_catalogar_pasta_vazia_devolve_codigo_2(tmp_path: Path, capsys):
    vazia = tmp_path / "vazia"
    vazia.mkdir()
    assert main.main(["--catalogar", str(vazia), "--saida", str(tmp_path / "c.csv")]) == 2
    assert "nenhum PDF" in capsys.readouterr().err
    assert not (tmp_path / "c.csv").exists()


def test_catalogar_caderno_inexistente_devolve_codigo_2(tmp_path: Path, capsys):
    assert main.main(["--catalogar", str(tmp_path / "nao_existe.pdf"), "--saida", str(tmp_path / "c.csv")]) == 2
    assert "não encontrado" in capsys.readouterr().err


def test_catalogar_com_caderno_ilegivel_devolve_codigo_1_mas_grava_o_resto(tmp_path: Path, capsys):
    pasta = tmp_path / "cadernos"
    pasta.mkdir()
    (pasta / "20260101_quebrado.pdf").write_bytes(b"nada")
    criar_pdf_texto(pasta / "20260102_bom.pdf", "EDITAL DBF Nº 001/2026\nCREDENCIAMENTO")
    assert main.main(["--catalogar", str(pasta), "--saida", str(tmp_path / "c.csv")]) == 1
    assert len(_linhas(tmp_path / "c.csv")) == 1
    capsys.readouterr()


@pytest.mark.parametrize(
    "extra",
    [["--simular"], ["--baixar", "--de", "2026-10-01"], ["--separar", "x.pdf"], ["--gravar"], ["--comparar"]],
)
def test_catalogar_nao_se_combina_com_outros_modos(extra, tmp_path: Path):
    with pytest.raises(SystemExit) as saida:
        main.main(["--catalogar", str(tmp_path), *extra])
    assert saida.value.code == 2


def test_saida_sem_catalogar_e_erro():
    with pytest.raises(SystemExit) as saida:
        main.main(["--saida", "x.csv"])
    assert saida.value.code == 2


# ---------------------------------------------------------------------------
# Escopo: portaria da SF só com programa da DBF (A) e órgão lido do título (B)
# ---------------------------------------------------------------------------


def _situacao_de(texto: str, especie: str) -> str:
    achado = next(a for a in identificar(texto) if a.especie == especie)
    return catalogo._situacao(achado, texto[achado.posicao : achado.fim], catalogo._motivo_de_conferencia(achado))


SF_COM_PROGRAMA = (
    "PORTARIA SF Nº 200, DE 20.10.2026\nO SECRETÁRIO DA FAZENDA, tendo em vista o inciso XI do art. 17 da "
    "Lei nº 11.675, de 11.10.1999, RESOLVE:\nArt. 1° Declarar a perda por renúncia ao incentivo do PRODEPE\n"
)
SF_SEM_PROGRAMA = (
    "PORTARIA SF Nº 76, DE 24.04.2026\nO SECRETÁRIO DA FAZENDA, RESOLVE:\n"
    "Art. 1º Dispensar o servidor da função de Gestor de Telemática.\n"
)


def test_portaria_sf_que_cita_programa_da_dbf_esta_no_escopo():
    assert _situacao_de(SF_COM_PROGRAMA, "Portaria") == "no_escopo: órgão SF; PRODEPE"


def test_portaria_sf_sem_programa_da_dbf_fica_fora_do_escopo():
    assert _situacao_de(SF_SEM_PROGRAMA, "Portaria") == "fora_do_escopo: órgão SF sem programa da DBF"


def test_portaria_sf_com_programa_so_no_corpo_fica_a_confirmar():
    enchimento = "Art. 2º Este artigo não cita nada de especial e serve só para encher o texto.\n" * 60
    texto = SF_SEM_PROGRAMA + enchimento + "Anexo: incentivos do PRODEPE.\n"
    assert _situacao_de(texto, "Portaria").startswith("a_confirmar: órgão SF: programa da DBF citado só no corpo")


def test_edital_da_dbf_continua_no_escopo_sem_exigir_programa_no_texto():
    texto = "EDITAL DBF Nº 063/2026\nCREDENCIAMENTO PRODEAUTO\nA Diretoria de Benefícios Fiscais, resolve credenciar.\n"
    assert _situacao_de(texto, "Edital") == "no_escopo: órgão DBF"


def test_edital_de_outro_orgao_lido_do_titulo_fica_fora_do_escopo():
    texto = (
        "SECRETARIA DE ADMINISTRAÇÃO\nAVISO DE ABERTURA – 2ª REPUBLICAÇÃO DE EDITAL\n"
        "EDITAL DE CREDENCIAMENTO N° 0014.2026.PNE\nPROCESSO DE INEXIGIBILIDADE\n"
    )
    assert _situacao_de(texto, "Edital") == "fora_do_escopo: órgão SECRETARIA DE ADMINISTRAÇÃO (lido do título da seção)"


def test_orgao_lido_do_titulo_aparece_na_coluna_orgao_do_csv(tmp_path: Path):
    criar_pdf_texto(
        tmp_path / "20260626_x.pdf",
        "SECRETARIA DE ADMINISTRAÇÃO\nAVISO DE ABERTURA\nEDITAL DE CREDENCIAMENTO N° 0014.2026.PNE\nPROCESSO",
    )
    (linha,) = catalogo.catalogar(tmp_path / "20260626_x.pdf").linhas
    assert linha.orgao == "SECRETARIA DE ADMINISTRAÇÃO"
    assert linha.situacao.startswith("fora_do_escopo") and "lido do título" in linha.situacao
    assert linha.motivo == ""  # a pista do título não vai para a lista "a conferir"


def test_titulo_de_outro_orgao_nao_esconde_ato_que_cita_programa_da_dbf():
    texto = (
        "SECRETARIA DE ADMINISTRAÇÃO\nEDITAL Nº 7/2026\n"
        "A empresa X, beneficiária do PRODEPE (Lei nº 11.675), torna público\n"
    )
    situacao = _situacao_de(texto, "Edital")
    assert situacao.startswith("a_confirmar: cita programa da DBF (PRODEPE)")
    assert "SECRETARIA DE ADMINISTRAÇÃO" in situacao


def test_edital_sem_numero_que_cita_programa_da_dbf_guarda_as_duas_razoes():
    # É o "EDITAL DE NÃO CONCORRÊNCIA" real (publicação particular sobre o PRODEPE).
    texto = (
        "SECRETARIA DE ADMINISTRAÇÃO\nEDITAL DE NÃO CONCORRÊNCIA\n"
        "A empresa X, beneficiária do PRODEPE (Lei nº 11.675), torna público\n"
    )
    situacao = _situacao_de(texto, "Edital")
    assert "cabeçalho de edital sem número identificável" in situacao
    assert "cita programa da DBF (PRODEPE)" in situacao


def test_cabecalho_com_orgao_prevalece_sobre_o_titulo():
    texto = "SECRETARIA DE ADMINISTRAÇÃO\nEDITAL DBF Nº 5/2026\nCREDENCIAMENTO\n"
    assert _situacao_de(texto, "Edital") == "no_escopo: órgão DBF"


def test_titulo_da_dpc_fica_fora_e_da_regiao_fiscal_fica_a_confirmar():
    dpc = "DIRETORIA GERAL DE PLANEJAMENTO E CONTROLE DA AÇÃO FISCAL – DPC\nEDITAL Nº 43/2026\nCREDENCIAMENTO\n"
    assert _situacao_de(dpc, "Edital").startswith("fora_do_escopo: órgão DIRETORIA GERAL DE PLANEJAMENTO")
    regiao = "DIRETORIA GERAL DA II REGIÃO FISCAL\nEDITAL DE INTIMAÇÃO Nº 005/2026\nCIÊNCIA DO LANÇAMENTO\n"
    assert _situacao_de(regiao, "Edital").startswith("a_confirmar: órgão lido do título da seção")


def test_sem_titulo_o_edital_sem_orgao_continua_a_confirmar():
    assert _situacao_de("EDITAL Nº 43/2026\nCREDENCIAMENTO\n", "Edital") == "a_confirmar: órgão não consta no cabeçalho"
