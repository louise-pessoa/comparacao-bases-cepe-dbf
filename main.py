"""Encaminhe os comandos para a CLI do pipeline sem exigir instalação do pacote.

Etapa: infraestrutura/CLI; chama `dbf_cepe.cli.main`, que encadeia as etapas 1 a 5.
Entrada / Saída: recebe os argumentos da linha de comando e devolve o código de saída
da CLI; os modos de gravação produzem PDFs, logs e o manifesto nas pastas configuradas.
Decisões e limites: `--simular` não move, apaga nem grava arquivos; código 1 indica
falha ao gravar ou ato com falha, enquanto 0 indica sucesso e 2 erro de uso/configuração.

Uso: `python main.py --simular`, `python main.py --baixar --de AAAA-MM-DD`,
`python main.py --separar CADERNO.pdf` ou `python main.py --catalogar PASTA`.
Com `pip install -e .`, os equivalentes são `dbf-cepe` e `python -m dbf_cepe`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from dbf_cepe.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
