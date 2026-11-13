"""Modo `--separar` (relatório dos cadernos): lista os atos de cada caderno, o tipo, o programa
da DBF e se já existem na base; com `--gravar`, grava os inéditos em pendentes; com
`--comparar`, compara o texto com o PDF da base. Chamado por `main.py`."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from dbf_cepe.pipeline import comparacao
from dbf_cepe.config import (
    Configuracao,
    ErroConfiguracao,
)
from dbf_cepe.pipeline.duplicidade import PastaDbfIndisponivel, carregar_indice, chave_nome, ja_existe, listar_nomes
from dbf_cepe.pipeline.publicacao import Publicacao, hash_do_texto
from dbf_cepe.pipeline.registro import Registro
from dbf_cepe.pipeline.republicacao import Acao, planejar
from dbf_cepe.pipeline.exportador import ErroExportacao, gravar_pdf_do_ato
from dbf_cepe.leitura.cascata import aplicar_cascata
from dbf_cepe.leitura.identificador import identificar
from dbf_cepe.leitura import criterio
from dbf_cepe.pipeline import limpeza
from dbf_cepe.regras.programas import cita_programa_so_no_corpo, programas_do_ato
from dbf_cepe.leitura.separador import (
    AtoSeparado, ErroSeparacao, ResultadoSeparacao, data_do_caderno, separar_caderno,
)

log = logging.getLogger("pipeline")


@dataclass
class ResumoRelatorio:
    """Acumule contagens, situações e comparações exibidas no relatório da separação."""
    cadernos: int = 0
    atos: int = 0
    avisos: int = 0  # pontos de atenção (cabeçalho suspeito, fecho ausente, repetido...)
    falhas: int = 0  # atos que não puderam ser gravados em dados/pendentes
    situacoes: Counter[str] = field(default_factory=Counter)
    comparacoes: list[tuple[str, comparacao.Comparacao]] = field(default_factory=list)
    # Decretos de programa da DBF que nenhum nível da leitura em cascata aprovou (revisão
    # manual): "Decreto N.pdf (caderno, p. X): motivos".
    reprovados_dbf: list[str] = field(default_factory=list)


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


@dataclass
class ContextoDaGravacao:
    """Para `--gravar` com cadernos datados: o registro de publicações (só lido) e os
    nomes reais da base."""

    registro: Registro
    nomes_na_base: set[str]
    # Textos (hash) de cada número nos cadernos desta execução: dois textos diferentes do
    # mesmo número são sinal de republicação (ver republicacao.planejar).
    textos_no_lote: dict[tuple[str, str], set[str]] = field(default_factory=dict)


def _relatar_caderno(
    cfg: Configuracao,
    caderno: Path,
    indice: set[str] | None,
    gravar: bool,
    resumo: ResumoRelatorio,
    comparar: bool = False,
    contexto: ContextoDaGravacao | None = None,
    resultado: ResultadoSeparacao | None = None,
) -> None:
    # Cada ato passa pelo critério da leitura em cascata; o reprovado em todos os níveis
    # sai com um aviso (revisão manual).
    if resultado is None:
        resultado = _separar(caderno)
    log.info(
        "%s: %d página(s), %d ato(s) encontrado(s).",
        caderno.name, resultado.paginas, len(resultado.atos),
    )
    resumo.cadernos += 1
    resumo.atos += len(resultado.atos)
    gravados: set[str] = set()
    falhas_antes = resumo.falhas
    decretos_dbf: list[dict[str, object]] = []  # para a limpeza dos cadernos (manifesto)
    for ato in resultado.atos:
        nome = ato.especie.nome_arquivo(ato.numero)
        ident = identificar(ato.texto)
        tipo = ident.tipo.nome if ident.tipo else "tipo não reconhecido"
        programas = programas_do_ato(ato.texto, ato.especie)
        e_da_dbf = criterio.da_dbf(ato)
        if ato.nivel_de_leitura is None and e_da_dbf:
            resumo.reprovados_dbf.append(
                f"{nome} ({caderno.name}, p. {ato.pagina_inicial}): {', '.join(ato.reprovacao)}"
            )
        paginas = (
            f"p. {ato.pagina_inicial}"
            if ato.pagina_inicial == ato.pagina_final
            else f"p. {ato.pagina_inicial}-{ato.pagina_final}"
        )
        if not e_da_dbf:
            situacao = "fora do escopo da DBF"
            if criterio.fora_por_ementa_sem_tipo(ato):
                log.warning(
                    "%s | AVISO: cita um programa da DBF só nos considerandos e a ementa não tem tipo "
                    "reconhecido; tratado como fora do escopo, confira", nome,
                )
                resumo.avisos += 1
            elif cita_programa_so_no_corpo(ato.texto, ato.especie):
                log.warning(
                    "%s | AVISO: cita um programa da DBF só no corpo do texto; "
                    "confira se não é um ato da DBF", nome,
                )
                resumo.avisos += 1
        elif gravar and ato.nivel_de_leitura is None:
            # Nenhum nível da leitura em cascata aprovou o texto (cortado ou misturado):
            # não vai para pendentes, para não chegar à base com texto errado.
            situacao = f"REVISÃO MANUAL (reprovado no critério de leitura: {', '.join(ato.reprovacao)}; não gravado)"
        elif gravar and indice is not None and ato.data_publicacao is not None and ato.caderno:
            situacao = _gravar_publicacao(cfg, ato, nome, contexto, resumo)
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
        if e_da_dbf:
            decretos_dbf.append(
                {"numero": ato.numero, "pagina": ato.pagina_inicial, "situacao": situacao.split(" (")[0]}
            )
        programa = "+".join(p.sigla for p in programas) or "-"
        log.info("%s | %s | %s | %s | %s", nome, paginas, tipo, programa, situacao)
        for aviso in ato.avisos:
            log.warning("%s | AVISO: %s", nome, aviso)
        resumo.avisos += bool(ato.avisos)
    for aviso in resultado.avisos:
        log.warning("AVISO: %s", aviso)
    resumo.avisos += len(resultado.avisos)
    if contexto is not None and data_do_caderno(caderno.name) is not None:
        # Só com --gravar (há contexto). O caderno foi processado até o fim: a limpeza (pipeline/limpeza.py) confere isto
        # antes de apagá-lo.
        limpeza.registrar_processamento(
            caderno, len(resultado.atos), decretos_dbf, resumo.falhas - falhas_antes,
            resultado.paginas_digitalizadas,
        )


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


def nome_em_pendentes(nome: str, ato: AtoSeparado) -> str:
    """Nome do PDF intermediário com a publicação: 'Decreto 61055 - 20260715_ResumoDiario p3.pdf'.
    Duas publicações do mesmo número (mesmo caderno ou não) não colidem em pendentes."""
    raiz, _, extensao = nome.rpartition(".")
    caderno = (ato.caderno or "").rpartition(".")[0] or (ato.caderno or "")
    return f"{raiz} - {caderno} p{ato.pagina_inicial}.{extensao}"


def _separar(caderno: Path) -> ResultadoSeparacao:
    return aplicar_cascata(separar_caderno(caderno), caderno)


def _textos_no_lote(resultados: list[ResultadoSeparacao]) -> dict[tuple[str, str], set[str]]:
    textos: dict[tuple[str, str], set[str]] = {}
    for resultado in resultados:
        for ato in resultado.atos:
            textos.setdefault((ato.especie.nome, ato.numero), set()).add(hash_do_texto(ato.texto))
    return textos


def _gravar_publicacao(
    cfg: Configuracao,
    ato: AtoSeparado,
    nome: str,
    contexto: ContextoDaGravacao | None,
    resumo: ResumoRelatorio,
) -> str:
    """`--gravar` de um ato com data de publicação: a regra de republicação decide se ele
    segue para pendentes (o lote decide o nome final de novo, com a base daquele momento)."""
    assert contexto is not None and cfg.pasta_dbf is not None and ato.data_publicacao and ato.caderno
    pasta_dbf = cfg.pasta_dbf
    publicacao = Publicacao(ato.data_publicacao, ato.caderno, ato.pagina_inicial, ato.marcado_como_republicacao)

    def identico(nome_base: str) -> bool | None:
        comparado = comparacao.comparar_com_base(ato.texto, pasta_dbf / nome_base)
        return comparado.identico if comparado.lido else None

    outros_textos = contexto.textos_no_lote.get((ato.especie.nome, ato.numero), set())
    plano = planejar(
        ato.especie, ato.numero, publicacao, hash_do_texto(ato.texto), contexto.registro,
        contexto.nomes_na_base, identico, outra_publicacao_no_lote=len(outros_textos) > 1,
    )
    for aviso in plano.avisos:
        log.warning("%s | AVISO: %s", nome, aviso)
    if plano.acao is Acao.JA_REGISTRADA:
        return "já registrado (mesma publicação)"
    if plano.acao is Acao.DUPLICATA:
        return "já existe na DBF (texto idêntico)"
    if plano.acao is Acao.BASE_IMAGEM:
        return "já existe na DBF (PDF da base é imagem ou ilegível; não comparado)"
    rotulo = (
        "CONFERIR DUPLICATA" if plano.acao is Acao.CONFERIR
        else "REPUBLICAÇÃO" if plano.republicacao
        else "ANTERIOR À DA BASE" if plano.renomeacoes
        else "INÉDITO"
    )
    destino = cfg.pasta_temp / nome_em_pendentes(nome, ato)
    if destino.exists():
        return f"{rotulo} (já está em pendentes)"
    try:
        gravar_pdf_do_ato(ato, destino)
    except ErroExportacao as erro:
        log.error("%s | falha ao gravar em pendentes: %s", nome, erro)
        resumo.falhas += 1
        return f"{rotulo} (FALHA ao gravar)"
    return f"{rotulo} (gravado em pendentes: {plano.motivo})"


def _gravar_inedito(
    cfg: Configuracao, ato: AtoSeparado, nome: str, gravados: set[str], resumo: ResumoRelatorio
) -> str:
    destino = cfg.pasta_temp / nome
    if chave_nome(nome) in gravados:
        log.warning(
            "%s | AVISO: repetido neste caderno, que não tem data no nome (AAAAMMDD_...); "
            "só a primeira ocorrência foi gravada", nome,
        )
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
    contexto = None
    if gravar and cfg.pasta_dbf is not None:
        registro = Registro.carregar(cfg.registro)  # ilegível: ErroRegistro (nada é gravado)
        if registro.operacao_em_andamento is not None:
            log.warning("O registro tem uma operação interrompida; o próximo lote a desfaz ou conclui.")
        contexto = ContextoDaGravacao(registro, listar_nomes(cfg.pasta_dbf, permitir_pasta_vazia))
    resumo = ResumoRelatorio()
    # Primeiro separa todos os cadernos: com --gravar, a decisão de cada ato depende de o
    # mesmo número aparecer com outro texto em outro caderno do lote.
    resultados = [_separar(caderno) for caderno in cadernos]
    if contexto is not None:
        contexto.textos_no_lote = _textos_no_lote(resultados)
    for caderno, resultado in zip(cadernos, resultados):
        _relatar_caderno(cfg, caderno, indice, gravar, resumo, comparar, contexto, resultado)
    log.info("Total: %d caderno(s), %d ato(s).", resumo.cadernos, resumo.atos)
    for situacao, quantidade in resumo.situacoes.most_common():
        log.info("  %3d  %s", quantidade, situacao)
    _resumir_comparacoes(resumo)
    if resumo.falhas:
        log.error("%d ato(s) não puderam ser gravados em pendentes.", resumo.falhas)
    return resumo
