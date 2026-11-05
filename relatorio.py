"""Modo `--separar` (relatório dos cadernos): lista os atos de cada caderno, o tipo, o programa
da DBF e se já existem na base; com `--gravar`, grava os inéditos em pendentes; com
`--comparar`, compara o texto com o PDF da base. Chamado por `main.py`."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import comparacao
from config import (
    Configuracao,
    ErroConfiguracao,
)
from duplicidade import PastaDbfIndisponivel, carregar_indice, chave_nome, ja_existe
from exportador import ErroExportacao, gravar_pdf_do_ato
from identificador import identificar
from programas import cita_programa_so_no_corpo, em_escopo, programas_do_ato
from separador import AtoSeparado, ErroSeparacao, separar_caderno

log = logging.getLogger("pipeline")


@dataclass
class ResumoRelatorio:
    cadernos: int = 0
    atos: int = 0
    avisos: int = 0  # pontos de atenção (cabeçalho suspeito, fecho ausente, repetido...)
    falhas: int = 0  # atos que não puderam ser gravados em dados/pendentes
    situacoes: Counter[str] = field(default_factory=Counter)
    comparacoes: list[tuple[str, comparacao.Comparacao]] = field(default_factory=list)


def _carregar_indice_do_relatorio(
    cfg: Configuracao, gravar: bool, permitir_pasta_vazia: bool = False
) -> set[str] | None:
    """Índice da base da DBF. No relatório puro, a falta dela só gera aviso; ao
    gravar é erro, porque sem ela um ato que a DBF já tem seria gravado."""
    if cfg.pasta_dbf is None:
        if gravar:
            raise ErroConfiguracao(
                "PASTA_DBF não está definida: sem a base da DBF não dá para saber "
                "quais atos já existem, então nada será gravado."
            )
        log.warning("PASTA_DBF não definida; não dá para dizer o que já existe.")
        return None
    try:
        return carregar_indice(cfg.pasta_dbf, permitir_pasta_vazia)
    except PastaDbfIndisponivel as erro:
        if gravar:
            raise
        log.warning("Base da DBF indisponível; não dá para dizer o que já existe: %s", erro)
        return None


def _relatar_caderno(
    cfg: Configuracao,
    caderno: Path,
    indice: set[str] | None,
    gravar: bool,
    resumo: ResumoRelatorio,
    comparar: bool = False,
) -> None:
    resultado = separar_caderno(caderno)
    log.info(
        "%s: %d página(s), %d ato(s) encontrado(s).",
        caderno.name, resultado.paginas, len(resultado.atos),
    )
    resumo.cadernos += 1
    resumo.atos += len(resultado.atos)
    gravados: set[str] = set()
    for ato in resultado.atos:
        nome = ato.especie.nome_arquivo(ato.numero)
        ident = identificar(ato.texto)
        tipo = ident.tipo.nome if ident.tipo else "tipo não reconhecido"
        programas = programas_do_ato(ato.texto, ato.especie)
        paginas = (
            f"p. {ato.pagina_inicial}"
            if ato.pagina_inicial == ato.pagina_final
            else f"p. {ato.pagina_inicial}-{ato.pagina_final}"
        )
        if not em_escopo(ato.especie, programas):
            situacao = "fora do escopo da DBF"
            if cita_programa_so_no_corpo(ato.texto, ato.especie):
                log.warning(
                    "%s | AVISO: cita um programa da DBF só no corpo do texto; "
                    "confira se não é um ato da DBF", nome,
                )
                resumo.avisos += 1
        elif indice is not None and ja_existe(indice, nome):
            situacao = "já existe na DBF"
            if comparar and cfg.pasta_dbf is not None:
                situacao += _comparar_com_a_base(cfg.pasta_dbf / nome, nome, ato, resumo)
        elif indice is None:
            situacao = "base da DBF não consultada"
        elif gravar:
            situacao = _gravar_inedito(cfg, ato, nome, gravados, resumo)
        else:
            situacao = "INÉDITO"
        resumo.situacoes[situacao.split(" (")[0]] += 1
        programa = "+".join(p.sigla for p in programas) or "-"
        log.info("%s | %s | %s | %s | %s", nome, paginas, tipo, programa, situacao)
        for aviso in ato.avisos:
            log.warning("%s | AVISO: %s", nome, aviso)
        resumo.avisos += bool(ato.avisos)
    for aviso in resultado.avisos:
        log.warning("AVISO: %s", aviso)
    resumo.avisos += len(resultado.avisos)


def _descrever(comparado: comparacao.Comparacao) -> str:
    if not comparado.lido:
        return f" (PDF da base não lido: {comparado.detalhe})"
    if comparado.identico:
        return " (texto idêntico ao da base)"
    return (
        f" (texto difere da base: similaridade {comparado.similaridade:.2%}, "
        f"{comparado.so_no_ato} elemento(s) só no caderno, {comparado.so_na_base} só na base)"
    )


def _comparar_com_a_base(
    pdf_base: Path, nome: str, ato: AtoSeparado, resumo: ResumoRelatorio
) -> str:
    comparado = comparacao.comparar_com_base(ato.texto, pdf_base)
    resumo.comparacoes.append((nome, comparado))
    for trecho in comparado.trechos:
        log.info("%s | diferença: %s", nome, trecho)
    if comparado.total_trechos > len(comparado.trechos):
        log.info(
            "%s | ... e mais %d diferença(s)", nome, comparado.total_trechos - len(comparado.trechos)
        )
    return _descrever(comparado)


def _resumir_comparacoes(resumo: ResumoRelatorio) -> None:
    if not resumo.comparacoes:
        return
    lidas = [(n, c) for n, c in resumo.comparacoes if c.lido]
    log.info("Comparação com a base (%d decreto(s) que já existem):", len(resumo.comparacoes))
    log.info("  %3d  texto idêntico", sum(c.identico for _, c in lidas))
    faixas = ((0.99, "similaridade de 99% a menos de 100%"), (0.95, "similaridade de 95% a menos de 99%"), (0.0, "similaridade abaixo de 95%"))
    restantes = [(n, c) for n, c in lidas if not c.identico]
    for piso, titulo in faixas:
        da_faixa = [(n, c) for n, c in restantes if c.similaridade >= piso]
        log.info("  %3d  %s", len(da_faixa), titulo)
        restantes = [(n, c) for n, c in restantes if c.similaridade < piso]
    log.info("  %3d  PDF da base não lido", len(resumo.comparacoes) - len(lidas))
    diferentes = sorted((par for par in lidas if not par[1].identico), key=lambda par: par[1].similaridade)
    for nome, comparado in diferentes[:20]:
        log.info("  menos parecidos: %s (%.2f%%)", nome, comparado.similaridade * 100)


def _gravar_inedito(
    cfg: Configuracao, ato: AtoSeparado, nome: str, gravados: set[str], resumo: ResumoRelatorio
) -> str:
    destino = cfg.pasta_temp / nome
    if chave_nome(nome) in gravados:
        log.warning("%s | AVISO: repetido neste caderno; só a primeira ocorrência foi gravada", nome)
        resumo.avisos += 1
        return "INÉDITO (repetido no caderno, não gravado)"
    if destino.exists():
        return "INÉDITO (já está em pendentes)"
    try:
        gravar_pdf_do_ato(ato, destino)
    except ErroExportacao as erro:
        log.error("%s | falha ao gravar em pendentes: %s", nome, erro)
        resumo.falhas += 1
        return "INÉDITO (FALHA ao gravar)"
    gravados.add(chave_nome(nome))
    return "INÉDITO (gravado em pendentes)"


def relatorio_dos_cadernos(
    cfg: Configuracao,
    caminho: Path,
    gravar: bool = False,
    permitir_pasta_vazia: bool = False,
    comparar: bool = False,
) -> ResumoRelatorio:
    """Lista os atos de um caderno (ou de todos os PDFs de uma pasta) e o
    que o pipeline faz com cada um.

    Sem `gravar`, só lê: nada é gravado, movido ou apagado. Com `gravar`, cada
    ato de programa da DBF que a base ainda não tem vira um PDF em
    `cfg.pasta_temp` (a pasta pendentes), sem sobrescrever nada.
    """
    if caminho.is_dir():
        cadernos = sorted(p for p in caminho.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
        if not cadernos:
            raise ErroSeparacao(f"nenhum PDF na pasta {caminho}")
    else:
        cadernos = [caminho]
    indice = _carregar_indice_do_relatorio(cfg, gravar, permitir_pasta_vazia)
    resumo = ResumoRelatorio()
    for caderno in cadernos:
        _relatar_caderno(cfg, caderno, indice, gravar, resumo, comparar)
    log.info("Total: %d caderno(s), %d ato(s).", resumo.cadernos, resumo.atos)
    for situacao, quantidade in resumo.situacoes.most_common():
        log.info("  %3d  %s", quantidade, situacao)
    _resumir_comparacoes(resumo)
    if resumo.falhas:
        log.error("%d ato(s) não puderam ser gravados em pendentes.", resumo.falhas)
    return resumo
