"""Ferramenta auxiliar (fora do pipeline): levanta, por ano, as páginas digitalizadas dos
cadernos e os PDFs em imagem da base da DBF, onde o OCR vai atuar.

É a fase 0 do plano do OCR (docs/plano_ocr.md). Responde: em que anos há páginas
digitalizadas nos cadernos, quantas, e quais cadernos servem de amostra; quantos PDFs da
base são imagem (digitalizados) ou têm texto ilegível. Com `--ocr`, lê as páginas
digitalizadas com o Tesseract e conta os cabeçalhos de decreto achados nelas (mostra se
as páginas em imagem têm decretos ou só outros atos).

Só LÊ os cadernos e a base; não grava nada além do JSON pedido em --json (e, com --ocr,
o cache do OCR em PASTA_CACHE_OCR, fora da base).

Uso:
    python -m ferramentas.levantar_digitalizadas                         # lê dados/cadernos
    python -m ferramentas.levantar_digitalizadas dados/historico          # procura nas subpastas
    python -m ferramentas.levantar_digitalizadas dados/historico --base "G:\\Meu Drive\\DBF\\Decretos"
    python -m ferramentas.levantar_digitalizadas dados/historico --ocr --json levantamento.json

A página digitalizada é a do separador (`reader._pagina_digitalizada`); a página com
texto ilegível (fonte sem conversão para Unicode) é a do leitor (`reader._ilegivel`). O
ano é o do nome do caderno (AAAAMMDD_...).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from dbf_cepe.leitura.reader import StatusLeitura, _ilegivel, _pagina_digitalizada, ler_pdf
from dbf_cepe.leitura.separador import data_do_caderno

PASTA_PADRAO = Path("dados/cadernos")
# Cabeçalho de decreto no texto do OCR: no início da linha, em maiúsculas. Mais tolerante
# que a regex do separador (o OCR troca "Nº" por "N°", "Nº", "N.º", "N?").
CABECALHO_NO_OCR = re.compile(r"^\s*DECRETO\s+N\S{0,2}\s*[\d.]{3,}", re.MULTILINE)


@dataclass
class Ano:
    cadernos: int = 0
    paginas: int = 0
    cadernos_com_digitalizadas: int = 0
    digitalizadas: int = 0
    ilegiveis: int = 0
    decretos_no_ocr: int = 0  # só com --ocr
    exemplos: list[str] = field(default_factory=list)


# Número do decreto no nome do arquivo da base ("Decreto 35945.pdf", "Decreto 47207 (republicação).pdf").
_NUMERO_NA_BASE = re.compile(r"(\d{4,6})")
TAMANHO_DA_FAIXA = 1000


@dataclass
class Base:
    total: int = 0
    situacoes: Counter[str] = field(default_factory=Counter)
    imagem: list[str] = field(default_factory=list)  # nome: detalhe
    # Faixa de número ("34000-34999") -> situação -> quantidade. Mostra quantos PDFs em imagem
    # têm números que podem aparecer nos cadernos baixados (os antigos nunca são comparados).
    faixas: dict[str, Counter[str]] = field(default_factory=dict)


def faixa_do_arquivo(nome: str) -> str:
    achado = _NUMERO_NA_BASE.search(nome)
    if achado is None:
        return "sem número"
    inicio = int(achado.group(1)) // TAMANHO_DA_FAIXA * TAMANHO_DA_FAIXA
    return f"{inicio}-{inicio + TAMANHO_DA_FAIXA - 1}"


def tabela_da_base(base: Base) -> str:
    """Por faixa de número: quantos PDFs em texto, imagem e texto ilegível. Só as faixas com
    algum PDF sem texto."""
    colunas = ("ok", "imagem (digitalizado)", "texto ilegível", "corrompido")
    linhas = [f"  {'faixa':<14}{'texto':>8}{'imagem':>8}{'ilegível':>10}{'corromp.':>10}"]

    def chave(faixa: str) -> int:
        return int(faixa.split("-")[0]) if faixa[0].isdigit() else -1

    for faixa in sorted(base.faixas, key=chave):
        q = base.faixas[faixa]
        if q[colunas[1]] + q[colunas[2]] == 0:
            continue
        linhas.append(f"  {faixa:<14}" + "".join(f"{q[c]:>{w}}" for c, w in zip(colunas, (8, 8, 10, 10))))
    return "\n".join(linhas)


def ano_do_caderno(caminho: Path) -> str:
    data = data_do_caderno(caminho.name)
    return str(data.year) if data else "sem data"


def _pdfs(pastas: list[Path]) -> list[Path]:
    return sorted({p for pasta in pastas for p in ([pasta] if pasta.is_file() else pasta.rglob("*.pdf"))})


def paginas_sem_texto(caderno: Path) -> tuple[int, list[int], list[int]]:
    """(total de páginas, digitalizadas, com texto ilegível), páginas em base 1."""
    digitalizadas: list[int] = []
    ilegiveis: list[int] = []
    with pymupdf.open(caderno) as doc:
        if doc.needs_pass:
            raise ValueError("PDF protegido por senha")
        for numero, pagina in enumerate(doc, start=1):
            texto = pagina.get_text("text")
            if _pagina_digitalizada(pagina, texto):
                digitalizadas.append(numero)
            elif texto.strip() and _ilegivel(texto):
                ilegiveis.append(numero)
        return doc.page_count, digitalizadas, ilegiveis


def levantar_cadernos(pastas: list[Path], leitor=None, limite_exemplos: int = 20) -> tuple[dict[str, Ano], list[str]]:
    """Conta as páginas sem texto dos cadernos, por ano. `leitor` (um `ocr.LeitorOcr`)
    liga a contagem de cabeçalhos de decreto nas páginas digitalizadas."""
    anos: dict[str, Ano] = {}
    falhas: list[str] = []
    for caderno in _pdfs(pastas):
        try:
            total, digitalizadas, ilegiveis = paginas_sem_texto(caderno)
        except Exception as erro:  # não abre, senha, estrutura quebrada
            falhas.append(f"{caderno.name}: {type(erro).__name__}: {erro}")
            continue
        ano = anos.setdefault(ano_do_caderno(caderno), Ano())
        ano.cadernos += 1
        ano.paginas += total
        ano.digitalizadas += len(digitalizadas)
        ano.ilegiveis += len(ilegiveis)
        sem_texto = sorted(digitalizadas + ilegiveis)
        if not sem_texto:
            continue
        ano.cadernos_com_digitalizadas += 1
        decretos = 0
        if leitor is not None:
            for numero in sem_texto:
                try:
                    decretos += len(CABECALHO_NO_OCR.findall(leitor.ler(caderno, numero).texto))
                except Exception as erro:  # ErroOcr: anota e segue
                    falhas.append(f"{caderno.name} p. {numero}: OCR falhou: {erro}")
            ano.decretos_no_ocr += decretos
        if len(ano.exemplos) < limite_exemplos:
            partes = []
            if digitalizadas:
                partes.append(f"digitalizadas {digitalizadas}")
            if ilegiveis:
                partes.append(f"ilegíveis {ilegiveis}")
            if leitor is not None:
                partes.append(f"{decretos} cabeçalho(s) de decreto no OCR")
            ano.exemplos.append(f"{caderno} | {total} p. | {'; '.join(partes)}")
    return anos, falhas


def levantar_base(pasta: Path) -> Base:
    """Classifica os PDFs da base (só a pasta, sem subpastas, como o pipeline) pela leitura."""
    base = Base()
    for pdf in sorted(p for p in pasta.iterdir() if p.is_file() and p.suffix.casefold() == ".pdf"):
        base.total += 1
        leitura = ler_pdf(pdf)
        if leitura.status is StatusLeitura.IMAGEM:
            situacao = "texto ilegível" if "ilegível" in leitura.detalhe else "imagem (digitalizado)"
            base.imagem.append(f"{pdf.name}: {leitura.detalhe}")
        else:
            situacao = leitura.status.value
        base.situacoes[situacao] += 1
        base.faixas.setdefault(faixa_do_arquivo(pdf.name), Counter())[situacao] += 1
    return base


def tabela(anos: dict[str, Ano], com_ocr: bool) -> str:
    cabecalho = (
        f"{'ano':<9}{'cadernos':>9}{'páginas':>9}{'cad. c/ img':>12}{'digitaliz.':>11}{'ilegíveis':>10}"
        + (f"{'decretos (OCR)':>16}" if com_ocr else "")
    )
    linhas = [cabecalho]
    for nome in sorted(anos):
        a = anos[nome]
        linhas.append(
            f"{nome:<9}{a.cadernos:>9}{a.paginas:>9}{a.cadernos_com_digitalizadas:>12}{a.digitalizadas:>11}"
            f"{a.ilegiveis:>10}" + (f"{a.decretos_no_ocr:>16}" if com_ocr else "")
        )
    linhas.append(
        "\n\"cad. c/ img\": cadernos com alguma página digitalizada ou com texto ilegível; "
        "os atos dessas páginas hoje não são vistos."
    )
    return "\n".join(linhas)


def _leitor_do_env():
    from dbf_cepe.config import carregar_configuracao
    from dbf_cepe.leitura import ocr

    cfg = carregar_configuracao()
    leitor = ocr.criar_leitor(cfg.ocr, cfg.cache_ocr)
    if leitor is None:
        raise ocr.ErroOcr("Tesseract não encontrado ou OCR_ATIVO=nao; rode python main.py --verificar-ocr")
    return leitor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pastas", nargs="*", type=Path, default=[PASTA_PADRAO],
                        help="cadernos ou pastas com cadernos (procura nas subpastas)")
    parser.add_argument("--base", type=Path, metavar="PASTA", help="também classifica os PDFs da base da DBF (só lê)")
    parser.add_argument("--ocr", action="store_true",
                        help="lê as páginas sem texto com o Tesseract e conta os cabeçalhos de decreto")
    parser.add_argument("--json", type=Path, metavar="ARQUIVO", help="grava o resultado completo em JSON")
    parser.add_argument("--exemplos", type=int, default=5, metavar="N",
                        help="quantos cadernos com páginas em imagem mostrar por ano (padrão: 5)")
    args = parser.parse_args(argv)
    faltando = [p for p in [*args.pastas, *([args.base] if args.base else [])] if not p.exists()]
    if faltando:
        print(f"Não encontrado: {', '.join(map(str, faltando))}", file=sys.stderr)
        return 2
    leitor = None
    if args.ocr:
        try:
            leitor = _leitor_do_env()
        except Exception as erro:
            print(f"Erro: {erro}", file=sys.stderr)
            return 2
    anos, falhas = levantar_cadernos(args.pastas, leitor, max(args.exemplos, 0))
    if not anos and not falhas and args.base is None:
        print("Nenhum PDF encontrado.", file=sys.stderr)
        return 2
    if anos:
        print(tabela(anos, leitor is not None))
        for nome in sorted(anos):
            if anos[nome].exemplos[: args.exemplos]:
                print(f"\n{nome}:")
                for exemplo in anos[nome].exemplos[: args.exemplos]:
                    print(f"  {exemplo}")
    base = levantar_base(args.base) if args.base else None
    if base is not None:
        print(f"\nBase da DBF ({args.base}): {base.total} PDFs")
        for situacao, quantidade in base.situacoes.most_common():
            print(f"  {quantidade:>6}  {situacao}")
        print("\nPor faixa de número (só as faixas com PDF sem texto):")
        print(tabela_da_base(base))
        print()
        for linha in base.imagem[: max(args.exemplos, 0) * 4]:
            print(f"  {linha}")
        if len(base.imagem) > args.exemplos * 4:
            print(f"  ... e mais {len(base.imagem) - args.exemplos * 4} (lista completa no --json)")
    for falha in falhas:
        print(f"AVISO: {falha}", file=sys.stderr)
    if args.json:
        dados: dict[str, object] = {"cadernos": {nome: a.__dict__ for nome, a in anos.items()}}
        if base is not None:
            dados["base"] = {
                "total": base.total, "situacoes": dict(base.situacoes), "imagem": base.imagem,
                "faixas": {faixa: dict(q) for faixa, q in base.faixas.items()},
            }
        args.json.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
