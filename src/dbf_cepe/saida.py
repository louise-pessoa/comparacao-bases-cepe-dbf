"""Formato da saída no terminal e no `pipeline.log`: os mesmos blocos nos dois lugares.

Decisões (27/11/2026):

- **Blocos.** Cada rodada (lote, captura, separação, limpeza, conferência) abre com um título
  entre traços, com o modo e a data e hora; no `.log`, duas linhas em branco antes dele separam
  uma rodada da outra. Dentro dela, cada bloco abre com `---- título ----` e termina com uma
  linha em branco. A rodada termina com `AVISOS E ERROS (n)` (se houver) e `RESUMO`.
- **Prefixo.** No terminal, só a mensagem; no `.log`, `AAAA-MM-DD HH:MM:SS | ` antes. Títulos e
  linhas em branco vão sem prefixo nos dois.
- **Destaque.** `[AVISO]` e `[ERRO]` no começo da linha, nos dois lugares; no terminal, também em
  amarelo e vermelho (sem cor se a saída não for um terminal ou se `NO_COLOR` estiver definida).
- **Avisos de um item** (um ato, um PDF do lote) saem recuados logo abaixo da linha do item, e
  são repetidos no bloco `AVISOS E ERROS` do fim, com o caderno e o item na frente.

Uso:

    with saida.rodada(log, "LOTE"):
        log.info("42 PDF(s) em dados/pendentes")     # cabeçalho da rodada
        saida.bloco(log, "PDFs EM PENDENTES (42)")
        with saida.item():
            saida.aviso(log, "texto do aviso")      # adiado até a linha do item
            saida.linha_do_item(log, "Decreto 1.pdf | concluído")
        saida.fechar(log)                           # AVISOS E ERROS + título RESUMO
        log.info("  40  concluído")
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TextIO

LARGURA = 60  # dos títulos de rodada (centralizados entre traços)
RECUO = "    "
AVISO = "[AVISO] "
ERRO = "[ERRO]  "  # dois espaços: a mensagem começa na mesma coluna que a do aviso

_AMARELO = "\033[33m"
_VERMELHO = "\033[31m"
_NORMAL = "\033[0m"

# Atributos dos registros (passados em `extra=`):
#   estrutura   título ou linha em branco: sem prefixo e fora do bloco de avisos
#   abre_rodada título da rodada: no .log, duas linhas em branco antes
#   fecha_rodada linha em branco do fim da rodada
#   recuo       linha recuada sob o item
#   contexto    nome do item do aviso (para o bloco do fim)
#   chave       nome do bloco (o caderno), para o bloco do fim
#   so_arquivo  só vai para o .log
#   repeticao   linha do bloco de avisos (não é coletada de novo)


# ----------------------------------------------------------------------------
# Títulos
# ----------------------------------------------------------------------------


def titulo(texto: str, traco: str = "-") -> str:
    """`------- TEXTO -------`, centralizado em LARGURA (com ao menos 4 traços de cada lado)."""
    miolo = f" {texto} "
    falta = max(LARGURA - len(miolo), 8)
    esquerda = falta // 2
    return traco * esquerda + miolo + traco * (falta - esquerda)


def subtitulo(texto: str) -> str:
    """`---- texto ----`: abre um bloco dentro da rodada."""
    return f"---- {texto} ----"


# ----------------------------------------------------------------------------
# Cores
# ----------------------------------------------------------------------------


def _ativar_cores_no_windows() -> bool:
    """Liga as sequências ANSI no console do Windows (Windows 10 ou mais novo)."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        console = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        modo = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(console, ctypes.byref(modo)):
            return False
        return bool(kernel32.SetConsoleMode(console, modo.value | 0x0004))  # VIRTUAL_TERMINAL_PROCESSING
    except Exception:  # sem console, sem ctypes...: segue sem cor
        return False


def usa_cores(fluxo: TextIO) -> bool:
    """Cor só num terminal de verdade e sem `NO_COLOR` (https://no-color.org)."""
    if os.environ.get("NO_COLOR") or not hasattr(fluxo, "isatty") or not fluxo.isatty():
        return False
    return _ativar_cores_no_windows() if sys.platform == "win32" else True


def marcar(texto: str, nivel: int = logging.INFO, cores: bool = False) -> str:
    """`[AVISO] texto` ou `[ERRO]  texto` conforme o nível; com `cores`, pinta a linha."""
    if nivel >= logging.ERROR:
        linha, cor = ERRO + texto, _VERMELHO
    elif nivel >= logging.WARNING:
        linha, cor = AVISO + texto, _AMARELO
    else:
        return texto
    return f"{cor}{linha}{_NORMAL}" if cores else linha


def imprimir_aviso(texto: str, fluxo: TextIO | None = None) -> None:
    """Para os modos sem log (catálogo, ferramentas): `[AVISO] texto` no stderr."""
    fluxo = fluxo or sys.stderr
    print(marcar(texto, logging.WARNING, usa_cores(fluxo)), file=fluxo)


def imprimir_erro(texto: str, fluxo: TextIO | None = None) -> None:
    """Para os modos sem log (catálogo, ferramentas): `[ERRO]  texto` no stderr."""
    fluxo = fluxo or sys.stderr
    print(marcar(texto, logging.ERROR, usa_cores(fluxo)), file=fluxo)


# ----------------------------------------------------------------------------
# Formatadores e configuração do log
# ----------------------------------------------------------------------------


class FormatoTerminal(logging.Formatter):
    """Só a mensagem, com `[AVISO]`/`[ERRO]` e o recuo; cores opcionais."""

    def __init__(self, cores: bool = False) -> None:
        super().__init__()
        self.cores = cores

    def _linha(self, registro: logging.LogRecord, cores: bool) -> str:
        mensagem = registro.getMessage()
        if getattr(registro, "estrutura", False):
            return mensagem
        linha = (RECUO if getattr(registro, "recuo", False) else "") + marcar(mensagem, registro.levelno, cores)
        if registro.exc_info:
            linha += "\n" + self.formatException(registro.exc_info)
        return linha

    def format(self, registro: logging.LogRecord) -> str:
        return self._linha(registro, self.cores)


class FormatoArquivo(FormatoTerminal):
    """O mesmo do terminal, sem cor e com a data e a hora antes de cada linha de conteúdo."""

    def __init__(self) -> None:
        super().__init__(cores=False)

    def format(self, registro: logging.LogRecord) -> str:
        linha = self._linha(registro, False)
        if getattr(registro, "estrutura", False):
            return ("\n\n" if getattr(registro, "abre_rodada", False) else "") + linha
        quando = datetime.fromtimestamp(registro.created).strftime("%Y-%m-%d %H:%M:%S")
        return f"{quando} | {linha}"


class _SoArquivo(logging.Filter):
    def filter(self, registro: logging.LogRecord) -> bool:
        return not getattr(registro, "so_arquivo", False)


_MARCA = "_dbf_cepe_saida"  # nos handlers criados aqui, para trocá-los sem mexer nos de fora


def configurar_log(log: logging.Logger, pasta_logs: Path, nome: str = "pipeline.log") -> None:
    """Terminal (stdout) e `pasta_logs/nome`, com os formatos acima. Chamar de novo troca os
    destinos (não duplica as linhas)."""
    pasta_logs.mkdir(parents=True, exist_ok=True)
    for antigo in [h for h in log.handlers if getattr(h, _MARCA, False)]:
        log.removeHandler(antigo)
        antigo.close()
    terminal = logging.StreamHandler(sys.stdout)
    terminal.setFormatter(FormatoTerminal(cores=usa_cores(sys.stdout)))
    terminal.addFilter(_SoArquivo())
    arquivo = logging.FileHandler(pasta_logs / nome, encoding="utf-8")
    arquivo.setFormatter(FormatoArquivo())
    for destino in (terminal, arquivo):
        setattr(destino, _MARCA, True)
        log.addHandler(destino)
    log.setLevel(logging.INFO)


def tem_arquivo(log: logging.Logger) -> bool:
    return any(isinstance(h, logging.FileHandler) and getattr(h, _MARCA, False) for h in log.handlers)


def erro_fatal(log: logging.Logger, texto: str) -> None:
    """Erro que encerra o programa: `[ERRO]` no stderr e, se o log já está configurado, também
    no `.log` (no terminal sai uma vez só)."""
    imprimir_erro(texto)
    if tem_arquivo(log):
        log.error(texto, extra={"so_arquivo": True})


# ----------------------------------------------------------------------------
# Estrutura: linhas em branco, títulos e blocos
# ----------------------------------------------------------------------------


def linha_em_branco(log: logging.Logger) -> None:
    log.info("", extra={"estrutura": True})


def bloco(log: logging.Logger, texto: str, chave: str | None = None) -> None:
    """Fecha o bloco anterior com uma linha em branco e abre outro com `---- texto ----`.
    `chave` (o nome do caderno, por exemplo) vai na frente dos avisos deste bloco no fim."""
    linha_em_branco(log)
    log.info(subtitulo(texto), extra={"estrutura": True, "chave": chave})


def _titulo_principal(log: logging.Logger, texto: str) -> None:
    linha_em_branco(log)
    log.info(titulo(texto), extra={"estrutura": True, "chave": None})


# ----------------------------------------------------------------------------
# Rodada e o bloco de avisos do fim
# ----------------------------------------------------------------------------


class _Coletor(logging.Handler):
    """Guarda os avisos e erros da rodada, com o bloco e o item, para repeti-los no fim."""

    def __init__(self) -> None:
        super().__init__(logging.NOTSET)  # vê também os títulos (INFO), para saber o bloco
        self.chave: str | None = None
        self.pilha: list[str | None] = []  # chaves das rodadas de fora, numa rodada aninhada
        self.linhas: list[tuple[int, str]] = []

    def emit(self, registro: logging.LogRecord) -> None:
        if getattr(registro, "abre_rodada", False):
            self.pilha.append(self.chave)
            self.chave = None
        elif getattr(registro, "fecha_rodada", False):
            self.chave = self.pilha.pop() if self.pilha else None
        elif hasattr(registro, "chave"):
            self.chave = registro.chave  # type: ignore[attr-defined]
        if registro.levelno < logging.WARNING or getattr(registro, "estrutura", False):
            return
        if getattr(registro, "repeticao", False):
            return
        partes = [p for p in (*self.pilha, self.chave, getattr(registro, "contexto", None)) if p]
        self.linhas.append((registro.levelno, " | ".join([*partes, registro.getMessage()])))


@dataclass
class _Rodada:
    log: logging.Logger
    coletor: _Coletor
    inicio: float = field(default_factory=time.perf_counter)
    fechada: bool = False


_rodadas: list[_Rodada] = []


@contextmanager
def rodada(log: logging.Logger, modo: str) -> Iterator[None]:
    """Abre a rodada com `---- MODO — dd/mm/aaaa hh:mm:ss ----`. Se `fechar` foi chamado, a
    rodada termina com a duração e uma linha em branco."""
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    log.info(titulo(f"{modo} — {agora}"), extra={"estrutura": True, "abre_rodada": True})
    atual = _Rodada(log, _Coletor())
    log.addHandler(atual.coletor)
    _rodadas.append(atual)
    try:
        yield
    finally:
        _rodadas.remove(atual)
        log.removeHandler(atual.coletor)
        if atual.fechada:
            log.info("Duração: %.1f s", time.perf_counter() - atual.inicio)
        log.info("", extra={"estrutura": True, "fecha_rodada": True})


def avisos_da_rodada() -> list[tuple[int, str]]:
    """Os avisos e erros já registrados na rodada em andamento (vazio fora de uma rodada)."""
    return list(_rodadas[-1].coletor.linhas) if _rodadas else []


def fechar(log: logging.Logger, resumo: str = "RESUMO") -> None:
    """Escreve o bloco `AVISOS E ERROS (n)` (se houver) e abre o bloco do resumo."""
    linhas = avisos_da_rodada()
    if linhas:
        _titulo_principal(log, f"AVISOS E ERROS ({len(linhas)})")
        for nivel, texto in linhas:
            log.log(nivel, "%s", texto, extra={"repeticao": True})
    _titulo_principal(log, resumo)
    if _rodadas:
        _rodadas[-1].fechada = True


# ----------------------------------------------------------------------------
# Itens: avisos recuados sob a linha do item
# ----------------------------------------------------------------------------


@dataclass
class _Item:
    nome: str | None
    pendentes: list[tuple[logging.Logger, int, str]] = field(default_factory=list)
    linha_escrita: bool = False


_item_atual: ContextVar[_Item | None] = ContextVar("item_atual", default=None)


def _escrever_recuado(log: logging.Logger, nivel: int, texto: str, contexto: str | None) -> None:
    log.log(nivel, "%s", texto, extra={"recuo": True, "contexto": contexto})


@contextmanager
def item(nome: str | None = None) -> Iterator[None]:
    """Dentro do bloco, `aviso` e `detalhe` esperam a linha do item (`linha_do_item`) e saem
    recuados abaixo dela. `nome` vai na frente do aviso no bloco do fim."""
    atual = _Item(nome)
    marca = _item_atual.set(atual)
    try:
        yield
    finally:
        _item_atual.reset(marca)
        for log, nivel, texto in atual.pendentes:  # o item terminou sem linha própria
            _escrever_recuado(log, nivel, texto, atual.nome)


def linha_do_item(log: logging.Logger, mensagem: str, *args: object, nivel: int = logging.INFO,
                  nome: str | None = None) -> None:
    """A linha principal do item; em seguida, o que `aviso` e `detalhe` deixaram pendente."""
    log.log(nivel, mensagem, *args)
    atual = _item_atual.get()
    if atual is None:
        return
    if nome is not None:
        atual.nome = nome
    atual.linha_escrita = True
    pendentes, atual.pendentes = atual.pendentes, []
    for destino, nivel_pendente, texto in pendentes:
        _escrever_recuado(destino, nivel_pendente, texto, atual.nome)


def _sob_o_item(log: logging.Logger, nivel: int, mensagem: str, args: tuple[object, ...]) -> None:
    texto = mensagem % args if args else mensagem
    atual = _item_atual.get()
    if atual is None:
        log.log(nivel, "%s", texto)
    elif atual.linha_escrita:
        _escrever_recuado(log, nivel, texto, atual.nome)
    else:
        atual.pendentes.append((log, nivel, texto))


def aviso(log: logging.Logger, mensagem: str, *args: object) -> None:
    """`[AVISO]` recuado sob a linha do item em andamento (ou linha própria, fora de um item)."""
    _sob_o_item(log, logging.WARNING, mensagem, args)


def erro(log: logging.Logger, mensagem: str, *args: object) -> None:
    """`[ERRO]` recuado sob a linha do item em andamento (ou linha própria, fora de um item)."""
    _sob_o_item(log, logging.ERROR, mensagem, args)


def detalhe(log: logging.Logger, mensagem: str, *args: object) -> None:
    """Informação recuada sob a linha do item (uma diferença de texto, uma renomeação...)."""
    _sob_o_item(log, logging.INFO, mensagem, args)


def imprimir_bloco_de_avisos(avisos: list[str], fluxo: TextIO | None = None) -> None:
    """Para os modos sem log: o bloco `AVISOS E ERROS (n)` no stderr (nada, se não houver)."""
    if not avisos:
        return
    fluxo = fluxo or sys.stderr
    print("\n" + titulo(f"AVISOS E ERROS ({len(avisos)})"), file=fluxo)
    for texto in avisos:
        imprimir_aviso(texto, fluxo)
