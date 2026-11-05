"""Ferramenta auxiliar (fora do pipeline) — confere que a separação dos decretos não mudou.

Extrai um commit antigo para uma pasta temporária (`git archive`), roda o separador dele e o
atual nos mesmos PDFs, cada um em um processo próprio, e diz, caderno a caderno, se os decretos
separados (número, cabeçalho, texto, páginas e avisos) são idênticos. Funciona com o layout
antigo (módulos na raiz) e com o atual (pacote em src/dbf_cepe). Só lê: nada é gravado no
repositório.

Uso (da raiz do repositório):
    python -m ferramentas.regressao_separador main
    python -m ferramentas.regressao_separador 0c54c8b dados/cadernos tests/amostras_cadernos

Sai com código 0 se tudo for idêntico e 1 se houver diferença.
"""

from __future__ import annotations

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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
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
    for pdf in pdfs:
        igual = antes[pdf] == depois[pdf]
        tudo &= igual
        total += len(depois[pdf]) - 1
        print(f"{Path(pdf).name[:44]:<44} decretos={len(depois[pdf]) - 1:>3}  {'IDÊNTICO' if igual else 'DIFERENTE'}")
    print(f"\n{total} decretos separados; tudo idêntico ao commit {base}: {tudo}")
    return 0 if tudo else 1


if __name__ == "__main__":
    sys.exit(main())
