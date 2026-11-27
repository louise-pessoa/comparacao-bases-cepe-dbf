"""Etapa 2 (parte 2) — identificação do ato por RegEx no texto bruto.

Regras de segurança (falhar para revisão, nunca em silêncio):
  - O cabeçalho precisa estar no INÍCIO do texto. Buscar "DECRETO Nº ..." em
    qualquer ponto pegaria citações do corpo e geraria o número errado.
  - Só é descartado o que COMEÇA por uma outra espécie conhecida (Lei,
    Portaria, Edital, Errata...). Texto sem correspondência vai para revisão.
  - Mais de um cabeçalho distinto no mesmo PDF (ex.: caderno com vários atos)
    vai para revisão, para não ignorar atos em silêncio.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

from dbf_cepe.regras import especies as _regras_especies
from dbf_cepe.regras.especies import NOMES_OUTRAS_ESPECIES, Especie, Tipo

_ESPACOS = re.compile(r"\s+")
_TAMANHO_EMENTA_PADRAO = 600


class StatusIdentificacao(Enum):
    """Descreva se o texto identifica um ato ativo, outra espécie ou caso de revisão."""
    IDENTIFICADO = "identificado"
    OUTRA_ESPECIE = "outra_especie"
    SEM_CORRESPONDENCIA = "sem_correspondencia"
    MULTIPLOS_ATOS = "multiplos_atos"
    # A espécie usa o ano no nome do arquivo ("Edital 064-2025.pdf") e o cabeçalho não o traz.
    SEM_ANO = "sem_ano"


@dataclass(frozen=True)
class Identificacao:
    """Resultado da identificação, com espécie, número e tipo quando disponíveis."""
    status: StatusIdentificacao
    especie: Especie | None = None
    numero: str | None = None  # sem separador de milhar, ex.: "60929"
    tipo: Tipo | None = None  # None se a ementa não casar com nenhum
    detalhe: str = ""
    ementa: str = ""  # do fim do cabeçalho até a abertura do corpo (texto com espaços colapsados)
    ano: str | None = None  # do cabeçalho; só entra no nome se a espécie usa ano (`chave_ano`)
    orgao: str | None = None  # do cabeçalho (edital, portaria); serve ao escopo

    @property
    def chave_ano(self) -> str | None:
        """O ano que identifica o ato: None na espécie que não usa ano no nome (decreto)."""
        return self.especie.ano_da_chave(self.ano) if self.especie else None

    @property
    def nome_padrao(self) -> str:
        """Nome padrão do arquivo ('Decreto 60929.pdf', 'Edital 064-2025.pdf'). Só para
        ato IDENTIFICADO (que sempre tem o ano, se a espécie o exige)."""
        assert self.especie is not None and self.numero is not None
        nome = self.especie.nome_do_ato(self.numero, self.ano)
        assert nome is not None, "ato identificado sem o ano que o nome exige"
        return nome


def normalizar_espacos(texto: str) -> str:
    """Unicode NFC e espaços colapsados.

    Necessário porque o texto justificado dos PDFs chega com palavras
    separadas por quebras de linha ("de \\njulho \\nde \\n2000").
    """
    return _ESPACOS.sub(" ", unicodedata.normalize("NFC", texto)).strip()


def _regex_outras_especies(ativas: tuple[Especie, ...]) -> re.Pattern[str] | None:
    nomes_ativos = {especie.nome.upper() for especie in ativas}
    nomes = [n for n in NOMES_OUTRAS_ESPECIES if n.upper() not in nomes_ativos]
    if not nomes:
        return None
    # Mais longos primeiro: "LEI COMPLEMENTAR" antes de "LEI".
    alternativas = "|".join(re.escape(n) for n in sorted(nomes, key=len, reverse=True))
    return re.compile(rf"(?:{alternativas})\b", re.IGNORECASE)


def _extrair_ementa(texto: str, fim_cabecalho: int, especie: Especie) -> str:
    """Ementa: do fim do cabeçalho até a abertura do corpo da espécie (no
    decreto, "A GOVERNADORA..."); sem ela, os primeiros caracteres."""
    resto = texto[fim_cabecalho:]
    marcador = especie.abertura_do_corpo.search(resto) if especie.abertura_do_corpo else None
    ementa = resto[: marcador.start()] if marcador else resto[:_TAMANHO_EMENTA_PADRAO]
    # "EDITAL DE ADITAMENTO DBM Nº 001/2010 - CREDENCIAMENTO ...": o travessão só separa.
    return ementa.lstrip(" .-–—")


def _classificar(especie: Especie, ementa: str) -> Tipo | None:
    for tipo in especie.tipos:
        if tipo.padrao.match(ementa):
            return tipo
    return None


def identificar(
    texto_bruto: str, especies: tuple[Especie, ...] | None = None
) -> Identificacao:
    """Identifique um único ato no início do texto e classifique sua ementa.

    Cabeçalhos ausentes, espécies não ativas e múltiplos números são devolvidos como
    status explícito para revisão; a função não escolhe silenciosamente um ato.
    Sem `especies`, as de `ESPECIES_ATIVAS`.
    """
    # Lido na hora da chamada (não no import): ativar uma espécie é mudar só especies.py.
    especies = especies if especies is not None else _regras_especies.ESPECIES_ATIVAS
    texto = normalizar_espacos(texto_bruto)
    if not texto:
        return Identificacao(
            StatusIdentificacao.SEM_CORRESPONDENCIA, detalhe="texto vazio"
        )

    for especie in especies:
        casamento = especie.cabecalho.match(texto)
        if not casamento and especie.cabecalhos_em_linhas:
            # Cabeçalho quebrado em linhas: procurado no texto ANTES de colapsar os espaços.
            bruto = unicodedata.normalize("NFC", texto_bruto).lstrip()
            em_linhas = [m for m in especie.cabecalhos_em_linhas(bruto) if m.start() == 0]
            if em_linhas:
                texto = bruto
                casamento = em_linhas[0]
        if not casamento:
            continue

        numeros = {
            especie.dados_do_cabecalho(m)[0] for m in especie.cabecalho.finditer(texto)
        } | {especie.dados_do_cabecalho(casamento)[0]}
        if len(numeros) > 1:
            return Identificacao(
                StatusIdentificacao.MULTIPLOS_ATOS,
                especie=especie,
                detalhe=f"cabeçalhos de mais de um ato no PDF: {sorted(numeros)}",
            )

        numero, ano, orgao = especie.dados_do_cabecalho(casamento)
        ementa = normalizar_espacos(_extrair_ementa(texto, casamento.end(), especie))
        titulo = especie.titulo_do_cabecalho(casamento) if especie.titulo_do_cabecalho else None
        if especie.usa_ano and not ano:
            return Identificacao(
                StatusIdentificacao.SEM_ANO,
                especie=especie,
                numero=numero,
                orgao=orgao,
                detalhe=f"{especie.nome} {numero}: o nome do arquivo exige o ano e o cabeçalho não o traz",
            )
        return Identificacao(
            StatusIdentificacao.IDENTIFICADO,
            especie=especie,
            numero=numero,
            tipo=(_classificar(especie, titulo) if titulo else None) or _classificar(especie, ementa),
            detalhe=f"ementa: {ementa[:80]!r}",
            ementa=ementa,
            ano=ano,
            orgao=orgao,
        )

    outras = _regex_outras_especies(especies)
    if outras and (casamento := outras.match(texto)):
        return Identificacao(
            StatusIdentificacao.OUTRA_ESPECIE,
            detalhe=f"começa por {casamento.group(0).upper()!r}",
        )

    return Identificacao(
        StatusIdentificacao.SEM_CORRESPONDENCIA,
        detalhe=f"cabeçalho não encontrado; início do texto: {texto[:80]!r}",
    )