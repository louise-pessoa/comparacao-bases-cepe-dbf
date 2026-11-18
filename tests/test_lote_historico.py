"""Ferramenta do lote histórico: meses em sequência, parando no primeiro sinal de problema.
Sem rede: captura, separação e lote são trocados por versões controladas."""

from __future__ import annotations

from collections import Counter
from datetime import date

from pathlib import Path

import pytest

import lote_historico as lh
from dbf_cepe import captura
from dbf_cepe.config import Configuracao
from dbf_cepe.pipeline.comparacao import Comparacao
from dbf_cepe.pipeline.limpeza import Avaliacao, ResultadoLimpeza
from dbf_cepe.pipeline.lote import Destino
from dbf_cepe.pipeline.relatorio import ResumoRelatorio


@pytest.fixture
def cfg(tmp_path):
    c = Configuracao(
        pasta_temp=tmp_path / "pendentes", pasta_cadernos=tmp_path / "cadernos", pasta_erros=tmp_path / "erros",
        pasta_ocr=tmp_path / "ocr", pasta_normalizados=tmp_path / "normalizados", pasta_logs=tmp_path / "logs",
        pasta_dbf=tmp_path / "dbf",
    )
    c.pasta_temp.mkdir()
    c.pasta_dbf.mkdir()
    return c


def test_meses_do_periodo():
    assert lh.meses(date(2020, 11, 1), date(2021, 2, 1)) == [
        date(2020, 11, 1), date(2020, 12, 1), date(2021, 1, 1), date(2021, 2, 1)
    ]
    with pytest.raises(lh.ErroDeUso):
        lh.meses(date(2021, 2, 1), date(2020, 11, 1))


def test_fim_do_mes_e_fonte():
    assert lh.Mes(date(2020, 2, 1)).fim == date(2020, 2, 29)
    assert lh.Mes(date(2021, 12, 1)).fim == date(2021, 12, 31)
    assert lh.Mes(date(2026, 5, 1)).fonte == "s3"
    assert lh.Mes(date(2026, 6, 1)).fonte == "resumo"


def test_motivos_na_separacao():
    resumo = ResumoRelatorio(
        reprovados_dbf=["Decreto 47176.pdf (c.pdf, p. 5): sem fecho"],
        comparacoes=[
            ("Decreto 1.pdf", Comparacao(lido=True, similaridade=0.94, so_no_ato=3)),
            ("Decreto 2.pdf", Comparacao(lido=True, similaridade=0.995, so_na_base=1)),
            ("Decreto 3.pdf", Comparacao(lido=False)),
            ("Decreto 4.pdf", Comparacao(lido=True, similaridade=1.0)),
        ],
    )
    motivos = lh.motivos_de_parada_na_separacao(resumo)
    assert len(motivos) == 2
    assert "Decreto 47176.pdf" in motivos[0] and "Decreto 1.pdf: similaridade 94.00%" in motivos[1]


def test_motivos_no_lote():
    assert lh.motivos_de_parada_no_lote(Counter({Destino.CONCLUIDO: 3, Destino.ENVIADO_CONFERIR: 1})) == []
    motivos = lh.motivos_de_parada_no_lote(Counter({Destino.ENVIADO_ERROS: 1, Destino.FALHA_AO_GRAVAR: 2}))
    assert len(motivos) == 2


class Roteiro:
    """Respostas controladas da captura, da separação e do lote, e o registro das chamadas."""

    def __init__(
        self, monkeypatch, *, baixados=5, sem_conexao=0, separacao=None, gravacao=None, simulacao=None, lote=None,
        limpeza=None,
    ):
        self.chamadas: list[str] = []
        self.separacao = separacao or ResumoRelatorio()
        self.gravacao = gravacao or ResumoRelatorio(situacoes=Counter({"INÉDITO": 2}))
        self.simulacao = simulacao or Counter({Destino.CONCLUIDO: 2})
        self.lote = lote or Counter({Destino.CONCLUIDO: 2})
        self.limpeza = limpeza or ResultadoLimpeza(apagados=["a.pdf", "b.pdf"], mantidos=[Avaliacao("c.pdf", ["x"])])

        def capturar(cfg, inicio, fim, simular, fonte):
            self.chamadas.append(f"baixar {inicio:%Y-%m} {fonte} em {cfg.pasta_cadernos.as_posix()}")
            return Counter({captura.StatusDownload.BAIXADO: baixados, captura.StatusDownload.SEM_CONEXAO: sem_conexao})

        def relatorio(cfg, pasta, gravar=False, comparar=False):
            self.chamadas.append("gravar" if gravar else "comparar")
            if gravar:
                (cfg.pasta_temp / f"{pasta.name}.pdf").write_bytes(b"%PDF")
            return self.gravacao if gravar else self.separacao

        def executar(cfg, simular=False):
            self.chamadas.append("simular" if simular else "lote")
            return self.simulacao if simular else self.lote

        monkeypatch.setattr(lh, "capturar", capturar)
        monkeypatch.setattr(lh, "relatorio_dos_cadernos", relatorio)
        def limpar(cfg, pasta, mes, exigir_simulacao=True):
            self.chamadas.append(f"limpar {mes:%Y-%m} {pasta.as_posix()} exigir={exigir_simulacao}")
            return self.limpeza

        monkeypatch.setattr(lh, "executar", executar)
        monkeypatch.setattr(lh.limpeza_dos_cadernos, "limpar", limpar)


def test_mes_completo_com_executar(cfg, monkeypatch):
    r = Roteiro(monkeypatch)
    mes = lh.rodar_mes(cfg, lh.Mes(date(2020, 3, 1)), executar_de_verdade=True)
    assert not mes.parada
    assert r.chamadas == [
        "baixar 2020-03 s3 em dados/historico/2020/2020-03", "comparar", "gravar", "simular", "lote",
        "limpar 2020-03 dados/historico/2020/2020-03 exigir=False",
    ]
    assert "cadernos apagados 2, guardados 1" in lh.resumo_do_mes(mes)


def test_manter_cadernos_nao_limpa(cfg, monkeypatch):
    r = Roteiro(monkeypatch)
    mes = lh.rodar_mes(cfg, lh.Mes(date(2020, 3, 1)), executar_de_verdade=True, limpar=False)
    assert not mes.parada and r.chamadas[-1] == "lote" and "apagados" not in lh.resumo_do_mes(mes)


def test_falha_ao_apagar_para_o_mes(cfg, monkeypatch):
    Roteiro(monkeypatch, limpeza=ResultadoLimpeza(falhas=["a.pdf"]))
    mes = lh.rodar_mes(cfg, lh.Mes(date(2020, 3, 1)), executar_de_verdade=True)
    assert "não puderam ser apagados" in mes.parada


def test_lote_com_problema_nao_limpa(cfg, monkeypatch):
    r = Roteiro(monkeypatch, lote=Counter({Destino.ENVIADO_ERROS: 1}))
    lh.rodar_mes(cfg, lh.Mes(date(2020, 3, 1)), executar_de_verdade=True)
    assert not any(c.startswith("limpar") for c in r.chamadas)


def test_sem_executar_so_simula_e_esvazia_pendentes(cfg, monkeypatch):
    r = Roteiro(monkeypatch)
    mes = lh.rodar_mes(cfg, lh.Mes(date(2020, 3, 1)), executar_de_verdade=False)
    assert not mes.parada and r.chamadas[-1] == "simular"  # sem limpeza
    assert list(cfg.pasta_temp.iterdir()) == []


@pytest.mark.parametrize(
    ("ajustes", "chamadas", "trecho"),
    [
        ({"baixados": 0}, ["baixar"], "nenhum caderno"),
        ({"sem_conexao": 1}, ["baixar"], "sem conexão"),
        ({"separacao": ResumoRelatorio(reprovados_dbf=["Decreto 9.pdf (c, p. 1): sem fecho"])}, ["baixar", "comparar"], "reprovado"),
        ({"gravacao": ResumoRelatorio(falhas=1)}, ["baixar", "comparar", "gravar"], "não puderam ser gravados"),
        ({"simulacao": Counter({Destino.ENVIADO_ERROS: 1})}, ["baixar", "comparar", "gravar", "simular"], "erros"),
        ({"lote": Counter({Destino.FALHA_AO_GRAVAR: 1})}, ["baixar", "comparar", "gravar", "simular", "lote"], "falha ao gravar"),
    ],
)
def test_para_no_primeiro_problema(cfg, monkeypatch, ajustes, chamadas, trecho):
    r = Roteiro(monkeypatch, **ajustes)
    mes = lh.rodar_mes(cfg, lh.Mes(date(2020, 3, 1)), executar_de_verdade=True)
    assert trecho in mes.parada
    assert [c.split()[0] for c in r.chamadas] == chamadas


def test_main_para_no_mes_com_problema_e_nao_segue(cfg, monkeypatch, capsys):
    reprovado = ResumoRelatorio(reprovados_dbf=["Decreto 9.pdf (c, p. 1): sem fecho"])
    r = Roteiro(monkeypatch, separacao=reprovado)
    monkeypatch.setattr(lh, "carregar_configuracao", lambda: cfg)
    assert lh.main(["--de", "2020-03", "--ate", "2020-05", "--executar"]) == 1
    assert [c for c in r.chamadas if c.startswith("baixar")] == ["baixar 2020-03 s3 em dados/historico/2020/2020-03"]
    assert "Parou em 2020-03" in capsys.readouterr().out


def test_main_varios_meses_ok(cfg, monkeypatch, capsys):
    r = Roteiro(monkeypatch)
    monkeypatch.setattr(lh, "carregar_configuracao", lambda: cfg)
    assert lh.main(["--de", "2020-11", "--ate", "2021-01"]) == 0
    assert [c.split()[1] for c in r.chamadas if c.startswith("baixar")] == ["2020-11", "2020-12", "2021-01"]
    assert "iriam para a base 2" in capsys.readouterr().out


def test_main_recusa_pendentes_com_sobra(cfg, monkeypatch, capsys):
    (cfg.pasta_temp / "sobra.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(lh, "carregar_configuracao", lambda: cfg)
    assert lh.main(["--de", "2020-03", "--ate", "2020-03"]) == 2
    assert "rodada anterior" in capsys.readouterr().err


def test_main_sem_pasta_dbf_ou_mes_invalido(cfg, monkeypatch, capsys):
    from dataclasses import replace

    monkeypatch.setattr(lh, "carregar_configuracao", lambda: replace(cfg, pasta_dbf=None))
    assert lh.main(["--de", "2020-03", "--ate", "2020-03"]) == 2
    with pytest.raises(SystemExit):
        lh.main(["--de", "2020-13", "--ate", "2020-03"])


@pytest.mark.parametrize(("extra", "limpou"), [([], True), (["--manter-cadernos"], False)])
def test_main_executar_limpa_ou_mantem(cfg, monkeypatch, capsys, extra, limpou):
    gravacao = ResumoRelatorio(situacoes=Counter({"INÉDITO": 1, "CONFERIR DUPLICATA": 1}))
    r = Roteiro(monkeypatch, gravacao=gravacao)
    monkeypatch.setattr(lh, "carregar_configuracao", lambda: cfg)
    assert lh.main(["--de", "2020-03", "--ate", "2020-03", "--executar", *extra]) == 0
    assert any(c.startswith("limpar") for c in r.chamadas) is limpou
    saida = capsys.readouterr().out
    assert ("--limpar-cadernos" in saida) is limpou and "rode python main.py --conferir" in saida


@pytest.mark.parametrize("executar_de_verdade", [False, True])
@pytest.mark.parametrize(
    "ajustes", [{"gravacao": ResumoRelatorio(falhas=1)}, {"simulacao": Counter({Destino.ENVIADO_ERROS: 1})}]
)
def test_parada_antes_do_lote_esvazia_pendentes(cfg, monkeypatch, ajustes, executar_de_verdade):
    """Junho de 2010: o --gravar parou num decreto e deixou os outros em pendentes; a rodada
    seguinte recusava começar."""
    Roteiro(monkeypatch, **ajustes)
    mes = lh.rodar_mes(cfg, lh.Mes(date(2020, 3, 1)), executar_de_verdade=executar_de_verdade)
    assert mes.parada and list(cfg.pasta_temp.iterdir()) == []


def test_parada_no_lote_de_verdade_mantem_pendentes(cfg, monkeypatch):
    Roteiro(monkeypatch, lote=Counter({Destino.FALHA_AO_GRAVAR: 1}))
    mes = lh.rodar_mes(cfg, lh.Mes(date(2020, 3, 1)), executar_de_verdade=True)
    assert "falha ao gravar" in mes.parada and [p.name for p in cfg.pasta_temp.iterdir()] == ["2020-03.pdf"]



# ----------------------------------------------------------------------------
# Pastas por ano (dados/historico/AAAA/AAAA-MM)
# ----------------------------------------------------------------------------


def test_pasta_do_mes_fica_dentro_do_ano():
    assert lh.pasta_do_mes("2020-03") == lh.PASTA_HISTORICO / "2020" / "2020-03"


def _manifesto(pasta: Path, **arquivos) -> None:
    from dbf_cepe import captura

    pasta.mkdir(parents=True, exist_ok=True)
    captura.gravar_manifesto(pasta, {nome: {"sha256": valor} for nome, valor in arquivos.items()})


def test_reorganizar_move_a_pasta_inteira_quando_o_destino_nao_existe(tmp_path):
    historico = tmp_path / "historico"
    _manifesto(historico / "2010-03", **{"20100301_PoderExecutivo.pdf": "a"})
    (historico / "2021").mkdir()  # pasta de ano: não é mês antigo
    (juncao,) = lh.reorganizar(historico)
    assert juncao.mover_pasta and juncao.nova == historico / "2010" / "2010-03"
    assert (historico / "2010-03").exists()  # simulação: nada mudou
    lh.reorganizar(historico, mover=True)
    assert (historico / "2010" / "2010-03" / "manifesto.json").exists()
    assert not (historico / "2010-03").exists()
    assert lh.pastas_no_formato_antigo(historico) == []


def test_reorganizar_junta_com_o_mes_que_ja_esta_na_pasta_do_ano(tmp_path):
    """Caso de 2010-09: o mês foi baixado de novo numa pasta solta depois da reorganização."""
    from dbf_cepe import captura

    historico = tmp_path / "historico"
    nova, antiga = historico / "2010" / "2010-09", historico / "2010-09"
    _manifesto(nova, **{"20100901_PoderExecutivo.pdf": "a"})
    (nova / "20100901_PoderExecutivo.pdf").write_bytes(b"caderno 1")
    _manifesto(antiga, **{"20100901_PoderExecutivo.pdf": "a", "20100902_PoderExecutivo.pdf": "b"})
    (antiga / "20100901_PoderExecutivo.pdf").write_bytes(b"caderno 1")  # idêntico
    (antiga / "20100902_PoderExecutivo.pdf").write_bytes(b"caderno 2")  # novo
    (antiga / "limpeza_simulada.json").write_text("{}", encoding="utf-8")
    (juncao,) = lh.reorganizar(historico)
    assert not juncao.conflitos and not juncao.mover_pasta
    assert juncao.mover == ["20100902_PoderExecutivo.pdf"] and juncao.repetidos == ["20100901_PoderExecutivo.pdf"]
    assert (antiga / "20100902_PoderExecutivo.pdf").exists()  # simulação
    lh.reorganizar(historico, mover=True)
    assert not antiga.exists()
    assert sorted(p.name for p in nova.iterdir()) == [
        "20100901_PoderExecutivo.pdf", "20100902_PoderExecutivo.pdf", "manifesto.json"]
    assert set(captura.ler_manifesto(nova)) == {"20100901_PoderExecutivo.pdf", "20100902_PoderExecutivo.pdf"}


@pytest.mark.parametrize("conflito", ["arquivo", "manifesto", "subpasta"])
def test_reorganizar_com_conflito_nao_mexe_em_nada(tmp_path, conflito):
    historico = tmp_path / "historico"
    nova, antiga = historico / "2010" / "2010-09", historico / "2010-09"
    _manifesto(nova, **{"20100901_PoderExecutivo.pdf": "a"})
    (nova / "20100901_PoderExecutivo.pdf").write_bytes(b"caderno 1")
    _manifesto(antiga, **{"20100901_PoderExecutivo.pdf": "b" if conflito == "manifesto" else "a"})
    (antiga / "20100901_PoderExecutivo.pdf").write_bytes(b"outro" if conflito == "arquivo" else b"caderno 1")
    (antiga / "20100902_PoderExecutivo.pdf").write_bytes(b"caderno 2")
    if conflito == "subpasta":
        (antiga / "sub").mkdir()
    antes = sorted(p.relative_to(historico).as_posix() for p in historico.rglob("*"))
    (juncao,) = lh.reorganizar(historico, mover=True)
    assert juncao.conflitos
    assert sorted(p.relative_to(historico).as_posix() for p in historico.rglob("*")) == antes
    assert "NÃO mexida" in lh.descrever(juncao, True)[0]


def test_main_recusa_comecar_com_pasta_no_formato_antigo(cfg, monkeypatch, capsys, tmp_path):
    historico = tmp_path / "historico"
    (historico / "2020-03").mkdir(parents=True)
    monkeypatch.setattr(lh, "PASTA_HISTORICO", historico)
    monkeypatch.setattr(lh, "carregar_configuracao", lambda: cfg)
    assert lh.main(["--de", "2020-04", "--ate", "2020-04"]) == 2
    assert "--reorganizar --executar" in capsys.readouterr().err
    assert lh.main(["--reorganizar"]) == 0
    assert "(simulação)" in capsys.readouterr().out
    assert lh.main(["--reorganizar", "--executar"]) == 0
    assert (historico / "2020" / "2020-03").is_dir()
    with pytest.raises(SystemExit):
        lh.main(["--reorganizar", "--de", "2020-03"])


def test_conflito_de_manifesto_mostra_os_campos_diferentes(tmp_path):
    from dbf_cepe import captura

    historico = tmp_path / "historico"
    nova, antiga = historico / "2010" / "2010-09", historico / "2010-09"
    nova.mkdir(parents=True)
    antiga.mkdir()
    comum = {"url": "u", "tamanho": 10, "sha256": "abc"}
    captura.gravar_manifesto(nova, {"20100901_PoderExecutivo.pdf": {**comum, "baixado_em": "2026-11-10",
                                                                    "processado_em": "2026-11-12", "processamento": {}}})
    captura.gravar_manifesto(antiga, {"20100901_PoderExecutivo.pdf": {**comum, "baixado_em": "2026-11-17"}})
    (juncao,) = lh.reorganizar(historico)
    texto = "\n".join(juncao.conflitos)
    assert "baixado_em (1)" in texto and "processado_em (1)" in texto
    assert "mesmo sha256 (mesmo arquivo) nos dois: 1 de 1" in texto
    assert "baixado_em: pasta solta 2026-11-17 | pasta do ano 2026-11-10" in texto
    assert "processamento: só em pasta do ano" in texto
