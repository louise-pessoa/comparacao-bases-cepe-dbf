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

Critério (um decreto só passa se cumprir tudo):
  - fecho: "...Independência do Brasil." exatamente uma vez. Sem ele, o motivo é
    "fecho antigo" se houver o fecho dos cadernos até 2010 ("PALÁCIO DO CAMPO DAS
    PRINCESAS, em 18 de março de 2010."), senão "sem fecho"; duas ou mais vezes,
    "fecho repetido" (o decreto levou o final de outro);
  - abertura: logo depois do cabeçalho, "A GOVERNADORA"/"O GOVERNADOR" ou, para quem
    substitui, "NO EXERCÍCIO DO CARGO DE GOVERNADOR(A)" (vice, presidente da Assembleia).
    Decisão de 09/11/2026;
  - nenhum outro cabeçalho "DECRETO Nº" no início de linha dentro do texto;
  - nenhum trecho do expediente do Diário (o quadro com governadora, secretários,
    preços e endereço da CEPE) dentro do texto;
  - decreto de programa da DBF: tipo reconhecido.

O ano é o do nome do caderno (AAAAMMDD_...); caderno sem data no nome conta como
"sem data".
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from dbf_cepe.leitura.identificador import identificar
from dbf_cepe.leitura.separador import AtoSeparado, ErroSeparacao, data_do_caderno, separar_caderno
from dbf_cepe.regras.especies import DECRETO, Especie
from dbf_cepe.regras.programas import em_escopo, programas_do_ato

PASTA_PADRAO = Path("dados/cadernos")

SEM_FECHO = "sem fecho"
FECHO_ANTIGO = "fecho antigo"
FECHO_REPETIDO = "fecho repetido"
SEM_ABERTURA = "sem abertura"
OUTRO_CABECALHO = "outro cabeçalho"
EXPEDIENTE = "expediente"
DBF_SEM_TIPO = "DBF sem tipo"

# Fecho dos decretos até 2010 (a troca foi entre set/2010 e mar/2012).
_FECHO_ANTIGO = re.compile(
    r"PAL[ÁA]CIO\s+DO\s+CAMPO\s+DAS\s+PRINCESAS,\s+em\s+\d{1,2}º?\s+de\s+\w+\s+de\s+\d{4}\.",
    re.IGNORECASE,
)
# Quem substitui o titular abre com o próprio cargo: "A VICE GOVERNADORA, NO EXERCÍCIO DO
# CARGO DE GOVERNADORA DO ESTADO", "O PRESIDENTE DA ASSEMBLÉIA LEGISLATIVA ..., NO EXERCÍCIO
# DO CARGO DE GOVERNADOR DO ESTADO".
_ABERTURA_SUBSTITUTO = re.compile(r"\bNO\s+EXERC[ÍI]CIO\s+DO\s+CARGO\s+DE\s+GOVERNADORA?\b")
_JANELA_ABERTURA = 1800  # caracteres desde o início do decreto
# Trechos que só existem no expediente do Diário.
_EXPEDIENTE = re.compile(
    r"COMPANHIA\s+EDITORA\s+DE\s+PERNAMBUCO|SECRET\S{1,2}RIOS\s+DE\s+ESTADO|DIRETOR\s+PRESIDENTE"
    r"|Quaisquer\s+reclama[çc][õo]es\s+sobre\s+mat[ée]rias",
    re.IGNORECASE,
)


def motivos(ato: AtoSeparado, da_dbf: bool, especie: Especie = DECRETO) -> list[str]:
    """Por que o decreto reprova no critério; lista vazia = aprovado."""
    texto = ato.texto
    resultado: list[str] = []
    fechos = len(especie.fecho.findall(texto)) if especie.fecho else 1
    if fechos == 0:
        resultado.append(FECHO_ANTIGO if _FECHO_ANTIGO.search(texto) else SEM_FECHO)
    elif fechos > 1:
        resultado.append(FECHO_REPETIDO)
    inicio = texto[:_JANELA_ABERTURA]
    abertura = especie.abertura_do_corpo
    if abertura and not (abertura.search(inicio) or _ABERTURA_SUBSTITUTO.search(inicio)):
        resultado.append(SEM_ABERTURA)
    for achado in especie.cabecalho.finditer(texto):
        inicio_da_linha = texto.rfind("\n", 0, achado.start()) + 1
        if achado.start() > 0 and not texto[inicio_da_linha:achado.start()].strip():
            resultado.append(OUTRO_CABECALHO)
            break
    if _EXPEDIENTE.search(texto):
        resultado.append(EXPEDIENTE)
    if da_dbf and identificar(texto).tipo is None:
        resultado.append(DBF_SEM_TIPO)
    return resultado


@dataclass
class Ano:
    decretos: int = 0
    reprovados: int = 0
    dbf: int = 0
    dbf_reprovados: int = 0
    motivos: Counter[str] = field(default_factory=Counter)
    exemplos: list[str] = field(default_factory=list)

    def para_dict(self) -> dict[str, object]:
        return {**self.__dict__, "motivos": dict(self.motivos.most_common())}


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
            da_dbf = em_escopo(ato.especie, programas_do_ato(ato.texto, ato.especie))
            achados = motivos(ato, da_dbf, ato.especie)
            ano.decretos += 1
            ano.dbf += da_dbf
            if achados:
                ano.reprovados += 1
                ano.dbf_reprovados += da_dbf
                ano.motivos.update(achados)
                if len(ano.exemplos) < limite_exemplos:
                    ano.exemplos.append(
                        f"{caderno.name} p. {ato.pagina_inicial} | Decreto {ato.numero}"
                        f"{' (DBF)' if da_dbf else ''} | {', '.join(achados)}"
                    )
    return anos, falhas


def tabela(anos: dict[str, Ano]) -> str:
    linhas = ["ano       decretos  reprovados  DBF  DBF reprovados  motivos"]
    for nome in sorted(anos, reverse=True):
        a = anos[nome]
        detalhe = ", ".join(f"{m} {n}" for m, n in a.motivos.most_common()) or "-"
        linhas.append(f"{nome:<9} {a.decretos:>8}  {a.reprovados:>10}  {a.dbf:>3}  {a.dbf_reprovados:>14}  {detalhe}")
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
