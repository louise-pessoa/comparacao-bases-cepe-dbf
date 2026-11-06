"""Modo lote (etapas 2 a 5): processa os PDFs da pasta temporária (PASTA_TEMP), um ato por
PDF: identificação -> duplicidade/republicação -> normalização -> gravação na base da DBF.
Chamado por `main.py` (python main.py [--simular]).

Duas passagens:
  1. lê a publicação (data do caderno, caderno, página) de cada PDF de pendentes, que
     vem nos metadados gravados por `--separar --gravar` (ver publicacao.py), e ordena
     o lote cronologicamente (data; no mesmo dia, normal antes de Extra; página);
  2. processa nessa ordem. Para um PDF com publicação, a etapa 3 segue a regra de
     republicação (republicacao.py) com o registro de publicações (registro.py): o
     nome final é decidido antes de gerar e gravar; arquivos da base podem ser
     renomeados para abrir o lugar de uma publicação mais antiga (com desfazer).
     Um PDF sem publicação (posto à mão em pendentes) segue a regra antiga: nome
     padrão, descartado se a base já o tiver.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from dbf_cepe.config import (
    Configuracao,
    ErroConfiguracao,
    validar_configuracao,
)
from dbf_cepe.leitura.identificador import Identificacao, StatusIdentificacao, identificar
from dbf_cepe.leitura.reader import StatusLeitura, ler_pdf
from dbf_cepe.pipeline import armazenamento, comparacao, normalizador
from dbf_cepe.pipeline.armazenamento import (
    ArquivoJaExiste,
    ErroArmazenamento,
    GravacaoDivergente,
    RenomeacaoNaoDesfeita,
)
from dbf_cepe.pipeline.duplicidade import chave_nome, ja_existe, listar_nomes
from dbf_cepe.pipeline.normalizador import ErroNormalizacao
from dbf_cepe.pipeline.publicacao import Publicacao, hash_do_texto, ler_do_pdf
from dbf_cepe.pipeline.registro import CONFERIR, DUPLICATA, GRAVADO, Entrada, ErroRegistro, Registro
from dbf_cepe.pipeline.republicacao import Acao, Plano, planejar

log = logging.getLogger("pipeline")


class Destino(Enum):
    DESCARTADO_OUTRA_ESPECIE = "descartado (outra espécie)"
    DESCARTADO_DUPLICADO = "descartado (já existe na DBF)"
    ENVIADO_OCR = "enviado para leitura tesseract"
    ENVIADO_ERROS = "enviado para erros (revisão manual)"
    ENVIADO_CONFERIR = "enviado para conferir duplicata (pasta local)"
    FALHA_AO_GRAVAR = "mantido na pasta temporária (falha ao gravar na DBF)"
    CONCLUIDO = "normalizado e armazenado na DBF"


@dataclass
class EstadoDoLote:
    """O que o lote sabe da base e do registro; atualizado a cada gravação (também
    na simulação, só em memória, para o PDF seguinte ver o efeito do anterior)."""

    registro: Registro
    nomes_na_base: set[str]  # nomes reais (para renomear e achar republicações)
    vistos_no_lote: set[str] = field(default_factory=set)  # PDFs sem publicação

    @property
    def indice(self) -> set[str]:
        return {chave_nome(nome) for nome in self.nomes_na_base}

    def renomear(self, de: str, para: str) -> None:
        self.nomes_na_base = {n for n in self.nomes_na_base if chave_nome(n) != chave_nome(de)} | {para}


def _mover(
    pdf: Path, pasta: Path, destino: Destino, motivo: str, simular: bool
) -> Destino:
    novo = armazenamento.mover_sem_sobrescrever(pdf, pasta, simular)
    log.info("%s | %s | %s | %s", pdf.name, destino.value, motivo, novo.name)
    return destino


def _descartar(pdf: Path, destino: Destino, motivo: str, simular: bool) -> Destino:
    armazenamento.descartar(pdf, simular)
    log.info("%s | %s | %s", pdf.name, destino.value, motivo)
    return destino


def _salvar(registro: Registro, simular: bool) -> None:
    if not simular:
        registro.salvar()


def processar_arquivo(
    pdf: Path,
    cfg: Configuracao,
    estado: EstadoDoLote,
    simular: bool,
    publicacao: Publicacao | None = None,
) -> Destino:
    """Decide o destino de um PDF. As etapas são marcadas nos comentários."""
    # --- Etapa 2: leitura -------------------------------------------------
    leitura = ler_pdf(pdf)
    if leitura.status is StatusLeitura.CORROMPIDO:
        return _mover(
            pdf, cfg.pasta_erros, Destino.ENVIADO_ERROS, leitura.detalhe, simular
        )
    if leitura.status is StatusLeitura.IMAGEM:
        return _mover(pdf, cfg.pasta_ocr, Destino.ENVIADO_OCR, leitura.detalhe, simular)

    # --- Etapa 2: identificação -------------------------------------------
    ident = identificar(leitura.texto)
    if ident.status is StatusIdentificacao.OUTRA_ESPECIE:
        return _descartar(pdf, Destino.DESCARTADO_OUTRA_ESPECIE, ident.detalhe, simular)
    if ident.status is not StatusIdentificacao.IDENTIFICADO:
        return _mover(
            pdf, cfg.pasta_erros, Destino.ENVIADO_ERROS, ident.detalhe, simular
        )

    if publicacao is not None:
        return _com_publicacao(pdf, cfg, estado, simular, ident, leitura.texto, publicacao)

    nome = ident.especie.nome_arquivo(ident.numero)

    # --- Etapa 3: duplicidade (PDF sem publicação: regra antiga) ------------
    # (o "visto no lote" vem antes: o primeiro já entrou em `estado.indice`)
    if chave_nome(nome) in estado.vistos_no_lote:
        return _mover(
            pdf,
            cfg.pasta_erros,
            Destino.ENVIADO_ERROS,
            f"{nome} aparece mais de uma vez no lote",
            simular,
        )
    if ja_existe(estado.indice, nome):
        return _descartar(pdf, Destino.DESCARTADO_DUPLICADO, nome, simular)
    if ident.tipo is None:
        return _sem_tipo(pdf, cfg, nome, ident, simular)
    estado.vistos_no_lote.add(chave_nome(nome))
    return _normalizar_e_gravar(pdf, cfg, estado, simular, ident, nome, ())


def _sem_tipo(pdf: Path, cfg: Configuracao, nome: str, ident: Identificacao, simular: bool) -> Destino:
    return _mover(
        pdf,
        cfg.pasta_erros,
        Destino.ENVIADO_ERROS,
        f"{nome}: tipo do ato não reconhecido ({ident.detalhe})",
        simular,
    )


def _com_publicacao(
    pdf: Path,
    cfg: Configuracao,
    estado: EstadoDoLote,
    simular: bool,
    ident: Identificacao,
    texto: str,
    publicacao: Publicacao,
) -> Destino:
    """Etapa 3 com a regra de republicação. Uma republicação nunca é descartada
    como duplicata só por ter o mesmo número de um arquivo da base."""
    especie, numero = ident.especie, ident.numero
    assert especie is not None and numero is not None
    hash_texto = hash_do_texto(texto)
    pasta_dbf = cfg.pasta_dbf
    assert pasta_dbf is not None

    def identico(nome: str) -> bool:
        return comparacao.comparar_com_base(texto, pasta_dbf / nome).identico

    plano = planejar(
        especie, numero, publicacao, hash_texto, estado.registro, estado.nomes_na_base, identico
    )
    for aviso in plano.avisos:
        log.warning("%s | AVISO: %s", pdf.name, aviso)

    def entrada(situacao: str, arquivo: str | None) -> Entrada:
        return Entrada(especie.nome, numero, None, publicacao, hash_texto, arquivo, situacao)

    if plano.acao is Acao.JA_REGISTRADA:
        return _descartar(pdf, Destino.DESCARTADO_DUPLICADO, plano.motivo, simular)
    if plano.acao is Acao.DUPLICATA:
        estado.registro.adicionar(entrada(DUPLICATA, None))
        _salvar(estado.registro, simular)
        return _descartar(pdf, Destino.DESCARTADO_DUPLICADO, plano.motivo, simular)
    if ident.tipo is None:
        return _sem_tipo(pdf, cfg, especie.nome_arquivo(numero), ident, simular)
    if plano.acao is Acao.CONFERIR:
        return _conferir(pdf, cfg, estado, simular, ident, plano, entrada)
    return _normalizar_e_gravar(pdf, cfg, estado, simular, ident, plano.nome_final or "", plano.renomeacoes,
                                plano=plano, nova_entrada=entrada(GRAVADO, plano.nome_final))


def _normalizar(pdf: Path, cfg: Configuracao, ident: Identificacao, nome: str, simular: bool) -> Path:
    return normalizador.gerar_pdf_normalizado(pdf, ident, cfg.pasta_normalizados, simular, nome_saida=nome)


def _conferir(
    pdf: Path,
    cfg: Configuracao,
    estado: EstadoDoLote,
    simular: bool,
    ident: Identificacao,
    plano: Plano,
    entrada,
) -> Destino:
    """Arquivo da base sem registro e com texto diferente: gera o PDF normalizado com
    "(conferir duplicata)" no nome e o põe na pasta LOCAL de conferência. Nada na base."""
    nome = plano.nome_final or ""
    try:
        gerado = _normalizar(pdf, cfg, ident, nome, simular)
    except ErroNormalizacao as erro:
        return _mover(pdf, cfg.pasta_erros, Destino.ENVIADO_ERROS, f"{nome}: normalização falhou ({erro})", simular)
    final = armazenamento.mover_sem_sobrescrever(gerado, cfg.conferir_duplicata, simular)
    estado.registro.adicionar(entrada(CONFERIR, final.name))
    _salvar(estado.registro, simular)
    armazenamento.descartar(pdf, simular)
    log.info("%s | %s | %s | %s", pdf.name, Destino.ENVIADO_CONFERIR.value, plano.motivo, final.name)
    return Destino.ENVIADO_CONFERIR


def _normalizar_e_gravar(
    pdf: Path,
    cfg: Configuracao,
    estado: EstadoDoLote,
    simular: bool,
    ident: Identificacao,
    nome: str,
    renomeacoes: tuple[tuple[str, str], ...],
    plano: Plano | None = None,
    nova_entrada: Entrada | None = None,
) -> Destino:
    especie, numero = ident.especie, ident.numero
    assert especie is not None and numero is not None and cfg.pasta_dbf is not None
    nome_no_texto = especie.nome_arquivo(numero)

    # --- Etapa 4: normalização --------------------------------------------
    try:
        pdf_gerado = _normalizar(pdf, cfg, ident, nome, simular)
    except ErroNormalizacao as erro:
        return _mover(
            pdf,
            cfg.pasta_erros,
            Destino.ENVIADO_ERROS,
            f"{nome}: normalização falhou ({erro})",
            simular,
        )

    # --- Etapa 5: armazenamento -------------------------------------------
    registro = estado.registro
    try:
        if simular:
            if not renomeacoes:  # com renomeações, o nome só fica livre depois delas
                armazenamento.armazenar(pdf_gerado, nome, cfg.pasta_dbf, pdf, True, nome_no_texto)
        elif nova_entrada is None:
            armazenamento.armazenar(pdf_gerado, nome, cfg.pasta_dbf, pdf, False, nome_no_texto)
        else:
            # Anota a operação ANTES de tocar na base: se o programa cair no meio, a
            # próxima execução a desfaz ou conclui (armazenamento.recuperar_operacao).
            registro.operacao_em_andamento = {
                "especie": especie.nome,
                "numero": numero,
                "ano": None,
                "renomeacoes": [list(par) for par in renomeacoes],
                "gravar": nome,
                "nova_entrada": nova_entrada.para_dict(),
            }
            registro.salvar()
            armazenamento.armazenar_com_renomeacoes(
                pdf_gerado, nome, cfg.pasta_dbf, pdf, renomeacoes, nome_no_texto
            )
    except RenomeacaoNaoDesfeita as erro:
        # O registro continua com a operação anotada: a próxima execução tenta desfazer.
        log.error("%s | %s | %s: %s", pdf.name, Destino.FALHA_AO_GRAVAR.value, nome, erro)
        return Destino.FALHA_AO_GRAVAR
    except ArquivoJaExiste:
        _encerrar_operacao(registro, simular)
        # Outra pessoa salvou o mesmo nome depois da etapa 3.
        armazenamento.descartar_se_existir(pdf_gerado)
        if nova_entrada is not None:
            # Com publicação, o nome tomado não prova duplicata: o PDF fica para o próximo lote.
            log.error("%s | %s | %s apareceu na DBF durante o lote", pdf.name, Destino.FALHA_AO_GRAVAR.value, nome)
            return Destino.FALHA_AO_GRAVAR
        return _descartar(
            pdf, Destino.DESCARTADO_DUPLICADO, f"{nome} (surgiu na DBF durante o lote)", simular
        )
    except GravacaoDivergente as erro:
        _encerrar_operacao(registro, simular)
        return _mover(
            pdf,
            cfg.pasta_erros,
            Destino.ENVIADO_ERROS,
            f"{nome}: gravação na DBF não confere ({erro})",
            simular,
        )
    except ErroArmazenamento as erro:
        _encerrar_operacao(registro, simular)
        # Falha de E/S (drive indisponível, sem permissão...): o original fica em
        # pendentes e o próximo lote tenta de novo.
        log.error("%s | %s | %s: %s", pdf.name, Destino.FALHA_AO_GRAVAR.value, nome, erro)
        return Destino.FALHA_AO_GRAVAR

    # Gravado: atualiza o que se sabe da base e o registro.
    for de, para in renomeacoes:
        estado.renomear(de, para)
        log.info("%s | renomeado na DBF: %s -> %s", pdf.name, de, para)
        if nova_entrada is not None:
            registro.renomear(especie.nome, numero, None, de, para)
    estado.nomes_na_base.add(nome)
    if nova_entrada is not None:
        registro.adicionar(nova_entrada)
        registro.operacao_em_andamento = None
        _salvar(registro, simular)
    tipo = ident.tipo.nome if ident.tipo else "-"
    extra = f"; {plano.motivo}" if plano is not None and plano.republicacao else ""
    log.info("%s | %s | %s (%s)%s", pdf.name, Destino.CONCLUIDO.value, nome, tipo, extra)
    return Destino.CONCLUIDO


def _encerrar_operacao(registro: Registro, simular: bool) -> None:
    """A gravação falhou e as renomeações foram desfeitas: a operação não existe mais."""
    if registro.operacao_em_andamento is not None:
        registro.operacao_em_andamento = None
        _salvar(registro, simular)


def listar_pdfs(pasta: Path) -> list[Path]:
    if not pasta.is_dir():
        return []
    return sorted(
        p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"
    )


def ordenar_por_publicacao(pdfs: list[Path]) -> list[tuple[Path, Publicacao | None]]:
    """Passagem 1: a publicação de cada PDF e a ordem cronológica do lote. Os PDFs
    sem publicação vêm depois, em ordem de nome (como antes)."""
    com_data = [(pdf, ler_do_pdf(pdf)) for pdf in pdfs]
    return sorted(
        com_data,
        key=lambda par: (0, par[1].chave, par[0].name) if par[1] else (1, (), par[0].name),  # type: ignore[union-attr]
    )


def executar(
    cfg: Configuracao, simular: bool = False, permitir_pasta_vazia: bool = False
) -> Counter[Destino]:
    """Processa o lote e devolve a contagem por destino."""
    if cfg.pasta_dbf is None:
        raise ErroConfiguracao(
            "PASTA_DBF não está definida. Copie .env.example para .env e "
            "informe a pasta da base da DBF."
        )

    # Defesa em profundidade: `executar` também é chamada fora do `main`, e é
    # ela que descarta arquivos.
    validar_configuracao(cfg)

    inicio = time.perf_counter()
    nomes_na_base = listar_nomes(cfg.pasta_dbf, permitir_pasta_vazia)
    registro = Registro.carregar(cfg.registro)  # ilegível: ErroRegistro, nada é tocado
    if registro.operacao_em_andamento is not None:
        if simular:
            raise ErroRegistro(
                "O registro de publicações tem uma operação que não terminou (queda na execução "
                "anterior). Rode sem --simular para desfazê-la ou concluí-la antes."
            )
        log.warning("Operação interrompida na execução anterior: %s",
                    armazenamento.recuperar_operacao(registro, cfg.pasta_dbf))
        nomes_na_base = listar_nomes(cfg.pasta_dbf, permitir_pasta_vazia)
    pdfs = ordenar_por_publicacao(listar_pdfs(cfg.pasta_temp))
    modo = " [SIMULAÇÃO: nada será movido ou apagado]" if simular else ""
    log.info(
        "Lote iniciado%s: %d PDF(s) em %s (%d com data de publicação); %d arquivo(s) na base da DBF.",
        modo,
        len(pdfs),
        cfg.pasta_temp,
        sum(1 for _, publicacao in pdfs if publicacao),
        len(nomes_na_base),
    )

    resumo: Counter[Destino] = Counter()
    estado = EstadoDoLote(registro, set(nomes_na_base))
    for pdf, publicacao in pdfs:
        try:
            destino = processar_arquivo(pdf, cfg, estado, simular, publicacao)
        except ErroRegistro:
            raise  # sem registro confiável, nada mais é gravado
        except Exception as erro:  # uma falha não interrompe o restante do lote
            log.exception("%s | falha inesperada: %s", pdf.name, erro)
            try:
                destino = _mover(
                    pdf, cfg.pasta_erros, Destino.ENVIADO_ERROS, str(erro), simular
                )
            except Exception:
                log.exception("%s | não foi possível isolar o arquivo", pdf.name)
                continue
        resumo[destino] += 1

    log.info("Lote finalizado em %.2f s.", time.perf_counter() - inicio)
    for destino, quantidade in resumo.most_common():
        log.info("  %3d  %s", quantidade, destino.value)
    return resumo
