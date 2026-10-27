# Comparação de bases de Atos Normativos da CEPE e da DBF

Sincroniza a base local de atos normativos de programas de estímulos fiscais da Diretoria de Benefícios Fiscais (DBF) com os atos publicados no Diário Oficial pela CEPE.

Além de evitar arquivos duplicados, recria cada decreto inédito em um PDF de layout limpo e linear ("Normalização na Fonte"), mais adequado à leitura por IA.

Análise semântica dos atos **não** faz parte deste projeto.

## Etapas e situação

| # | Etapa | Arquivo | Situação |
|---|-------|---------|----------|
| 1 | Captura dos cadernos da CEPE | `captura.py` | **implementada** |
| 1b | Separação dos decretos do caderno e gravação em `dados/pendentes` | `separador.py`, `programas.py`, `exportador.py` | **implementada** |
| 2 | Identificação (PyMuPDF + RegEx) | `reader.py`, `identificador.py`, `especies.py` | **implementada** |
| 3 | Validação de duplicidade | `duplicidade.py` | **implementada** |
| 4 | Normalização e geração do PDF (fpdf2) | `normalizador.py` | **implementada** |
| 5 | Armazenamento na pasta da DBF | `armazenamento.py` | parcial (mover/descartar prontos; `armazenar` pendente) |

Enquanto a etapa 5 não existe, um decreto inédito **permanece na pasta temporária** (o PDF normalizado já é gerado em `dados/normalizados`): nada é descartado antes de o resultado ser gravado na DBF. Os cadernos baixados pela etapa 1 ainda não seguem para a etapa 2, que espera um ato por PDF: a etapa 1b separa os decretos e grava os inéditos da DBF em `dados/pendentes`, de onde o lote os lê.

### O que a etapa 2 decide

| Situação do PDF | Destino |
|---|---|
| Não abre, está vazio, sem páginas ou com senha | `dados/erros` |
| Digitalizado (total ou parcialmente) | `dados/leitura tesseract`, com o nome original |
| Começa por Lei, Portaria, Edital, Errata... | descartado |
| Sem cabeçalho reconhecível, ou com cabeçalhos de mais de um ato | `dados/erros` (revisão manual) |
| Decreto | segue para a etapa 3 |

Na etapa 3, o arquivo é comparado, pela nomenclatura `Decreto {numero}.pdf` (número sem separador de milhar), com a pasta da DBF. Se já existir, o PDF temporário é descartado. Decretos cujo tipo não é reconhecido (concessão, alteração, alteração da norma do programa, prorrogação, renovação, segunda prorrogação/renovação, transferência, revogação, terceirização, PROIND) vão para `dados/erros`. Na etapa 4, o decreto inédito é recriado em um PDF limpo (ver abaixo); se a normalização falhar, o PDF também vai para `dados/erros`, com o motivo no log.

## Instalação

Requer Python 3.10 ou superior (exigência dos pacotes fixados; testado com 3.10, 3.13 e 3.14). No WSL2, mantenha o repositório no sistema de arquivos do Linux (por exemplo, `~/projetos`) e não em `/mnt/c`.

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

Edite o `.env` e informe `PASTA_DBF` (a pasta da base oficial da DBF). No WSL2, o drive precisa estar montado: `sudo mount -t drvfs G: /mnt/g`.

As versões das dependências diretas ficam fixas em `requirements*.txt`, para a instalação ser reproduzível (as indiretas não são fixadas). Com o repositório no GitHub, o Dependabot (`.github/dependabot.yml`) propõe atualizações semanais por pull request.

## Uso

### Etapa 1: baixar os cadernos

Os decretos sempre estão no PDF completo do dia (o "caderno"). Editais, portarias e erratas também aparecem como arquivos isolados por órgão, mas só os cadernos completos são baixados, para um decreto não chegar duas vezes.

```bash
python main.py --baixar --de 2026-10-01 --simular   # mostra o que baixaria; sem rede
python main.py --baixar --de 2026-10-01 --ate 2026-10-20
python main.py --baixar --de 2026-04-01 --ate 2026-04-30 --fonte s3   # endereços históricos
```

O `--ate` é opcional (padrão: hoje). A `--fonte` escolhe de onde baixar: `resumo` (padrão) usa o site atual da CEPE (`diariooficial.cepe.com.br/.../resumoDiario/AAAA-MM-DD/`) e consulta, para cada dia, o caderno do dia e a edição extra, salvos em `dados/cadernos/` como `AAAAMMDD_ResumoDiario.pdf` e `AAAAMMDD_ResumoDiarioExtra.pdf`; `s3` usa o bucket histórico e consulta o Poder Executivo, o Executivo Extra e o Poder Legislativo (`AAAAMMDD_PoderExecutivo.pdf` e assim por diante). Não misture as duas fontes no mesmo período, senão o mesmo dia é baixado duas vezes. O registro do que já foi baixado fica em `dados/cadernos/manifesto.json`: rodar de novo não baixa o mesmo caderno. Dia sem edição (resposta 403 ou 404 do servidor) não é registrado e é consultado outra vez na próxima execução.

Sem conexão com o servidor (timeout, proxy, firewall), o programa tenta 2 vezes cada caderno e, depois de 2 cadernos seguidos nessa situação, cancela o resto do período e termina com código de saída 1. Se o navegador abre o link e o Python não, a rede provavelmente exige um proxy que o Python não descobre sozinho (proxy automático, PAC ou WPAD). Para achar o endereço, rode no PowerShell `([System.Net.WebRequest]::GetSystemWebProxy().GetProxy([uri]"https://diariooficial.cepe.com.br/")).AbsoluteUri`: se o resultado for outro endereço que não o do site da CEPE (por exemplo, `http://127.0.0.1:9000/`), esse é o proxy; coloque-o na linha `HTTPS_PROXY` do `.env`. Não coloque senha nessa linha. Se o download falhar com `CERTIFICATE_VERIFY_FAILED ... Missing Authority Key Identifier` (acontece no Python 3.13 ou mais novo quando o proxy inspeciona o HTTPS com um certificado fora do padrão estrito), coloque `SSL_ESTRITO=nao` no `.env`: isso desliga apenas a checagem de conformidade do certificado, e a cadeia de confiança e o nome do servidor continuam sendo verificados. Ctrl+C interrompe sem deixar arquivo parcial.

### Etapa 1b: separar os decretos do caderno

```bash
python main.py --separar "dados/cadernos/20260626_ResumoDiario.pdf"      # só lista
python main.py --separar dados/cadernos --gravar                          # grava os inéditos em dados/pendentes
```

Sem `--gravar` o comando só lê: não grava, move nem apaga nada. Para cada decreto do caderno (ou de todos os PDFs da pasta, se você passar uma pasta) ele mostra o nome do arquivo (`Decreto 60929.pdf`), as páginas, o tipo (concessão, alteração etc., ou "tipo não reconhecido"), o programa da DBF e a situação: `fora do escopo da DBF`, `já existe na DBF` ou `INÉDITO`. Com `--gravar`, cada decreto de programa da DBF que a base ainda não tem vira um PDF próprio em `dados/pendentes/` (`INÉDITO (gravado em pendentes)`), pronto para o lote. O comando exige a pasta da DBF (`PASTA_DBF`), nunca sobrescreve um arquivo e termina com código 1 se algum decreto não puder ser gravado. Rodar de novo não duplica nada: o decreto que já está em pendentes é só informado.

O escopo vem de `programas.py`: um decreto é da DBF se a ementa ou os considerandos (do início até o `DECRETA:`) citam um dos programas de benefício fiscal da lista, entre eles PRODEPE, PROIND, PEAP, PRODINPE e PRODEAUTO. Cada programa é reconhecido pela sigla, pelo nome oficial, pela lei ou decreto de origem e pelo anexo do RICMS-PE (o "Anexo 33" só vale com o Decreto nº 44.650 ou o RICMS ao lado); termos genéricos como "setor automotivo" ou "indústria naval" soltos não marcam programa. Os programas ainda não decididos (COOPERA, fios, tecidos e confecções, monofásica, Inovar-PE e FEEF) já têm o padrão pronto em `PROGRAMAS_OPCIONAIS`: para incluí-los, mova-os para `PROGRAMAS_DBF`. Se um programa só aparece no corpo do texto, o decreto fica fora do escopo mas o log traz um `AVISO` para você conferir. Para incluir um programa, acrescente-o em `PROGRAMAS_DBF`.

A separação é feita pelo texto, não por páginas, porque as páginas do Diário têm colunas e os decretos começam e terminam no meio delas: cada decreto vai do cabeçalho `DECRETO Nº ...` em maiúsculas, no início da linha, até as assinaturas que seguem o fecho (ou até o fim do anexo, se houver), e as citações a outros decretos no corpo do texto nunca são confundidas com cabeçalho. Os cabeçalhos e rodapés de página do Diário são removidos e as ligaduras (`ﬁ cam`) são convertidas (`ficam`); com isso o texto do decreto 60929 extraído do caderno de 26/06/2026 é idêntico ao do PDF avulso da DBF. Como o recorte de página não serve, o PDF de cada decreto é gerado de novo com o texto, em Liberation Sans (licença SIL OFL, versionada em `fontes/`), só com título e linhas, e é conferido antes de ser guardado: o PDF é relido e o texto e o número precisam bater com os do decreto, senão nada é gravado. Esse PDF é intermediário; o layout limpo dos modelos da DBF é a etapa 4. Os pontos de atenção aparecem como `AVISO` no log: cabeçalho no meio da linha, decreto sem o preâmbulo `A GOVERNADORA`, fecho não encontrado (caderno cortado), mesmo número repetido no caderno (republicação) e páginas digitalizadas.

### Etapa 4: o PDF normalizado

A etapa 4 recria cada decreto inédito em um PDF limpo, em `dados/normalizados/Decreto {numero}.pdf`, com o layout dos decretos que a DBF já guarda: fonte de 12 pontos (a Liberation Serif, de mesmas medidas da Times New Roman e de licença livre, versionada em `fontes/`), texto justificado e sem negrito, itálico, sublinhado ou link. O título (`DECRETO Nº ..., DE ... DE ....`) e as assinaturas ficam centralizados, a ementa fica no bloco da metade direita da página, artigos, incisos, alíneas e o fecho têm recuo na primeira linha, os trechos citados dos decretos de alteração ficam recuados dos dois lados e uma linha em branco separa os parágrafos. A logomarca e as assinaturas não são reproduzidas. Os seis modelos têm o mesmo layout, por isso há um gerador só, para qualquer tipo de decreto.

O texto chega em linhas fixas, sem marca de parágrafo, então a estrutura é recuperada: uma linha abre um parágrafo novo se começa como um dispositivo (`Art. 1º`, `I -`, `a)`, `1.1.`, `Parágrafo único`), como um considerando ou como `DECRETA:` e se a linha anterior terminou uma frase; o resto continua o parágrafo anterior. Essas regras ficam na `Especie` (`inicios_com_recuo` e `inicios_sem_recuo`), e nos seis modelos a divisão em parágrafos é idêntica à do Word. Só o espaço em branco muda: o PDF gerado é relido e o texto, o número e o tipo precisam bater com os do decreto, senão ele é descartado e o original vai para `dados/erros` com o motivo (por exemplo, um caractere que a fonte não tem). O `--simular` também gera e confere o PDF, em uma pasta temporária, sem gravar nada. Os pontos de atenção (ementa não encontrada, fecho ausente, citação sem aspas de fechamento, anexo) aparecem como `AVISO` no log. Um PDF normalizado que sobrou de uma execução anterior, sem a etapa 5, é substituído, porque é uma cópia derivada e não a base da DBF.

### Lote (etapas 2 a 5)

Coloque os PDFs de um ato por arquivo em `dados/pendentes/` e rode:

```bash
python main.py --simular   # mostra o que seria feito; nada é movido ou apagado
python main.py             # executa de verdade
```

Rode sempre `--simular` primeiro: o descarte de PDFs é definitivo (sem lixeira). O registro de cada decisão fica em `logs/pipeline.log`.

A execução é abortada, sem tocar em nenhum arquivo, se a pasta da DBF não existir, não puder ser lida ou estiver vazia (por exemplo, drive não montado ou ainda sem sincronizar). Para uma base realmente vazia, use `--permitir-pasta-vazia`.

Também é abortada se a pasta da DBF e as pastas de trabalho não forem todas diferentes ou se uma ficar dentro de outra. Exemplo: com `PASTA_TEMP` igual à `PASTA_DBF`, o script trataria cada decreto da base como duplicado e o apagaria.

## Testes

```bash
pytest
```

Parte dos testes usa os 6 decretos reais de `tests/amostras/` e dois trechos do caderno de 26/06/2026 em `tests/amostras_cadernos/` (páginas 24 a 26 e página 9) (sem os metadados do arquivo original), já publicados pela CEPE e por isso versionados. Se alguma amostra faltar, os testes **reprovam** (não são pulados). O resto da suíte usa PDFs sintéticos criados pelos próprios testes, e os testes de download não acessam a internet.

A cada push na `main` e a cada pull request, o GitHub Actions (`.github/workflows/testes.yml`) roda a suíte em Python 3.10 e 3.13. Não há deploy.

## Estrutura

```
main.py            orquestra a captura (--baixar) e o lote (etapas 2 a 5)
config.py          pastas e variáveis do .env
especies.py        regras por espécie: cabeçalho, nome de arquivo, tipos, fecho, escopo
separador.py       etapa 1b: um bloco de texto por decreto do caderno
programas.py       escopo: programas de benefício fiscal da DBF
exportador.py      etapa 1b: grava o PDF de cada decreto em pendentes
fontes/            Liberation Sans e Liberation Serif (SIL OFL) e as licenças
reader.py          etapa 2: leitura e detecção de PDF de imagem
identificador.py   etapa 2: número, espécie e tipo do ato
duplicidade.py     etapa 3: comparação com a pasta da DBF
normalizador.py    etapa 4: estrutura o texto e gera o PDF limpo (fpdf2)
armazenamento.py   etapa 5 (parcial)
captura.py         etapa 1: download dos cadernos do dia
tests/             testes automatizados (tests/amostras: decretos reais; tests/amostras_cadernos: trecho de caderno real)
requirements*.txt  dependências, com versões fixas
.github/           workflow de testes (Actions) e configuração do Dependabot
```

Para tratar uma nova espécie (por exemplo, Edital, em fase futura), defina uma nova `Especie` em `especies.py` e inclua-a em `ESPECIES_ATIVAS`; as demais etapas não precisam ser reescritas. A `Especie` reúne tudo o que muda de um ato para outro: o cabeçalho com o número, o padrão do nome do arquivo, os tipos, o fecho que encerra o texto, o cargo de quem assina, a frase que abre o corpo depois da ementa, o ponto onde termina o cabeçalho (no decreto, o `DECRETA:`), a lista de programas que define o escopo e os inícios de parágrafo que dão a estrutura do PDF normalizado. Os campos opcionais podem ficar vazios: sem fecho o separador não o procura, e sem lista de programas todo ato da espécie entra no escopo. O teste `tests/test_especie_generica.py` usa uma espécie fictícia para garantir que nenhuma etapa dependa de regra de decreto.

## Decisões e pressupostos

- Escopo inicial: apenas Decretos. Editais, Erratas e Portarias ficam para uma fase futura, desde que relacionados aos programas de estímulos da DBF.
- Dentro dos decretos, só interessam os de programas de benefício fiscal da DBF (`programas.py`); crédito suplementar, utilidade pública e outros assuntos são ignorados.
- Cada decreto que a DBF ainda não tem ganha o seu próprio PDF; os decretos não são agrupados como no Diário.
- O PDF gerado não reproduz assinaturas nem logomarcas. O layout dele segue os decretos da DBF (justificado, sem negrito, itálico, sublinhado ou link; título e assinaturas centralizados); a fonte é a Liberation Serif no lugar da Times New Roman, que é proprietária e não pode ser versionada.
- A captura baixa apenas os cadernos completos do dia, não os arquivos isolados por órgão, porque os decretos sempre estão no caderno completo e assim nenhum decreto chega duplicado.
- O modelo de cada tipo de ato é o decreto indicado em `especies.py` (concessão 60929, alteração 60951, prorrogação 60950, renovação 60945, transferência 60505, terceirização 60233). Alteração da norma do programa ("Introduz modificações no Decreto nº 21.959", "Modifica o Decreto nº 44.650"), revogação e PROIND ainda não têm modelo. O credenciamento no PEAP é feito por edital, não por decreto, e por isso não é um tipo de decreto: fica para a fase dos editais. Cada tipo e cada programa traz em `especies.py` e `programas.py` o campo `evidencia`: `amostra` (decreto real da DBF em mãos), `publicado` (decreto real publicado na Alepe ou na SEFAZ) ou `presumido` (deduzido da legislação, a confirmar). Para segunda prorrogação/renovação usa-se o modelo da prorrogação/renovação; o padrão de texto dessa ementa é presumido, pois não há amostra real.
- O tipo do decreto é lido só pelo **início** da ementa. A palavra "prorrogação" aparece também na ementa da revogação e no corpo da renovação.
- O cabeçalho "DECRETO Nº ..." precisa estar no início do texto e em maiúsculas, para não confundir o ato com decretos citados no corpo.
- Um PDF com mais de um ato, ou sem cabeçalho reconhecível, nunca é descartado: vai para revisão manual.
- Uma página conta como digitalizada se tem menos de 20 caracteres (sem espaços) e uma imagem cobrindo 10% ou mais da sua área. O brasão da CEPE cobre cerca de 3% e não dispara; o Decreto 35945, que é uma imagem, cobre 29%. Um PDF sem nenhum texto extraível também vai para OCR.
- A base da DBF é tratada como uma pasta plana (sem pastas). Maiúsculas e minúsculas não diferenciam nomes de arquivo (a base fica no Windows).
- Um PDF inédito repetido no mesmo lote vai para revisão manual.
- Os PDFs de `tests/amostras/` são decretos já publicados pela CEPE (publicidade confirmada pelo responsável pelo projeto), por isso podem ficar no repositório. Não adicionar ali PDFs que não sejam públicos. Antes de incluir uma amostra nova, confira as propriedades do PDF, pois arquivos mais antigos da CEPE (Word 2010) podem trazer o nome do autor do documento em seus metadados.

## Limitações conhecidas

- Download truncado não é detectável na leitura: os PDFs da CEPE trazem dados após o último `%%EOF` e o PyMuPDF os marca como "reparados" mesmo íntegros. Por isso a etapa 1 confere o tamanho recebido contra o `Content-Length` e recusa respostas sem esse cabeçalho.
- Os endereços da fonte `s3` (bucket público da CEPE) foram conferidos em janeiro a maio de 2026 (Executivo) e até 2025 (Extra e Legislativo), mas responderam 403 para 19 a 22/10/2026. Os da fonte `resumo` foram vistos em publicações de julho a outubro de 2026 e ainda precisam ser confirmados na rede da DBF. Se nenhum caderno for encontrado em 5 ou mais consultas, o log avisa; nesse caso, copie o endereço do PDF de um caderno recente no site e ajuste `Caderno.modelo_url` em `captura.py`. Nem todo dia tem edição extra.
- O S3 responde 403 para arquivo inexistente (e possivelmente o site da CEPE também), por isso 403 é lido como "sem edição" e não se distingue de um bloqueio real (o aviso acima cobre esse caso).
- Um caderno republicado no mesmo endereço não é baixado de novo, porque o manifesto só guarda o que já veio. Para rebaixar, apague a entrada dele no manifesto.
- Decretos republicados ou com errata têm o mesmo número do original. Pela regra atual (comparação estrita por número), a republicação seria tratada como duplicada e descartada.
- PDFs de imagem só são separados na pasta `leitura tesseract`; o OCR em si ainda não existe.
- A estrutura do PDF normalizado é inferida do texto. Um erro de inferência (um parágrafo que não foi separado, ou separado a mais) só muda o espaço em branco, nunca o texto, que é conferido; os atos com anexo mantêm as linhas do anexo como estão, sem reorganizar tabelas.
- Um PDF com cabeçalho em texto (20 caracteres ou mais na página) e corpo em imagem não é detectado como digitalizado: é lido como texto, não casa com nenhum cabeçalho de ato e acaba em `dados/erros`, não em `leitura tesseract`.
- A checagem de pastas sobrepostas compara caminhos (já resolvidos, sem diferenciar maiúsculas). A mesma pasta acessada por dois caminhos de natureza diferente (por exemplo, `G:` e um caminho de rede UNC) não é detectada.