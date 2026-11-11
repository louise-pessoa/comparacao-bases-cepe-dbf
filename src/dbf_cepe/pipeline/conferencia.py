"""Modo `--conferir`: revisa os PDFs de `dados/conferir duplicata` pelo terminal.

Para cada PDF (uma publicação do Diário cujo número a base já tinha, com texto
diferente), mostra as diferenças para o arquivo da base e pergunta o que fazer:

    (d) usar o texto do Diário: o arquivo da base é copiado para a pasta de cópias de
        segurança (`dados/substituidos`, com data e hora no nome) e substituído pelo PDF
        do Diário, com conferência e devolução do antigo se algo falhar
        (`armazenamento.substituir_na_base`); o PDF sai da conferência;
    (b) manter a base: o PDF sai da conferência e a base não é tocada;
    (v) ver todas as diferenças;
    (p) pular: o PDF fica para a próxima vez.

A decisão vai para o registro de publicações (`decisao`, `decidido_em`). Com (d), a
publicação passa a "gravado" com o nome do arquivo da base: daí em diante o lote sabe a
data dele (republicações futuras seguem a regra normal). Com `--simular`, só mostra as
diferenças de todos, sem perguntar nem mexer em nada.

Uma queda entre a troca na base e a gravação do registro deixa o PDF na conferência: na
próxima vez ele aparece com texto idêntico ao da base e basta escolher de novo.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dbf_cepe.config import Configuracao, ErroConfiguracao
from dbf_cepe.leitura.identificador import StatusIdentificacao, identificar
from dbf_cepe.leitura.reader import StatusLeitura, ler_pdf
from dbf_cepe.pipeline import armazenamento
from dbf_cepe.pipeline.comparacao import Comparacao, comparar_textos
from dbf_cepe.pipeline.duplicidade import chave_nome, listar_nomes
from dbf_cepe.pipeline.registro import CONFERIR, GRAVADO, Entrada, ErroRegistro, Registro
from dbf_cepe.regras.especies import Especie

log = logging.getLogger("pipeline")

DIARIO = "usar o texto do Diário"
BASE = "manter a base"
PULADO = "pulado"
ILEGIVEL = "não identificado"

MANTIDA_A_BASE = "mantida a base"


@dataclass
class Item:
    pdf: Path
    especie: Especie | None = None
    numero: str | None = None
    texto: str = ""
    nome_base: str | None = None  # arquivo deste número na base (None: a base não tem)
    comparacao: Comparacao | None = None  # None: sem arquivo na base
    entrada: Entrada | None = None  # a publicação no registro
    erro: str = ""


def _arquivo_da_base(especie: Especie, numero: str, nomes: set[str]) -> str | None:
    padrao = especie.nome_arquivo(numero)
    por_chave = {chave_nome(n): n for n in nomes}
    if chave_nome(padrao) in por_chave:
        return por_chave[chave_nome(padrao)]
    familia = sorted(n for n in nomes if especie.e_da_familia(n, numero))
    return familia[0] if familia else None


def preparar(pdf: Path, pasta_dbf: Path, nomes_na_base: set[str], registro: Registro) -> Item:
    item = Item(pdf, entrada=registro.entrada_da_conferencia(pdf.name))
    leitura = ler_pdf(pdf)
    if leitura.status is not StatusLeitura.OK:
        item.erro = f"o PDF não pôde ser lido ({leitura.status.value})"
        return item
    ident = identificar(leitura.texto)
    if ident.status is not StatusIdentificacao.IDENTIFICADO or ident.especie is None or ident.numero is None:
        item.erro = f"o PDF não se identifica como um ato ({ident.detalhe})"
        return item
    item.especie, item.numero, item.texto = ident.especie, ident.numero, leitura.texto
    item.nome_base = _arquivo_da_base(ident.especie, ident.numero, nomes_na_base)
    if item.nome_base is not None:
        base = ler_pdf(pasta_dbf / item.nome_base)
        if base.status is StatusLeitura.OK:
            item.comparacao = comparar_textos(leitura.texto, base.texto, limite=None)
        else:
            item.comparacao = Comparacao(lido=False, detalhe=f"{base.status.value}: {base.detalhe}")
    return item


def _trecho(trecho: str) -> str:
    return trecho.replace("[caderno:", "[Diário:")


def descrever(item: Item, todas: bool = False, mostrar: int = 3) -> list[str]:
    origem = (
        f"Diário de {item.entrada.publicacao}" if item.entrada else "publicação sem registro"
    )
    linhas = [f"{item.pdf.name} — {origem} × base ({item.nome_base or 'sem arquivo na base'})"]
    c = item.comparacao
    if c is None:
        linhas.append("      a base não tem arquivo deste número")
    elif not c.lido:
        linhas.append(f"      o PDF da base não tem texto para comparar ({c.detalhe}); o do Diário está no PDF da conferência")
    elif c.identico:
        linhas.append("      texto idêntico ao da base")
    else:
        linhas.append(
            f"      similaridade {c.similaridade:.2%}: {c.total_trechos} diferença(s); "
            f"{c.so_no_ato} elemento(s) só no Diário, {c.so_na_base} só na base"
        )
        trechos = c.trechos if todas else c.trechos[:mostrar]
        linhas += [f"      {_trecho(t)}" for t in trechos]
        if len(trechos) < len(c.trechos):
            linhas.append(f"      ... e mais {len(c.trechos) - len(trechos)} (v para ver todas)")
    return linhas


def _opcoes(item: Item) -> dict[str, str]:
    opcoes = {}
    if item.nome_base is not None:
        opcoes["d"] = f"{DIARIO} — substitui {item.nome_base} na base (cópia em dados/substituidos)"
    opcoes["b"] = f"{BASE} — apaga o PDF da conferência"
    if item.comparacao is not None and item.comparacao.lido and item.comparacao.trechos:
        opcoes["v"] = "ver todas as diferenças"
    opcoes["p"] = "pular por enquanto"
    return opcoes


def _decidir(cfg: Configuracao, registro: Registro, item: Item, escolha: str) -> str:
    assert cfg.pasta_dbf is not None
    if escolha == "d":
        assert item.nome_base is not None and item.especie is not None and item.numero is not None
        copia = armazenamento.substituir_na_base(
            item.pdf, item.nome_base, cfg.pasta_dbf, cfg.substituidos, item.especie.nome_arquivo(item.numero)
        )
        decisao = f"substituída pelo Diário (cópia do arquivo antigo: {copia.name})"
        if item.entrada is not None:
            registro.decidir(item.entrada, GRAVADO, item.nome_base, decisao)
            registro.salvar()
        armazenamento.descartar_se_existir(item.pdf)
        log.info("%s | %s | %s", item.pdf.name, DIARIO, decisao)
        return DIARIO
    if item.entrada is not None:
        registro.decidir(item.entrada, CONFERIR, item.entrada.arquivo, MANTIDA_A_BASE)
        registro.salvar()
    armazenamento.descartar_se_existir(item.pdf)
    log.info("%s | %s", item.pdf.name, BASE)
    return BASE


def conferir(
    cfg: Configuracao,
    simular: bool = False,
    perguntar: Callable[[str], str] = input,
    escrever: Callable[[str], None] = print,
) -> Counter[str]:
    """Percorre a pasta de conferência; devolve quantos ficaram com cada decisão."""
    if cfg.pasta_dbf is None:
        raise ErroConfiguracao("PASTA_DBF não está definida: sem a base não há o que conferir.")
    registro = Registro.carregar(cfg.registro)  # ilegível: ErroRegistro, nada é tocado
    if registro.operacao_em_andamento is not None and not simular:
        raise ErroRegistro(
            "O registro tem uma operação que não terminou; rode o lote (python main.py) para "
            "desfazê-la ou concluí-la antes de conferir."
        )
    pdfs = sorted(p for p in cfg.conferir_duplicata.glob("*.pdf")) if cfg.conferir_duplicata.is_dir() else []
    resumo: Counter[str] = Counter()
    if not pdfs:
        escrever(f"Nada para conferir em {cfg.conferir_duplicata}.")
        return resumo
    nomes = listar_nomes(cfg.pasta_dbf)
    modo = " [SIMULAÇÃO: só mostra as diferenças]" if simular else ""
    escrever(f"{len(pdfs)} PDF(s) para conferir{modo}.")
    for posicao, pdf in enumerate(pdfs, start=1):
        item = preparar(pdf, cfg.pasta_dbf, nomes, registro)
        escrever("")
        escrever(f"[{posicao}/{len(pdfs)}] " + (f"{pdf.name}: {item.erro}" if item.erro else descrever(item)[0]))
        if item.erro:
            resumo[ILEGIVEL] += 1
            continue
        for linha in descrever(item)[1:]:
            escrever(linha)
        if item.entrada is None:
            escrever("      aviso: esta publicação não está no registro; a decisão não será anotada")
        if simular:
            resumo[PULADO] += 1
            continue
        resumo[_perguntar(cfg, registro, item, perguntar, escrever)] += 1
    escrever("")
    escrever("Resumo: " + ", ".join(f"{n} {decisao}" for decisao, n in resumo.most_common()))
    return resumo


def _perguntar(
    cfg: Configuracao, registro: Registro, item: Item,
    perguntar: Callable[[str], str], escrever: Callable[[str], None],
) -> str:
    opcoes = _opcoes(item)
    for letra, texto in opcoes.items():
        escrever(f"  ({letra}) {texto}")
    while True:
        try:
            escolha = perguntar("> ").strip().lower()
        except EOFError:
            return PULADO
        if escolha in ("", "p"):
            return PULADO
        if escolha == "v" and "v" in opcoes:
            for linha in descrever(item, todas=True)[2:]:
                escrever(linha)
            continue
        if escolha in ("d", "b") and escolha in opcoes:
            try:
                return _decidir(cfg, registro, item, escolha)
            except armazenamento.ErroArmazenamento as erro:
                escrever(f"  ERRO: {erro}. O PDF continua na conferência.")
                log.error("%s | falha na substituição: %s", item.pdf.name, erro)
                return PULADO
        escrever(f"  responda {', '.join(opcoes)}")
