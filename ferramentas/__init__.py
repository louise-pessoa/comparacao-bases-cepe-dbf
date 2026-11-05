"""Ferramentas auxiliares, fora do pipeline. Rodar da raiz: python -m ferramentas.<nome>.

Põem src/ no caminho de importação, para funcionar sem instalar o pacote."""

import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
