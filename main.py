"""Atalho: `python main.py ...` roda a linha de comando do pacote (src/dbf_cepe/cli.py) sem instalar
nada. Com o pacote instalado (`pip install -e .`), também valem `dbf-cepe ...` e `python -m dbf_cepe ...`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from dbf_cepe.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
