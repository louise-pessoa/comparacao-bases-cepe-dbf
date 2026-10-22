"""Etapa 4 — normalização e geração do PDF limpo com fpdf2. NÃO IMPLEMENTADA.

Contrato: recebe o PDF original e a identificação (etapa 2) e devolve o caminho
de um PDF novo, de layout limpo e linear, gerado em `pasta_saida` (pasta LOCAL,
nunca a da DBF; quem grava na DBF é a etapa 5).

Decisões já tomadas (ver README):
  - fpdf2 (`from fpdf import FPDF`) com fonte TTF Unicode, para acentos;
  - sem assinaturas nem logomarcas no PDF gerado;
  - o modelo de cada tipo de ato é o decreto indicado em `tipo.modelo`
    (especies.py); a segunda prorrogação/renovação usa o modelo da prorrogação
    ou da renovação, conforme o caso.

Pontos de atenção para a implementação:
  - o texto justificado chega com palavras em linhas separadas ("de \\njulho");
    reconstruir parágrafos a partir dos blocos (`get_text("blocks")`) em vez
    de usar o texto linha a linha;
  - decretos longos (ex.: 60951, com um parágrafo enorme de NCMs) exigem
    quebra de página automática;
  - descartar numeração de página e rodapés como "Este texto não substitui o
    publicado no DOE" apenas se isso não alterar o conteúdo do ato;
  - validar o resultado (número do ato e quantidade de artigos conferem com o
    original) antes de entregá-lo à etapa 5.
"""

from __future__ import annotations

from pathlib import Path

from identificador import Identificacao


##################################################################################

def gerar_pdf_normalizado(
    original: Path, ident: Identificacao, pasta_saida: Path
) -> Path:
    raise NotImplementedError("Etapa 4 (normalização com fpdf2) ainda não implementada.")
