"""Etapa 1 (captura): downloads de cadernos, sempre sem acesso à internet."""

from __future__ import annotations

import http.server
import json
import logging
import threading
import urllib.error
import urllib.request
from datetime import date

import pymupdf
import pytest

import captura
from captura import (
    Caderno,
    ErroCaptura,
    StatusDownload,
    baixar_cadernos,
    criar_abridor,
    dias_do_periodo,
)
from config import Configuracao
from main import capturar, main

DIA = date(2026, 6, 27)
URL_EXECUTIVO = (
    "https://cepebr-prod.s3.amazonaws.com/1/cadernos/2026/20260627/"
    "1-PoderExecutivo/PoderExecutivo(20260627).pdf"
)
URL_EXTRA = (
    "https://cepebr-prod.s3.amazonaws.com/1/cadernos/2026/20260627/"
    "1-PoderExecutivoExtra/PoderExecutivoExtra(20260627).pdf"
)
URL_LEGISLATIVO = (
    "https://cepebr-prod.s3.amazonaws.com/1/cadernos/2026/20260627/"
    "6-PoderLegislativo/PoderLegislativo(20260627).pdf"
)


def pdf_valido(paginas: int = 2) -> bytes:
    doc = pymupdf.open()
    for i in range(paginas):
        doc.new_page().insert_text((72, 72), f"página {i + 1}")
    conteudo = doc.tobytes()
    doc.close()
    return conteudo


class RespostaFalsa:
    """Imita a resposta do urllib: contexto com .status, .headers e .read(n)."""

    def __init__(self, corpo: bytes, *, status=200, tamanho_anunciado="auto", entregar=None):
        self.status = status
        anunciado = len(corpo) if tamanho_anunciado == "auto" else tamanho_anunciado
        self.headers = {} if anunciado is None else {"Content-Length": str(anunciado)}
        self._corpo = corpo if entregar is None else corpo[:entregar]  # truncamento

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, n):
        bloco, self._corpo = self._corpo[:n], self._corpo[n:]
        return bloco


class ServidorFalso:
    """Abridor injetável: respostas (ou exceções) por URL, na ordem das chamadas."""

    def __init__(self, respostas: dict[str, list]):
        self.respostas = {url: list(itens) for url, itens in respostas.items()}
        self.chamadas: list[str] = []

    def __call__(self, url):
        self.chamadas.append(url)
        itens = self.respostas.get(url)
        if itens is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        item = itens.pop(0) if len(itens) > 1 else itens[0]
        if isinstance(item, Exception):
            raise item
        return item


def http_erro(url, codigo):
    return urllib.error.HTTPError(url, codigo, "erro", {}, None)


def baixar(pasta, servidor, dia=DIA, fim=None, **extras):
    opcoes = dict(abrir=servidor, esperar=lambda s: None, pausa=0)
    opcoes.update(extras)
    return baixar_cadernos(dia, fim or dia, pasta, **opcoes)


def status(resultados):
    return [r.status for r in resultados]


def arquivos(pasta):
    return sorted(p.name for p in pasta.iterdir())


# ----------------------------------------------------------------------------
# Endereços e nomes
# ----------------------------------------------------------------------------


def test_urls_dos_cadernos_seguem_o_padrao_do_s3():
    assert captura.PODER_EXECUTIVO.url(DIA) == URL_EXECUTIVO
    assert captura.PODER_EXECUTIVO_EXTRA.url(DIA) == URL_EXTRA
    assert captura.PODER_LEGISLATIVO.url(DIA) == URL_LEGISLATIVO


def test_nome_do_arquivo_vem_so_da_data_e_do_caderno():
    assert captura.PODER_EXECUTIVO.nome_arquivo(DIA) == "20260627_PoderExecutivo.pdf"
    assert (
        captura.PODER_EXECUTIVO_EXTRA.nome_arquivo(date(2026, 1, 5))
        == "20260105_PoderExecutivoExtra.pdf"
    )


def test_cadernos_ativos_incluem_executivo_extra_e_legislativo():
    assert [c.nome for c in captura.CADERNOS_ATIVOS] == [
        "PoderExecutivo",
        "PoderExecutivoExtra",
        "PoderLegislativo",
    ]


# ----------------------------------------------------------------------------
# Download
# ----------------------------------------------------------------------------


def test_baixa_grava_o_arquivo_e_o_manifesto(tmp_path):
    conteudo = pdf_valido()
    servidor = ServidorFalso({URL_EXECUTIVO: [RespostaFalsa(conteudo)]})
    resultados = baixar(tmp_path / "cadernos", servidor)

    assert status(resultados) == [StatusDownload.BAIXADO] + [StatusDownload.SEM_EDICAO] * 2
    salvo = tmp_path / "cadernos" / "20260627_PoderExecutivo.pdf"
    assert salvo.read_bytes() == conteudo
    assert resultados[0].caminho == salvo
    assert arquivos(tmp_path / "cadernos") == ["20260627_PoderExecutivo.pdf", "manifesto.json"]

    registro = json.loads((tmp_path / "cadernos" / "manifesto.json").read_text("utf-8"))
    entrada = registro["arquivos"]["20260627_PoderExecutivo.pdf"]
    assert entrada["bytes"] == len(conteudo)
    assert entrada["url"] == URL_EXECUTIVO
    assert len(entrada["sha256"]) == 64


def test_segunda_execucao_nao_baixa_de_novo(tmp_path):
    pasta = tmp_path / "cadernos"
    baixar(pasta, ServidorFalso({URL_EXECUTIVO: [RespostaFalsa(pdf_valido())]}))

    segundo = ServidorFalso({})
    resultados = baixar(pasta, segundo)
    assert resultados[0].status is StatusDownload.JA_BAIXADO
    assert URL_EXECUTIVO not in segundo.chamadas  # só consultou o Extra, ainda ausente


def test_caderno_ja_baixado_continua_registrado_mesmo_se_o_arquivo_for_movido(tmp_path):
    # Quando o passo de separação mover/apagar o caderno, não pode rebaixá-lo.
    pasta = tmp_path / "cadernos"
    baixar(pasta, ServidorFalso({URL_EXECUTIVO: [RespostaFalsa(pdf_valido())]}))
    (pasta / "20260627_PoderExecutivo.pdf").unlink()

    segundo = ServidorFalso({URL_EXECUTIVO: [RespostaFalsa(pdf_valido())]})
    resultados = baixar(pasta, segundo)
    assert resultados[0].status is StatusDownload.JA_BAIXADO
    assert URL_EXECUTIVO not in segundo.chamadas


@pytest.mark.parametrize("codigo", [403, 404])
def test_403_e_404_significam_sem_edicao(tmp_path, codigo):
    servidor = ServidorFalso(
        {
            url: [http_erro(url, codigo)]
            for url in (URL_EXECUTIVO, URL_EXTRA, URL_LEGISLATIVO)
        }
    )
    resultados = baixar(tmp_path / "cadernos", servidor)
    assert status(resultados) == [StatusDownload.SEM_EDICAO] * 3
    assert arquivos(tmp_path / "cadernos") == []  # nem manifesto: nada foi baixado
    assert len(servidor.chamadas) == 3  # sem repetição


def test_sem_edicao_nao_e_registrada_e_sera_consultada_de_novo(tmp_path):
    pasta = tmp_path / "cadernos"
    baixar(pasta, ServidorFalso({}))
    segundo = ServidorFalso({URL_EXECUTIVO: [RespostaFalsa(pdf_valido())]})
    assert baixar(pasta, segundo)[0].status is StatusDownload.BAIXADO


def test_download_truncado_e_repetido_e_depois_aceito(tmp_path):
    conteudo = pdf_valido()
    servidor = ServidorFalso(
        {URL_EXECUTIVO: [RespostaFalsa(conteudo, entregar=len(conteudo) // 2), RespostaFalsa(conteudo)]}
    )
    resultados = baixar(tmp_path / "cadernos", servidor)
    assert resultados[0].status is StatusDownload.BAIXADO
    assert (tmp_path / "cadernos" / "20260627_PoderExecutivo.pdf").read_bytes() == conteudo
    assert servidor.chamadas.count(URL_EXECUTIVO) == 2


def test_download_sempre_truncado_falha_sem_deixar_arquivo(tmp_path):
    conteudo = pdf_valido()
    servidor = ServidorFalso({URL_EXECUTIVO: [RespostaFalsa(conteudo, entregar=100)]})
    resultados = baixar(tmp_path / "cadernos", servidor, tentativas=3)

    assert resultados[0].status is StatusDownload.FALHA
    assert "incompleto" in resultados[0].detalhe
    assert servidor.chamadas.count(URL_EXECUTIVO) == 3
    assert arquivos(tmp_path / "cadernos") == []  # sem .part, sem final, sem manifesto


def test_resposta_sem_content_length_e_recusada(tmp_path):
    servidor = ServidorFalso({URL_EXECUTIVO: [RespostaFalsa(pdf_valido(), tamanho_anunciado=None)]})
    resultados = baixar(tmp_path / "cadernos", servidor)
    assert resultados[0].status is StatusDownload.FALHA
    assert "Content-Length" in resultados[0].detalhe
    assert servidor.chamadas.count(URL_EXECUTIVO) == 1
    assert arquivos(tmp_path / "cadernos") == []


def test_arquivo_maior_que_o_limite_e_recusado_sem_baixar(tmp_path):
    conteudo = pdf_valido()
    servidor = ServidorFalso({URL_EXECUTIVO: [RespostaFalsa(conteudo)]})
    resultados = baixar(tmp_path / "cadernos", servidor, max_bytes=len(conteudo) - 1)
    assert resultados[0].status is StatusDownload.FALHA
    assert "limite" in resultados[0].detalhe
    assert arquivos(tmp_path / "cadernos") == []


def test_servidor_que_envia_mais_que_o_anunciado_e_cortado_no_limite(tmp_path):
    conteudo = pdf_valido()
    resposta = RespostaFalsa(conteudo + b"0" * 5000, tamanho_anunciado=len(conteudo))
    servidor = ServidorFalso({URL_EXECUTIVO: [resposta]})
    resultados = baixar(tmp_path / "cadernos", servidor, max_bytes=len(conteudo) + 10)
    assert resultados[0].status is StatusDownload.FALHA
    assert arquivos(tmp_path / "cadernos") == []


def test_html_no_lugar_do_pdf_e_recusado(tmp_path):
    html = b"<html><body>Access denied</body></html>"
    servidor = ServidorFalso({URL_EXECUTIVO: [RespostaFalsa(html)]})
    resultados = baixar(tmp_path / "cadernos", servidor)
    assert resultados[0].status is StatusDownload.FALHA
    assert "não é um PDF" in resultados[0].detalhe
    assert arquivos(tmp_path / "cadernos") == []


def test_pdf_com_cabecalho_mas_conteudo_quebrado_e_recusado(tmp_path):
    lixo = b"%PDF-1.7\n" + b"nada de pdf aqui\n" * 20
    servidor = ServidorFalso({URL_EXECUTIVO: [RespostaFalsa(lixo)]})
    resultados = baixar(tmp_path / "cadernos", servidor)
    assert resultados[0].status is StatusDownload.FALHA
    assert arquivos(tmp_path / "cadernos") == []


def test_erro_500_e_repetido_e_depois_aceito(tmp_path):
    servidor = ServidorFalso(
        {URL_EXECUTIVO: [http_erro(URL_EXECUTIVO, 503), RespostaFalsa(pdf_valido())]}
    )
    esperas: list[float] = []
    resultados = baixar(tmp_path / "cadernos", servidor, esperar=esperas.append)
    assert resultados[0].status is StatusDownload.BAIXADO
    assert captura.ESPERA_ENTRE_TENTATIVAS in esperas


def test_erro_500_persistente_falha_depois_das_tentativas(tmp_path):
    servidor = ServidorFalso({URL_EXECUTIVO: [http_erro(URL_EXECUTIVO, 500)]})
    resultados = baixar(tmp_path / "cadernos", servidor, tentativas=2)
    assert resultados[0].status is StatusDownload.FALHA
    assert servidor.chamadas.count(URL_EXECUTIVO) == 2


def test_outro_erro_4xx_nao_e_repetido(tmp_path):
    servidor = ServidorFalso({URL_EXECUTIVO: [http_erro(URL_EXECUTIVO, 429)]})
    resultados = baixar(tmp_path / "cadernos", servidor)
    assert resultados[0].status is StatusDownload.FALHA
    assert servidor.chamadas.count(URL_EXECUTIVO) == 1


def test_sem_conexao_e_repetida_so_uma_vez_e_depois_reportada(tmp_path):
    servidor = ServidorFalso({URL_EXECUTIVO: [urllib.error.URLError(TimeoutError("sem rede"))]})
    resultados = baixar(tmp_path / "cadernos", servidor, tentativas=3)
    assert resultados[0].status is StatusDownload.SEM_CONEXAO
    assert "TimeoutError" in resultados[0].detalhe
    assert servidor.chamadas.count(URL_EXECUTIVO) == captura.TENTATIVAS_SEM_CONEXAO == 2


def test_queda_no_meio_do_download_e_falha_comum_e_repetida(tmp_path):
    servidor = ServidorFalso({URL_EXECUTIVO: [ConnectionResetError("caiu")]})
    resultados = baixar(tmp_path / "cadernos", servidor, tentativas=3)
    assert resultados[0].status is StatusDownload.FALHA
    assert servidor.chamadas.count(URL_EXECUTIVO) == 3


def test_desiste_do_periodo_apos_cadernos_seguidos_sem_conexao(tmp_path, caplog):
    sem_rede = urllib.error.URLError(TimeoutError("timed out"))
    servidor = ServidorFalso({})
    for dia in ("20260627", "20260628", "20260629"):
        for pasta in ("1-PoderExecutivo/PoderExecutivo", "1-PoderExecutivoExtra/PoderExecutivoExtra", "6-PoderLegislativo/PoderLegislativo"):
            url = f"https://cepebr-prod.s3.amazonaws.com/1/cadernos/2026/{dia}/{pasta}({dia}).pdf"
            servidor.respostas[url] = [sem_rede]
    with caplog.at_level(logging.ERROR, logger="pipeline"):
        resultados = baixar(tmp_path / "cadernos", servidor, dia=DIA, fim=date(2026, 6, 29))

    assert status(resultados) == [StatusDownload.SEM_CONEXAO] * captura.LIMITE_SEM_CONEXAO
    assert len(servidor.chamadas) == captura.LIMITE_SEM_CONEXAO * captura.TENTATIVAS_SEM_CONEXAO
    assert "restante do período foi cancelado" in caplog.text
    assert "HTTPS_PROXY" in caplog.text


def test_resposta_do_servidor_zera_a_contagem_de_falta_de_conexao(tmp_path):
    # 404 prova que há conexão: não deve acumular com os timeouts vizinhos.
    sem_rede = urllib.error.URLError(TimeoutError("timed out"))
    servidor = ServidorFalso(
        {
            URL_EXECUTIVO: [sem_rede],
            URL_EXTRA: [http_erro(URL_EXTRA, 404)],
            URL_LEGISLATIVO: [sem_rede],
        }
    )
    resultados = baixar(tmp_path / "cadernos", servidor)
    assert status(resultados) == [
        StatusDownload.SEM_CONEXAO,
        StatusDownload.SEM_EDICAO,
        StatusDownload.SEM_CONEXAO,
    ]


def test_ctrl_c_remove_o_arquivo_parcial_e_repassa_a_interrupcao(tmp_path):
    class RespostaInterrompida(RespostaFalsa):
        def read(self, n):
            raise KeyboardInterrupt

    pasta = tmp_path / "cadernos"
    servidor = ServidorFalso({URL_EXECUTIVO: [RespostaInterrompida(pdf_valido())]})
    with pytest.raises(KeyboardInterrupt):
        baixar(pasta, servidor)
    assert arquivos(pasta) == []  # sem .part


def test_falha_de_um_caderno_nao_interrompe_os_demais(tmp_path):
    servidor = ServidorFalso(
        {
            URL_EXECUTIVO: [http_erro(URL_EXECUTIVO, 500)],
            URL_EXTRA: [RespostaFalsa(pdf_valido())],
        }
    )
    resultados = baixar(tmp_path / "cadernos", servidor, tentativas=1)
    assert status(resultados) == [
        StatusDownload.FALHA,
        StatusDownload.BAIXADO,
        StatusDownload.SEM_EDICAO,
    ]


def test_pausa_entre_requisicoes_so_quando_ha_rede(tmp_path):
    pausas: list[float] = []
    servidor = ServidorFalso({})
    baixar_cadernos(
        DIA, date(2026, 6, 28), tmp_path / "cadernos",
        abrir=servidor, esperar=pausas.append, pausa=0.5,
    )
    assert len(servidor.chamadas) == 6
    assert pausas == [0.5] * 5  # entre as 6 requisições, não antes da primeira


# ----------------------------------------------------------------------------
# Simulação, período e segurança
# ----------------------------------------------------------------------------


def test_simulacao_nao_usa_rede_nem_cria_pasta(tmp_path):
    def proibido(url):
        raise AssertionError("simulação não pode acessar a rede")

    pasta = tmp_path / "cadernos"
    resultados = baixar_cadernos(DIA, DIA, pasta, simular=True, abrir=proibido)
    assert status(resultados) == [StatusDownload.SIMULADO] * 3
    assert resultados[0].detalhe == URL_EXECUTIVO
    assert not pasta.exists()


def test_simulacao_respeita_o_manifesto(tmp_path):
    pasta = tmp_path / "cadernos"
    baixar(pasta, ServidorFalso({URL_EXECUTIVO: [RespostaFalsa(pdf_valido())]}))
    resultados = baixar_cadernos(DIA, DIA, pasta, simular=True)
    assert status(resultados) == [StatusDownload.JA_BAIXADO] + [StatusDownload.SIMULADO] * 2


def test_periodo_percorre_todos_os_dias_inclusive_o_ultimo():
    assert dias_do_periodo(date(2026, 6, 29), date(2026, 7, 2)) == [
        date(2026, 6, 29), date(2026, 6, 30), date(2026, 7, 1), date(2026, 7, 2),
    ]


def test_periodo_invertido_e_recusado():
    with pytest.raises(ErroCaptura, match="invertido"):
        dias_do_periodo(date(2026, 7, 2), date(2026, 7, 1))


def test_periodo_longo_demais_e_recusado():
    with pytest.raises(ErroCaptura, match="excede"):
        dias_do_periodo(date(2025, 1, 1), date(2026, 6, 1))
    assert len(dias_do_periodo(date(2026, 1, 1), date(2026, 12, 31))) == 365


@pytest.mark.parametrize(
    "url",
    [
        "http://cepebr-prod.s3.amazonaws.com/x.pdf",  # sem https
        "https://outro-site.example.com/x.pdf",  # host fora da lista
        "https://cepebr-prod.s3.amazonaws.com.evil.example/x.pdf",  # host parecido
        "file:///etc/passwd",
    ],
)
def test_url_fora_da_lista_permitida_e_recusada(tmp_path, url):
    servidor = ServidorFalso({})
    caderno = Caderno("Qualquer", url)
    with pytest.raises(ErroCaptura):
        baixar_cadernos(DIA, DIA, tmp_path / "cadernos", cadernos=(caderno,), abrir=servidor)
    assert servidor.chamadas == []


def test_manifesto_ilegivel_e_guardado_e_recriado(tmp_path, caplog):
    pasta = tmp_path / "cadernos"
    pasta.mkdir()
    (pasta / "manifesto.json").write_text("{ isto não é json", encoding="utf-8")
    servidor = ServidorFalso({URL_EXECUTIVO: [RespostaFalsa(pdf_valido())]})
    with caplog.at_level(logging.WARNING, logger="pipeline"):
        resultados = baixar(pasta, servidor)

    assert resultados[0].status is StatusDownload.BAIXADO
    assert (pasta / "manifesto.json.invalido").exists()
    assert "20260627_PoderExecutivo.pdf" in json.loads(
        (pasta / "manifesto.json").read_text("utf-8")
    )["arquivos"]
    assert "Manifesto ilegível" in caplog.text


def test_avisa_quando_nada_e_encontrado_em_varios_dias(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="pipeline"):
        baixar(tmp_path / "cadernos", ServidorFalso({}), dia=DIA, fim=date(2026, 6, 29))
    assert "Confira o padrão da URL" in caplog.text


def test_nao_avisa_quando_ha_ao_menos_um_caderno(tmp_path, caplog):
    servidor = ServidorFalso({URL_EXECUTIVO: [RespostaFalsa(pdf_valido())]})
    with caplog.at_level(logging.WARNING, logger="pipeline"):
        baixar(tmp_path / "cadernos", servidor, dia=DIA, fim=date(2026, 6, 29))
    assert "Confira o padrão da URL" not in caplog.text


# ----------------------------------------------------------------------------
# Abridor real (urllib), contra um servidor HTTP local
# ----------------------------------------------------------------------------


@pytest.fixture
def servidor_http():
    conteudo = pdf_valido()

    class Manipulador(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/ok.pdf":
                self.send_response(200)
                self.send_header("Content-Length", str(len(conteudo)))
                self.end_headers()
                self.wfile.write(conteudo)
            elif self.path == "/redireciona":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/destino")
                self.end_headers()
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

        def log_message(self, *args):
            pass

    servidor = http.server.HTTPServer(("127.0.0.1", 0), Manipulador)
    fio = threading.Thread(target=servidor.serve_forever, daemon=True)
    fio.start()
    yield f"http://127.0.0.1:{servidor.server_port}", conteudo
    servidor.shutdown()
    servidor.server_close()


def test_abridor_real_le_status_cabecalho_e_corpo(servidor_http):
    base, conteudo = servidor_http
    with criar_abridor(timeout=5)(base + "/ok.pdf") as resposta:
        assert resposta.status == 200
        assert int(resposta.headers["Content-Length"]) == len(conteudo)
        assert resposta.read(10_000_000) == conteudo


def test_abridor_real_levanta_httperror_no_404(servidor_http):
    base, _ = servidor_http
    with pytest.raises(urllib.error.HTTPError) as erro:
        criar_abridor(timeout=5)(base + "/nao_existe")
    assert erro.value.code == 404


def test_abridor_real_recusa_redirecionamento(servidor_http):
    base, _ = servidor_http
    with pytest.raises(urllib.error.HTTPError) as erro:
        criar_abridor(timeout=5)(base + "/redireciona")
    assert erro.value.code == 302
    assert "recusado" in str(erro.value.reason)


# ----------------------------------------------------------------------------
# Integração com main.py
# ----------------------------------------------------------------------------


@pytest.fixture
def cfg(tmp_path):
    return Configuracao(
        pasta_temp=tmp_path / "pendentes",
        pasta_cadernos=tmp_path / "cadernos",
        pasta_erros=tmp_path / "erros",
        pasta_ocr=tmp_path / "leitura tesseract",
        pasta_normalizados=tmp_path / "normalizados",
        pasta_logs=tmp_path / "logs",
        pasta_dbf=None,
    )


def test_capturar_em_simulacao_conta_o_que_seria_baixado(cfg):
    resumo = capturar(cfg, DIA, date(2026, 6, 28), simular=True)
    assert resumo == {StatusDownload.SIMULADO: 6}
    assert not cfg.pasta_cadernos.exists()


def test_capturar_nao_mexe_nas_pastas_do_lote(cfg, monkeypatch):
    servidor = ServidorFalso({URL_EXECUTIVO: [RespostaFalsa(pdf_valido())]})
    monkeypatch.setattr(captura, "criar_abridor", lambda *a, **k: servidor)
    monkeypatch.setattr(captura.time, "sleep", lambda s: None)
    resumo = capturar(cfg, DIA, DIA)
    assert resumo[StatusDownload.BAIXADO] == 1
    assert not cfg.pasta_temp.exists() and not cfg.pasta_erros.exists()


def test_cli_baixar_exige_de(capsys):
    with pytest.raises(SystemExit) as saida:
        main(["--baixar"])
    assert saida.value.code == 2
    assert "--de" in capsys.readouterr().err


def test_cli_de_sem_baixar_e_recusado(capsys):
    with pytest.raises(SystemExit) as saida:
        main(["--de", "2026-06-27"])
    assert saida.value.code == 2


def test_cli_data_invalida_e_recusada(capsys):
    with pytest.raises(SystemExit) as saida:
        main(["--baixar", "--de", "27/06/2026"])
    assert saida.value.code == 2
    assert "AAAA-MM-DD" in capsys.readouterr().err


def test_cli_periodo_invertido_sai_com_erro_2(cfg, monkeypatch, capsys):
    import main as modulo_main

    monkeypatch.setattr(modulo_main, "carregar_configuracao", lambda: cfg)
    assert main(["--baixar", "--de", "2026-07-02", "--ate", "2026-07-01", "--simular"]) == 2
    assert "invertido" in capsys.readouterr().err


def test_remover_tenta_de_novo_apos_coleta_quando_o_windows_segura_o_arquivo(
    tmp_path, monkeypatch
):
    parcial = tmp_path / "x.pdf.part"
    parcial.write_bytes(b"x")
    original = type(parcial).unlink
    chamadas = []

    def unlink_com_trava(self, missing_ok=False):
        chamadas.append(1)
        if len(chamadas) == 1:
            raise PermissionError(32, "O arquivo já está sendo usado por outro processo")
        return original(self, missing_ok=missing_ok)

    monkeypatch.setattr(type(parcial), "unlink", unlink_com_trava)
    captura._remover(parcial)
    assert not parcial.exists() and len(chamadas) == 2


def test_remover_nao_levanta_erro_se_o_arquivo_continua_preso(tmp_path, monkeypatch, caplog):
    parcial = tmp_path / "x.pdf.part"
    parcial.write_bytes(b"x")

    def sempre_preso(self, missing_ok=False):
        raise PermissionError(32, "preso")

    monkeypatch.setattr(type(parcial), "unlink", sempre_preso)
    with caplog.at_level(logging.WARNING, logger="pipeline"):
        captura._remover(parcial)  # não pode levantar
    assert "não foi possível apagar" in caplog.text


def test_cli_sai_com_codigo_1_quando_ha_falta_de_conexao(cfg, monkeypatch):
    import main as modulo_main

    sem_rede = urllib.error.URLError(TimeoutError("timed out"))

    def abridor_sem_rede(url):
        raise sem_rede

    monkeypatch.setattr(modulo_main, "carregar_configuracao", lambda: cfg)
    monkeypatch.setattr(captura, "criar_abridor", lambda *a, **k: abridor_sem_rede)
    monkeypatch.setattr(captura.time, "sleep", lambda s: None)
    assert main(["--baixar", "--de", "2026-06-27", "--ate", "2026-06-27"]) == 1


def test_cli_ctrl_c_sai_com_codigo_130_sem_traceback(cfg, monkeypatch, capsys):
    import main as modulo_main

    def interrompe(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(modulo_main, "carregar_configuracao", lambda: cfg)
    monkeypatch.setattr(captura, "baixar_cadernos", interrompe)
    assert main(["--baixar", "--de", "2026-06-27"]) == 130
    assert "Interrompido" in capsys.readouterr().err


# ----------------------------------------------------------------------------
# Proxy
# ----------------------------------------------------------------------------


def test_abridor_com_proxy_envia_o_pedido_ao_proxy(monkeypatch):
    # O "proxy" é um servidor local que recebe o pedido com a URL completa
    # (como um proxy HTTP de verdade) e devolve um PDF.
    conteudo = pdf_valido()
    recebidos: list[str] = []

    class FalsoProxy(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            recebidos.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", str(len(conteudo)))
            self.end_headers()
            self.wfile.write(conteudo)

        def log_message(self, *args):
            pass

    servidor = http.server.HTTPServer(("127.0.0.1", 0), FalsoProxy)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    for variavel in ("NO_PROXY", "no_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(variavel, raising=False)
    try:
        abridor = criar_abridor(timeout=5, proxy=f"http://127.0.0.1:{servidor.server_port}")
        with abridor("http://destino.invalid/arquivo.pdf") as resposta:
            assert resposta.read(10_000_000) == conteudo
    finally:
        servidor.shutdown()
        servidor.server_close()
    assert recebidos == ["http://destino.invalid/arquivo.pdf"]


def test_capturar_repassa_o_proxy_da_configuracao(cfg, monkeypatch):
    recebido = {}

    def falso_criar_abridor(*args, **kwargs):
        recebido.update(kwargs)
        return ServidorFalso({})

    monkeypatch.setattr(captura, "criar_abridor", falso_criar_abridor)
    monkeypatch.setattr(captura.time, "sleep", lambda s: None)
    com_proxy = Configuracao(**{**cfg.__dict__, "proxy_https": "http://127.0.0.1:9000"})
    capturar(com_proxy, DIA, DIA)
    assert recebido["proxy"] == "http://127.0.0.1:9000"
    assert recebido["ssl_estrito"] is True


def test_contexto_ssl_estrito_por_padrao_e_flexivel_so_se_pedido():
    import ssl

    estrito = captura._contexto_ssl(True)
    flexivel = captura._contexto_ssl(False)
    # A verificação do certificado e do nome do servidor nunca é desligada.
    for contexto in (estrito, flexivel):
        assert contexto.verify_mode is ssl.CERT_REQUIRED
        assert contexto.check_hostname is True
    assert not flexivel.verify_flags & ssl.VERIFY_X509_STRICT
    assert estrito.verify_flags == ssl.create_default_context().verify_flags


def test_opener_usa_o_contexto_ssl_pedido():
    import ssl

    opener = captura._criar_opener(None, ssl_estrito=False)
    manipulador = next(
        h for h in opener.handlers if isinstance(h, urllib.request.HTTPSHandler)
    )
    assert not manipulador._context.verify_flags & ssl.VERIFY_X509_STRICT


def test_capturar_repassa_ssl_estrito_desligado(cfg, monkeypatch):
    recebido = {}

    def falso_criar_abridor(*args, **kwargs):
        recebido.update(kwargs)
        return ServidorFalso({})

    monkeypatch.setattr(captura, "criar_abridor", falso_criar_abridor)
    monkeypatch.setattr(captura.time, "sleep", lambda s: None)
    capturar(Configuracao(**{**cfg.__dict__, "ssl_estrito": False}), DIA, DIA)
    assert recebido["ssl_estrito"] is False