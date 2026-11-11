"""Configuração do pipeline: pastas de trabalho e variáveis do .env."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

from dbf_cepe.proxy import VALOR_AUTOMATICO

# Raiz do repositório (src/dbf_cepe/config.py -> ../..): onde ficam o .env e, por padrão, dados/.
RAIZ = Path(__file__).resolve().parents[2]

_UNIDADE_WINDOWS = re.compile(r"^[A-Za-z]:[\\/]")


class ErroConfiguracao(Exception):
    """Configuração ausente ou inválida."""


@dataclass(frozen=True)
class Configuracao:
    pasta_temp: Path  # área temporária: um ato por PDF, lido pela etapa 2
    pasta_cadernos: Path  # etapa 1: cadernos (PDF completo do dia) baixados da CEPE
    pasta_erros: Path  # revisão manual
    pasta_ocr: Path  # "leitura tesseract": PDFs de imagem
    pasta_normalizados: Path  # saída local da etapa 4, antes de ir para a DBF
    pasta_logs: Path
    pasta_dbf: Path | None  # base oficial da DBF; obrigatória para executar
    proxy_https: str | None = None  # proxy da rede: http://127.0.0.1:9000 ou "auto"
    ssl_estrito: bool = True  # False: aceita certificado de cadeia sem Authority Key Identifier
    # Endereço de HTTPS_PROXY vindo do ambiente quando ele sobrepõe um valor diferente do
    # .env (a captura avisa; senha, se houver, não é guardada aqui).
    proxy_do_ambiente: str | None = None
    # Republicação: pasta LOCAL dos PDFs "(conferir duplicata)" e registro das
    # publicações gravadas. None = ao lado de PASTA_TEMP ("dados/conferir duplicata"
    # e "dados/registro_publicacoes.json" com a configuração padrão).
    pasta_conferir_duplicata: Path | None = None
    arquivo_registro: Path | None = None
    # `--conferir`: cópia de cada arquivo da base substituído pelo texto do Diário.
    pasta_substituidos: Path | None = None

    @property
    def substituidos(self) -> Path:
        return self.pasta_substituidos or self.pasta_temp.parent / "substituidos"

    @property
    def conferir_duplicata(self) -> Path:
        return self.pasta_conferir_duplicata or self.pasta_temp.parent / "conferir duplicata"

    @property
    def registro(self) -> Path:
        return self.arquivo_registro or self.pasta_temp.parent / "registro_publicacoes.json"


def _resolver_caminho(variavel: str, valor: str) -> Path:
    # Caminho de unidade do Windows (G:\...) rodando em Linux/WSL2 viraria,
    # em silêncio, uma pasta relativa estranha dentro do projeto.
    if os.name != "nt" and _UNIDADE_WINDOWS.match(valor):
        raise ErroConfiguracao(
            f"{variavel}={valor!r} é um caminho do Windows, mas o script está "
            "rodando em Linux/WSL2. Use o caminho montado, por exemplo "
            "/mnt/g/... (monte o drive com: sudo mount -t drvfs G: /mnt/g)."
        )
    caminho = Path(valor).expanduser()
    return caminho if caminho.is_absolute() else RAIZ / caminho


def _pastas_nomeadas(cfg: Configuracao) -> list[tuple[str, Path]]:
    pastas = [
        ("PASTA_TEMP", cfg.pasta_temp),
        ("PASTA_CADERNOS", cfg.pasta_cadernos),
        ("PASTA_ERROS", cfg.pasta_erros),
        ("PASTA_OCR", cfg.pasta_ocr),
        ("PASTA_NORMALIZADOS", cfg.pasta_normalizados),
        ("PASTA_LOGS", cfg.pasta_logs),
        ("PASTA_CONFERIR_DUPLICATA", cfg.conferir_duplicata),
        ("PASTA_SUBSTITUIDOS", cfg.substituidos),
    ]
    if cfg.pasta_dbf is not None:
        pastas.append(("PASTA_DBF", cfg.pasta_dbf))
    return pastas


def _partes(caminho: Path) -> tuple[str, ...]:
    # resolve(): segue atalhos e elimina "..". casefold(): o G: (Windows) não
    # diferencia maiúsculas de minúsculas, então "DBF" e "dbf" são a mesma pasta.
    return tuple(parte.casefold() for parte in caminho.resolve().parts)


def _se_sobrepoem(a: Path, b: Path) -> bool:
    """True se as pastas são a mesma ou se uma está dentro da outra.

    Compara por componentes do caminho, não por texto: "dbf" e "dbf2" são
    pastas irmãs.
    """
    partes_a, partes_b = _partes(a), _partes(b)
    menor = min(len(partes_a), len(partes_b))
    return partes_a[:menor] == partes_b[:menor]


def validar_configuracao(cfg: Configuracao) -> None:
    """Recusa pastas iguais ou aninhadas (a da DBF e as de trabalho).

    Principal risco: com PASTA_TEMP igual à pasta da DBF, todo decreto da base
    seria visto como "já existe na DBF" e apagado.
    """
    pastas = _pastas_nomeadas(cfg)
    conflitos = [
        f"{nome_a} ({pasta_a}) e {nome_b} ({pasta_b})"
        for i, (nome_a, pasta_a) in enumerate(pastas)
        for nome_b, pasta_b in pastas[i + 1 :]
        if _se_sobrepoem(pasta_a, pasta_b)
    ]
    # O registro é um arquivo: só não pode ficar DENTRO da base da DBF.
    if cfg.pasta_dbf is not None and _partes(cfg.registro)[: len(_partes(cfg.pasta_dbf))] == _partes(cfg.pasta_dbf):
        conflitos.append(f"REGISTRO_PUBLICACOES ({cfg.registro}) e PASTA_DBF ({cfg.pasta_dbf})")
    if conflitos:
        raise ErroConfiguracao(
            "As pastas configuradas não podem ser iguais nem ficar uma dentro "
            "da outra:\n"
            + "\n".join(f"  - {conflito}" for conflito in conflitos)
            + "\nPastas sobrepostas podem misturar PDFs de trabalho com a base "
            "da DBF e levar o script a apagar decretos dela (o descarte é "
            "definitivo). Corrija o .env."
        )


def _ler_proxy(valor: str) -> str | None:
    valor = valor.strip()
    if not valor:
        return None
    if valor.casefold() == VALOR_AUTOMATICO:  # o proxy é descoberto no Windows a cada captura
        return VALOR_AUTOMATICO
    partes = urlsplit(valor)
    if partes.scheme not in ("http", "https") or not partes.hostname:
        raise ErroConfiguracao(
            f"HTTPS_PROXY={valor!r} é inválido. Use auto (descobre o proxy do Windows) "
            "ou o formato http://endereco:porta (por exemplo, http://127.0.0.1:9000)."
        )
    return valor


_VERDADEIRO = {"1", "true", "sim", "s", "yes", "y"}
_FALSO = {"0", "false", "nao", "não", "n", "no"}


def _ler_booleano(variavel: str, valor: str, padrao: bool) -> bool:
    texto = valor.strip().casefold()
    if not texto:
        return padrao
    if texto in _VERDADEIRO:
        return True
    if texto in _FALSO:
        return False
    raise ErroConfiguracao(
        f"{variavel}={valor!r} é inválido. Use sim ou não (também aceita true/false, 1/0)."
    )


def _ler_proxy_do_arquivo(arquivo_env: Path | None) -> str:
    return (dotenv_values(arquivo_env or RAIZ / ".env").get("HTTPS_PROXY") or "").strip()


def _sem_credenciais(proxy: str) -> str:
    """host:porta de um endereço de proxy, sem usuário nem senha."""
    partes = urlsplit(proxy)
    if not partes.hostname:
        return "(endereço inválido)"
    return f"{partes.hostname}:{partes.port}" if partes.port else partes.hostname


def carregar_configuracao(arquivo_env: Path | None = None) -> Configuracao:
    """Lê o .env (variáveis de ambiente reais têm precedência sobre ele)."""
    valores = {**dotenv_values(arquivo_env or RAIZ / ".env"), **os.environ}

    def pasta(variavel: str, padrao: str) -> Path:
        valor = (valores.get(variavel) or "").strip() or padrao
        return _resolver_caminho(variavel, valor)

    bruto_dbf = (valores.get("PASTA_DBF") or "").strip()
    do_arquivo = _ler_proxy_do_arquivo(arquivo_env)
    do_ambiente = (os.environ.get("HTTPS_PROXY") or "").strip()
    cfg = Configuracao(
        pasta_temp=pasta("PASTA_TEMP", "dados/pendentes"),
        pasta_cadernos=pasta("PASTA_CADERNOS", "dados/cadernos"),
        pasta_erros=pasta("PASTA_ERROS", "dados/erros"),
        pasta_ocr=pasta("PASTA_OCR", "dados/leitura tesseract"),
        pasta_normalizados=pasta("PASTA_NORMALIZADOS", "dados/normalizados"),
        pasta_logs=pasta("PASTA_LOGS", "logs"),
        pasta_conferir_duplicata=pasta("PASTA_CONFERIR_DUPLICATA", "dados/conferir duplicata"),
        arquivo_registro=pasta("REGISTRO_PUBLICACOES", "dados/registro_publicacoes.json"),
        pasta_substituidos=pasta("PASTA_SUBSTITUIDOS", "dados/substituidos"),
        pasta_dbf=_resolver_caminho("PASTA_DBF", bruto_dbf) if bruto_dbf else None,
        proxy_https=_ler_proxy(valores.get("HTTPS_PROXY") or ""),
        ssl_estrito=_ler_booleano("SSL_ESTRITO", valores.get("SSL_ESTRITO") or "", True),
        proxy_do_ambiente=(
            _sem_credenciais(do_ambiente)
            if do_ambiente and do_arquivo and do_ambiente.casefold() != do_arquivo.casefold()
            else None
        ),
    )
    validar_configuracao(cfg)
    return cfg