# comparacao-bases-CEPE-DBF

Normalização na Fonte dos decretos da CEPE (DBF/SEFAZ). Sincroniza a base local de documentos da Diretoria de Benefícios Fiscais (DBF)
com os decretos publicados no Diário Oficial pela CEPE. Além de evitar arquivos
duplicados, recria cada decreto inédito em um PDF de layout limpo e linear
("Normalização na Fonte"), mais adequado à leitura por IA.

A análise semântica dos atos **não** faz parte deste projeto: será feita por
uma IA homologada pela SEFAZ.

## Etapas e situação

| # | Etapa | Arquivo | Situação |
|---|-------|---------|----------|
| 1 | Captura (web scraping da CEPE) | `captura.py` | esqueleto |
| 2 | Identificação (PyMuPDF + RegEx) | `reader.py`, `identificador.py`, `especies.py` | **implementada** |
| 3 | Validação de duplicidade | `duplicidade.py` | **implementada** |
| 4 | Normalização e geração do PDF (fpdf2) | `normalizador.py` | esqueleto |
| 5 | Armazenamento na pasta da DBF | `armazenamento.py` | parcial (mover/descartar prontos; `armazenar` pendente) |

Enquanto as etapas 4 e 5 não existem, um decreto inédito **permanece na pasta
temporária**: nada é descartado antes de o resultado ser gravado na DBF.

### O que a etapa 2 decide

| Situação do PDF | Destino |
|---|---|
| Não abre, está vazio, sem páginas ou com senha | `dados/erros` |
| Digitalizado (total ou parcialmente) | `dados/leitura tesseract`, com o nome original |
| Começa por Lei, Portaria, Edital, Errata... | descartado |
| Sem cabeçalho reconhecível, ou com cabeçalhos de mais de um ato | `dados/erros` (revisão manual) |
| Decreto | segue para a etapa 3 |

Na etapa 3, o arquivo é comparado, pela nomenclatura `Decreto {numero}.pdf`
(número sem separador de milhar), com a pasta da DBF. Se já existir, o PDF
temporário é descartado. Decretos cujo tipo não é reconhecido (concessão,
alteração, prorrogação, renovação, segunda prorrogação/renovação,
transferência, revogação, terceirização) vão para `dados/erros`.

## Instalação

Requer Python 3.10 ou superior (exigência dos pacotes fixados; testado apenas
com 3.13). No WSL2, mantenha o
repositório no sistema de arquivos do Linux (por exemplo, `~/projetos`) e não
em `/mnt/c`.

Linux / WSL2:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env
```

Edite o `.env` e informe `PASTA_DBF` (a pasta da base oficial da DBF). No
WSL2, o drive precisa estar montado: `sudo mount -t drvfs G: /mnt/g`.

As versões das dependências diretas ficam fixas em `requirements*.txt`, para a
instalação ser reproduzível (as indiretas não são fixadas). Com o repositório
no GitHub, o Dependabot (`.github/dependabot.yml`) propõe atualizações
semanais por pull request.

## Uso

Até a etapa 1 ser implementada, coloque os PDFs em `dados/pendentes/` e rode:

```bash
python main.py --simular   # mostra o que seria feito; nada é movido ou apagado
python main.py             # executa de verdade
```

Rode sempre `--simular` primeiro: o descarte de PDFs é definitivo (sem lixeira).
O registro de cada decisão fica em `logs/pipeline.log`.

A execução é abortada, sem tocar em nenhum arquivo, se a pasta da DBF não
existir, não puder ser lida ou estiver vazia (por exemplo, drive não montado
ou ainda sem sincronizar). Para uma base realmente vazia, use
`--permitir-pasta-vazia`.

Também é abortada se a pasta da DBF e as pastas de trabalho não forem todas
diferentes ou se uma ficar dentro de outra. Exemplo: com `PASTA_TEMP` igual à
`PASTA_DBF`, o script trataria cada decreto da base como duplicado e o apagaria.

## Testes

```bash
pytest
```

Parte dos testes usa os 6 decretos reais de `tests/amostras/`, já publicados
pela CEPE e por isso versionados. Se alguma amostra faltar, os testes
**reprovam** (não são pulados). O resto da suíte usa PDFs sintéticos criados
pelos próprios testes.

A cada push na `main` e a cada pull request, o GitHub Actions
(`.github/workflows/testes.yml`) roda a suíte em Python 3.10 e 3.13. Não há
deploy.

## Estrutura

```
main.py            orquestra o lote (etapas 2 a 5)
config.py          pastas e variáveis do .env
especies.py        regras por espécie: cabeçalho, nome de arquivo, tipos
reader.py          etapa 2: leitura e detecção de PDF de imagem
identificador.py   etapa 2: número, espécie e tipo do ato
duplicidade.py     etapa 3: comparação com a pasta da DBF
normalizador.py    etapa 4 (esqueleto)
armazenamento.py   etapa 5 (parcial)
captura.py         etapa 1 (esqueleto)
tests/             testes automatizados (tests/amostras: decretos reais publicados)
requirements*.txt  dependências, com versões fixas
.github/           workflow de testes (Actions) e configuração do Dependabot
```

Para tratar uma nova espécie (por exemplo, Edital, em fase futura), defina uma
nova `Especie` em `especies.py` e inclua-a em `ESPECIES_ATIVAS`; as demais
etapas não precisam ser reescritas.

## Decisões e pressupostos

- Escopo: apenas decretos. Editais ficam para uma fase futura.
- O PDF gerado não reproduz assinaturas nem logomarcas.
- O modelo de cada tipo de ato é o decreto indicado em `especies.py`
  (concessão 60929, alteração 60951, prorrogação 60950, renovação 60945,
  transferência 60505, terceirização 60233). Revogação ainda não tem amostra
  nem modelo. Para segunda
  prorrogação/renovação usa-se o modelo da prorrogação/renovação; o padrão de
  texto dessa ementa é presumido, pois não há amostra real.
- O tipo do decreto é lido só pelo **início** da ementa. A palavra "prorrogação"
  aparece também na ementa da revogação e no corpo da renovação.
- O cabeçalho "DECRETO Nº ..." precisa estar no início do texto e em
  maiúsculas, para não confundir o ato com decretos citados no corpo.
- Um PDF com mais de um ato, ou sem cabeçalho reconhecível, nunca é
  descartado: vai para revisão manual.
- Uma página conta como digitalizada se tem menos de 20 caracteres (sem
  espaços) e uma imagem cobrindo 10% ou mais da sua área. O brasão da CEPE cobre
  cerca de 3% e não dispara; o Decreto 35945, que é uma imagem, cobre 29%. Um
  PDF sem nenhum texto extraível também vai para OCR.
- A base da DBF é tratada como uma pasta plana (sem pastas). Maiúsculas e
  minúsculas não diferenciam nomes de arquivo (a base fica no Windows).
- Um PDF inédito repetido no mesmo lote vai para revisão manual.
- Os PDFs de `tests/amostras/` são decretos já publicados pela CEPE (publicidade
  confirmada pelo responsável pelo projeto), por isso podem ficar no
  repositório. Não adicione ali PDFs que não sejam públicos. Antes de incluir
  uma amostra nova, confira as propriedades do PDF: arquivos mais antigos da
  CEPE (Word 2010) trazem o nome do autor do documento.

## Limitações conhecidas

- Download truncado não é detectável na leitura: os PDFs da CEPE trazem dados
  após o último `%%EOF` e o PyMuPDF os marca como "reparados" mesmo íntegros.
  A conferência de integridade deve ocorrer na etapa 1.
- Decretos republicados ou com errata têm o mesmo número do original. Pela
  regra atual (comparação estrita por número), a republicação seria tratada
  como duplicada e descartada.
- PDFs de imagem só são separados na pasta `leitura tesseract`; o OCR em si
  ainda não existe.
- Um PDF com cabeçalho em texto (20 caracteres ou mais na página) e corpo em
  imagem não é detectado como digitalizado: é lido como texto, não casa com
  nenhum cabeçalho de ato e acaba em `dados/erros`, não em `leitura tesseract`.
- A checagem de pastas sobrepostas compara caminhos (já resolvidos, sem
  diferenciar maiúsculas). A mesma pasta acessada por dois caminhos de natureza
  diferente (por exemplo, `G:` e um caminho de rede UNC) não é detectada.
