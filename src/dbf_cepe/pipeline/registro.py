"""Registro local das publicações gravadas na base (`dados/registro_publicacoes.json`).

Para cada publicação de um ato que passou pela etapa 3 com data conhecida, guarda:
espécie, número, (ano), data de publicação, caderno, página, hash do texto
normalizado, nome do arquivo e situação:

  - "gravado": está na base da DBF com o nome `arquivo`;
  - "duplicata": texto idêntico a outra publicação já na base; não foi gravada;
  - "conferir duplicata": a base tinha um arquivo deste número sem registro e com
    texto diferente; o PDF foi para a pasta local de conferência (`arquivo`);
  - "base é imagem (não comparado)": a base tinha o número num PDF digitalizado, sem
    texto para comparar, e não havia sinal de republicação; não foi gravada.

É com ele que a regra de republicação sabe a data de cada arquivo da base. Arquivos
da base gravados antes deste registro existir não constam dele (ver
`republicacao.py`).

Segurança:
  - escrita atômica: grava em "<arquivo>.tmp", força para o disco e só então
    substitui o registro (`os.replace`); uma queda no meio deixa o registro antigo;
  - registro ilegível NÃO é recriado nem sobrescrito: `ErroRegistro` interrompe o
    programa até alguém restaurar ou apagar o arquivo (decisão da DBF);
  - `operacao_em_andamento`: antes de renomear ou gravar na base, a operação é
    anotada no registro; se o programa cair no meio, a próxima execução a desfaz ou
    a conclui (`armazenamento.recuperar_operacao`).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from dbf_cepe.pipeline.publicacao import Publicacao

VERSAO = 1
GRAVADO = "gravado"
DUPLICATA = "duplicata"
CONFERIR = "conferir duplicata"
# A base tem o número, mas o PDF dela é imagem (digitalizado): não há texto para comparar.
# Sem sinal de republicação, conta como já existente (decisão de 10/11/2026).
BASE_IMAGEM = "base é imagem (não comparado)"
SITUACOES = (GRAVADO, DUPLICATA, CONFERIR, BASE_IMAGEM)


class ErroRegistro(Exception):
    """Registro de publicações ilegível ou impossível de gravar."""


@dataclass(frozen=True)
class Entrada:
    especie: str
    numero: str
    ano: str | None
    publicacao: Publicacao
    hash: str
    arquivo: str | None  # nome na base (gravado) ou na pasta de conferência
    situacao: str
    registrado_em: str = ""
    # Decisão tomada no `--conferir` ("mantida a base", "substituída pelo Diário ...") e quando.
    decisao: str = ""
    decidido_em: str = ""

    def para_dict(self) -> dict[str, Any]:
        return {
            "especie": self.especie,
            "numero": self.numero,
            "ano": self.ano,
            "data_publicacao": self.publicacao.data.isoformat(),
            "caderno": self.publicacao.caderno,
            "pagina": self.publicacao.pagina,
            "hash": self.hash,
            "arquivo": self.arquivo,
            "situacao": self.situacao,
            "registrado_em": self.registrado_em,
            **({"decisao": self.decisao, "decidido_em": self.decidido_em} if self.decisao else {}),
        }

    @classmethod
    def de_dict(cls, dados: dict[str, Any]) -> Entrada:
        situacao = dados["situacao"]
        if situacao not in SITUACOES:
            raise ValueError(f"situação desconhecida: {situacao!r}")
        for chave in ("especie", "numero", "hash"):
            if not isinstance(dados[chave], str) or not dados[chave]:
                raise ValueError(f"{chave} inválido: {dados[chave]!r}")
        arquivo = dados.get("arquivo")
        if situacao not in (DUPLICATA, BASE_IMAGEM) and (not isinstance(arquivo, str) or not arquivo):
            raise ValueError(f"arquivo inválido: {arquivo!r}")
        return cls(
            especie=dados["especie"],
            numero=dados["numero"],
            ano=dados.get("ano"),
            publicacao=Publicacao.de_dict(
                {"data": dados["data_publicacao"], "caderno": dados["caderno"], "pagina": dados["pagina"]}
            ),
            hash=dados["hash"],
            arquivo=arquivo,
            situacao=situacao,
            registrado_em=str(dados.get("registrado_em") or ""),
            decisao=str(dados.get("decisao") or ""),
            decidido_em=str(dados.get("decidido_em") or ""),
        )

    def do_ato(self, especie: str, numero: str, ano: str | None) -> bool:
        return (self.especie, self.numero, self.ano) == (especie, numero, ano)


@dataclass
class Registro:
    caminho: Path
    entradas: list[Entrada] = field(default_factory=list)
    # Operação na base que começou e não terminou (ver armazenamento.recuperar_operacao).
    operacao_em_andamento: dict[str, Any] | None = None

    @classmethod
    def carregar(cls, caminho: Path) -> Registro:
        """Lê o registro; um arquivo ausente é um registro vazio. Ilegível: ErroRegistro."""
        if not caminho.exists():
            return cls(caminho)
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
            if not isinstance(dados, dict) or dados.get("versao") != VERSAO:
                raise ValueError(f"versão do registro não reconhecida: {dados.get('versao') if isinstance(dados, dict) else dados!r}")
            entradas = [Entrada.de_dict(item) for item in dados["publicacoes"]]
            operacao = dados.get("operacao_em_andamento")
            if operacao is not None and not isinstance(operacao, dict):
                raise ValueError("operacao_em_andamento inválida")
        except (OSError, ValueError, KeyError, TypeError) as erro:
            raise ErroRegistro(
                f"O registro de publicações {caminho} está ilegível ({type(erro).__name__}: {erro}). "
                "Nada foi gravado nem renomeado. Restaure o arquivo de uma cópia ou, se tiver "
                "certeza, apague-o (os arquivos já gravados na base passam a ser tratados como "
                "anteriores ao registro)."
            ) from erro
        return cls(caminho, entradas, operacao)

    def salvar(self) -> None:
        """Grava o registro de forma atômica (".tmp" + fsync + os.replace)."""
        dados = {
            "versao": VERSAO,
            "publicacoes": [entrada.para_dict() for entrada in self.entradas],
            "operacao_em_andamento": self.operacao_em_andamento,
        }
        temporario = self.caminho.with_name(self.caminho.name + ".tmp")
        try:
            self.caminho.parent.mkdir(parents=True, exist_ok=True)
            with open(temporario, "w", encoding="utf-8") as saida:
                json.dump(dados, saida, ensure_ascii=False, indent=1)
                saida.flush()
                os.fsync(saida.fileno())
            os.replace(temporario, self.caminho)
        except OSError as erro:
            try:
                temporario.unlink(missing_ok=True)
            except OSError:
                pass
            raise ErroRegistro(f"não foi possível gravar o registro {self.caminho}: {erro}") from erro

    # --- consultas ---------------------------------------------------------

    def do_ato(self, especie: str, numero: str, ano: str | None = None) -> list[Entrada]:
        return [e for e in self.entradas if e.do_ato(especie, numero, ano)]

    def gravadas(self, especie: str, numero: str, ano: str | None = None) -> list[Entrada]:
        return [e for e in self.do_ato(especie, numero, ano) if e.situacao == GRAVADO]

    def ja_registrada(self, especie: str, numero: str, ano: str | None, publicacao: Publicacao) -> Entrada | None:
        return next(
            (e for e in self.do_ato(especie, numero, ano) if e.publicacao.mesma_que(publicacao)), None
        )

    # --- alterações (só em memória; `salvar` grava) -----------------------

    def adicionar(self, entrada: Entrada) -> None:
        if not entrada.registrado_em:
            entrada = replace(entrada, registrado_em=datetime.now().isoformat(timespec="seconds"))
        self.entradas.append(entrada)

    def entrada_da_conferencia(self, arquivo: str) -> Entrada | None:
        """A publicação mandada para conferência com o PDF `arquivo` (ainda sem decisão)."""
        return next(
            (e for e in self.entradas if e.situacao == CONFERIR and e.arquivo == arquivo and not e.decisao), None
        )

    def decidir(self, entrada: Entrada, situacao: str, arquivo: str | None, decisao: str) -> None:
        """Anota a decisão do `--conferir` na entrada (só em memória; `salvar` grava)."""
        posicao = self.entradas.index(entrada)
        self.entradas[posicao] = replace(
            entrada, situacao=situacao, arquivo=arquivo, decisao=decisao,
            decidido_em=datetime.now().isoformat(timespec="seconds"),
        )

    def renomear(self, especie: str, numero: str, ano: str | None, de: str, para: str) -> None:
        """Atualiza o nome de um arquivo gravado (depois de renomeado na base)."""
        for posicao, entrada in enumerate(self.entradas):
            if entrada.do_ato(especie, numero, ano) and entrada.situacao == GRAVADO and entrada.arquivo == de:
                self.entradas[posicao] = replace(entrada, arquivo=para)
                return
        raise ErroRegistro(f"{de} não consta do registro como gravado")
