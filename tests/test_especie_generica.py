"""O pipeline não conhece "decreto": tudo vem da `Especie`.

Estes testes usam uma espécie FICTÍCIA ("Parecer"), com cabeçalho, fecho, cargo
de quem assina, abertura do corpo, fim do cabeçalho e programa próprios. Se
alguma etapa ainda dependesse de regra de decreto (fecho "Independência do
Brasil", "A GOVERNADORA", "DECRETA:"), estes testes falhariam. É a garantia de
que incluir editais no futuro é só definir uma `Especie` nova em `especies.py`.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date

import pytest

from conftest import criar_pdf_texto
from dbf_cepe.config import Configuracao
from dbf_cepe.leitura import criterio
from dbf_cepe.pipeline.exportador import gravar_pdf_do_ato
from dbf_cepe.pipeline.lote import Destino, executar
from dbf_cepe.pipeline.registro import Registro
from dbf_cepe.regras import especies as modulo_especies
from dbf_cepe.regras.especies import DECRETO, Especie, Tipo
from dbf_cepe.leitura.identificador import StatusIdentificacao, identificar
from dbf_cepe.regras.programas import (
    Programa,
    cabecalho_do_ato,
    cita_programa_so_no_corpo,
    em_escopo,
    programas_do_ato,
)
from dbf_cepe.leitura.separador import AtoSeparado, separar_texto

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
"""Proteja que o pipeline use a configuração de espécie sem assumir decreto."""


# ---------------------------------------------------------------------------
# Espécie com ano no nome do arquivo e escopo por órgão (como o edital)
# ---------------------------------------------------------------------------
# A "Deliberação" fictícia tem o que o decreto não tem: o ano no nome ("Deliberacao 12-2026.pdf",
# como "Edital 064-2025.pdf") e o órgão no cabeçalho, que decide o escopo junto com o programa.


def _orgao_da_deliberacao(cabecalho: re.Match[str]) -> str | None:
    return cabecalho["qualificador"]


DELIBERACAO = Especie(
    nome="Deliberacao",
    cabecalho=re.compile(
        r"(?m)^DELIBERAÇÃO\s+(?P<qualificador>[A-Z]{2,5})\s+N[º°]\s*(?P<numero>\d+)(?:/(?P<ano>\d{4}))?"
    ),
    padrao_arquivo="{especie} {numero}-{ano}.pdf",
    tipos=(Tipo("APROVACAO", re.compile(r"Aprova\b"), None),),
    abertura_do_corpo=re.compile(r"\bO CONSELHO\b"),
    programas=(ALFA,),
    orgaos_do_escopo=frozenset({"CDF"}),
    orgao_do_cabecalho=_orgao_da_deliberacao,
)


def _deliberacao(numero: str, orgao: str = "CDF", ano: str | None = "2026") -> str:
    barra = f"/{ano}" if ano else ""
    return (
        f"DELIBERAÇÃO {orgao} Nº {numero}{barra}\n"
        "Aprova o enquadramento da empresa X no programa Alfa.\n"
        "O CONSELHO, no uso de suas atribuições,\nRESOLVE:\nArt. 1º Fica aprovado o enquadramento.\n"
    )


def test_nome_com_ano_e_rotulo_sem_ano():
    assert DELIBERACAO.usa_ano and not DECRETO.usa_ano
    assert DELIBERACAO.nome_do_ato("12", "2026") == "Deliberacao 12-2026.pdf"
    assert DELIBERACAO.nome_do_ato("12", None) is None
    assert DELIBERACAO.rotulo("12", None) == "Deliberacao 12 (sem ano)"
    # No decreto o ano não entra na chave: o registro de publicações continua com "ano": null.
    assert DECRETO.ano_da_chave("2026") is None and DECRETO.nome_do_ato("60929", "2026") == "Decreto 60929.pdf"


def test_separador_leva_ano_e_orgao_do_cabecalho():
    texto = _deliberacao("12") + _deliberacao("12", ano="2025") + _deliberacao("13", orgao="SAD")
    atos, avisos = separar_texto(texto, especies=(DELIBERACAO,))
    assert [(a.numero, a.ano, a.orgao, a.nome_padrao) for a in atos] == [
        ("12", "2026", "CDF", "Deliberacao 12-2026.pdf"),
        ("12", "2025", "CDF", "Deliberacao 12-2025.pdf"),
        ("13", "2026", "SAD", "Deliberacao 13-2026.pdf"),
    ]
    # Mesmo número em anos diferentes não é o mesmo ato (não é "republicação?").
    assert avisos == []


def test_decreto_continua_sem_ano_na_chave():
    (ato,), _ = separar_texto(TEXTO.split("Texto solto")[1], especies=(DECRETO,))
    assert (ato.numero, ato.ano, ato.chave_ano, ato.orgao, ato.nome_padrao) == (
        "60001", "2026", None, None, "Decreto 60001.pdf",
    )


def test_identificador_leva_ano_e_orgao_e_recusa_o_ato_sem_ano():
    ident = identificar(_deliberacao("12"), (DELIBERACAO,))
    assert ident.status is StatusIdentificacao.IDENTIFICADO
    assert (ident.numero, ident.ano, ident.orgao, ident.nome_padrao) == ("12", "2026", "CDF", "Deliberacao 12-2026.pdf")
    sem_ano = identificar(_deliberacao("12", ano=None), (DELIBERACAO,))
    assert sem_ano.status is StatusIdentificacao.SEM_ANO
    assert "exige o ano" in sem_ano.detalhe


@pytest.mark.parametrize(("orgao", "esperado"), [("CDF", True), ("SAD", False), (None, False)])
def test_escopo_pelo_orgao_e_pelo_programa(orgao, esperado):
    assert em_escopo(DELIBERACAO, (ALFA,), orgao) is esperado
    assert em_escopo(DELIBERACAO, (), "CDF") is False  # órgão certo, sem programa
    assert em_escopo(DECRETO, DECRETO.programas[:1]) is True  # decreto não se divide por órgão


def test_criterio_da_dbf_usa_o_orgao_do_ato():
    texto = _deliberacao("12")
    ato = AtoSeparado(DELIBERACAO, "12", "DELIBERAÇÃO CDF Nº 12/2026", texto, 1, 1, ano="2026", orgao="CDF")
    assert criterio.da_dbf(ato)
    assert not criterio.da_dbf(AtoSeparado(DELIBERACAO, "12", "", texto, 1, 1, ano="2026", orgao="SAD"))


@pytest.fixture
def com_deliberacao_ativa(monkeypatch, tmp_path):
    """A deliberação passa a ser uma espécie ativa: só `ESPECIES_ATIVAS` muda."""
    monkeypatch.setattr(modulo_especies, "ESPECIES_ATIVAS", (DECRETO, DELIBERACAO))
    cfg = Configuracao(
        pasta_temp=tmp_path / "pendentes",
        pasta_cadernos=tmp_path / "cadernos",
        pasta_erros=tmp_path / "erros",
        pasta_ocr=tmp_path / "leitura tesseract",
        pasta_normalizados=tmp_path / "normalizados",
        pasta_logs=tmp_path / "logs",
        pasta_dbf=tmp_path / "dbf",
        arquivo_registro=tmp_path / "registro_publicacoes.json",
    )
    cfg.pasta_temp.mkdir()
    cfg.pasta_dbf.mkdir()
    return cfg


def test_lote_grava_com_o_ano_no_nome_e_manda_o_sem_ano_para_revisao(com_deliberacao_ativa):
    cfg = com_deliberacao_ativa
    (cfg.pasta_dbf / "Deliberacao 12-2025.pdf").write_bytes(b"de outro ano")
    criar_pdf_texto(cfg.pasta_temp / "a.pdf", _deliberacao("12"))
    criar_pdf_texto(cfg.pasta_temp / "b.pdf", _deliberacao("12", ano="2025"))
    criar_pdf_texto(cfg.pasta_temp / "c.pdf", _deliberacao("14", ano=None))

    resumo = executar(cfg)

    # 12/2026 é inédita mesmo com a 12/2025 na base; a 12/2025 é duplicata; a sem ano vai para erros.
    assert resumo == {Destino.CONCLUIDO: 1, Destino.DESCARTADO_DUPLICADO: 1, Destino.ENVIADO_ERROS: 1}
    assert sorted(p.name for p in cfg.pasta_dbf.iterdir()) == ["Deliberacao 12-2025.pdf", "Deliberacao 12-2026.pdf"]
    assert [p.name for p in cfg.pasta_erros.iterdir()] == ["c.pdf"]


def test_lote_com_publicacao_registra_o_ano_na_chave(com_deliberacao_ativa):
    cfg = com_deliberacao_ativa
    (cfg.pasta_dbf / "Decreto 1.pdf").write_bytes(b"base nao vazia")
    for ano, dia in (("2026", 10), ("2025", 11)):
        (ato,), _ = separar_texto(_deliberacao("12", ano=ano), especies=(DELIBERACAO,))
        ato = replace(ato, data_publicacao=date(2026, 6, dia), caderno=f"202606{dia}_PoderExecutivo.pdf")
        gravar_pdf_do_ato(ato, cfg.pasta_temp / f"{ano}.pdf")

    assert executar(cfg) == {Destino.CONCLUIDO: 2}

    registro = Registro.carregar(cfg.registro)
    assert sorted((e.numero, e.ano, e.arquivo) for e in registro.entradas) == [
        ("12", "2025", "Deliberacao 12-2025.pdf"), ("12", "2026", "Deliberacao 12-2026.pdf"),
    ]
