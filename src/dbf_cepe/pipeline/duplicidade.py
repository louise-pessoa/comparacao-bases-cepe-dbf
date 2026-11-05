"""Etapa 3 — validação de duplicidade (diff) contra a pasta da DBF.

A pasta é listada UMA vez por execução e comparada em memória. Em Drive
sincronizado/montado, uma consulta por arquivo seria bem mais lenta.

Falhas que abortam a execução em vez de assumir "não existe" (o que faria o
pipeline regerar tudo):
  - pasta inexistente ou inacessível (ex.: /mnt/g não montado);
  - pasta vazia (drive montado mas ainda sem sincronizar). Use
    `permitir_vazia=True` apenas quando a base estiver realmente vazia.

Pressuposto: a base da DBF é uma pasta plana (não recursiva).
"""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path


class PastaDbfIndisponivel(Exception):
    """A pasta da DBF não pôde ser lida de forma confiável."""


def chave_nome(nome: str) -> str:
    """Chave de comparação: Unicode NFC e sem diferença de maiúsculas.

    Maiúsculas/minúsculas não diferenciam arquivos no Windows (G:), que é onde
    a base realmente vive.
    """
    return unicodedata.normalize("NFC", nome).casefold()


def carregar_indice(pasta_dbf: Path, permitir_vazia: bool = False) -> set[str]:
    """Lista os nomes de arquivo da pasta da DBF como conjunto de chaves."""
    if not pasta_dbf.is_dir():
        raise PastaDbfIndisponivel(
            f"Pasta da DBF não encontrada ou inacessível: {pasta_dbf}. "
            "Confira o PASTA_DBF no .env e se o drive está montado/sincronizado."
        )
    try:
        with os.scandir(pasta_dbf) as entradas:
            nomes = [entrada.name for entrada in entradas if entrada.is_file()]
    except OSError as erro:
        raise PastaDbfIndisponivel(
            f"Não foi possível listar a pasta da DBF ({pasta_dbf}): {erro}"
        ) from erro

    if not nomes and not permitir_vazia:
        raise PastaDbfIndisponivel(
            f"A pasta da DBF está vazia: {pasta_dbf}. Se o drive ainda não "
            "sincronizou, aguarde. Se a base está realmente vazia, execute "
            "com --permitir-pasta-vazia."
        )
    return {chave_nome(nome) for nome in nomes}


def ja_existe(indice: set[str], nome_arquivo: str) -> bool:
    return chave_nome(nome_arquivo) in indice
