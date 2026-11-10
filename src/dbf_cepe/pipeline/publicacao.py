"""Dados da publicação de um ato (data, caderno, página) e a impressão do texto.

A data de publicação é a data do CADERNO, tirada do nome do arquivo baixado pela
etapa 1 (`AAAAMMDD_...`; ver `separador.data_do_caderno`), e não o manifesto. Ela
acompanha o ato do separador até a etapa 5 dentro do próprio PDF intermediário de
`dados/pendentes`, no campo "Keywords" dos metadados (`gravar_no_pdf` ao gerar,
`ler_do_pdf` no lote). Um PDF sem esse campo (posto à mão em pendentes) não tem
data: segue a regra antiga (nome padrão; descartado se a base já tiver o nome).

Ordem entre publicações do mesmo número (`Publicacao.chave`): data; no mesmo dia, a
edição normal antes da Extra; no mesmo caderno, a página.

`hash_do_texto` usa os mesmos elementos de `comparacao.palavras` (palavras, números
e sinais, sem o layout), então dois textos que a comparação dá como idênticos têm o
mesmo hash.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pymupdf

from dbf_cepe.pipeline.comparacao import palavras

# Prefixo do campo Keywords: distingue os nossos metadados de outros que o PDF tenha.
PREFIXO_METADADOS = "dbf-cepe-publicacao:"


@dataclass(frozen=True)
class Publicacao:
    data: date  # data do caderno
    caderno: str  # nome do arquivo do caderno, ex.: "20260715_ResumoDiario.pdf"
    pagina: int  # página do caderno onde o ato começa (base 1)
    # O caderno marca o ato como republicação ("(REPUBLICADO ...)"); não entra na ordem.
    marcada_como_republicacao: bool = False

    @property
    def extra(self) -> bool:
        """Edição extra do dia ("...Extra.pdf", "...PoderExecutivoExtra.pdf")."""
        return "extra" in self.caderno.casefold()

    @property
    def chave(self) -> tuple[date, int, str, int]:
        """Ordem cronológica: data, normal antes de Extra, caderno, página."""
        return (self.data, int(self.extra), self.caderno.casefold(), self.pagina)

    def mesma_que(self, outra: Publicacao) -> bool:
        return self.chave == outra.chave

    def para_dict(self) -> dict[str, object]:
        dados: dict[str, object] = {"data": self.data.isoformat(), "caderno": self.caderno, "pagina": self.pagina}
        if self.marcada_como_republicacao:
            dados["republicado"] = True
        return dados

    @classmethod
    def de_dict(cls, dados: dict[str, object]) -> Publicacao:
        pagina = dados["pagina"]
        if not isinstance(pagina, int) or isinstance(pagina, bool) or pagina < 1:
            raise ValueError(f"página inválida: {pagina!r}")
        caderno = dados["caderno"]
        if not isinstance(caderno, str) or not caderno:
            raise ValueError(f"caderno inválido: {caderno!r}")
        return cls(date.fromisoformat(str(dados["data"])), caderno, pagina, dados.get("republicado") is True)

    def __str__(self) -> str:
        return f"{self.data:%d/%m/%Y}, {self.caderno}, p. {self.pagina}"


def hash_do_texto(texto: str) -> str:
    """SHA-256 do texto normalizado (os elementos de `comparacao.palavras`)."""
    return hashlib.sha256("\x1f".join(palavras(texto)).encode("utf-8")).hexdigest()


def metadados(publicacao: Publicacao) -> str:
    """Valor do campo Keywords do PDF intermediário."""
    return PREFIXO_METADADOS + json.dumps(publicacao.para_dict(), ensure_ascii=False, sort_keys=True)


def ler_do_pdf(caminho: Path) -> Publicacao | None:
    """Publicação gravada nos metadados do PDF; None se não houver (ou se for ilegível)."""
    try:
        with pymupdf.open(caminho) as doc:
            palavras_chave = (doc.metadata or {}).get("keywords") or ""
    except Exception:  # PDF corrompido: a etapa 2 trata e manda para erros
        return None
    if not palavras_chave.startswith(PREFIXO_METADADOS):
        return None
    try:
        return Publicacao.de_dict(json.loads(palavras_chave[len(PREFIXO_METADADOS):]))
    except (ValueError, KeyError, TypeError):
        return None
