"""Etapa 4 — normalização: recria o ato em um PDF limpo e linear com fpdf2.

Contrato: recebe o PDF do ato (o intermediário de `dados/pendentes` ou o
original da CEPE) e a identificação (etapa 2) e devolve o caminho de um PDF
novo em `pasta_saida` (pasta LOCAL, nunca a da DBF; quem grava na DBF é a
etapa 5), com o nome da espécie ("Decreto 60929.pdf").

Layout (decisão do projeto, 27/10/2026), copiado dos decretos-modelo da base
da DBF (G:): Times New Roman 12 (aqui, a Liberation Serif, de mesmas medidas e
licença livre), texto justificado, SEM negrito, itálico, sublinhado ou link.
Título (espécie, número e data) e assinaturas ficam centralizados; a ementa
fica no bloco da metade direita; artigos, incisos e o fecho têm recuo na
primeira linha; trechos citados (decretos de alteração) ficam recuados dos dois
lados; uma linha em branco separa os parágrafos. Logomarca e assinaturas não
são reproduzidas.

Como a estrutura é recuperada: o texto chega em linhas fixas (as do Diário ou
as do PDF intermediário), sem marca de parágrafo. Uma linha abre parágrafo novo
quando casa com um dos inícios da espécie (`Especie.inicios_com_recuo` e
`inicios_sem_recuo`: "Art. 1º", "I -", "a)", "CONSIDERANDO"...) e a linha
anterior termina uma frase (pontuação, "; e"). Todo o resto continua o
parágrafo. Só o espaço em branco muda: o texto é conferido depois de gerado e
qualquer diferença (por exemplo, um caractere que a fonte não tem) é erro.

Os seis modelos têm o mesmo layout; o que muda de um tipo para outro é só o
conteúdo, então não há um gerador por tipo. Os tipos sem modelo (alteração de
norma, PROIND, revogação) usam o mesmo layout.

Casos de borda (viram aviso no log, nunca falha silenciosa):
  - ementa não encontrada (sem a abertura do corpo): o texto é gerado sem o
    bloco da ementa;
  - fecho não encontrado: nada é centralizado como assinatura;
  - trecho citado sem aspas de fechamento: segue como citação até o fecho;
  - anexo: título centralizado e linhas mantidas como estão (tabelas não são
    reorganizadas); conferir o resultado.
"""

from __future__ import annotations

import gc
import logging
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from fpdf import FPDF

from especies import Especie
from identificador import (
    Identificacao,
    StatusIdentificacao,
    identificar,
    normalizar_espacos,
)
from reader import StatusLeitura, ler_pdf
from separador import limpar_ligaduras

log = logging.getLogger("pipeline")

PASTA_FONTES = Path(__file__).resolve().parent / "fontes"
FONTE_TEXTO = "LiberationSerif-Regular.ttf"

# Medidas (pontos), conferidas nos decretos-modelo da DBF.
_MARGEM_ESQUERDA = 85.0
_MARGEM_DIREITA = 54.0
_MARGEM_SUPERIOR = 85.0
_MARGEM_INFERIOR = 70.0
_TAMANHO_FONTE = 12
_FATOR_LINHA = 1.15  # 12 pt x 1,15 = 13,8 pt, a altura de linha dos modelos
_RECUO_PRIMEIRA_LINHA = 28.3
_RECUO_EMENTA = 198.5
_RECUO_CITACAO = 42.5

_ABERTURA_DE_CITACAO = "“"
# Aspas de fechamento no fim da linha, tolerando a marca de alteração ("(NR)") e pontuação depois.
_FECHA_CITACAO = re.compile(r"”\s*(?:\([A-Z]{2}\))?\s*[.;,]?\s*$")
# Linha só de pontos ("........"): supressão de trecho dentro de uma citação.
_PONTILHADO = re.compile(r"^\.{4,}[”\"]?$")
_CONTEM_PONTILHADO = re.compile(r"\.{20,}")
_INICIO_DE_ANEXO = re.compile(r"^ANEXO\b")
# A linha anterior terminou uma frase: ponto, ponto e vírgula, dois-pontos,
# vírgula, fecha parênteses ("(NR)"), fecha aspas ou "; e" / "; ou".
_FIM_DE_FRASE = re.compile(r"(?:[.;:,)”]|[;,]\s*(?:e|ou))\s*$")
_PONTO_DO_TITULO = re.compile(r"[ \t]*\.")
# Hífen no fim da linha seguido de minúscula: palavra composta partida na
# quebra ("pré-\nfabricado"); a junção é sem espaço.
_HIFEN_DE_QUEBRA = re.compile(r"(?<=\w)-\s+(?=[a-zà-ÿ])")
_TERMINA_EM_HIFEN = re.compile(r"\w-$")
_COMECA_EM_MINUSCULA = re.compile(r"[a-zà-ÿ]")


class ErroNormalizacao(Exception):
    """O PDF normalizado não pôde ser gerado ou não passou na conferência."""


class Estilo(Enum):
    TITULO = "título"  # centralizado
    EMENTA = "ementa"  # bloco da metade direita, justificado
    CORPO = "corpo"  # justificado, sem recuo
    DISPOSITIVO = "dispositivo"  # justificado, recuo na primeira linha
    CITACAO = "citação"  # recuado dos dois lados, justificado
    ASSINATURA = "assinatura"  # centralizado
    ANEXO_TITULO = "anexo"  # centralizado
    ANEXO_LINHA = "linha de anexo"  # à esquerda, linha a linha


@dataclass(frozen=True)
class Bloco:
    estilo: Estilo
    texto: str  # "\n" = quebra de linha obrigatória
    colado: bool = False  # sem linha em branco antes (assinaturas seguidas)


@dataclass(frozen=True)
class Documento:
    blocos: tuple[Bloco, ...]
    avisos: tuple[str, ...] = field(default=())


# ----------------------------------------------------------------------------
# Estrutura do texto
# ----------------------------------------------------------------------------


def _juntar(linhas: list[str]) -> str:
    """Junta as linhas de um parágrafo. Linha do pontilhado fica em linha própria;
    hífen de quebra ("pré-" + "fabricado") é junto, sem espaço."""
    texto = ""
    for linha in linhas:
        if not texto:
            texto = linha
        elif _PONTILHADO.match(linha) or _PONTILHADO.match(texto.rsplit("\n", 1)[-1]):
            texto += "\n" + linha
        elif _TERMINA_EM_HIFEN.search(texto) and _COMECA_EM_MINUSCULA.match(linha):
            texto += linha
        else:
            texto += " " + linha
    return texto


def _paragrafos(linhas: list[str], especie: Especie, avisos: list[str]) -> list[Bloco]:
    com_recuo, sem_recuo = especie.inicios_com_recuo, especie.inicios_sem_recuo

    def inicio(linha: str) -> Estilo | None:
        if sem_recuo and sem_recuo.match(linha):
            return Estilo.CORPO
        if com_recuo and com_recuo.match(linha):
            return Estilo.DISPOSITIVO
        if linha.startswith(_ABERTURA_DE_CITACAO):
            return Estilo.CITACAO
        return None

    blocos: list[Bloco] = []
    atual: list[str] = []
    estilo = Estilo.CORPO
    em_citacao = False
    anterior = ""

    def fechar() -> None:
        if atual:
            blocos.append(Bloco(estilo, _juntar(atual)))
            atual.clear()

    for bruta in linhas:
        linha = " ".join(bruta.split())
        if not linha:
            continue
        novo_estilo = inicio(linha) if _FIM_DE_FRASE.search(anterior) or not atual else None
        if not atual or novo_estilo is not None:
            fechar()
            estilo = novo_estilo or Estilo.CORPO
            if linha.startswith(_ABERTURA_DE_CITACAO):
                estilo = Estilo.CITACAO
                em_citacao = True
            elif em_citacao:
                estilo = Estilo.CITACAO
        atual.append(linha)
        if em_citacao and _FECHA_CITACAO.search(linha):
            em_citacao = False
        anterior = linha
    fechar()
    if em_citacao:
        avisos.append("trecho citado sem aspas de fechamento; o resto do ato saiu como citação")
    return blocos


def _assinaturas_e_anexos(texto: str, especie: Especie) -> list[Bloco]:
    """Linhas depois do fecho: assinaturas (centralizadas) e, se houver, anexos."""
    blocos: list[Bloco] = []
    em_anexo = False
    anterior_foi_cargo = False
    for bruta in texto.split("\n"):
        linha = " ".join(bruta.split())
        if not linha:
            continue
        if _INICIO_DE_ANEXO.match(linha):
            em_anexo = True
            blocos.append(Bloco(Estilo.ANEXO_TITULO, linha))
            anterior_foi_cargo = False
            continue
        if em_anexo:
            blocos.append(Bloco(Estilo.ANEXO_LINHA, linha, colado=True))
            continue
        blocos.append(Bloco(Estilo.ASSINATURA, linha, colado=bool(blocos) and not anterior_foi_cargo))
        anterior_foi_cargo = bool(especie.cargo_assinante and especie.cargo_assinante.match(linha))
    return blocos


def _regex_no_inicio_da_linha(padrao: re.Pattern[str]) -> re.Pattern[str]:
    return re.compile(rf"(?m)^[ \t]*(?:{padrao.pattern})", padrao.flags)


def estruturar(texto_bruto: str, especie: Especie) -> Documento:
    """Divide o texto do ato em blocos de layout. Só mexe em espaços em branco."""
    texto = unicodedata.normalize("NFC", limpar_ligaduras(texto_bruto)).replace("\r", "")
    avisos: list[str] = []
    blocos: list[Bloco] = []

    cabecalho = especie.cabecalho.search(texto)
    if not cabecalho:
        raise ErroNormalizacao(f"cabeçalho de {especie.nome} não encontrado no texto")
    antes = texto[: cabecalho.start()]
    if antes.strip():
        avisos.append("havia texto antes do cabeçalho; foi mantido antes do título")
        blocos.extend(_paragrafos(antes.split("\n"), especie, avisos))

    # Título: o cabeçalho e o ponto final que o fecha na mesma linha ("... DE 2026.").
    ponto = _PONTO_DO_TITULO.match(texto, cabecalho.end())
    fim_titulo = ponto.end() if ponto else cabecalho.end()
    blocos.append(Bloco(Estilo.TITULO, " ".join(texto[cabecalho.start() : fim_titulo].split())))
    resto = texto[fim_titulo:]

    # Ementa: até a abertura do corpo ("A GOVERNADORA...").
    abertura = _regex_no_inicio_da_linha(especie.abertura_do_corpo).search(resto) if especie.abertura_do_corpo else None
    if abertura:
        ementa = " ".join(_juntar([" ".join(l.split()) for l in resto[: abertura.start()].split("\n") if l.strip()]).split())
        if ementa:
            blocos.append(Bloco(Estilo.EMENTA, ementa))
        resto = resto[abertura.start() :]
    else:
        avisos.append("abertura do corpo não encontrada; o ato saiu sem o bloco da ementa")

    # Fecho: o que vem depois são assinaturas (e anexos).
    fecho = especie.fecho.search(resto) if especie.fecho else None
    if fecho:
        corpo, final = resto[: fecho.end()], resto[fecho.end() :]
    else:
        corpo, final = resto, ""
        avisos.append("fecho não encontrado; assinaturas não foram centralizadas")

    blocos.extend(_paragrafos(corpo.split("\n"), especie, avisos))
    blocos.extend(_assinaturas_e_anexos(final, especie))
    if any(b.estilo is Estilo.ANEXO_TITULO for b in blocos):
        avisos.append("o ato tem anexo; as linhas foram mantidas como estão, confira o resultado")
    return Documento(tuple(blocos), tuple(avisos))


# ----------------------------------------------------------------------------
# Geração do PDF
# ----------------------------------------------------------------------------


def _montar_pdf(documento: Documento, titulo: str, pasta_fontes: Path) -> FPDF:
    fonte = pasta_fontes / FONTE_TEXTO
    if not fonte.is_file():
        raise ErroNormalizacao(
            f"fonte não encontrada: {fonte}. A pasta 'fontes/' do projeto precisa conter {FONTE_TEXTO}."
        )
    pdf = FPDF(unit="pt", format="A4")
    pdf.set_margins(_MARGEM_ESQUERDA, _MARGEM_SUPERIOR, _MARGEM_DIREITA)
    pdf.c_margin = 0  # sem folga interna: o texto começa e termina nas margens, como no Word
    pdf.set_auto_page_break(True, _MARGEM_INFERIOR)
    pdf.add_font("Texto", "", str(fonte))
    pdf.set_font("Texto", "", _TAMANHO_FONTE)
    pdf.set_title(titulo)
    pdf.set_lang("pt-BR")
    pdf.add_page()
    altura = _TAMANHO_FONTE * _FATOR_LINHA

    # (alinhamento, margem esquerda extra, margem direita extra, recuo da 1ª linha)
    layout = {
        Estilo.TITULO: ("C", 0.0, 0.0, 0.0),
        Estilo.EMENTA: ("J", _RECUO_EMENTA, 0.0, 0.0),
        Estilo.CORPO: ("J", 0.0, 0.0, 0.0),
        Estilo.DISPOSITIVO: ("J", 0.0, 0.0, _RECUO_PRIMEIRA_LINHA),
        Estilo.CITACAO: ("J", _RECUO_CITACAO, _RECUO_CITACAO, 0.0),
        Estilo.ASSINATURA: ("C", 0.0, 0.0, 0.0),
        Estilo.ANEXO_TITULO: ("C", 0.0, 0.0, 0.0),
        Estilo.ANEXO_LINHA: ("L", 0.0, 0.0, 0.0),
    }
    for posicao, bloco in enumerate(documento.blocos):
        if posicao and not bloco.colado:
            pdf.ln(altura)
        alinhamento, esquerda, direita, recuo = layout[bloco.estilo]
        pdf.set_x(pdf.l_margin)
        largura = pdf.w - pdf.l_margin - pdf.r_margin - esquerda - direita
        with pdf.text_columns(
            text_align=alinhamento,
            line_height=_FATOR_LINHA,
            l_margin=pdf.l_margin + esquerda,
            r_margin=pdf.r_margin + direita,
        ) as colunas:
            # Cada linha obrigatória ("\n", só existe com pontilhado) é um
            # parágrafo próprio, para ajustar o corpo da fonte à largura.
            for numero, segmento in enumerate(bloco.texto.split("\n")):
                pdf.set_font_size(_corpo_que_cabe(pdf, segmento, largura))
                with colunas.paragraph(first_line_indent=recuo if numero == 0 else 0) as paragrafo:
                    paragrafo.write(segmento)
                pdf.set_font_size(_TAMANHO_FONTE)
    return pdf


def _corpo_que_cabe(pdf: FPDF, segmento: str, largura: float) -> float:
    """Corpo da fonte para uma linha de pontos caber inteira na largura (nos
    modelos, ela vai de margem a margem). Texto comum mantém o corpo normal e
    quebra em linhas."""
    if not _CONTEM_PONTILHADO.search(segmento):
        return _TAMANHO_FONTE
    pdf.set_font_size(_TAMANHO_FONTE)
    necessaria = pdf.get_string_width(segmento)
    if necessaria <= largura * 0.995:
        return _TAMANHO_FONTE
    return round(_TAMANHO_FONTE * largura * 0.99 / necessaria, 2)


def _para_conferencia(texto: str) -> str:
    return normalizar_espacos(_HIFEN_DE_QUEBRA.sub("-", limpar_ligaduras(texto)))


def _conferir(parcial: Path, esperado: str, ident: Identificacao) -> None:
    leitura = ler_pdf(parcial)
    if leitura.status is not StatusLeitura.OK:
        raise ErroNormalizacao(
            f"PDF gerado não pôde ser relido ({leitura.status.value}): {leitura.detalhe}"
        )
    nova = identificar(leitura.texto, (ident.especie,))
    if nova.status is not StatusIdentificacao.IDENTIFICADO or nova.numero != ident.numero:
        raise ErroNormalizacao(
            f"PDF gerado foi identificado como {nova.numero!r} ({nova.status.value}), "
            f"esperado {ident.numero!r}"
        )
    if (nova.tipo and nova.tipo.nome) != (ident.tipo and ident.tipo.nome):
        raise ErroNormalizacao("o tipo do ato mudou no PDF gerado")
    if _para_conferencia(leitura.texto) != esperado:
        raise ErroNormalizacao(
            "o texto do PDF gerado difere do texto do ato (caractere sem glifo na fonte?)"
        )


def _remover(caminho: Path) -> None:
    try:
        caminho.unlink(missing_ok=True)
    except PermissionError:  # Windows: identificador do PyMuPDF ainda aberto
        gc.collect()
        try:
            caminho.unlink(missing_ok=True)
        except OSError:
            pass


def _gerar(
    texto: str, ident: Identificacao, destino: Path, pasta_fontes: Path
) -> Documento:
    especie = ident.especie
    documento = estruturar(texto, especie)
    esperado = _para_conferencia(texto)
    parcial = destino.with_name(destino.name + ".part")
    destino.parent.mkdir(parents=True, exist_ok=True)
    try:
        _montar_pdf(documento, f"{especie.nome} {ident.numero}", pasta_fontes).output(str(parcial))
        _conferir(parcial, esperado, ident)
        parcial.replace(destino)
    except ErroNormalizacao:
        _remover(parcial)
        raise
    except Exception as erro:
        _remover(parcial)
        raise ErroNormalizacao(f"{type(erro).__name__}: {erro}") from erro
    return documento


def gerar_pdf_normalizado(
    original: Path,
    ident: Identificacao,
    pasta_saida: Path,
    simular: bool = False,
    pasta_fontes: Path = PASTA_FONTES,
) -> Path:
    """Gera o PDF normalizado do ato em `pasta_saida` e devolve o caminho.

    Um arquivo anterior com o mesmo nome em `pasta_saida` (sobra de uma
    execução em que a etapa 5 não terminou) é substituído: é cópia derivada,
    não a base da DBF. Em `simular`, o PDF é gerado e conferido em uma pasta
    temporária que é apagada em seguida, e `pasta_saida` não é tocada.
    """
    if ident.status is not StatusIdentificacao.IDENTIFICADO or ident.especie is None:
        raise ErroNormalizacao("ato não identificado; nada a normalizar")
    leitura = ler_pdf(original)
    if leitura.status is not StatusLeitura.OK:
        raise ErroNormalizacao(
            f"{original.name} não pôde ser lido ({leitura.status.value}): {leitura.detalhe}"
        )
    nome = ident.especie.nome_arquivo(ident.numero)
    destino = pasta_saida / nome
    if simular:
        with tempfile.TemporaryDirectory() as pasta_temporaria:
            documento = _gerar(leitura.texto, ident, Path(pasta_temporaria) / nome, pasta_fontes)
    else:
        documento = _gerar(leitura.texto, ident, destino, pasta_fontes)
    for aviso in documento.avisos:
        log.warning("%s | AVISO (normalização): %s", nome, aviso)
    return destino