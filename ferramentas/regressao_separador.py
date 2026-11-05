"""Ferramenta auxiliar (fora do pipeline) — confere que a separação dos decretos não mudou.

Pega o `separador.py` e o `especies.py` de um commit antigo (via `git show`), roda os dois
separadores nos mesmos PDFs e diz, caderno a caderno, se os decretos separados (número, texto,
páginas e avisos) são idênticos. Só lê: nada é gravado no repositório (os módulos antigos vão
para uma pasta temporária). Serve para provar que uma mudança não altera o texto que vai para a
comparação com a base.

Uso (da raiz do repositório):
    python -m ferramentas.regressao_separador 0c54c8b
    python -m ferramentas.regressao_separador main dados/cadernos tests/amostras_cadernos

Sai com código 0 se tudo for idêntico e 1 se houver diferença.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

PASTAS_PADRAO = (Path("dados/cadernos"), Path("tests/amostras_cadernos"))


def _resumo(resultado):
    return (
        [(a.numero, a.cabecalho, a.texto, a.pagina_inicial, a.pagina_final, a.avisos) for a in resultado.atos],
        resultado.avisos,
        resultado.paginas,
        resultado.paginas_digitalizadas,
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__)
        return 2
    base, pastas = args[0], [Path(p) for p in args[1:]] or list(PASTAS_PADRAO)
    sys.path.insert(0, str(Path.cwd()))
    with tempfile.TemporaryDirectory() as tmp:
        for nome in ("separador", "especies"):
            fonte = subprocess.run(
                ["git", "show", f"{base}:{nome}.py"], capture_output=True, check=True
            ).stdout.decode("utf-8")
            fonte = fonte.replace("from especies import", "from especies_antes import")
            (Path(tmp) / f"{nome}_antes.py").write_text(fonte, encoding="utf-8")
        sys.path.insert(1, tmp)
        import especies_antes
        import separador as depois
        import separador_antes as antes

        pdfs = [pdf for pasta in pastas for pdf in sorted(pasta.glob("*.pdf"))]
        if not pdfs:
            print(f"nenhum PDF em {', '.join(map(str, pastas))}")
            return 2
        tudo, total = True, 0
        for pdf in pdfs:
            a = _resumo(antes.separar_caderno(pdf, (especies_antes.DECRETO,)))
            d = _resumo(depois.separar_caderno(pdf))
            total += len(d[0])
            tudo &= a == d
            print(f"{pdf.name[:44]:<44} decretos={len(d[0]):>3}  {'IDÊNTICO' if a == d else 'DIFERENTE'}")
    print(f"\n{total} decretos separados; tudo idêntico ao commit {base}: {tudo}")
    return 0 if tudo else 1


if __name__ == "__main__":
    sys.exit(main())
