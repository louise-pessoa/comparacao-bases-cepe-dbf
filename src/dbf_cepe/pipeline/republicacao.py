"""Regra de republicação: que nome dar a uma publicação de um número que talvez já exista.

Decisões da DBF (implementadas aqui, sem ler nem gravar nada; quem executa o plano é
o lote):

  1. Entre as publicações do mesmo número, a de data mais antiga fica com o nome
     padrão (`Decreto N.pdf`); as seguintes, em ordem cronológica, são
     `Decreto N (republicação).pdf`, `Decreto N (republicação 2).pdf`... (os
     sufixos vêm de `especies.py`). No mesmo dia, a edição normal vem antes da
     Extra; no mesmo caderno, vale a página (`Publicacao.chave`).
  2. Se a publicação que chega é MAIS ANTIGA que uma já gravada, os arquivos da
     base são renomeados para abrir o lugar dela (`Plano.renomeacoes`, na ordem
     segura de execução).
  3. Texto idêntico (mesmo hash) a uma publicação já gravada: duplicata, não grava.
  4. A base tem arquivo deste número que não consta do registro (gravado antes do
     registro existir): texto idêntico (mesmo critério de `comparacao.py`) é
     duplicata; diferente vai para conferência manual, numa pasta LOCAL, com
     `(conferir duplicata)` no nome. Nunca se grava nem se renomeia na base nesse caso.
     Se o PDF da base for imagem (digitalizado, sem texto para comparar), a publicação
     conta como já existente (`Acao.BASE_IMAGEM`), a não ser que haja sinal de
     republicação: a marca "(REPUBLICADO ...)" no caderno, outra publicação do mesmo
     número com texto diferente no registro ou no mesmo lote. Com sinal, vai para
     conferência. Decisão de 10/11/2026.
  5. A mesma publicação (mesma data, caderno e página) já registrada é ignorada:
     rodar o mesmo lote de novo não muda nada.

"Arquivo deste número" na base (`Especie.e_da_familia`) inclui nomes fora do padrão
que começam pelo nome padrão ("Decreto 61055 (1).pdf", "Decreto 61055-A.pdf"); como
não constam do registro, caem na regra 4.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum

from dbf_cepe.pipeline.publicacao import Publicacao
from dbf_cepe.pipeline.registro import Registro
from dbf_cepe.regras.especies import Especie


class Acao(Enum):
    JA_REGISTRADA = "publicação já registrada"
    DUPLICATA = "duplicata (texto idêntico)"
    BASE_IMAGEM = "já existe (base é imagem; não comparado)"
    CONFERIR = "conferir duplicata"
    GRAVAR = "gravar"


@dataclass(frozen=True)
class Plano:
    acao: Acao
    motivo: str
    nome_final: str | None = None  # GRAVAR: nome na base; CONFERIR: nome na pasta local
    ordem: int | None = None  # GRAVAR: 0 = original, 1 = republicação, 2 = republicação 2...
    renomeacoes: tuple[tuple[str, str], ...] = ()  # (de, para) na base, na ordem de execução
    avisos: tuple[str, ...] = ()

    @property
    def republicacao(self) -> bool:
        return self.acao is Acao.GRAVAR and bool(self.ordem)


def _chave(nome: str) -> str:
    return unicodedata.normalize("NFC", nome).casefold()


def _ordenar_renomeacoes(
    renomeacoes: list[tuple[str, str]], ocupados: set[str]
) -> list[tuple[str, str]] | None:
    """Ordem em que cada renomeação encontra o destino livre; None se não houver."""
    pendentes, ordem = list(renomeacoes), []
    ocupados = set(ocupados)
    while pendentes:
        livre = next((par for par in pendentes if _chave(par[1]) not in ocupados), None)
        if livre is None:
            return None
        pendentes.remove(livre)
        ocupados.discard(_chave(livre[0]))
        ocupados.add(_chave(livre[1]))
        ordem.append(livre)
    return ordem


def planejar(
    especie: Especie,
    numero: str,
    publicacao: Publicacao,
    hash_texto: str,
    registro: Registro,
    nomes_na_base: Iterable[str],
    identico_ao_da_base: Callable[[str], bool | None],
    ano: str | None = None,
    outra_publicacao_no_lote: bool = False,
) -> Plano:
    """Decide o que fazer com uma publicação. `identico_ao_da_base(nome)` compara o
    texto do ato com o arquivo `nome` da base (só é chamado para arquivos sem registro):
    True/False, ou None se o PDF da base não tiver texto legível (imagem).
    `outra_publicacao_no_lote`: o mesmo número aparece com outro texto no lote em curso."""
    nome_padrao = especie.nome_arquivo(numero, ano)
    ja = registro.ja_registrada(especie.nome, numero, ano, publicacao)
    if ja is not None:
        return Plano(Acao.JA_REGISTRADA, f"{nome_padrao}: publicação de {publicacao} já registrada ({ja.situacao})")

    familia = {_chave(n): n for n in nomes_na_base if especie.e_da_familia(n, numero, ano)}
    avisos: list[str] = []
    gravadas = []
    for entrada in registro.gravadas(especie.nome, numero, ano):
        if entrada.arquivo is not None and _chave(entrada.arquivo) in familia:
            gravadas.append(entrada)
        else:
            avisos.append(f"{entrada.arquivo} consta do registro, mas não está na base; ignorado")

    igual = next((e for e in gravadas if e.hash == hash_texto), None)
    if igual is not None:
        return Plano(
            Acao.DUPLICATA,
            f"{nome_padrao}: texto idêntico ao de {igual.arquivo} (publicado em {igual.publicacao.data:%d/%m/%Y})",
            avisos=tuple(avisos),
        )

    registrados = {_chave(e.arquivo) for e in gravadas if e.arquivo}
    sem_registro = sorted(nome for chave, nome in familia.items() if chave not in registrados)
    if sem_registro:
        resultados = {nome: identico_ao_da_base(nome) for nome in sem_registro}
        identico = next((nome for nome, igual in resultados.items() if igual), None)
        if identico is not None:
            return Plano(
                Acao.DUPLICATA,
                f"{nome_padrao}: texto idêntico ao de {identico} (arquivo da base sem registro)",
                avisos=tuple(avisos),
            )
        if all(igual is None for igual in resultados.values()):
            sinais = []
            if publicacao.marcada_como_republicacao:
                sinais.append("o caderno o marca como republicado")
            if outra_publicacao_no_lote:
                sinais.append("o mesmo número aparece com outro texto neste lote")
            if any(e.hash != hash_texto for e in registro.do_ato(especie.nome, numero, ano)):
                sinais.append("o registro tem outra publicação deste número com outro texto")
            if not sinais:
                return Plano(
                    Acao.BASE_IMAGEM,
                    f"{nome_padrao}: a base tem {', '.join(sem_registro)} em imagem (sem texto para "
                    "comparar) e não há sinal de republicação",
                    avisos=tuple(avisos),
                )
            return Plano(
                Acao.CONFERIR,
                f"{nome_padrao}: a base tem {', '.join(sem_registro)} em imagem e {'; '.join(sinais)}; "
                "o PDF vai para conferência manual",
                nome_final=especie.nome_conferir_duplicata(numero, ano),
                avisos=tuple(avisos),
            )
        return Plano(
            Acao.CONFERIR,
            f"{nome_padrao}: a base tem {', '.join(sem_registro)} sem registro de publicação e com "
            "texto diferente; o PDF vai para conferência manual",
            nome_final=especie.nome_conferir_duplicata(numero, ano),
            avisos=tuple(avisos),
        )

    # Todos os arquivos deste número na base estão no registro: ordem cronológica.
    nova = object()
    itens: list[tuple[tuple, object]] = [(e.publicacao.chave, e) for e in gravadas]
    itens.append((publicacao.chave, nova))
    itens.sort(key=lambda item: item[0])
    renomeacoes: list[tuple[str, str]] = []
    nome_final, ordem_nova = "", 0
    for ordem, (_, item) in enumerate(itens):
        alvo = especie.nome_da_publicacao(numero, ordem, ano)
        if item is nova:
            nome_final, ordem_nova = alvo, ordem
        elif _chave(item.arquivo) != _chave(alvo):  # type: ignore[union-attr]
            renomeacoes.append((item.arquivo, alvo))  # type: ignore[union-attr]
    em_ordem = _ordenar_renomeacoes(renomeacoes, set(familia))
    if em_ordem is None or _chave(nome_final) in (
        set(familia) - {_chave(de) for de, _ in renomeacoes} | {_chave(para) for _, para in renomeacoes}
    ):
        return Plano(
            Acao.CONFERIR,
            f"{nome_padrao}: os nomes deste número na base não seguem a ordem do registro; "
            "o PDF vai para conferência manual",
            nome_final=especie.nome_conferir_duplicata(numero, ano),
            avisos=tuple(avisos),
        )
    motivo = (
        f"{nome_final}: publicado em {publicacao}"
        + (f"; antes, renomear {'; '.join(f'{de} -> {para}' for de, para in em_ordem)}" if em_ordem else "")
    )
    return Plano(Acao.GRAVAR, motivo, nome_final, ordem_nova, tuple(em_ordem), tuple(avisos))
