"""Comparação do texto de um decreto do caderno com o OCR do PDF da base (imagem ou texto
ilegível): que diferenças o OCR NÃO explica?

Em que etapa entra: fase 2 do plano do OCR (calibração, `ferramentas/medir_ocr.py`) e, depois,
etapa 3 (duplicidade) quando o PDF da base não tem texto (fase 5, docs/plano_ocr.md).

Por que não basta a similaridade (`comparacao.comparar_textos`): na calibração de 19/11/2026
(117 decretos de 2010 e 2020 com a base em imagem), o mesmo decreto deu de 61% a 99% de
similaridade por causa do ruído do OCR ("estímulo" -> "estimulo", "PRODEPE" -> "PRODEFE",
"IV" -> "[ V", "65ª" -> "65º"), e decretos DIFERENTES do mesmo modelo de texto chegaram a 93,7%
(95,4% de cobertura). Contar quanto difere não separa os dois casos; o que separa é o QUE
difere: no mesmo decreto, letras trocadas e números lidos com pouca confiança; em decretos
diferentes, número, data, empresa e CNPJ lidos com confiança.

Recebe o texto do decreto (do caderno) e as palavras do OCR da base com a confiança de cada uma;
devolve as diferenças não explicadas (e quantas foram explicadas). Regras, palavra a palavra,
depois de alinhar os dois textos:

  - normalização dos dois lados: sem acentos, minúsculas, "ª"/"°" como "º", "[", "]", "|" e
    "!" como "i" (o "I" lido como sinal: "IV" -> "[ V"), sem a outra pontuação;
    números seguidos ("11 . 675", "48 , 565") viram um número só ("11675");
  - texto que só está na base: ignorado (o recorte da página de 2010 traz o cabeçalho da
    página e o fim do decreto vizinho);
  - texto do caderno que falta na base: NÃO explicado, salvo letras soltas (o OCR perde "e",
    "a" isolados);
  - trecho trocado com número do lado do caderno: explicado só se o OCR leu esse trecho com
    confiança abaixo de `confianca_critica` e os dois são parecidos (`PARECIDO_COM_POUCA_CONFIANCA`);
    número lido com confiança alta e diferente é diferença de verdade;
  - trecho trocado só com letras: explicado se, trocando as confusões típicas do OCR
    (`CONFUSOES`), os dois são muito parecidos (`PARECIDO`), ou parecidos e lidos com pouca
    confiança.

Na dúvida, a diferença fica como não explicada: o decreto vai para "conferir duplicata"
(decisão humana), nunca para descarte. Só lê.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from dbf_cepe.pipeline.comparacao import palavras

# Semelhança de caracteres (0 a 1) a partir da qual uma troca só de letras é ruído do OCR.
PARECIDO = 0.75
# Semelhança mínima quando o OCR leu o trecho com pouca confiança (letras ou números).
PARECIDO_COM_POUCA_CONFIANCA = 0.5
# Confusões típicas do OCR entre letras e algarismos (aplicadas só na comparação de letras).
# "h" vale "ii": o "III" dos incisos lido como "HI".
CONFUSOES = str.maketrans({"l": "i", "1": "i", "0": "o", "6": "o", "5": "s", "8": "b", "h": "ii"})
# Ordinais num só; "[", "]", "|" e "!" são o "I" que o OCR lê como sinal ("IV" -> "[ V", "| V").
_ORDINAIS = str.maketrans({"ª": "º", "°": "º", "[": "i", "]": "i", "|": "i", "!": "i"})


@dataclass(frozen=True)
class Diferenca:
    caderno: str  # trecho do caderno (vazio: só na base)
    base: str  # trecho do OCR da base (vazio: falta na base)
    motivo: str

    def __str__(self) -> str:
        return f"[caderno: {self.caderno or '(nada)'} | base: {self.base or '(nada)'}] {self.motivo}"


@dataclass(frozen=True)
class ResultadoOcr:
    nao_explicadas: tuple[Diferenca, ...]
    explicadas: int

    @property
    def sem_diferenca_de_verdade(self) -> bool:
        return not self.nao_explicadas


@dataclass(frozen=True)
class _Token:
    normal: str
    original: str
    confianca: float  # 100 para o lado do caderno


def normalizar(elemento: str) -> str:
    """Sem acentos, minúsculo, ordinal único, só letras e algarismos ('' = pontuação)."""
    # Os ordinais antes do NFKD, que transformaria "ª" em "a" e "º" em "o" (e "°" não).
    unificado = elemento.translate(_ORDINAIS)
    sem_acento = "".join(c for c in unicodedata.normalize("NFKD", unificado) if not unicodedata.combining(c))
    return "".join(c for c in sem_acento.casefold() if c.isalnum())


def _juntar_numeros(tokens: list[_Token]) -> list[_Token]:
    juntos: list[_Token] = []
    for token in tokens:
        if juntos and token.normal.isdigit() and juntos[-1].normal.isdigit():
            anterior = juntos[-1]
            juntos[-1] = _Token(anterior.normal + token.normal, f"{anterior.original}.{token.original}",
                                min(anterior.confianca, token.confianca))
        else:
            juntos.append(token)
    return juntos


def tokens_do_caderno(texto: str) -> list[_Token]:
    return _juntar_numeros([_Token(n, e, 100.0) for e in palavras(texto) if (n := normalizar(e))])


def tokens_do_ocr(palavras_ocr: Sequence[tuple[str, float]]) -> list[_Token]:
    return _juntar_numeros([
        _Token(n, e, confianca) for texto, confianca in palavras_ocr for e in palavras(texto) if (n := normalizar(e))
    ])


def _parecido(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def _explicar_troca(caderno: list[_Token], base: list[_Token], confianca_critica: float) -> str | None:
    """None se a troca é ruído do OCR; senão, o motivo."""
    a, b = "".join(t.normal for t in caderno), "".join(t.normal for t in base)
    if a == b:
        return None
    pouca_confianca = min(t.confianca for t in base) < confianca_critica
    if any(c.isdigit() for c in a):
        if pouca_confianca and _parecido(a, b) >= PARECIDO_COM_POUCA_CONFIANCA:
            return None
        return "número diferente" + ("" if pouca_confianca else " (lido com confiança)")
    semelhanca = _parecido(a.translate(CONFUSOES), b.translate(CONFUSOES))
    if semelhanca >= PARECIDO or (pouca_confianca and semelhanca >= PARECIDO_COM_POUCA_CONFIANCA):
        return None
    return "palavra diferente"


def comparar_com_ocr(
    texto_ato: str, palavras_ocr: Sequence[tuple[str, float]], confianca_critica: float = 90.0
) -> ResultadoOcr:
    """Diferenças entre o texto do decreto e o OCR da base que o OCR não explica (ver o
    início do módulo). `palavras_ocr`: (texto, confiança 0 a 100) na ordem do OCR."""
    caderno, base = tokens_do_caderno(texto_ato), tokens_do_ocr(palavras_ocr)
    operacoes = SequenceMatcher(None, [t.normal for t in caderno], [t.normal for t in base], autojunk=False)
    nao_explicadas: list[Diferenca] = []
    explicadas = 0
    for etiqueta, i1, i2, j1, j2 in operacoes.get_opcodes():
        if etiqueta in ("equal", "insert"):  # "insert": texto que só está na base, ignorado
            continue
        trecho_caderno = " ".join(t.original for t in caderno[i1:i2])
        trecho_base = " ".join(t.original for t in base[j1:j2])
        if etiqueta == "delete":
            if all(len(t.normal) <= 1 and not t.normal.isdigit() for t in caderno[i1:i2]):
                explicadas += 1
            else:
                nao_explicadas.append(Diferenca(trecho_caderno, "", "falta na base"))
            continue
        motivo = _explicar_troca(caderno[i1:i2], base[j1:j2], confianca_critica)
        if motivo is None:
            explicadas += 1
        else:
            nao_explicadas.append(Diferenca(trecho_caderno, trecho_base, motivo))
    return ResultadoOcr(tuple(nao_explicadas), explicadas)
