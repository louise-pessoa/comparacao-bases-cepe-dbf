"""Ferramenta auxiliar (fora do pipeline) — confere que a separação dos decretos não mudou.

Extrai um commit antigo para uma pasta temporária (`git archive`), roda o separador dele e o
atual nos mesmos PDFs, cada um em um processo próprio, e diz, caderno a caderno, se os decretos
separados (número, cabeçalho, texto, páginas e avisos) são idênticos. Funciona com o layout
antigo (módulos na raiz) e com o atual (pacote em src/dbf_cepe). Só lê: nada é gravado no
repositório.

Uso (da raiz do repositório):
    python -m ferramentas.regressao_separador main
    python -m ferramentas.regressao_separador 0c54c8b dados/cadernos tests/amostras_cadernos
    python -m ferramentas.regressao_separador main --detalhes   # o que mudou, decreto a decreto

Com --detalhes, cada decreto diferente aparece com as linhas que saíram e as que entraram no
texto e os avisos que mudaram, e o fim traz quantos decretos tiveram cada tipo de mudança
("só linhas removidas", "linhas acrescentadas", "só avisos", "decreto novo ou sumido").

Sai com código 0 se tudo for idêntico e 1 se houver diferença.
"""

from __future__ import annotations

import difflib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

PASTAS_PADRAO = ("dados/cadernos", "tests/amostras_cadernos")

# Roda dentro de cada versão: importa o separador do layout que existir e imprime JSON.
_SEPARAR = r"""
import json, sys
try:
    from dbf_cepe.leitura import separador
except ImportError:
    import separador
saida = {}
for pdf in sys.argv[1:]:
    r = separador.separar_caderno(pdf)
    saida[pdf] = [[a.numero, a.cabecalho, a.texto, a.pagina_inicial, a.pagina_final, list(a.avisos)] for a in r.atos]
    saida[pdf].append([list(r.avisos), r.paginas, list(r.paginas_digitalizadas)])
print(json.dumps(saida, ensure_ascii=False))
"""


def _separar(raiz: Path, pdfs: list[str]) -> dict:
    caminho = f"{raiz / 'src'}{';' if sys.platform == 'win32' else ':'}{raiz}"
    resultado = subprocess.run(
        [sys.executable, "-c", _SEPARAR, *pdfs],
        cwd=raiz, env={**__import__("os").environ, "PYTHONPATH": caminho},
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return json.loads(resultado.stdout)


_AMOSTRA_DE_LINHAS = 3


def _resumo(texto: str, limite: int = 70) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= limite else texto[: limite - 1] + "…"


def detalhar(antes: list, depois: list) -> tuple[list[str], list[str]]:
    """Linhas de relatório e o tipo de cada mudança, decreto a decreto, entre duas
    separações do mesmo caderno (listas de [numero, cabecalho, texto, pag_ini, pag_fim,
    avisos], com os dados do caderno no último item)."""
    def por_chave(atos: list) -> dict:
        return {(a[0], a[3]): a for a in atos[:-1]}

    a, d = por_chave(antes), por_chave(depois)
    linhas, tipos = [], []
    for chave in sorted(a.keys() | d.keys(), key=lambda k: (k[1], k[0])):
        rotulo = f"  Decreto {chave[0]} (p. {chave[1]})"
        if chave not in a or chave not in d:
            linhas.append(f"{rotulo}: {'novo' if chave not in a else 'sumiu'}")
            tipos.append("decreto novo ou sumido")
            continue
        velho, novo = a[chave], d[chave]
        if velho == novo:
            continue
        diff = list(difflib.ndiff(velho[2].split("\n"), novo[2].split("\n")))
        saiu = [x[2:] for x in diff if x.startswith("- ")]
        entrou = [x[2:] for x in diff if x.startswith("+ ")]
        avisos_saiu = [x for x in velho[5] if x not in novo[5]]
        avisos_entrou = [x for x in novo[5] if x not in velho[5]]
        partes = []
        if saiu:
            partes.append(f"{len(saiu)} linha(s) saíram ({' | '.join(_resumo(x, 40) for x in saiu[:_AMOSTRA_DE_LINHAS])}"
                          f"{' | …' if len(saiu) > _AMOSTRA_DE_LINHAS else ''})")
        if entrou:
            partes.append(f"{len(entrou)} linha(s) entraram ({' | '.join(_resumo(x, 40) for x in entrou[:_AMOSTRA_DE_LINHAS])}"
                          f"{' | …' if len(entrou) > _AMOSTRA_DE_LINHAS else ''})")
        partes += [f"aviso saiu: {_resumo(x)}" for x in avisos_saiu]
        partes += [f"aviso entrou: {_resumo(x)}" for x in avisos_entrou]
        if velho[1] != novo[1] or velho[4] != novo[4]:
            partes.append(f"cabeçalho/página final: {velho[1]!r} p. {velho[4]} -> {novo[1]!r} p. {novo[4]}")
        linhas.append(f"{rotulo}: " + "; ".join(partes or ["mudança sem diferença de linhas"]))
        tipos.append(
            "linhas acrescentadas" if entrou else "só linhas removidas" if saiu else "só avisos"
        )
    if antes[-1] != depois[-1]:
        linhas.append(f"  avisos do caderno: {antes[-1][0]} -> {depois[-1][0]}")
        tipos.append("avisos do caderno")
    return linhas, tipos


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    detalhes = "--detalhes" in args
    args = [x for x in args if x != "--detalhes"]
    if not args:
        print(__doc__)
        return 2
    base, pastas = args[0], args[1:] or list(PASTAS_PADRAO)
    pdfs = [str(pdf.resolve()) for pasta in pastas for pdf in sorted(Path(pasta).glob("*.pdf"))]
    if not pdfs:
        print(f"nenhum PDF em {', '.join(pastas)}")
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        arquivo = subprocess.run(["git", "archive", base], capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(arquivo)) as tar:
            tar.extractall(tmp, filter="data")
        antes = _separar(Path(tmp), pdfs)
    depois = _separar(Path.cwd(), pdfs)
    tudo, total = True, 0
    contagem: dict[str, int] = {}
    for pdf in pdfs:
        igual = antes[pdf] == depois[pdf]
        tudo &= igual
        total += len(depois[pdf]) - 1
        print(f"{Path(pdf).name[:44]:<44} decretos={len(depois[pdf]) - 1:>3}  {'IDÊNTICO' if igual else 'DIFERENTE'}")
        if detalhes and not igual:
            linhas, tipos = detalhar(antes[pdf], depois[pdf])
            print("\n".join(linhas))
            for tipo in tipos:
                contagem[tipo] = contagem.get(tipo, 0) + 1
    print(f"\n{total} decretos separados; tudo idêntico ao commit {base}: {tudo}")
    if detalhes and contagem:
        print("Mudanças: " + ", ".join(f"{tipo} {n}" for tipo, n in sorted(contagem.items())))
    return 0 if tudo else 1


if __name__ == "__main__":
    sys.exit(main())
