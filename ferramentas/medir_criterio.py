"""Ferramenta auxiliar (fora do pipeline): mede, por ano, quantos decretos dos cadernos
seriam REPROVADOS pelo critério de aprovação da leitura em cascata, e por quê.

É a etapa A do plano da leitura em cascata (ver docs/funcionamento.md, "Cadernos
antigos"). A cascata relê um decreto com outra estratégia (fecho antigo, leitura por
coluna) quando a leitura atual não passa no critério. Esta ferramenta mostra quantos
decretos cada estratégia precisa recuperar, e serve de régua antes e depois de cada
etapa. Só LÊ os cadernos; não grava nada além do JSON pedido em --json.

Uso:
    python -m ferramentas.medir_criterio                          # (da raiz) lê dados/cadernos
    python -m ferramentas.medir_criterio dados/sondagem           # procura PDFs nas subpastas
    python -m ferramentas.medir_criterio dados/cadernos --json saida.json --exemplos 10

O critério é o de `dbf_cepe/leitura/criterio.py` e os níveis de recuperação são os de
`dbf_cepe/leitura/cascata.py`: "recuperados" são os decretos que reprovam na leitura
atual e são aprovados num nível de recuperação; "reprovados", os que nenhum nível
aprova (na tabela, "nenhum"; o da DBF vai para revisão manual), com os motivos do nível 1.

O ano é o do nome do caderno (AAAAMMDD_...); caderno sem data no nome conta como
"sem data".
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from dbf_cepe.leitura import criterio
from dbf_cepe.leitura.cascata import NIVEIS, avaliar
from dbf_cepe.leitura.separador import ErroSeparacao, data_do_caderno, separar_caderno

PASTA_PADRAO = Path("dados/cadernos")


@dataclass
class Ano:
    decretos: int = 0
    reprovados: int = 0
    dbf: int = 0
    dbf_reprovados: int = 0
    recuperados: int = 0  # reprovados no nível 1 e aprovados num nível de recuperação
    por_nivel: Counter[int] = field(default_factory=Counter)  # recuperados, por nível que os leu
    motivos: Counter[str] = field(default_factory=Counter)
    exemplos: list[str] = field(default_factory=list)

    @property
    def nivel_1(self) -> int:
        return self.decretos - self.recuperados - self.reprovados

    def para_dict(self) -> dict[str, object]:
        return {
            **self.__dict__, "motivos": dict(self.motivos.most_common()),
            "por_nivel": {str(n): q for n, q in sorted(self.por_nivel.items())},
        }


def ano_do_caderno(caminho: Path) -> str:
    data = data_do_caderno(caminho.name)
    return str(data.year) if data else "sem data"


def medir(pastas: list[Path], limite_exemplos: int = 20) -> tuple[dict[str, Ano], list[str]]:
    """Aplica o critério a todos os decretos dos PDFs das pastas (e subpastas)."""
    anos: dict[str, Ano] = {}
    falhas: list[str] = []
    cadernos = sorted({p for pasta in pastas for p in ([pasta] if pasta.is_file() else pasta.rglob("*.pdf"))})
    for caderno in cadernos:
        try:
            atos = separar_caderno(caderno).atos
        except ErroSeparacao as erro:
            falhas.append(f"{caderno.name}: {erro}")
            continue
        ano = anos.setdefault(ano_do_caderno(caderno), Ano())
        for ato in atos:
            da_dbf = criterio.da_dbf(ato)
            achados = criterio.motivos_de_reprovacao(ato, da_dbf)  # motivos no nível 1
            ano.decretos += 1
            ano.dbf += da_dbf
            if not achados:
                continue
            final = avaliar(ato, NIVEIS, caderno)
            if final.nivel_de_leitura is not None:
                ano.recuperados += 1
                ano.por_nivel[final.nivel_de_leitura] += 1
                continue
            ano.reprovados += 1
            ano.dbf_reprovados += da_dbf
            ano.motivos.update(achados)
            if len(ano.exemplos) < limite_exemplos:
                ano.exemplos.append(
                    f"{caderno.name} p. {ato.pagina_inicial} | {ato.especie.nome} {ato.numero}"
                    f"{' (DBF)' if da_dbf else ''} | {', '.join(achados)}"
                )
    return anos, falhas


def tabela(anos: dict[str, Ano]) -> str:
    """Por ano: em que nível da cascata cada decreto foi lido ("nenhum": reprovado em todos
    os níveis), para todos os decretos e para os da DBF, e os motivos dos "nenhum"."""
    niveis = [1, *(n.numero for n in NIVEIS)]
    colunas_todos = "".join(f"{f'nível {n}':>9}" for n in niveis) + f"{'nenhum':>9}"
    largura_todos = 7 + len(colunas_todos)
    linhas = [
        f"{'':<9} {'todos os decretos: lidos em qual nível':<{largura_todos}}   {'da DBF':<16}",
        f"{'ano':<9} {'total':>7}{colunas_todos}   {'total':>7}{'nenhum':>9}   "
        "por que a leitura atual reprovou os \"nenhum\"",
    ]
    for nome in sorted(anos, reverse=True):
        a = anos[nome]
        lidos = [a.nivel_1, *(a.por_nivel[n] for n in niveis[1:])]
        detalhe = ", ".join(f"{m} {n}" for m, n in a.motivos.most_common()) or "-"
        linhas.append(
            f"{nome:<9} {a.decretos:>7}" + "".join(f"{q:>9}" for q in lidos) + f"{a.reprovados:>9}   "
            f"{a.dbf:>7}{a.dbf_reprovados:>9}   {detalhe}"
        )
    linhas.append(
        "\n\"nenhum\": nenhum nível da cascata leu o decreto inteiro; o da DBF vai para revisão manual, "
        "o de fora da DBF é ignorado pelo pipeline."
    )
    return "\n".join(linhas)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pastas", nargs="*", type=Path, default=[PASTA_PADRAO],
                        help="cadernos ou pastas com cadernos (procura nas subpastas)")
    parser.add_argument("--json", type=Path, metavar="ARQUIVO", help="grava o resultado completo em JSON")
    parser.add_argument("--exemplos", type=int, default=5, metavar="N",
                        help="quantos decretos reprovados mostrar por ano (padrão: 5)")
    args = parser.parse_args(argv)
    faltando = [p for p in args.pastas if not p.exists()]
    if faltando:
        print(f"Não encontrado: {', '.join(map(str, faltando))}", file=sys.stderr)
        return 2
    anos, falhas = medir(args.pastas, max(args.exemplos, 0))
    if not anos and not falhas:
        print("Nenhum PDF encontrado.", file=sys.stderr)
        return 2
    print(tabela(anos))
    for nome in sorted(anos, reverse=True):
        if anos[nome].exemplos[: args.exemplos]:
            print(f"\n{nome}:")
            for exemplo in anos[nome].exemplos[: args.exemplos]:
                print(f"  {exemplo}")
    for falha in falhas:
        print(f"AVISO: caderno não lido: {falha}", file=sys.stderr)
    if args.json:
        args.json.write_text(
            json.dumps({nome: a.para_dict() for nome, a in anos.items()}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
