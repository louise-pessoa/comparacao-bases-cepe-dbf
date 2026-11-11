"""Etapa 5 — armazenamento e descarte dos PDFs.

`mover_sem_sobrescrever` e `descartar` são usados desde a etapa 2 (PDF de imagem
-> "leitura tesseract", outra espécie -> descarte, erros -> revisão).

`armazenar` grava o PDF normalizado na pasta da DBF (por exemplo, o Drive em G:):

  1. confere o PDF gerado: abre, lê o texto e confirma que ele se identifica como
     o ato esperado (nome final igual ao calculado pela espécie e pelo número);
  2. confere de novo, na hora de gravar, que o nome ainda não existe na pasta da
     DBF (outra pessoa pode ter salvado o mesmo decreto desde a etapa 3);
  3. copia para "<nome>.part" na própria pasta da DBF e compara o SHA-256 do que
     foi gravado com o do PDF gerado;
  4. renomeia para o nome final (no Windows, o renomear recusa um nome que já
     existe, então nunca sobrescreve) e relê o arquivo final;
  5. só então descarta o PDF original (em `dados/pendentes`) e a cópia gerada (em
     `dados/normalizados`).

Qualquer falha antes do passo 5 deixa o original onde estava, para reprocessar, e
não deixa arquivo parcial na pasta da DBF. A pasta é sincronizada com o Drive:
o ".part" é o único arquivo temporário e só existe durante a cópia.

Exceções de `armazenar`:
  - `ArquivoJaExiste`: o nome já existe na DBF (o original deve ser descartado como
    duplicado);
  - `GravacaoDivergente`: o conteúdo gravado não confere (o original deve ir para
    revisão);
  - `ErroArmazenamento`: falha de E/S (pasta indisponível, sem permissão, disco
    cheio); o original deve ficar onde está para uma nova tentativa.
"""

from __future__ import annotations

import gc
import hashlib
import logging
import os
import shutil
from pathlib import Path

from dbf_cepe.pipeline.duplicidade import chave_nome
from dbf_cepe.leitura.identificador import StatusIdentificacao, identificar
from dbf_cepe.leitura.reader import StatusLeitura, ler_pdf

log = logging.getLogger("pipeline")

_BLOCO = 1024 * 1024


class ErroArmazenamento(Exception):
    """Não foi possível gravar o PDF na pasta da DBF (falha de E/S; tentar de novo)."""


class ArquivoJaExiste(ErroArmazenamento):
    """O nome final já existe na pasta da DBF; nada foi gravado."""


class GravacaoDivergente(ErroArmazenamento):
    """O PDF gerado ou o arquivo gravado não passou na conferência."""


def mover_sem_sobrescrever(
    origem: Path, pasta_destino: Path, simular: bool = False
) -> Path:
    """Move o arquivo mantendo o nome; se já existir, acrescenta " (2)", " (3)"...

    Nunca sobrescreve: um PDF baixado de novo com o mesmo nome da CEPE não pode
    apagar um anterior que ainda aguarda OCR ou revisão.
    """
    destino = pasta_destino / origem.name
    contador = 2
    while destino.exists():
        destino = pasta_destino / f"{origem.stem} ({contador}){origem.suffix}"
        contador += 1
    if not simular:
        pasta_destino.mkdir(parents=True, exist_ok=True)
        shutil.move(str(origem), str(destino))
    return destino


def descartar(origem: Path, simular: bool = False) -> None:
    """Apaga o PDF temporário. A exclusão é definitiva (sem lixeira)."""
    if not simular:
        origem.unlink()


def descartar_se_existir(caminho: Path) -> bool:
    """Apaga o arquivo se existir; nunca levanta exceção. Devolve False se não conseguiu.

    No Windows, um identificador do PyMuPDF ainda aberto pode bloquear a exclusão;
    uma coleta de lixo e uma segunda tentativa resolvem.
    """
    for tentativa in (1, 2):
        try:
            caminho.unlink(missing_ok=True)
            return True
        except OSError:
            if tentativa == 1:
                gc.collect()
    log.warning("Não foi possível apagar %s; apague à mão.", caminho)
    return False


def _sha256(caminho: Path) -> str:
    resumo = hashlib.sha256()
    with open(caminho, "rb") as arquivo:
        while bloco := arquivo.read(_BLOCO):
            resumo.update(bloco)
    return resumo.hexdigest()


def _conferir_se_identifica_como(pdf: Path, nome_final: str, descricao: str) -> None:
    leitura = ler_pdf(pdf)
    if leitura.status is not StatusLeitura.OK:
        raise GravacaoDivergente(
            f"{descricao} não pôde ser lido ({leitura.status.value}): {leitura.detalhe}"
        )
    ident = identificar(leitura.texto)
    if (
        ident.status is not StatusIdentificacao.IDENTIFICADO
        or ident.especie is None
        or chave_nome(ident.especie.nome_arquivo(ident.numero)) != chave_nome(nome_final)
    ):
        raise GravacaoDivergente(
            f"{descricao} não se identifica como {nome_final} ({ident.status.value}: {ident.detalhe})"
        )


def armazenar(
    pdf_gerado: Path,
    nome_final: str,
    pasta_dbf: Path,
    original: Path,
    simular: bool = False,
    nome_identificado: str | None = None,
) -> Path:
    """Grava `pdf_gerado` em `pasta_dbf / nome_final` e então descarta `original`.

    Devolve o caminho gravado. Em `simular`, só confere que o nome ainda está livre.
    `nome_identificado` é o nome que o TEXTO do PDF produz (padrão: `nome_final`);
    difere numa republicação ('Decreto N (republicação).pdf' se identifica como
    'Decreto N.pdf').
    """
    esperado_no_texto = nome_identificado or nome_final
    destino = pasta_dbf / nome_final
    if destino.exists():
        raise ArquivoJaExiste(f"{nome_final} já existe em {pasta_dbf}")
    if simular:
        return destino

    _conferir_se_identifica_como(pdf_gerado, esperado_no_texto, "o PDF gerado")

    parcial = destino.with_name(destino.name + ".part")
    try:
        if not pasta_dbf.is_dir():
            raise ErroArmazenamento(f"pasta da DBF indisponível: {pasta_dbf}")
        esperado = _sha256(pdf_gerado)
        # "wb" substitui a sobra de uma execução interrompida
        with open(pdf_gerado, "rb") as origem, open(parcial, "wb") as saida:
            shutil.copyfileobj(origem, saida, _BLOCO)
            saida.flush()
            os.fsync(saida.fileno())
        if _sha256(parcial) != esperado:
            raise GravacaoDivergente(f"a cópia de {nome_final} na DBF difere do PDF gerado")
        if destino.exists():
            raise ArquivoJaExiste(f"{nome_final} apareceu em {pasta_dbf} durante a gravação")
        try:
            os.rename(parcial, destino)  # no Windows, recusa um nome que já existe
        except FileExistsError as erro:
            raise ArquivoJaExiste(f"{nome_final} apareceu em {pasta_dbf} durante a gravação") from erro
        try:
            _conferir_se_identifica_como(destino, esperado_no_texto, f"{nome_final} gravado na DBF")
        except GravacaoDivergente:
            descartar_se_existir(destino)  # fomos nós que o criamos há instantes
            raise
    except ErroArmazenamento:
        descartar_se_existir(parcial)
        raise
    except OSError as erro:
        descartar_se_existir(parcial)
        raise ErroArmazenamento(f"{type(erro).__name__}: {erro}") from erro

    # Gravação confirmada: só agora os PDFs de trabalho podem ir embora.
    descartar_se_existir(original)
    descartar_se_existir(pdf_gerado)
    return destino

# ----------------------------------------------------------------------------
# Republicação: renomear arquivos da base antes de gravar (com desfazer)
# ----------------------------------------------------------------------------


def renomear_na_base(pasta_dbf: Path, de: str, para: str) -> None:
    """Renomeia um arquivo da base sem nunca sobrescrever (no Linux, `os.rename`
    substituiria o destino em silêncio; por isso a conferência antes)."""
    origem, destino = pasta_dbf / de, pasta_dbf / para
    if not origem.is_file():
        raise ErroArmazenamento(f"{de} não está mais em {pasta_dbf}")
    if destino.exists():
        raise ArquivoJaExiste(f"{para} já existe em {pasta_dbf}")
    try:
        os.rename(origem, destino)
    except FileExistsError as erro:
        raise ArquivoJaExiste(f"{para} apareceu em {pasta_dbf}") from erro
    except OSError as erro:
        raise ErroArmazenamento(f"não foi possível renomear {de} para {para}: {erro}") from erro


def desfazer_renomeacoes(pasta_dbf: Path, feitas: list[tuple[str, str]]) -> list[str]:
    """Desfaz as renomeações (da última para a primeira). Devolve o que não deu para desfazer."""
    falhas = []
    for de, para in reversed(feitas):
        try:
            renomear_na_base(pasta_dbf, para, de)
        except ErroArmazenamento as erro:
            falhas.append(f"{para} -> {de}: {erro}")
    return falhas


class RenomeacaoNaoDesfeita(ErroArmazenamento):
    """A gravação falhou e nem todas as renomeações puderam ser desfeitas."""


def armazenar_com_renomeacoes(
    pdf_gerado: Path,
    nome_final: str,
    pasta_dbf: Path,
    original: Path,
    renomeacoes: tuple[tuple[str, str], ...],
    nome_identificado: str,
) -> Path:
    """Renomeia os arquivos da base (`renomeacoes`, na ordem dada) e grava o novo.

    Se qualquer passo falhar, as renomeações já feitas são desfeitas e a exceção
    original sobe. Se nem o desfazer funcionar, sobe `RenomeacaoNaoDesfeita` (o
    registro guarda a operação para a próxima execução tentar de novo)."""
    feitas: list[tuple[str, str]] = []
    try:
        for de, para in renomeacoes:
            renomear_na_base(pasta_dbf, de, para)
            feitas.append((de, para))
        return armazenar(pdf_gerado, nome_final, pasta_dbf, original, nome_identificado=nome_identificado)
    except Exception as erro:
        falhas = desfazer_renomeacoes(pasta_dbf, feitas)
        if falhas:
            raise RenomeacaoNaoDesfeita(
                f"{erro}; e não foi possível desfazer: {'; '.join(falhas)}"
            ) from erro
        raise


def recuperar_operacao(registro, pasta_dbf: Path) -> str:
    """Trata a operação que ficou anotada no registro (queda no meio da anterior).

    Se o arquivo novo já está na base e todas as renomeações foram feitas, a operação
    terminou: o registro é atualizado (concluída). Senão, as renomeações feitas são
    desfeitas e um ".part" que tenha sobrado do arquivo novo é apagado (desfeita).
    Devolve uma frase para o log. `registro` é um `registro.Registro`.
    """
    from dbf_cepe.pipeline.registro import Entrada, ErroRegistro

    operacao = registro.operacao_em_andamento
    if operacao is None:
        return "nenhuma"
    try:
        especie, numero, ano = operacao["especie"], operacao["numero"], operacao.get("ano")
        renomeacoes = [(de, para) for de, para in operacao["renomeacoes"]]
        gravar = operacao["gravar"]
        nova = Entrada.de_dict(operacao["nova_entrada"])
    except (KeyError, TypeError, ValueError) as erro:
        raise ErroRegistro(f"operacao_em_andamento ilegível no registro: {erro}") from erro

    # Cada passo da operação deixa um conjunto conhecido de nomes na base: antes de tudo,
    # todos os "de"; depois de k renomeações, os k primeiros "de" viram "para"; no fim,
    # soma-se o arquivo novo. O estado atual diz em que passo o programa caiu.
    envolvidos = {chave_nome(n) for par in renomeacoes for n in par} | {chave_nome(gravar)}
    atual = {chave_nome(e.name) for e in pasta_dbf.iterdir() if e.is_file() and chave_nome(e.name) in envolvidos}
    estado = {chave_nome(de) for de, _ in renomeacoes}
    passos = [set(estado)]
    for de, para in renomeacoes:
        estado = (estado - {chave_nome(de)}) | {chave_nome(para)}
        passos.append(set(estado))
    if atual == passos[-1] | {chave_nome(gravar)}:
        for de, para in renomeacoes:
            registro.renomear(especie, numero, ano, de, para)
        registro.adicionar(nova)
        registro.operacao_em_andamento = None
        registro.salvar()
        return f"{gravar} já estava gravado: operação concluída no registro"
    feitos = next((k for k, passo in enumerate(passos) if passo == atual), None)
    if feitos is None:
        raise ErroRegistro(
            f"os arquivos da base não conferem com a operação interrompida ({renomeacoes}, gravar "
            f"{gravar}); confira à mão e depois apague 'operacao_em_andamento' do registro"
        )
    feitas = renomeacoes[:feitos]
    falhas = desfazer_renomeacoes(pasta_dbf, feitas)
    if falhas:
        raise ErroRegistro(f"não foi possível desfazer a operação interrompida: {'; '.join(falhas)}")
    descartar_se_existir(pasta_dbf / (gravar + ".part"))
    registro.operacao_em_andamento = None
    registro.salvar()
    desfeitas = ", ".join(f"{para} -> {de}" for de, para in feitas) or "nenhuma renomeação feita"
    return f"{gravar} não chegou a ser gravado: operação desfeita ({desfeitas})"


# ----------------------------------------------------------------------------
# Conferência (`--conferir`): substituir um arquivo da base pelo texto do Diário
# ----------------------------------------------------------------------------


def _copiar_conferindo(origem: Path, destino_parcial: Path) -> str:
    """Copia `origem` para `destino_parcial` (gravado no disco) e devolve o SHA-256,
    conferido contra o da origem."""
    esperado = _sha256(origem)
    with open(origem, "rb") as entrada, open(destino_parcial, "wb") as saida:
        shutil.copyfileobj(entrada, saida, _BLOCO)
        saida.flush()
        os.fsync(saida.fileno())
    if _sha256(destino_parcial) != esperado:
        raise GravacaoDivergente(f"a cópia de {origem.name} difere do original")
    return esperado


def substituir_na_base(
    pdf_novo: Path, nome: str, pasta_dbf: Path, pasta_copias: Path, nome_identificado: str | None = None
) -> Path:
    """Troca o arquivo `nome` da base pelo `pdf_novo` e devolve a cópia de segurança.

    1. confere que o PDF novo se identifica como o ato esperado;
    2. copia o arquivo atual da base para `pasta_copias` (nome com data e hora) e confere;
    3. copia o novo para "<nome>.part" na base, confere o SHA-256 e troca de uma vez
       (`os.replace`), relendo o resultado;
    4. se o passo 3 falhar depois da troca, devolve o arquivo antigo a partir da cópia.

    Nunca apaga a cópia de segurança. O `pdf_novo` não é apagado (quem chama decide).
    """
    destino = pasta_dbf / nome
    if not destino.is_file():
        raise ErroArmazenamento(f"{nome} não está em {pasta_dbf}")
    esperado_no_texto = nome_identificado or nome
    _conferir_se_identifica_como(pdf_novo, esperado_no_texto, "o PDF do Diário")

    from datetime import datetime

    pasta_copias.mkdir(parents=True, exist_ok=True)
    raiz, _, extensao = nome.rpartition(".")
    copia = pasta_copias / f"{raiz} - substituído em {datetime.now():%Y%m%d-%H%M%S}.{extensao}"
    try:
        _copiar_conferindo(destino, copia)
    except OSError as erro:
        descartar_se_existir(copia)
        raise ErroArmazenamento(f"não foi possível guardar a cópia de {nome}: {erro}") from erro

    parcial = destino.with_name(destino.name + ".part")
    trocado = False
    try:
        _copiar_conferindo(pdf_novo, parcial)
        os.replace(parcial, destino)
        trocado = True
        _conferir_se_identifica_como(destino, esperado_no_texto, f"{nome} na base")
        if _sha256(destino) != _sha256(pdf_novo):
            raise GravacaoDivergente(f"{nome} na base difere do PDF do Diário")
    except (ErroArmazenamento, OSError) as erro:
        descartar_se_existir(parcial)
        if trocado:
            _devolver(copia, destino)
        if isinstance(erro, ErroArmazenamento):
            raise
        raise ErroArmazenamento(f"{type(erro).__name__}: {erro}") from erro
    return copia


def _devolver(copia: Path, destino: Path) -> None:
    """Põe a cópia de segurança de volta no lugar (troca atômica)."""
    parcial = destino.with_name(destino.name + ".part")
    try:
        _copiar_conferindo(copia, parcial)
        os.replace(parcial, destino)
    except (ErroArmazenamento, OSError) as erro:
        descartar_se_existir(parcial)
        raise ErroArmazenamento(
            f"a substituição falhou e o arquivo antigo não pôde ser devolvido; ele está em {copia}: {erro}"
        ) from erro
