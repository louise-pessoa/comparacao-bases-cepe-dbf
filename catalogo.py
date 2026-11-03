"""Catálogo somente leitura dos atos dos cadernos do Diário Oficial.

`python main.py --catalogar <pasta de cadernos>` lê cada caderno (nada é movido,
renomeado nem gravado nos cadernos nem na base da DBF), reconhece decretos,
editais, portarias, erratas e republicações com `regras_atos.identificar` e grava
um CSV com um ato por linha. Depois imprime as contagens por espécie e por tipo e a
lista do que a RegEx não reconheceu por completo (página e trecho): nenhum ato
some sem aviso.

Colunas do CSV (nesta ordem):
    especie, numero, ano, orgao, tipo, data_do_ato, data_de_publicacao,
    caderno, pagina, republicacao, situacao

  - `data_de_publicacao` vem do NOME do caderno (AAAAMMDD_...), não de manifesto;
  - `data_do_ato` vem do cabeçalho do ato ou, no edital, da linha "Recife, <data>";
  - errata: `numero`/`ano` são os do ATO REFERIDO (a errata não tem número próprio)
    e `tipo` é o destino (decreto, edital, portaria ou outro);
  - `republicacao`: "sim" (logo após o fecho ou em título acima do edital), "provavel"
    ou "nao"; marcador sem ato associado vira uma linha "Republicação" (a confirmar);
  - `situacao`: "no_escopo", "fora_do_escopo" ou "a_confirmar", seguida de ": <motivo>".
    Escopo = decreto que cita programa da DBF no cabeçalho, edital de órgão DBF/DBM,
    portaria de órgão SF. O que depende de decisão da DBF (errata entra no catálogo?)
    fica "a_confirmar" em vez de ser decidido aqui.

O CSV usa ";" como separador e UTF-8 com BOM, que o Excel em português abre sem
ajustes. Um arquivo existente no mesmo caminho é substituído.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from especies import DECRETO
from programas import cita_programa_so_no_corpo, programas_do_ato
from regras_atos import Achado, identificar
from separador import ErroSeparacao, ler_caderno

PASTA_PADRAO_DO_CSV = Path("dados")
NOME_PADRAO_DO_CSV = "catalogo_atos.csv"

COLUNAS = (
    "especie", "numero", "ano", "orgao", "tipo", "data_do_ato", "data_de_publicacao",
    "caderno", "pagina", "republicacao", "situacao",
)

NO_ESCOPO = "no_escopo"
FORA_DO_ESCOPO = "fora_do_escopo"
A_CONFIRMAR = "a_confirmar"

_DATA_NO_NOME = re.compile(r"^(\d{4})(\d{2})(\d{2})(?=\D|$)")
# Avisos de regras_atos que só informam (não pedem conferência).
_AVISOS_INFORMATIVOS = ("data lida da linha 'Recife", "órgão não consta no cabeçalho")


@dataclass(frozen=True)
class LinhaDoCatalogo:
    especie: str
    numero: str
    ano: str
    orgao: str
    tipo: str
    data_do_ato: str
    data_de_publicacao: str
    caderno: str
    pagina: int
    republicacao: str
    situacao: str
    trecho: str = ""  # só para a lista "não reconhecido"; não vai para o CSV
    motivo: str = ""  # por que precisa de olho humano; "" = reconhecido por completo

    def como_linha(self) -> list[str]:
        return [str(getattr(self, coluna)) for coluna in COLUNAS]


@dataclass
class ResultadoDoCatalogo:
    linhas: list[LinhaDoCatalogo] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    cadernos: int = 0
    cadernos_com_falha: int = 0

    @property
    def nao_reconhecidos(self) -> list[LinhaDoCatalogo]:
        return [linha for linha in self.linhas if linha.motivo]


def data_de_publicacao(caderno: Path) -> str:
    """'20260425_PoderExecutivo.pdf' -> '2026-04-25'; '' se o nome não começa por AAAAMMDD válido."""
    m = _DATA_NO_NOME.match(caderno.name)
    if not m:
        return ""
    try:
        return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    except ValueError:
        return ""


def _motivo_de_conferencia(achado: Achado) -> str:
    """O que, no achado, precisa de olho humano ('' se nada)."""
    if achado.especie == "Republicação":
        return "marcador de republicação sem decreto, edital ou portaria associado"
    if achado.especie == "Errata":
        return "ato referido da errata não identificado" if achado.ato_referido == "?" else ""
    motivos = [
        aviso
        for aviso in achado.avisos
        if not aviso.startswith(_AVISOS_INFORMATIVOS)
    ]
    return "; ".join(motivos)


def _situacao(achado: Achado, texto_do_ato: str, motivo: str) -> str:
    if achado.especie == "Republicação":
        return f"{A_CONFIRMAR}: marcador de republicação sem ato associado"
    if achado.especie == "Errata":
        # Decisão pendente da DBF: errata de decreto/edital/portaria entra no catálogo?
        if achado.destino == "outro":
            return f"{FORA_DO_ESCOPO}: errata de {achado.ato_referido}"
        return f"{A_CONFIRMAR}: errata de {achado.ato_referido} (falta a DBF decidir se erratas entram)"

    if achado.especie == "Decreto":
        if achado.numero is None:
            return f"{A_CONFIRMAR}: {motivo}"
        programas = programas_do_ato(texto_do_ato, DECRETO)
        if programas:
            base, detalhe = NO_ESCOPO, ", ".join(p.sigla for p in programas)
        elif cita_programa_so_no_corpo(texto_do_ato, DECRETO):
            base, detalhe = A_CONFIRMAR, "programa da DBF citado só no corpo do decreto"
        else:
            base, detalhe = FORA_DO_ESCOPO, "nenhum programa da DBF no cabeçalho"
    else:  # Edital e Portaria: pelo órgão do cabeçalho
        if achado.sefaz is True:
            base, detalhe = NO_ESCOPO, f"órgão {achado.orgao}"
        elif achado.sefaz is False:
            base, detalhe = FORA_DO_ESCOPO, f"órgão {achado.orgao}"
        else:
            base, detalhe = A_CONFIRMAR, "órgão não consta no cabeçalho"

    if motivo:  # sem número, possível citação...: não dá para afirmar o escopo
        return f"{A_CONFIRMAR}: {motivo}" + (f" ({base}: {detalhe})" if base != A_CONFIRMAR else "")
    return f"{base}: {detalhe}"


def _linha(achado: Achado, texto: str, nome_do_caderno: str, publicacao: str) -> LinhaDoCatalogo:
    texto_do_ato = texto[achado.posicao : achado.fim]
    motivo = _motivo_de_conferencia(achado)
    eh_errata = achado.especie == "Errata"
    sem_ato = achado.especie in ("Errata", "Republicação")
    return LinhaDoCatalogo(
        especie=achado.especie,
        numero=(achado.ref_numero if eh_errata else achado.numero) or "",
        ano=(achado.ref_ano if eh_errata else achado.ano) or "",
        orgao=achado.orgao or "",
        tipo=(achado.destino if eh_errata else achado.tipo) or "",
        data_do_ato=achado.data_do_ato or "",
        data_de_publicacao=publicacao,
        caderno=nome_do_caderno,
        pagina=achado.pagina or 1,
        republicacao="" if sem_ato else (achado.republicacao or "nao"),
        situacao=_situacao(achado, texto_do_ato, motivo),
        trecho=achado.trecho,
        motivo=motivo,
    )


def catalogar_caderno(caminho: Path) -> tuple[list[LinhaDoCatalogo], list[str]]:
    """Linhas do catálogo de um caderno e os avisos dele. Só lê o PDF."""
    texto, inicios, digitalizadas, _paginas = ler_caderno(caminho)
    publicacao = data_de_publicacao(caminho)
    avisos: list[str] = []
    if not publicacao:
        avisos.append(f"{caminho.name}: o nome não começa por AAAAMMDD; data de publicação em branco")
    if digitalizadas:
        avisos.append(
            f"{caminho.name}: páginas digitalizadas {list(digitalizadas)}: atos nelas não foram "
            "vistos (precisam de OCR)"
        )
    linhas = [
        _linha(achado, texto, caminho.name, publicacao) for achado in identificar(texto, inicios)
    ]
    return linhas, avisos


def listar_cadernos(caminho: Path) -> list[Path]:
    if caminho.is_dir():
        cadernos = sorted(p for p in caminho.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
        if not cadernos:
            raise ErroSeparacao(f"nenhum PDF na pasta {caminho}")
        return cadernos
    if not caminho.is_file():
        raise ErroSeparacao(f"caderno ou pasta não encontrado: {caminho}")
    return [caminho]


def catalogar(caminho: Path) -> ResultadoDoCatalogo:
    """Cataloga um caderno ou todos os PDFs de uma pasta. Um caderno ilegível vira aviso
    e não interrompe os demais."""
    resultado = ResultadoDoCatalogo()
    for caderno in listar_cadernos(caminho):
        resultado.cadernos += 1
        try:
            linhas, avisos = catalogar_caderno(caderno)
        except ErroSeparacao as erro:
            resultado.cadernos_com_falha += 1
            resultado.avisos.append(f"{caderno.name}: não foi possível ler ({erro})")
            continue
        resultado.linhas.extend(linhas)
        resultado.avisos.extend(avisos)
    return resultado


def gravar_csv(linhas: list[LinhaDoCatalogo], destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", encoding="utf-8-sig", newline="") as arquivo:
        escritor = csv.writer(arquivo, delimiter=";")
        escritor.writerow(COLUNAS)
        escritor.writerows(linha.como_linha() for linha in linhas)


def resumo_em_texto(resultado: ResultadoDoCatalogo, destino: Path | None = None) -> str:
    linhas = resultado.linhas
    saida = [
        f"Catálogo: {resultado.cadernos} caderno(s), {len(linhas)} linha(s)"
        + (f", gravado em {destino}" if destino else "")
        + "."
    ]
    por_especie = Counter(linha.especie for linha in linhas)
    ordem = ("Decreto", "Edital", "Portaria", "Errata", "Republicação")
    saida.append(
        "Por espécie: " + ", ".join(f"{e} {por_especie[e]}" for e in ordem if por_especie[e])
        if por_especie
        else "Por espécie: nenhum ato reconhecido"
    )
    saida.append("Por tipo:")
    for especie in ordem[:3]:
        tipos = Counter(linha.tipo or "(sem tipo: a confirmar)" for linha in linhas if linha.especie == especie)
        if tipos:
            saida.append(f"  {especie}: " + ", ".join(f"{t} {n}" for t, n in tipos.most_common()))
    destinos = Counter(linha.tipo for linha in linhas if linha.especie == "Errata")
    if destinos:
        saida.append("  Errata (destino): " + ", ".join(f"{t} {n}" for t, n in destinos.most_common()))
    situacoes = Counter(linha.situacao.split(":")[0] for linha in linhas)
    saida.append("Situação: " + ", ".join(f"{s} {n}" for s, n in situacoes.most_common()))
    republicacoes = Counter(linha.republicacao for linha in linhas if linha.republicacao in ("sim", "provavel"))
    if republicacoes:
        saida.append("Republicações ligadas a um ato: " + ", ".join(f"{r} {n}" for r, n in republicacoes.most_common()))
    nao = resultado.nao_reconhecidos
    saida.append("")
    saida.append(f"Não reconhecido ou a conferir ({len(nao)}):" if nao else "Não reconhecido ou a conferir: nenhum.")
    for linha in nao:
        saida.append(f"  {linha.caderno}, página {linha.pagina} [{linha.especie}] {linha.motivo}")
        saida.append(f"      {linha.trecho[:140]}")
    if resultado.avisos:
        saida += ["", "Avisos:", *[f"  - {aviso}" for aviso in resultado.avisos]]
    return "\n".join(saida)
