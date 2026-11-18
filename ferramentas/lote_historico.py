"""Ferramenta auxiliar: roda o lote histórico de vários meses em sequência, um mês por vez,
e para no primeiro sinal de problema.

Para cada mês, em ordem cronológica, faz o mesmo que o passo a passo de docs/uso.md
("Lote histórico"):

    1. baixa os cadernos em dados/historico/AAAA/AAAA-MM (--fonte s3 até 05/2026, resumo depois);
    2. --separar --comparar (só lê);
    3. --separar --gravar (grava em dados/pendentes);
    4. lote simulado;
    5. lote de verdade (só com --executar);
    6. limpeza dos cadernos do mês (só com --executar): mostra no log a lista do que pode
       ser apagado e apaga em seguida (pipeline/limpeza.py). O caderno com alguma pendência
       (conferir duplicata sem decisão, PDF em erros...) fica, com o motivo no log.

Para ANTES de gravar qualquer coisa do mês quando:
  - o download não trouxe nenhum caderno, ou houve falha / falta de conexão;
  - algum decreto da DBF foi reprovado no critério da leitura em cascata (texto cortado ou
    misturado: revisão manual);
  - algum decreto da DBF que a base já tem ficou com similaridade abaixo de 95%;
  - algum decreto não pôde ser gravado em pendentes;
  - a simulação do lote mandaria algo para erros ou falharia ao gravar.

E não começa se `dados/pendentes` já tiver PDFs (sobra de uma rodada anterior).

O que vai para "conferir duplicata" NÃO para a sequência: é anotado no resumo, e a
conferência fica para o fim (`python main.py --conferir`).

Uso (da raiz do repositório, com o .env apontando para a base certa):
    python -m ferramentas.lote_historico --de 2020-03 --ate 2020-06              # só simula
    python -m ferramentas.lote_historico --de 2020-03 --ate 2020-06 --executar   # grava na base
    python -m ferramentas.lote_historico --reorganizar [--executar]  # AAAA-MM -> AAAA/AAAA-MM

Pastas: um ano por pasta e, dentro dela, um mês por pasta (dados/historico/2020/2020-03;
decisão de 17/11/2026, para o histórico de 1999 a 2026). A pasta do mês mantém o ano no nome
para ser reconhecível sozinha. Até 17/11/2026 os meses ficavam direto em dados/historico
(dados/historico/2020-03): o `--reorganizar` move essas pastas (sem --executar, só mostra), e o
lote não começa enquanto houver uma, para não baixar de novo um mês que já existe. Mover não
perde nada: o manifesto, a simulação da limpeza e o registro de publicações guardam o nome do
caderno, não o caminho.

Sem --executar, cada mês é baixado, separado e simulado, mas nada vai para a base,
`dados/pendentes` é esvaziado no fim do mês (os PDFs gerados ali só serviam à simulação)
e nenhum caderno é apagado. Se o mês parar depois do --gravar e antes do lote de verdade
(falha ao gravar em pendentes, simulação com problema), `dados/pendentes` também é
esvaziado, com ou sem --executar: nada daquilo foi para a base, e rodar o mês de novo
gera tudo outra vez. Se parar no lote de verdade, o que sobrou em pendentes fica. Com --executar e --manter-cadernos, também não se apaga nada.
Sai com código 0 se todos os meses terminaram, 1 se parou num mês, 2 em erro de uso.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path

from dbf_cepe import captura
from dbf_cepe.cli import _configurar_log, capturar
from dbf_cepe.config import Configuracao, ErroConfiguracao, carregar_configuracao
from dbf_cepe.pipeline import limpeza as limpeza_dos_cadernos
from dbf_cepe.pipeline.lote import Destino, executar, listar_pdfs
from dbf_cepe.pipeline.registro import ErroRegistro
from dbf_cepe.pipeline.relatorio import ResumoRelatorio, relatorio_dos_cadernos

log = logging.getLogger("pipeline")

PASTA_HISTORICO = Path("dados/historico")
_MES_NO_FORMATO_ANTIGO = re.compile(r"^\d{4}-\d{2}$")
# Até este mês (inclusive) o histórico vem do S3; depois, do site atual da CEPE.
ULTIMO_MES_DO_S3 = date(2026, 5, 1)
SIMILARIDADE_MINIMA = 0.95


class ErroDeUso(Exception):
    pass


@dataclass
class Mes:
    inicio: date
    cadernos_baixados: int = 0
    resumo_separacao: ResumoRelatorio | None = None
    resumo_gravacao: ResumoRelatorio | None = None
    lote: Counter[Destino] = field(default_factory=Counter)
    limpeza: limpeza_dos_cadernos.ResultadoLimpeza | None = None
    parada: str = ""  # motivo da parada; vazio = mês concluído

    @property
    def nome(self) -> str:
        return f"{self.inicio:%Y-%m}"

    @property
    def fim(self) -> date:
        proximo = (self.inicio.replace(day=28) + timedelta(days=4)).replace(day=1)
        return proximo - timedelta(days=1)

    @property
    def fonte(self) -> str:
        return "s3" if self.inicio <= ULTIMO_MES_DO_S3 else "resumo"


def meses(de: date, ate: date) -> list[date]:
    if ate < de:
        raise ErroDeUso("--ate é anterior a --de")
    lista, atual = [], de.replace(day=1)
    while atual <= ate:
        lista.append(atual)
        atual = (atual.replace(day=28) + timedelta(days=4)).replace(day=1)
    return lista


def _mes_iso(texto: str) -> date:
    try:
        return date.fromisoformat(texto + "-01")
    except ValueError:
        raise argparse.ArgumentTypeError(f"mês inválido: {texto!r} (use AAAA-MM)")


def motivos_de_parada_na_separacao(resumo: ResumoRelatorio) -> list[str]:
    motivos = [f"decreto da DBF reprovado no critério de leitura: {r}" for r in resumo.reprovados_dbf]
    for nome, comparado in resumo.comparacoes:
        if comparado.lido and not comparado.identico and comparado.similaridade < SIMILARIDADE_MINIMA:
            motivos.append(f"{nome}: similaridade {comparado.similaridade:.2%} com o arquivo da base")
    return motivos


def motivos_de_parada_no_lote(simulacao: Counter[Destino]) -> list[str]:
    motivos = []
    for destino in (Destino.ENVIADO_ERROS, Destino.ENVIADO_OCR, Destino.FALHA_AO_GRAVAR):
        if simulacao[destino]:
            motivos.append(f"{simulacao[destino]} PDF(s) com destino \"{destino.value}\" na simulação")
    return motivos


def _esvaziar_pendentes(cfg: Configuracao) -> None:
    """Apaga os PDFs que o --gravar deste mês pôs em pendentes e que não vão para a base
    (simulação, ou parada antes do lote de verdade). Ao rodar o mês de novo, eles são gerados
    outra vez; sem isto, a rodada seguinte recusaria começar ("PDFs de uma rodada anterior")."""
    for pdf in listar_pdfs(cfg.pasta_temp):
        pdf.unlink()


def pasta_do_mes(nome: str, historico: Path | None = None) -> Path:
    """dados/historico/AAAA/AAAA-MM."""
    return (historico or PASTA_HISTORICO) / nome[:4] / nome


def pastas_no_formato_antigo(historico: Path | None = None) -> list[Path]:
    """As pastas de mês direto em dados/historico (AAAA-MM), do formato anterior a 17/11/2026."""
    historico = historico or PASTA_HISTORICO
    if not historico.is_dir():
        return []
    return sorted(p for p in historico.iterdir() if p.is_dir() and _MES_NO_FORMATO_ANTIGO.match(p.name))


@dataclass
class Juncao:
    """O que fazer para levar uma pasta de mês solta (dados/historico/AAAA-MM) para dentro da
    pasta do ano. `conflitos` não vazio: nada é feito com esta pasta."""
    antiga: Path
    nova: Path
    mover_pasta: bool = False  # o destino não existe: a pasta inteira é movida
    mover: list[str] = field(default_factory=list)  # arquivos levados para o destino
    repetidos: list[str] = field(default_factory=list)  # idênticos no destino: a cópia solta é apagada
    descartar: list[str] = field(default_factory=list)  # a simulação da limpeza (é refeita)
    manifesto: dict | None = None  # manifesto combinado, se as duas pastas tinham manifesto
    entradas_combinadas: int = 0  # o mesmo caderno nos dois manifestos, combinado (`combinar_entradas`)
    conflitos: list[str] = field(default_factory=list)


def _sha256(arquivo: Path) -> str:
    import hashlib

    resumo = hashlib.sha256()
    with open(arquivo, "rb") as entrada:
        for bloco in iter(lambda: entrada.read(1 << 20), b""):
            resumo.update(bloco)
    return resumo.hexdigest()


# Campos que podem diferir entre as duas entradas do MESMO arquivo: o mês baixado duas vezes
# (data do download) e processado numa das pastas só (caso de 2010-09, 18/11/2026).
_CAMPOS_COMBINAVEIS = {"baixado_em", "processado_em", "processamento"}


def combinar_entradas(solta: dict, do_ano: dict) -> dict | None:
    """A entrada do mesmo caderno nos dois manifestos, combinada; None se não for seguro.
    Só combina o mesmo arquivo (sha256, tamanho e url iguais) com diferença apenas na data do
    download e no processamento: fica o processamento de quem o tem (os dois com processamento
    diferente: None) e a data do download mais antiga. Caderno marcado como apagado em uma das
    pastas (`apagado_em`) nunca é combinado."""
    if not solta.get("sha256") or any(solta.get(c) != do_ano.get(c) for c in ("sha256", "tamanho", "url")):
        return None
    diferentes = {c for c in solta.keys() | do_ano.keys() if solta.get(c) != do_ano.get(c)}
    if not diferentes <= _CAMPOS_COMBINAVEIS:
        return None
    com_processamento = [e for e in (solta, do_ano) if "processamento" in e]
    if len(com_processamento) == 2 and {"processamento", "processado_em"} & diferentes:
        return None
    base = dict(com_processamento[0] if com_processamento else do_ano)
    datas = [e["baixado_em"] for e in (solta, do_ano) if e.get("baixado_em")]
    if datas:
        base["baixado_em"] = min(datas)
    return base


def diferencas_do_manifesto(antigo: dict, atual: dict, diferentes: list[str]) -> list[str]:
    """Que campos diferem entre as entradas do mesmo caderno nos dois manifestos, quantas
    vezes, e o exemplo do primeiro. Serve para decidir a junção à mão: só `baixado_em`
    diferente, com o mesmo `sha256`, é o mesmo caderno baixado duas vezes."""
    campos: Counter[str] = Counter()
    mesmo_arquivo = 0
    for chave in diferentes:
        a, b = antigo[chave], atual[chave]
        campos.update(c for c in a.keys() | b.keys() if a.get(c) != b.get(c))
        mesmo_arquivo += bool(a.get("sha256")) and a.get("sha256") == b.get("sha256")
    linhas = [
        "  campos diferentes: " + ", ".join(f"{c} ({n})" for c, n in campos.most_common()),
        f"  mesmo sha256 (mesmo arquivo) nos dois: {mesmo_arquivo} de {len(diferentes)}",
    ]
    exemplo = diferentes[0]
    for campo in sorted(c for c in campos if c != "processamento"):
        a, b = antigo[exemplo].get(campo, "(ausente)"), atual[exemplo].get(campo, "(ausente)")
        if a != b:
            linhas.append(f"  {exemplo} | {campo}: pasta solta {a} | pasta do ano {b}")
    if "processamento" in campos:
        tem = [nome for nome, m in (("pasta solta", antigo[exemplo]), ("pasta do ano", atual[exemplo]))
               if "processamento" in m]
        linhas.append(f"  {exemplo} | processamento: só em {', '.join(tem) or 'nenhuma'}"
                      if len(tem) < 2 else f"  {exemplo} | processamento: diferente nas duas")
    return linhas


def planejar_juncao(antiga: Path, nova: Path) -> Juncao:
    """Como juntar `antiga` em `nova` sem sobrescrever nada (só lê)."""
    juncao = Juncao(antiga, nova)
    if not nova.exists():
        juncao.mover_pasta = True
        return juncao
    for item in sorted(antiga.iterdir()):
        destino = nova / item.name
        if item.is_dir():
            juncao.conflitos.append(f"{item.name}: subpasta (junte à mão)")
        elif item.name == captura.NOME_MANIFESTO:
            try:
                antigo, atual = captura.ler_manifesto(antiga), captura.ler_manifesto(nova)
            except captura.ErroCaptura as erro:
                juncao.conflitos.append(str(erro))
                continue
            diferentes = sorted(k for k in antigo.keys() & atual.keys() if antigo[k] != atual[k])
            combinadas = {k: c for k in diferentes if (c := combinar_entradas(antigo[k], atual[k])) is not None}
            diferentes = [k for k in diferentes if k not in combinadas]
            juncao.entradas_combinadas = len(combinadas)
            if diferentes:
                juncao.conflitos.append(
                    f"{captura.NOME_MANIFESTO}: {len(diferentes)} caderno(s) com dados diferentes nas duas pastas "
                    f"(ex.: {diferentes[0]})"
                )
                juncao.conflitos += diferencas_do_manifesto(antigo, atual, diferentes)
            else:
                juncao.manifesto = {**atual, **antigo, **combinadas}
        elif item.name == limpeza_dos_cadernos.NOME_SIMULACAO:
            juncao.descartar.append(item.name)
        elif not destino.exists():
            juncao.mover.append(item.name)
        elif _sha256(item) == _sha256(destino):
            juncao.repetidos.append(item.name)
        else:
            juncao.conflitos.append(f"{item.name}: existe nas duas pastas com conteúdo diferente")
    return juncao


def executar_juncao(juncao: Juncao) -> None:
    """Faz o planejado (só se não houver conflito). O manifesto é gravado antes de mover os
    arquivos, e a pasta solta só é removida se ficar vazia."""
    if juncao.conflitos:
        return
    if juncao.mover_pasta:
        juncao.nova.parent.mkdir(parents=True, exist_ok=True)
        juncao.antiga.rename(juncao.nova)
        return
    if juncao.manifesto is not None:
        captura.gravar_manifesto(juncao.nova, juncao.manifesto)
        (juncao.antiga / captura.NOME_MANIFESTO).unlink()
    for nome in juncao.mover:
        (juncao.antiga / nome).rename(juncao.nova / nome)
    for nome in juncao.repetidos + juncao.descartar:
        (juncao.antiga / nome).unlink()
    juncao.antiga.rmdir()


def reorganizar(historico: Path | None = None, mover: bool = False) -> list[Juncao]:
    """Leva cada dados/historico/AAAA-MM para dados/historico/AAAA/AAAA-MM: move a pasta, ou,
    se o destino já existe, junta as duas (ver `planejar_juncao`). Sem `mover`, só planeja.
    A pasta com conflito fica como está."""
    juncoes = [planejar_juncao(antiga, pasta_do_mes(antiga.name, historico)) for antiga in pastas_no_formato_antigo(historico)]
    if mover:
        for juncao in juncoes:
            executar_juncao(juncao)
    return juncoes


def descrever(juncao: Juncao, feito: bool) -> list[str]:
    verbo = "" if feito else " (simulação)"
    if juncao.conflitos:
        return [f"{juncao.antiga}: NÃO mexida, junte à mão:"] + [f"    {c}" for c in juncao.conflitos]
    if juncao.mover_pasta:
        return [f"{juncao.antiga} -> {juncao.nova}{verbo}"]
    partes = [f"{len(juncao.mover)} arquivo(s) movido(s)"]
    if juncao.repetidos:
        partes.append(f"{len(juncao.repetidos)} idêntico(s) já no destino (cópia solta apagada)")
    if juncao.manifesto is not None:
        partes.append(f"manifestos combinados ({len(juncao.manifesto)} cadernos)")
    if juncao.entradas_combinadas:
        partes.append(f"{juncao.entradas_combinadas} caderno(s) nos dois manifestos com o mesmo arquivo: mantido o "
                      "processamento e a data do primeiro download")
    if juncao.descartar:
        partes.append("simulação da limpeza descartada (rode o --limpar-cadernos --simular de novo)")
    return [f"{juncao.antiga} juntada a {juncao.nova}{verbo}: {'; '.join(partes)}"]


def rodar_mes(cfg: Configuracao, mes: Mes, executar_de_verdade: bool, limpar: bool = True) -> Mes:
    pasta = pasta_do_mes(mes.nome)
    log.info("===== %s (fonte %s) =====", mes.nome, mes.fonte)

    downloads = capturar(replace(cfg, pasta_cadernos=pasta), mes.inicio, mes.fim, False, mes.fonte)
    mes.cadernos_baixados = downloads[captura.StatusDownload.BAIXADO] + downloads[captura.StatusDownload.JA_BAIXADO]
    if downloads[captura.StatusDownload.FALHA] or downloads[captura.StatusDownload.SEM_CONEXAO]:
        mes.parada = "download com falha ou sem conexão (rode de novo; o manifesto não baixa o que já veio)"
        return mes
    if not mes.cadernos_baixados:
        mes.parada = "nenhum caderno no mês: confira a rede, o proxy e a fonte"
        return mes

    mes.resumo_separacao = relatorio_dos_cadernos(cfg, pasta, comparar=True)
    motivos = motivos_de_parada_na_separacao(mes.resumo_separacao)
    if motivos:
        mes.parada = "; ".join(motivos)
        return mes

    mes.resumo_gravacao = relatorio_dos_cadernos(cfg, pasta, gravar=True)
    if mes.resumo_gravacao.falhas:
        mes.parada = f"{mes.resumo_gravacao.falhas} decreto(s) não puderam ser gravados em pendentes"
        _esvaziar_pendentes(cfg)
        return mes

    simulacao = executar(cfg, simular=True)
    motivos = motivos_de_parada_no_lote(simulacao)
    if motivos:
        mes.parada = "; ".join(motivos)
        _esvaziar_pendentes(cfg)
        return mes
    if executar_de_verdade:
        mes.lote = executar(cfg)
        if mes.lote[Destino.FALHA_AO_GRAVAR] or mes.lote[Destino.ENVIADO_ERROS]:
            mes.parada = "o lote de verdade teve falha ao gravar ou mandou PDF para erros"
        elif limpar:
            mes.limpeza = limpeza_dos_cadernos.limpar(cfg, pasta, mes.inicio, exigir_simulacao=False)
            if mes.limpeza.falhas:
                mes.parada = f"{len(mes.limpeza.falhas)} caderno(s) não puderam ser apagados"
    else:
        mes.lote = simulacao
        _esvaziar_pendentes(cfg)  # só serviam à simulação
    return mes


def resumo_do_mes(mes: Mes, executado: bool = True) -> str:
    s = mes.resumo_gravacao or mes.resumo_separacao
    situacoes = s.situacoes if s else Counter()
    partes = [
        f"{mes.cadernos_baixados} caderno(s)",
        f"{s.atos if s else 0} decreto(s)",
        f"inéditos {situacoes['INÉDITO'] + situacoes['REPUBLICAÇÃO'] + situacoes['ANTERIOR À DA BASE']}",
        f"já existem {situacoes['já existe na DBF'] + situacoes['já registrado']}",
        f"conferir {situacoes['CONFERIR DUPLICATA']}",
        f"{'gravados na base' if executado else 'iriam para a base'} {mes.lote[Destino.CONCLUIDO]}",
    ]
    if mes.limpeza is not None:
        partes.append(f"cadernos apagados {len(mes.limpeza.apagados)}, guardados {len(mes.limpeza.mantidos)}")
    estado = f"PAROU: {mes.parada}" if mes.parada else "ok"
    return f"{mes.nome}: {', '.join(partes)} | {estado}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--de", type=_mes_iso, metavar="AAAA-MM", help="primeiro mês")
    parser.add_argument("--ate", type=_mes_iso, metavar="AAAA-MM", help="último mês, inclusive")
    parser.add_argument("--reorganizar", action="store_true",
                        help="move dados/historico/AAAA-MM para dados/historico/AAAA/AAAA-MM "
                        "(sem --executar, só mostra)")
    parser.add_argument("--executar", action="store_true",
                        help="grava na base da DBF (sem isto, cada mês só é baixado, separado e simulado)")
    parser.add_argument("--manter-cadernos", action="store_true",
                        help="com --executar: não apaga os cadernos processados no fim de cada mês")
    args = parser.parse_args(argv)
    if args.reorganizar:
        if args.de or args.ate or args.manter_cadernos:
            parser.error("--reorganizar só se combina com --executar")
        try:
            juncoes = reorganizar(mover=args.executar)
        except OSError as erro:
            print(f"Erro: {erro}", file=sys.stderr)
            return 2
        for juncao in juncoes:
            for linha in descrever(juncao, args.executar):
                print(linha, file=sys.stderr if juncao.conflitos else sys.stdout)
        if not juncoes:
            print(f"Nada a reorganizar em {PASTA_HISTORICO}.")
        elif not args.executar and any(not j.conflitos for j in juncoes):
            print("Simulação: rode de novo com --executar para mover.")
        return 1 if any(j.conflitos for j in juncoes) else 0
    if args.de is None or args.ate is None:
        parser.error("informe --de e --ate (ou use --reorganizar)")
    try:
        antigas = pastas_no_formato_antigo()
        if antigas:
            raise ErroDeUso(
                f"{len(antigas)} pasta(s) de mês no formato antigo em {PASTA_HISTORICO} (ex.: {antigas[0].name}); "
                "rode antes: python -m ferramentas.lote_historico --reorganizar --executar"
            )
        lista = meses(args.de, args.ate)
        cfg = carregar_configuracao()
        if cfg.pasta_dbf is None:
            raise ErroConfiguracao("PASTA_DBF não está definida no .env")
        _configurar_log(cfg.pasta_logs)
        sobra = listar_pdfs(cfg.pasta_temp)
        if sobra:
            raise ErroDeUso(
                f"{cfg.pasta_temp} tem {len(sobra)} PDF(s) de uma rodada anterior; rode o lote "
                "(python main.py) ou esvazie a pasta antes"
            )
    except (ErroDeUso, ErroConfiguracao) as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 2
    modo = "GRAVANDO NA BASE" if args.executar else "SIMULAÇÃO (nada vai para a base)"
    log.info("Lote histórico de %s a %s: %d mês(es), %s; base: %s",
             lista[0].strftime("%Y-%m"), lista[-1].strftime("%Y-%m"), len(lista), modo, cfg.pasta_dbf)
    feitos: list[Mes] = []
    try:
        for inicio in lista:
            mes = rodar_mes(cfg, Mes(inicio), args.executar, limpar=not args.manter_cadernos)
            feitos.append(mes)
            log.info("%s", resumo_do_mes(mes, args.executar))
            if mes.parada:
                break
    except (ErroRegistro, ErroConfiguracao, captura.ErroCaptura, limpeza_dos_cadernos.ErroLimpeza) as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrompido (Ctrl+C). O mês em andamento pode ter PDFs em dados/pendentes.", file=sys.stderr)
        return 130
    print("\nResumo do lote histórico:")
    for mes in feitos:
        print(f"  {resumo_do_mes(mes, args.executar)}")
    conferir = sum((m.resumo_gravacao.situacoes["CONFERIR DUPLICATA"] if m.resumo_gravacao else 0) for m in feitos)
    if conferir and args.executar:
        print(f"\n{conferir} decreto(s) foram para 'conferir duplicata': rode python main.py --conferir")
        if not args.manter_cadernos:
            print("Os cadernos deles ficaram guardados; depois do --conferir, apague-os com "
                  "python main.py --limpar-cadernos dados\\historico\\AAAA\\AAAA-MM --simular (e sem --simular).")
    if feitos and feitos[-1].parada:
        print(f"\nParou em {feitos[-1].nome}. Resolva e rode de novo a partir de --de {feitos[-1].nome}.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
