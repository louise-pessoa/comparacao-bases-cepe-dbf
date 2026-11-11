# Comparação de bases de Atos Normativos da CEPE e da DBF

Sincroniza a base local de atos dos programas de estímulos fiscais da Diretoria de Benefícios Fiscais (DBF) com os atos
publicados pela CEPE no Diário Oficial de Pernambuco:

- baixa os cadernos do Diário e separa os decretos;
- descarta o que a DBF já tem e reconhece republicações (o mesmo número publicado de novo, em outra data);
- recria cada decreto inédito em um PDF limpo e linear, mais fácil de ler por IA ("Normalização na Fonte");
- cataloga, só para leitura, decretos, editais, portarias, erratas e republicações.

Análise semântica dos atos **não** faz parte do projeto.

## Etapas

| # | Etapa | Situação |
|---|-------|----------|
| 1 | Captura dos cadernos da CEPE | implementada |
| 1b | Separação dos decretos do caderno e gravação dos inéditos em `dados/pendentes` | implementada |
| 2 | Identificação (PyMuPDF + RegEx) | implementada |
| 3 | Validação de duplicidade e republicação contra a base da DBF | implementada |
| 4 | Normalização: PDF limpo com o layout da DBF (fpdf2) | implementada |
| 5 | Gravação na pasta da DBF | implementada |
| – | Catálogo dos atos (CSV, só leitura) | implementada; edital e portaria só reconhecidos, fora do pipeline |

O original só é descartado depois de o resultado ser gravado e conferido na base.

Republicação: a publicação mais antiga de um número fica com `Decreto N.pdf`; as seguintes, com `Decreto N
(republicação).pdf`, `(republicação 2)` e assim por diante. A data de cada arquivo gravado fica em
`dados/registro_publicacoes.json`. Detalhes em [`docs/funcionamento.md`](docs/funcionamento.md#republicação).

Anos antigos: o pipeline lê corretamente os cadernos de **2020 em diante**. Antes disso há problemas de leitura
conhecidos ([`docs/funcionamento.md`](docs/funcionamento.md#cadernos-antigos-ano-limite)).

## Instalação

Python 3.11 ou mais novo.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
cp .env.example .env                 # Windows: copy .env.example .env
```

No `.env`, informe `PASTA_DBF` (a pasta da base oficial da DBF). Proxy, certificado e WSL2: veja [`docs/uso.md`](docs/uso.md).

Opcional: `pip install -e .` (sempre em modo editável, na pasta do repositório) dá o comando `dbf-cepe`, que é o mesmo que
`python main.py`.

## Uso

```bash
python main.py --baixar --de 2026-10-01 --ate 2026-10-20   # etapa 1: baixa os cadernos para dados/cadernos
python main.py --separar dados/cadernos                     # etapa 1b: lista os decretos e se a DBF já os tem (só lê)
python main.py --separar dados/cadernos --comparar          # compara o texto com o PDF da base (só lê)
python main.py --separar dados/cadernos --gravar            # grava os inéditos e as republicações em dados/pendentes
python main.py --simular                                     # lote (etapas 2 a 5): mostra o que faria
python main.py                                               # lote de verdade: grava na base da DBF
python main.py --conferir [--simular]                       # revisa a pasta "conferir duplicata" pelo terminal
python main.py --catalogar dados/cadernos [--com-base]      # CSV de todos os atos (só lê)
```

Rode sempre `--simular` antes do lote: o descarte de PDFs é definitivo. Rode os lotes em **ordem cronológica** (dos
cadernos mais antigos para os mais novos). O registro de cada decisão fica em
`logs/pipeline.log`. Opções, mensagens e casos de erro de cada modo: [`docs/uso.md`](docs/uso.md).

### Lote histórico (meses anteriores a 2026)

Um mês por vez, em ordem cronológica, cada mês na própria pasta e **sempre com `--fonte s3`**: o site atual da CEPE
(a fonte padrão) só foi conferido para 2026, e o S3 tem os cadernos de 2005 a 2025. Exemplo, fevereiro de 2020
(PowerShell):

```powershell
$mes = "2020-02"; $de = "2020-02-01"; $ate = "2020-02-29"
$pasta = "dados\historico\$mes"
$env:PASTA_CADERNOS = $pasta                                 # baixa na pasta do mês, não em dados\cadernos
python main.py --baixar --fonte s3 --de $de --ate $ate
Remove-Item Env:PASTA_CADERNOS
python main.py --separar $pasta --comparar                   # lista e compara com a base (só lê)
python main.py --separar $pasta --gravar
python main.py --simular
python main.py
python main.py --conferir                                    # decide os que foram para "conferir duplicata"
```

Passo a passo, o que conferir em cada etapa e o ano-limite (2020): [`docs/uso.md`](docs/uso.md#lote-histórico).

## Testes

```bash
pytest
ruff check .
```

Parte dos testes usa decretos e trechos de cadernos reais, já publicados (`tests/amostras*`). Se faltar uma amostra, os
testes reprovam. O CI (GitHub Actions) roda o ruff e a suíte em Python 3.11 e 3.14 a cada push na `main` e a cada pull
request.

## Estrutura

```
main.py              atalho: python main.py ... roda a linha de comando sem instalar
src/dbf_cepe/        o pacote: cli, config, proxy, captura, catalogo
  regras/            RegEx dos atos (regras_atos.py, fonte única das regras), espécies e programas da DBF
  leitura/           leitura do PDF, separação dos atos do caderno e identificação
  pipeline/          duplicidade, normalização, gravação, comparação e os modos lote e relatório
  recursos/fontes/   fontes Liberation (SIL OFL)
ferramentas/         fora do pipeline: levantamento de exemplos e regressão do separador
docs/                documentação
tests/               testes automatizados
```

## Documentação

- [`docs/uso.md`](docs/uso.md): como rodar cada modo, o que ele mostra, e problemas de rede e de base.
- [`docs/funcionamento.md`](docs/funcionamento.md): como funciona por dentro, decisões e limitações.
- [`docs/roteiro_de_testes.md`](docs/roteiro_de_testes.md): o que conferir antes de aceitar uma mudança.

## Como ler o código

Cada módulo Python começa com quatro blocos: o que faz, em que etapa entra, o que
recebe e devolve, e quais decisões ou limites mantém. Funções e classes públicas
explicam somente argumentos não óbvios, retornos, exceções e efeitos em disco.
Assim, a corrente principal pode ser lida de fora para dentro:

1. [`main.py`](main.py) encaminha a CLI; [`src/dbf_cepe/cli.py`](src/dbf_cepe/cli.py)
   escolhe captura, separação, lote ou catálogo.
2. [`src/dbf_cepe/regras/especies.py`](src/dbf_cepe/regras/especies.py) e
   [`src/dbf_cepe/regras/programas.py`](src/dbf_cepe/regras/programas.py) concentram
   as regras de domínio, com evidência `amostra`, `publicado` ou `presumido`.
3. [`src/dbf_cepe/leitura/`](src/dbf_cepe/leitura/) separa, lê e identifica; a
   comparação e a duplicidade só confirmam o que já existe, sem análise semântica.
4. [`src/dbf_cepe/pipeline/normalizador.py`](src/dbf_cepe/pipeline/normalizador.py)
   reconstrói o PDF e [`src/dbf_cepe/pipeline/armazenamento.py`](src/dbf_cepe/pipeline/armazenamento.py)
   só apaga os temporários depois da cópia, SHA-256 e releitura confirmadas.

Os comentários de linha ficam reservados a decisões que não são evidentes no
código, como tolerâncias de layout, ligaduras do Diário e o motivo de
`SSL_ESTRITO=nao`. As ferramentas em [`ferramentas/`](ferramentas/) investigam ou
fazem regressão; não são etapas do pipeline. A suíte em [`tests/`](tests/) protege
as regras de negócio, os limites de segurança e as transições entre etapas.
