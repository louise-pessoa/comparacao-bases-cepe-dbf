"""Programas de benefício fiscal da DBF: critério de escopo do pipeline.

Decisão do projeto (26/10/2026): só interessam os atos ligados aos programas
de benefício fiscal da DBF. Os demais decretos do caderno (crédito
suplementar, utilidade pública, nomeações etc.) são ignorados.

As funções daqui valem para qualquer espécie: quem diz onde o cabeçalho
termina (`fim_do_cabecalho`) e quais programas contam (`programas`) é a
`Especie` (ver `especies.py`). Para o decreto, o critério olha apenas o
CABEÇALHO: da linha de abertura ("DECRETO Nº ...") até o "DECRETA:", isto é,
ementa e considerandos. É ali que
os decretos da DBF citam o programa ou a lei dele (o decreto 60.233, por
exemplo, não traz "PRODEPE" na ementa, mas os considerandos citam a Lei nº
11.675/1999). Citações soltas no corpo do texto NÃO bastam; quando um programa
só aparece no corpo, o pipeline avisa em vez de decidir sozinho.

Cada programa é reconhecido por: sigla, nome oficial, lei ou decreto de
origem e o anexo do RICMS-PE (Decreto nº 44.650/2017) que o regulamenta. Os
padrões evitam termos genéricos ("setor automotivo", "indústria naval" soltos),
que apareceriam em decretos de PRODEPE de empresas desses ramos e marcariam um
segundo programa por engano.

Lista levantada em 26/10/2026 a partir do Decreto nº 44.650/2017 (RICMS-PE) e
de leis e decretos dos programas; conferir com a DBF antes de depender dela.
Para incluir um programa, acrescente-o em `PROGRAMAS_DBF` (os candidatos ainda
não decididos estão prontos em `PROGRAMAS_OPCIONAIS`: basta movê-los).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # só para as anotações: evita importação circular com especies.py
    from especies import Especie

_FLAGS = re.IGNORECASE

# Origem do padrão (documentação e testes; não altera o comportamento):
#   amostra   - conferido em decretos reais da DBF (tests/amostras/, caderno de 26/06/2026);
#   publicado - conferido em decreto real publicado (Alepe/SEFAZ), sem amostra versionada;
#   presumido - deduzido da legislação; falta decreto real.
EVIDENCIA_AMOSTRA = "amostra"
EVIDENCIA_PUBLICADO = "publicado"
EVIDENCIA_PRESUMIDO = "presumido"


@dataclass(frozen=True)
class Programa:
    sigla: str
    nome: str
    base: str  # base legal, só para documentação e relatórios
    padrao: re.Pattern[str]  # aplicado no cabeçalho do decreto (ementa + considerandos)
    evidencia: str = EVIDENCIA_PRESUMIDO


# ----------------------------------------------------------------------------
# Peças dos padrões
# ----------------------------------------------------------------------------

# "nº" em todas as grafias vistas: nº, n°, n.º, no, N.
_N = r"n\s*\.?\s*[ºo°]\.?\s*"


def _numero(texto: str) -> str:
    """'11.675' -> '11\\.?675' (o ponto de milhar pode faltar)."""
    return re.escape(texto).replace(r"\.", r"\.?")


def _lei(numero: str) -> str:
    """'Lei nº 11.675', 'Lei Estadual nº 11.675', 'Lei n.º 11675'."""
    return rf"Lei(?:\s+Estadual)?\s+{_N}{_numero(numero)}\b"


def _decreto(numero: str) -> str:
    return rf"Decreto\s+{_N}{_numero(numero)}\b"


_RICMS = rf"(?:{_decreto('44.650')}|RICMS(?:[/\-\s]*PE)?|Regulamento\s+do\s+ICMS)"


def _anexo(numero: int) -> str:
    """'Anexo 33 do Decreto nº 44.650' / 'Anexo 33 ao RICMS/PE'. Exige o RICMS
    ao lado, porque 'Anexo 33' sozinho aparece em qualquer decreto."""
    return rf"Anexo\s+{numero}\s+(?:do|da|ao)\s+{_RICMS}"


def _p(*alternativas: str) -> re.Pattern[str]:
    return re.compile("|".join(alternativas), _FLAGS)


# ----------------------------------------------------------------------------
# Programas ativos (escopo do pipeline)
# ----------------------------------------------------------------------------

PROGRAMAS_DBF: tuple[Programa, ...] = (
    Programa(
        "PRODEPE",
        "Programa de Desenvolvimento do Estado de Pernambuco",
        "Lei nº 11.675/1999; Decreto nº 21.959/1999",
        _p(
            r"\bPRODEPE\b",
            _lei("11.675"),
            _decreto("21.959"),
            r"Programa\s+de\s+Desenvolvimento\s+do\s+Estado\s+de\s+Pernambuco",
        ),
        EVIDENCIA_AMOSTRA,
    ),
    Programa(
        "PROIND",
        "Programa de Estímulo à Indústria do Estado de Pernambuco",
        "Anexo 33 do Decreto nº 44.650/2017",
        _p(r"\bPROIND\b", _anexo(33)),
        EVIDENCIA_AMOSTRA,
    ),
    Programa(
        "PEAP",
        "Programa de Estímulo à Atividade Portuária (PEAP-I e PEAP-II)",
        "Decreto nº 34.560/2010; Lei nº 13.942/2009; Anexo 27 do Decreto nº 44.650/2017",
        _p(
            r"\bPEAP\b",  # casa "Peap", "PEAP-II", "Peap II", "Peap-I"
            r"Programa\s+de\s+Est[íi]mulo\s+[àa]\s+Atividade\s+Portu[áa]ria",
            _decreto("34.560"),
            _lei("13.942"),
            _anexo(27),
        ),
        EVIDENCIA_PUBLICADO,  # decretos 36.571/2011, 52.040/2021 e 59.024/2025
    ),
    Programa(
        "PRODINPE",
        "Programa de Desenvolvimento da Indústria Naval e de Mecânica Pesada Associada",
        "Lei nº 12.710/2004; Anexo 29 do Decreto nº 44.650/2017",
        _p(
            r"\bPRODINPE\b",
            r"Ind[úu]stria\s+Naval\s+e\s+de\s+Mec[âa]nica\s+Pesada",
            _lei("12.710"),
            _anexo(29),
        ),
    ),
    Programa(
        "PRODEAUTO",
        "Programa de Desenvolvimento do Setor Automotivo do Estado de Pernambuco",
        "Lei nº 13.484/2008; Anexo 36 do Decreto nº 44.650/2017",
        _p(
            r"\bPRODEAUTO\b",
            r"Programa\s+de\s+Desenvolvimento\s+do\s+Setor\s+Automotivo",
            _lei("13.484"),
            _anexo(36),
        ),
    ),
    Programa(
        "VITIVINICOLA",
        "Tratamento tributário do Setor Vitivinícola",
        "Anexo 30 do Decreto nº 44.650/2017",
        _p(r"Setor\s+Vitivin[íi]cola", _anexo(30)),
    ),
    Programa(
        "CALCADOS",
        "Indústria de Calçados, Bolsas, Cintos e Bolas Esportivas",
        "Anexo 35 do Decreto nº 44.650/2017",
        _p(r"Cal[çc]ados,\s+Bolsas", _anexo(35)),
    ),
    Programa(
        "MAIS_ATACADISTAS",
        "Sistemática \"Mais Atacadistas – Pernambuco\"",
        "Anexo 26 do Decreto nº 44.650/2017",
        _p(r"Mais\s+Atacadistas", _anexo(26)),
    ),
)

# ----------------------------------------------------------------------------
# Candidatos ainda não decididos (INATIVOS)
# ----------------------------------------------------------------------------

# Programas e sistemáticas que NÃO entram no escopo enquanto a DBF não decidir.
# Os padrões já estão prontos: para incluir um, mova-o para `PROGRAMAS_DBF`.
PROGRAMAS_OPCIONAIS: tuple[Programa, ...] = (
    Programa(
        "COOPERA",
        "Programa de conformidade tributária COOPERA",
        "Anexo 44 do Decreto nº 44.650/2017",
        _p(r"\bCOOPERA\b", _anexo(44)),
    ),
    Programa(
        "FIOS_TECIDOS_CONFECCOES",
        "Sistemática de fios, tecidos e confecções",
        "Anexo 40 do Decreto nº 44.650/2017",
        _p(r"fios,\s+tecidos", _anexo(40)),
    ),
    Programa(
        "MONOFASICA",
        "Regime de tributação monofásica",
        "Anexo 41 do Decreto nº 44.650/2017",
        _p(r"tributa[çc][ãa]o\s+monof[áa]sica", _anexo(41)),
    ),
    Programa(
        "INOVAR_PE",
        "Inovar-PE",
        "legislação própria",
        _p(r"\bInovar[\s\-]*PE\b"),
    ),
    Programa(
        "FEEF",
        "Fundo Estadual de Equilíbrio Fiscal",
        "legislação própria (fundo, não é benefício a empresa)",
        _p(r"\bFEEF\b", r"Fundo\s+Estadual\s+de\s+Equil[íi]brio\s+Fiscal"),
    ),
)

_LIMITE_CABECALHO = 4000  # sem marcador de fim, o cabeçalho são os primeiros caracteres


def cabecalho_do_ato(texto: str, especie: Especie) -> str:
    """Do início do texto até o fim do cabeçalho da espécie (no decreto, o
    'DECRETA:'; na falta dele, os primeiros caracteres): ementa e considerandos."""
    fim = especie.fim_do_cabecalho.search(texto) if especie.fim_do_cabecalho else None
    return texto[: fim.start()] if fim else texto[:_LIMITE_CABECALHO]


def programas_do_ato(
    texto: str,
    especie: Especie,
    programas: tuple[Programa, ...] | None = None,
) -> tuple[Programa, ...]:
    """Programas citados no cabeçalho do ato. Por padrão usa os da espécie;
    `programas` permite testar outra lista (por exemplo, com os opcionais)."""
    lista = programas if programas is not None else (especie.programas or ())
    cabecalho = cabecalho_do_ato(texto, especie)
    return tuple(p for p in lista if p.padrao.search(cabecalho))


def em_escopo(especie: Especie, programas_citados: tuple[Programa, ...]) -> bool:
    """Espécie sem lista de programas: todo ato entra. Com lista: só se o
    cabeçalho citar algum programa dela."""
    return especie.programas is None or bool(programas_citados)


def cita_programa_so_no_corpo(
    texto: str,
    especie: Especie,
    programas: tuple[Programa, ...] | None = None,
) -> bool:
    """True se algum programa aparece depois do cabeçalho, embora o cabeçalho
    não case com nenhum (possível ato da DBF perdido)."""
    lista = programas if programas is not None else (especie.programas or ())
    if programas_do_ato(texto, especie, lista):
        return False
    corpo = texto[len(cabecalho_do_ato(texto, especie)) :]
    return any(p.padrao.search(corpo) for p in lista)