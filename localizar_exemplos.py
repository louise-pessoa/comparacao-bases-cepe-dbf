"""Ferramenta auxiliar (fora do pipeline) — acha, nos cadernos já baixados, páginas
com erratas, marcas de republicação e decretos que aparecem mais de uma vez.

Serve para reunir exemplos reais antes de escrever as regras de errata e de
republicação. Só LÊ os cadernos; as páginas achadas são copiadas, uma por PDF, para
uma pasta de exemplos (nunca sobrescreve), junto de um `relatorio.txt`. As páginas
são do Diário Oficial (publicação pública), como as amostras versionadas em
`tests/amostras_cadernos/`.

Uso:
    python localizar_exemplos.py                          # lê dados/cadernos, grava em dados/exemplos
    python localizar_exemplos.py dados/cadernos --saida dados/exemplos
    python localizar_exemplos.py --sem-paginas           # só o relatório, sem copiar páginas

Os critérios são aproximados, de propósito: é preferível mostrar uma página a mais
do que perder um exemplo.
  - errata: linha que começa com "ERRATA" (ou "Errata"). O relatório diz a que ato
    ela se refere (decreto, portaria, edital, despacho...), com o número, se houver;
  - republicação: as fórmulas que o Diário usa, como "(REPUBLICADO POR HAVER SAÍDO COM
    INCORREÇÃO NO ORIGINAL)", "republicada por haver inconsistência", "1ª/2ª
    REPUBLICAÇÃO DE EDITAL", "aviso de republicação". Citações como "o Decreto nº
    56.378, republicado no DOE de 06/06/2024" e a "Revolução Republicana" do fecho
    dos decretos NÃO contam;
  - retificação: linha que começa com "RETIFICAÇÃO" ou "AVISO DE RETIFICAÇÃO"
    (parecida com a errata, mas de outro mecanismo; separada para não misturar);
  - decreto repetido: o mesmo "DECRETO Nº" em maiúsculas, no início de uma linha,
    em mais de uma página, no mesmo caderno ou em cadernos diferentes;
  - cabeçalho de edital/portaria (levantamento de formato, para escrever as RegEx dessas
    espécies): linha em maiúsculas que começa com "EDITAL" ou "PORTARIA". O relatório
    agrupa os cabeçalhos por FORMATO (os números viram "N"), com a contagem e um
    exemplo de cada, e só as primeiras páginas de cada formato são copiadas.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from atos_acessorios import ERRATA_LINHA as _ERRATA
from atos_acessorios import REPUBLICACAO_MARCADOR as _REPUBLICACAO
from atos_acessorios import RETIFICACAO_LINHA as _RETIFICACAO
from atos_acessorios import ato_referido as _ato_referido

PASTA_CADERNOS_PADRAO = Path("dados/cadernos")
PASTA_EXEMPLOS_PADRAO = Path("dados/exemplos")

ESPECIES_CABECALHO_PADRAO = ("EDITAL", "PORTARIA")
_TAMANHO_DO_FORMATO = 70
_DECRETO = re.compile(r"^[ \t]*DECRETO\s+N[ºo°]\s*(\d[\d.]*)", re.M)

ERRATA = "errata"
REPUBLICACAO = "republicacao"
RETIFICACAO = "retificacao"
DECRETO_REPETIDO = "decreto-repetido"
PREFIXO_CABECALHO = "cabecalho-"  # ex.: "cabecalho-edital"


@dataclass(frozen=True)
class Achado:
    caderno: Path
    pagina: int  # 1 = primeira página
    tipo: str
    trecho: str
    # errata: a que ato se refere ("Decreto 61.560", "Despacho", "?");
    # cabeçalho: o formato, com os números trocados por N ("EDITAL DBF Nº N/N")
    referente: str = ""


def _compactar(texto: str, limite: int) -> str:
    return " ".join(texto.split())[:limite]


def ato_referido(trecho: str) -> str:
    """O ato que a errata corrige, lido do começo do trecho: "Decreto 61.560", "Despacho" (sem
    número) ou "?" se não achou nenhuma espécie conhecida. A RegEx mora em atos_acessorios."""
    return _ato_referido(trecho)


def formato_do_cabecalho(trecho: str) -> str:
    """A primeira linha do cabeçalho sem os números: "EDITAL DBF Nº 12/2026" -> "EDITAL DBF Nº N/N".
    Só a primeira linha, para que a ementa (que muda de ato para ato) não separe os formatos."""
    primeira_linha = next((linha for linha in trecho.splitlines() if linha.strip()), "")
    return re.sub(r"\d+", "N", _compactar(primeira_linha, _TAMANHO_DO_FORMATO))


def _regex_cabecalhos(especies: tuple[str, ...]) -> re.Pattern[str] | None:
    if not especies:
        return None
    alternativas = "|".join(re.escape(e) for e in sorted(especies, key=len, reverse=True))
    # só maiúsculas, no início da linha: "Portaria nº 12" no meio do texto não conta
    return re.compile(rf"^[ \t]*(?:{alternativas})\b", re.M)


def _achados_da_pagina(
    caderno: Path,
    pagina: int,
    texto: str,
    cabecalhos: dict[str, re.Pattern[str]] | None = None,
) -> list[Achado]:
    achados = []
    for especie, regex in (cabecalhos or {}).items():
        for m in regex.finditer(texto):
            bruto = texto[m.start():m.start() + 400]
            achados.append(
                Achado(
                    caderno, pagina, PREFIXO_CABECALHO + especie.casefold(),
                    _compactar(bruto, 200), formato_do_cabecalho(bruto),
                )
            )
    for m in _ERRATA.finditer(texto):
        trecho = _compactar(texto[m.start():], 300)
        achados.append(Achado(caderno, pagina, ERRATA, trecho, ato_referido(trecho)))
    for m in _RETIFICACAO.finditer(texto):
        achados.append(Achado(caderno, pagina, RETIFICACAO, _compactar(texto[m.start():], 300)))
    for m in _REPUBLICACAO.finditer(texto):
        inicio = max(0, m.start() - 120)
        achados.append(Achado(caderno, pagina, REPUBLICACAO, _compactar(texto[inicio:m.end() + 120], 300)))
    return achados


def procurar(
    pasta_cadernos: Path,
    especies_cabecalho: tuple[str, ...] = ESPECIES_CABECALHO_PADRAO,
) -> tuple[list[Achado], list[str]]:
    """Devolve os achados (errata, republicação, decreto repetido e cabeçalhos das espécies
    pedidas) e os avisos."""
    cabecalhos = {e: _regex_cabecalhos((e,)) for e in especies_cabecalho}
    achados: list[Achado] = []
    avisos: list[str] = []
    decretos: dict[str, list[tuple[Path, int]]] = {}
    cadernos = sorted(p for p in pasta_cadernos.glob("*.pdf") if p.is_file())
    if not cadernos:
        avisos.append(f"nenhum PDF em {pasta_cadernos}")
    for caderno in cadernos:
        try:
            with pymupdf.open(caderno) as doc:
                if doc.needs_pass:
                    avisos.append(f"{caderno.name}: PDF protegido por senha; ignorado")
                    continue
                for indice, pagina in enumerate(doc, start=1):
                    texto = pagina.get_text()
                    achados.extend(_achados_da_pagina(caderno, indice, texto, cabecalhos))
                    for m in _DECRETO.finditer(texto):
                        decretos.setdefault(m.group(1).replace(".", ""), []).append((caderno, indice))
        except Exception as erro:  # arquivo corrompido não interrompe a busca
            avisos.append(f"{caderno.name}: não foi possível ler ({type(erro).__name__}: {erro})")
    for numero, onde in sorted(decretos.items()):
        if len(set(onde)) > 1:
            for caderno, pagina in sorted(set(onde)):
                achados.append(
                    Achado(caderno, pagina, f"{DECRETO_REPETIDO}-{numero}", f"DECRETO Nº {numero} aparece {len(set(onde))} vezes")
                )
    achados.sort(key=lambda a: (a.caderno.name, a.pagina, a.tipo))
    return achados, avisos


def _nome_da_pagina(achado: Achado) -> str:
    return f"{achado.caderno.stem}_p{achado.pagina:03d}_{achado.tipo}.pdf"


def copiar_paginas(achados: list[Achado], pasta_saida: Path) -> list[Path]:
    """Copia cada página achada para um PDF próprio, sem sobrescrever nenhum arquivo."""
    pasta_saida.mkdir(parents=True, exist_ok=True)
    copiadas: list[Path] = []
    for achado in achados:
        destino = pasta_saida / _nome_da_pagina(achado)
        if destino.exists() or destino in copiadas:
            continue
        with pymupdf.open(achado.caderno) as origem, pymupdf.open() as novo:
            novo.insert_pdf(origem, from_page=achado.pagina - 1, to_page=achado.pagina - 1)
            novo.save(destino)
        copiadas.append(destino)
    return copiadas


def _e_cabecalho(achado: Achado) -> bool:
    return achado.tipo.startswith(PREFIXO_CABECALHO)


def selecionar_para_copia(achados: list[Achado], por_formato: int = 2) -> list[Achado]:
    """Errata, republicação etc. são copiadas todas; dos cabeçalhos (que podem ser
    centenas), só as primeiras `por_formato` páginas de cada formato."""
    vistos: Counter[tuple[str, str]] = Counter()
    escolhidos = []
    for achado in achados:
        if _e_cabecalho(achado):
            chave = (achado.tipo, achado.referente)
            vistos[chave] += 1
            if vistos[chave] > por_formato:
                continue
        escolhidos.append(achado)
    return escolhidos


def _especie_do_referente(referente: str) -> str:
    return re.sub(r"\s+\d[\d./-]*$", "", referente)  # tira o número, fica a espécie


def montar_relatorio(achados: list[Achado], avisos: list[str]) -> str:
    linhas = [f"{len(achados)} página(s)/trecho(s) para olhar.", ""]
    erratas = Counter(_especie_do_referente(a.referente) for a in achados if a.tipo == ERRATA)
    if erratas:
        linhas.append("Erratas por ato referido: " + ", ".join(f"{e} {n}" for e, n in erratas.most_common()))
        linhas.append("")
    formatos: dict[tuple[str, str], list[Achado]] = {}
    for achado in achados:
        if _e_cabecalho(achado):
            formatos.setdefault((achado.tipo, achado.referente), []).append(achado)
    if formatos:
        linhas.append("Formatos de cabeçalho encontrados (números trocados por N):")
        for (tipo, formato), grupo in sorted(formatos.items(), key=lambda kv: (kv[0][0], -len(kv[1]))):
            exemplo = grupo[0]
            linhas.append(f"  [{tipo}] {len(grupo)}x  {formato}")
            linhas.append(f"      ex.: {exemplo.caderno.name}, página {exemplo.pagina}: {exemplo.trecho}")
        linhas.append("")
    for achado in achados:
        if _e_cabecalho(achado):
            linhas.append(f"[{achado.tipo}] {achado.caderno.name}, página {achado.pagina} | formato: {achado.referente}")
            continue
        refere = f" | refere-se a: {achado.referente}" if achado.referente else ""
        linhas.append(f"[{achado.tipo}] {achado.caderno.name}, página {achado.pagina}{refere}")
        linhas.append(f"    {achado.trecho}")
    if avisos:
        linhas += ["", "Avisos:", *[f"  - {aviso}" for aviso in avisos]]
    return "\n".join(linhas) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Acha erratas, republicações e decretos repetidos nos cadernos baixados."
    )
    parser.add_argument("pasta", nargs="?", type=Path, default=PASTA_CADERNOS_PADRAO)
    parser.add_argument("--saida", type=Path, default=PASTA_EXEMPLOS_PADRAO)
    parser.add_argument("--sem-paginas", action="store_true", help="só o relatório; não copia páginas")
    parser.add_argument(
        "--cabecalhos",
        default=",".join(ESPECIES_CABECALHO_PADRAO),
        help="espécies cujo formato de cabeçalho será levantado, separadas por vírgula "
        "(padrão: %(default)s; vazio desliga)",
    )
    parser.add_argument(
        "--por-formato", type=int, default=2, help="páginas copiadas de cada formato de cabeçalho (padrão: 2)"
    )
    args = parser.parse_args(argv)

    if not args.pasta.is_dir():
        print(f"Erro: pasta não encontrada: {args.pasta}", file=sys.stderr)
        return 2
    especies = tuple(e.strip().upper() for e in args.cabecalhos.split(",") if e.strip())
    achados, avisos = procurar(args.pasta, especies)
    relatorio = montar_relatorio(achados, avisos)
    print(relatorio)
    args.saida.mkdir(parents=True, exist_ok=True)
    (args.saida / "relatorio.txt").write_text(relatorio, encoding="utf-8")
    if not args.sem_paginas:
        copiadas = copiar_paginas(selecionar_para_copia(achados, args.por_formato), args.saida)
        print(f"{len(copiadas)} página(s) copiada(s) para {args.saida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())