# Uso

Como rodar cada modo, o que ele mostra e o que fazer quando algo dá errado. Todos os comandos são rodados da raiz do
repositório (`python main.py ...`, ou `dbf-cepe ...` com o pacote instalado).

## Instalação: detalhes

- As versões das dependências diretas ficam fixas em `requirements*.txt`, para a instalação ser reproduzível. O
  Dependabot propõe atualizações semanais por pull request.
- `pyproject.toml` guarda a versão mínima do Python e a configuração do pytest e do ruff.
- **WSL2:** mantenha o repositório no sistema de arquivos do Linux (por exemplo, `~/projetos`), não em `/mnt/c`. O
  drive da base precisa estar montado: `sudo mount -t drvfs G: /mnt/g`.
- **Pacote instalado:** use sempre `pip install -e .` na pasta do repositório. O `.env` e a pasta `dados/` são
  procurados na raiz do repositório.

## Etapa 1: baixar os cadernos (`--baixar`)

```bash
python main.py --baixar --de 2026-10-01 --simular                       # mostra o que baixaria; sem rede
python main.py --baixar --de 2026-10-01 --ate 2026-10-20
python main.py --baixar --de 2026-04-01 --ate 2026-04-30 --fonte s3     # endereços históricos
```

Só o caderno completo do dia é baixado, não os arquivos isolados por órgão. Os decretos sempre estão no caderno, e assim
nenhum decreto chega duas vezes.

- `--ate` é opcional; o padrão é hoje.
- `--fonte resumo` (padrão): site atual da CEPE (`diariooficial.cepe.com.br/.../resumoDiario/AAAA-MM-DD/`). Baixa o
  caderno do dia e a edição extra como `AAAAMMDD_ResumoDiario.pdf` e `AAAAMMDD_ResumoDiarioExtra.pdf`.
- `--fonte s3`: bucket histórico. Baixa Poder Executivo, Executivo Extra e Poder Legislativo
  (`AAAAMMDD_PoderExecutivo.pdf` e assim por diante).
- Não misture as duas fontes no mesmo período: o mesmo dia seria baixado duas vezes.
- `dados/cadernos/manifesto.json` registra o que já veio; rodar de novo não baixa outra vez. Um dia sem edição (resposta
  403 ou 404) não é registrado e é consultado de novo na próxima execução.
- Ctrl+C interrompe sem deixar arquivo parcial.

### Problemas de rede

- **Sem conexão** (timeout, proxy, firewall): o programa tenta 2 vezes cada caderno. Depois de 2 cadernos seguidos sem
  conexão, cancela o resto do período e termina com código 1.
- **O navegador abre o link e o Python não:** a rede deve exigir um proxy automático (PAC ou WPAD). Coloque
  `HTTPS_PROXY=auto` no `.env`: a cada captura o programa pergunta ao Windows qual é o proxy para o site da CEPE, então a
  porta pode mudar entre sessões. Se o Windows não indicar proxy, a captura segue direto; se a consulta falhar, o log
  avisa e a captura segue sem proxy explícito.
- **Proxy fixo:** `HTTPS_PROXY=http://127.0.0.1:9000`. Para descobrir o endereço:
  `([System.Net.WebRequest]::GetSystemWebProxy().GetProxy([uri]"https://diariooficial.cepe.com.br/")).AbsoluteUri`.
  Não coloque senha nessa linha.
- **`HTTPS_PROXY` definido na janela do PowerShell** vale mais que o `.env` e, com uma porta antiga, faz a captura (e o
  Git) falhar. Apague com `Remove-Item Env:HTTPS_PROXY`.
- **`CERTIFICATE_VERIFY_FAILED ... Missing Authority Key Identifier`** (Python 3.13 ou mais novo, com proxy que inspeciona
  o HTTPS): coloque `SSL_ESTRITO=nao` no `.env`. Isso desliga só a checagem de conformidade do certificado; a cadeia de
  confiança e o nome do servidor continuam sendo verificados.

### Limitações da captura

- **Download truncado:** os PDFs da CEPE têm dados depois do último `%%EOF`, e o PyMuPDF os marca como "reparados" mesmo
  íntegros. Por isso a etapa 1 confere o tamanho recebido contra o `Content-Length` e recusa respostas sem esse
  cabeçalho.
- **Endereços:** os da fonte `s3` foram conferidos de janeiro a maio de 2026 (Executivo) e até 2025 (Extra e
  Legislativo), mas responderam 403 de 19 a 22/10/2026. Os da fonte `resumo` foram vistos de julho a outubro de 2026 e
  ainda precisam ser confirmados na rede da DBF. Se nenhum caderno for encontrado em 5 ou mais consultas, o log avisa.
  Nesse caso, copie o endereço do PDF de um caderno recente no site e ajuste `Caderno.modelo_url` em `captura.py`. Nem
  todo dia tem edição extra.
- **403:** o S3 (e talvez o site da CEPE) responde 403 para arquivo inexistente. Por isso 403 é lido como "sem edição" e
  não se distingue de um bloqueio real; o aviso acima cobre esse caso.
- **Anos antigos (`--fonte s3`):** o S3 responde de 2025 até pelo menos 2005. Não há caderno às segundas-feiras (o
  Diário sai de terça a sábado), e algumas Extras não estão no S3 (por exemplo, 14/03/2020). Para baixar um período
  antigo sem misturar com `dados/cadernos`, defina `PASTA_CADERNOS` só para aquela execução (cada pasta tem o próprio
  manifesto). Antes de 2020 há problemas de leitura: [`funcionamento.md`](funcionamento.md#cadernos-antigos-ano-limite).
- **Caderno republicado no mesmo endereço** não é baixado de novo, porque o manifesto só guarda o que já veio. Para
  rebaixar, apague a entrada dele no manifesto.

## Lote histórico

Para gravar na base os decretos de meses anteriores a 2026:

- **Um mês por vez, em ordem cronológica** (do mais antigo para o mais novo), para a publicação original chegar
  antes da republicação.
- **Sempre `--fonte s3`.** O site atual da CEPE, a fonte padrão, só foi conferido para 2026. O S3 tem os cadernos
  de 2005 a 2025, mas o pipeline só lê corretamente a partir de **2020**
  ([`funcionamento.md`](funcionamento.md#cadernos-antigos-ano-limite)).
- **Cada mês na própria pasta** (`dados\historico\AAAA-MM`), com `PASTA_CADERNOS` definido só para o download. Assim
  o mês não se mistura com `dados\cadernos`, e cada pasta tem o próprio manifesto.

Em PowerShell, para fevereiro de 2020:

```powershell
$mes = "2020-02"; $de = "2020-02-01"; $ate = "2020-02-29"
$pasta = "dados\historico\$mes"

$env:PASTA_CADERNOS = $pasta
python main.py --baixar --fonte s3 --de $de --ate $ate
Remove-Item Env:PASTA_CADERNOS

python main.py --separar $pasta --comparar
python main.py --separar $pasta | Select-String "reprovado no crit"
python main.py --separar $pasta --gravar
python main.py --simular
python main.py
python main.py --conferir --simular
python main.py --conferir
python main.py --limpar-cadernos $pasta --simular
python main.py --limpar-cadernos $pasta
```

O que conferir em cada passo:

| Passo | O que olhar |
|---|---|
| `--baixar` | o resumo: "baixado" e "sem edição nesse dia" (feriados, domingos, segundas e dias sem Extra). Nenhum baixado no mês inteiro é problema de rede ou de proxy (ver "Problemas de rede") |
| `--separar --comparar` | o bloco "Comparação com a base": idênticos, de 99% a menos de 100% (vão para conferência), abaixo de 95% (investigar antes de gravar) e "PDF da base não lido" (base em imagem) |
| `Select-String "reprovado no crit"` | nenhuma linha. Se aparecer um decreto reprovado que não esteja "fora do escopo da DBF", apague o PDF dele em `dados\pendentes` depois do `--gravar`. A busca é sem acento por causa da codificação do console |
| `--gravar` | nenhum "não puderam ser gravados em pendentes" |
| `--simular` | o que vai para a base ("normalizado e armazenado na DBF") e o que vai para conferência |
| `--conferir` | decidir cada PDF (usar o Diário ou manter a base) |

Para o console mostrar os acentos, rode antes, na mesma janela: `$env:PYTHONUTF8 = "1"` e
`[Console]::OutputEncoding = [System.Text.Encoding]::UTF8`. Depois do `--conferir`, apague os cadernos do mês com
`--limpar-cadernos` ([abaixo](#limpeza-dos-cadernos---limpar-cadernos)); o manifesto da pasta guarda o que foi baixado
e o que foi feito com cada caderno.

### Vários meses de uma vez (`ferramentas.lote_historico`)

```powershell
python -m ferramentas.lote_historico --de 2020-03 --ate 2020-12              # baixa, separa e simula; nada vai para a base
python -m ferramentas.lote_historico --de 2020-03 --ate 2020-12 --executar   # o mesmo, gravando na base
python main.py --conferir                                                     # no fim, decide o que foi para conferência
```

Roda os passos acima mês a mês, em ordem, e **para antes de gravar o mês** no primeiro sinal de problema:

- o download não trouxe nenhum caderno, ou houve falha ou falta de conexão;
- um decreto da DBF foi reprovado no critério de leitura (texto cortado ou misturado);
- um decreto da DBF que a base já tem ficou abaixo de 95% de similaridade;
- algum decreto não pôde ir para `dados\pendentes`;
- a simulação do lote mandaria algo para erros ou para OCR, ou falharia ao gravar.

Também não começa se `dados\pendentes` tiver sobra de uma rodada anterior. O que vai para "conferir duplicata" não
para a sequência: fica para o `--conferir`, no fim. A fonte é escolhida sozinha (`s3` até maio de 2026, o site atual
depois). Ao parar, a ferramenta diz o mês e o motivo; resolva e rode de novo a partir desse mês (`--de`). Os meses já
gravados são reconhecidos como já registrados.

Sem `--executar`, cada mês é baixado, separado e simulado, e `dados\pendentes` é esvaziado no fim do mês. Como nada é
gravado, a simulação de um mês não enxerga o que o anterior gravaria: uma republicação entre meses aparece como
inédita. Isso só afeta a simulação.

Com `--executar`, no fim de cada mês gravado sem problema, a ferramenta **apaga os cadernos do mês já processados por
completo** ([limpeza](#limpeza-dos-cadernos---limpar-cadernos)): mostra no log a lista do que pode ser apagado e apaga
em seguida, sem pedir a simulação separada. O caderno com alguma pendência (por exemplo, um decreto em "conferir
duplicata" ainda sem decisão) fica, com o motivo no log; depois do `--conferir`, apague-o com `--limpar-cadernos`. Para
não apagar nada, use `--manter-cadernos`. Se um caderno não puder ser apagado (arquivo em uso, por exemplo), a
ferramenta para nesse mês.

No fim, uma linha por mês: cadernos, decretos, inéditos, já existentes, para conferir, gravados na base e, com
`--executar`, cadernos apagados e guardados. O log completo fica em `logs\pipeline.log`.

## Etapa 1b: separar os decretos (`--separar`)

```bash
python main.py --separar "dados/cadernos/20260626_ResumoDiario.pdf"     # só lista
python main.py --separar dados/cadernos --gravar                         # grava os inéditos em dados/pendentes
python main.py --separar dados/cadernos --comparar                       # compara o texto com o da base
```

Para cada decreto do caderno (ou de todos os PDFs da pasta), mostra:

- o nome do arquivo (`Decreto 60929.pdf`) e as páginas;
- o tipo (concessão, alteração etc., ou "tipo não reconhecido") e o programa da DBF;
- a situação: `fora do escopo da DBF`, `já existe na DBF` ou `INÉDITO`.

Sem `--gravar`, só lê. Com `--gravar`, cada decreto de programa da DBF vira um PDF em `dados/pendentes/`, pronto para
o lote. O comando:

- exige `PASTA_DBF` e lê o registro de publicações (`dados/registro_publicacoes.json`) sem alterá-lo; registro ilegível
  interrompe o comando (código 2) sem gravar nada;
- nunca sobrescreve um arquivo, e rodar de novo não duplica nada;
- termina com código 1 se algum decreto não puder ser gravado.

**Caderno com data no nome** (`AAAAMMDD_...`, como os baixados pela etapa 1): a data do caderno é a data de publicação e
segue dentro do PDF de pendentes até a etapa 5. O PDF se chama `Decreto 61055 - 20260715_ResumoDiario p3.pdf`, então o
mesmo número repetido no mesmo caderno ou em cadernos diferentes sai em PDFs separados. A situação mostrada é:

| Situação | O que acontece |
|---|---|
| `INÉDITO (gravado em pendentes: ...)` | número que a base não tem |
| `REPUBLICAÇÃO (gravado em pendentes: Decreto N (republicação).pdf ...)` | publicação mais nova de um número já gravado, com texto diferente |
| `ANTERIOR À DA BASE (gravado em pendentes: ...; antes, renomear ...)` | publicação mais antiga que a gravada: o lote renomeia o arquivo da base para abrir lugar |
| `CONFERIR DUPLICATA (gravado em pendentes: ...)` | a base tem o número sem registro e com texto diferente; o lote manda para conferência manual |
| `REVISÃO MANUAL (reprovado no critério de leitura: ...; não gravado)` | não grava: nenhum nível da leitura em cascata leu o decreto inteiro ([`funcionamento.md`](funcionamento.md#leitura-em-cascata)); confira o caderno e grave à mão, se for o caso |
| `já existe na DBF (texto idêntico)` | não grava |
| `já existe na DBF (PDF da base é imagem ou ilegível; não comparado)` | não grava: o PDF da base é digitalizado, ou tem texto que sai como códigos de controle (fonte sem conversão para Unicode, comum em 2010), e não há sinal de republicação (marca "REPUBLICADO" no caderno ou outro texto do mesmo número no lote) |
| `já registrado (mesma publicação)` | não grava (mesma data, caderno e página já no registro) |

O lote decide o nome final de novo, com a base daquele momento. **Caderno sem data no nome:** regra antiga (só o que a
base não tem; número repetido no caderno grava só a primeira ocorrência).

Os pontos de atenção aparecem como `AVISO` no log: cabeçalho no meio da linha, decreto sem o preâmbulo `A GOVERNADORA`,
fecho não encontrado (caderno cortado), mesmo número repetido no caderno (republicação), página digitalizada, programa
citado só no corpo do decreto, programa citado só nos considerandos de um decreto sem tipo (fica fora do escopo),
decreto recuperado por um nível da leitura em cascata (`lido no nível 3 (leitura por
coluna): ...; confira o texto`), decreto com um trecho de outro ato removido (`trecho de outro ato removido (N palavras,
lido por coluna): "..."; confira o texto`) e decreto reprovado no critério da leitura em cascata (`reprovado no critério de leitura
(sem fecho) em todos os níveis; revisão manual`; ver [`funcionamento.md`](funcionamento.md#leitura-em-cascata)).

### `--comparar`

Compara o texto de cada decreto que "já existe na DBF" com o do PDF da base. Serve para saber se um decreto repetido é a
mesma publicação ou uma possível republicação. Só lê.

- A comparação é elemento por elemento: palavras, números e cada sinal de pontuação. Maiúsculas, acentos e pontuação
  contam.
- Espaços, quebras de linha e pontilhado (três ou mais pontos seguidos, reticências) não contam. O pontilhado marca,
  nos decretos de alteração, o trecho não alterado; a base de 2020 não o guarda. Ligaduras, aspas e apóstrofos curvos e travessões são padronizados: "Recife/ PE"
  e "Recife/PE" são iguais.
- Cada linha diz "texto idêntico ao da base" ou dá a similaridade, quantos elementos estão só no caderno ou só na base e
  as 8 primeiras diferenças, com contexto.
- O fim do relatório conta quantos caem em cada faixa (idêntico, 99% a menos de 100%, 95% a 99%, abaixo de 95%, PDF da
  base não lido) e lista os 20 menos parecidos.

## Lote: etapas 2 a 5

Coloque os PDFs, um ato por arquivo, em `dados/pendentes/` e rode:

```bash
python main.py --simular   # mostra o que seria feito; nada é movido ou apagado
python main.py             # executa de verdade
```

O que acontece com cada PDF está em [`funcionamento.md`](funcionamento.md#lote-etapas-2-a-5). A execução é **abortada sem
tocar em nenhum arquivo** se:

- a pasta da DBF não existir, não puder ser lida ou estiver vazia (drive não montado, ainda sem sincronizar). Para uma
  base realmente vazia, use `--permitir-pasta-vazia`;
- a pasta da DBF e as pastas de trabalho não forem todas diferentes, ou uma ficar dentro de outra. Exemplo: com
  `PASTA_TEMP` igual a `PASTA_DBF`, cada decreto da base seria tratado como duplicado e apagado.

Se a gravação na DBF falhar por acesso (drive desmontado, sem permissão, disco cheio), o original continua em
`dados/pendentes`, o log registra o erro e o programa termina com código 1. Basta rodar de novo depois de resolver.

Se o Windows não deixar apagar o original depois de uma gravação confirmada, o log avisa para apagá-lo à mão; se isso não
for feito, o próximo lote o descarta como duplicado.

Se o computador desligar ou o drive cair durante a cópia, pode sobrar um `Decreto NNNNN.pdf.part` na pasta da DBF. Ele é
substituído na próxima gravação do mesmo decreto, mas pode ser apagado à mão. O Drive pode sincronizá-lo antes disso.

### Republicação no lote

- **Rode os lotes em ordem cronológica**, dos cadernos mais antigos aos mais novos. Dentro do lote, os PDFs são
  processados em ordem de publicação (data; no mesmo dia, a edição normal antes da Extra; no mesmo caderno, a página).
- Republicação gravada: `normalizado e armazenado na DBF | Decreto N (republicação).pdf`. Se a publicação que chega for
  mais antiga que a gravada, o log mostra `renomeado na DBF: Decreto N.pdf -> Decreto N (republicação).pdf` antes.
- Texto idêntico ao de uma publicação já gravada: descartado como duplicata e anotado no registro.
- Arquivo da base sem registro (gravado antes deste controle existir) e com texto diferente: o PDF normalizado vai para
  `dados/conferir duplicata/Decreto N (conferir duplicata).pdf`. **Nada é gravado nem renomeado na base nesse caso.**
  Confira à mão e, se for mesmo uma republicação, mova e renomeie você.
- PDF posto à mão em `dados/pendentes` (sem data de publicação): regra antiga, descartado se a base já tiver o nome.
- **Registro ilegível** (`dados/registro_publicacoes.json`): o lote para antes de mexer em qualquer arquivo (código 2).
  Restaure o arquivo de uma cópia ou, se tiver certeza, apague-o; aí os arquivos da base passam a ser tratados como
  anteriores ao registro.
- **Queda no meio de uma renomeação:** a operação fica anotada no registro, e a próxima execução (sem `--simular`) a
  desfaz ou conclui antes de tudo, com um aviso no log. Com `--simular`, o programa só avisa e para. Se os arquivos da
  base não conferirem com nenhum passo da operação, ele pede conferência manual e não mexe em nada.

## Conferência (`--conferir`)

```bash
python main.py --conferir --simular   # só mostra as diferenças de cada PDF, sem perguntar nem mexer em nada
python main.py --conferir             # pergunta, PDF a PDF, o que fazer
```

Revisa os PDFs de `dados/conferir duplicata`: publicações do Diário cujo número a base já tinha (gravado antes do
registro de publicações existir) com texto diferente. Para cada PDF, mostra a publicação (data, caderno, página), a
similaridade e as diferenças para o arquivo da base, no formato `...contexto [Diário: X | base: Y] contexto...`, e
pergunta:

| Resposta | O que acontece |
|---|---|
| `d` | **usa o texto do Diário**: o arquivo da base vai para `dados/substituidos` (com data e hora no nome) e o PDF do Diário entra no lugar dele, conferido; se algo falhar depois da troca, o antigo volta. O PDF sai da conferência e o registro passa a "gravado" |
| `b` | **mantém a base**: o PDF sai da conferência; a base não é tocada |
| `v` | mostra todas as diferenças (a tela mostra as 3 primeiras) |
| `p` ou Enter | pula: o PDF fica para a próxima vez |

- A decisão e a data ficam no registro de publicações (`decisao`, `decidido_em`). Uma publicação já decidida não volta
  para a conferência.
- Quando o PDF da base é imagem, a tela avisa que não há texto para comparar; o texto do Diário está no PDF da
  conferência.
- Se o número não tiver mais arquivo na base, a opção `d` não aparece.
- A pasta das cópias pode ser trocada com `PASTA_SUBSTITUIDOS` no `.env`. As cópias nunca são apagadas pelo programa.
- Para registro ilegível ou com uma operação do lote pela metade, o comando para sem mexer em nada (rode o lote antes).

## Limpeza dos cadernos (`--limpar-cadernos`)

```powershell
python main.py --limpar-cadernos dados\historico\2020-03 --simular   # lista o que seria apagado e por que o resto fica
python main.py --limpar-cadernos dados\historico\2020-03             # apaga exatamente a lista simulada
python main.py --limpar-cadernos dados\cadernos --mes 2026-09 --simular   # só os cadernos de um mês
```

Apaga o PDF dos cadernos já processados por completo e deixa no `manifesto.json` da pasta o registro do que foi feito.
Um caderno só é apagado se:

1. o mês dele já acabou;
2. o `--separar --gravar` rodou nele até o fim, sem falha ao gravar em pendentes (ele anota isso no manifesto);
3. não tem página digitalizada;
4. nenhum decreto da DBF dele ficou em revisão manual;
5. nenhum PDF dele está em pendentes, erros, leitura tesseract ou conferir duplicata;
6. todo decreto da DBF dele que foi para pendentes está no registro de publicações, e o que foi para "conferir
   duplicata" já foi decidido no `--conferir`.

Senão, o caderno fica, e a saída diz o motivo (`20200310_PoderExecutivo.pdf | limpeza: fica (Decreto 48700 em conferir
duplicata, sem decisão (rode --conferir))`).

- **Simulação obrigatória:** o `--simular` grava a lista em `limpeza_simulada.json`, na pasta. Sem ela, ou se a lista
  (ou o `--mes`) mudou desde a simulação, nada é apagado e o comando termina com código 2.
- **O que fica no manifesto:** endereço, tamanho, SHA-256 e data do download, mais `processado_em`, `processamento`
  (decretos, decretos da DBF com número, página e situação) e `apagado_em`. O caderno apagado continua no manifesto, e
  por isso a captura não o baixa de novo; para rebaixar, apague a entrada dele no manifesto.
- **Cadernos baixados antes desta versão** não têm a anotação do `--separar --gravar`: rode o `--separar --gravar` de
  novo na pasta (não grava nada repetido) e depois a limpeza.
- Nunca mexe na base da DBF: recusa uma pasta dentro dela.

## Catálogo (`--catalogar`)

```bash
python main.py --catalogar dados/cadernos                   # grava dados/catalogo_atos.csv
python main.py --catalogar dados/cadernos --saida outro.csv
python main.py --catalogar dados/cadernos --com-base        # também consulta a base, para as erratas
```

Reconhece decretos, editais, portarias, erratas e republicações e grava um CSV com um ato por linha. Separador `;` e
UTF-8 com BOM, que o Excel em português abre sem ajuste. Colunas: `especie, numero, ano, orgao, tipo, data_do_ato,
data_de_publicacao, caderno, pagina, republicacao, situacao`.

- **Só lê os cadernos.** Não usa o `.env`, a base, os logs nem as pastas do pipeline, e só grava o CSV (um arquivo
  existente no mesmo caminho é substituído).
- **`--com-base`** lê o `.env` e a **lista** de arquivos da base, para decidir o escopo das erratas. Não grava nada. Se a
  base estiver indisponível, avisa e segue sem ela.
- **Rode os lotes em ordem cronológica**, dos mais antigos aos mais novos: assim o ato e a errata dele não caem em lotes
  trocados.
- Ao final, imprime as contagens por espécie e por tipo e a lista **"não reconhecido ou a conferir"**, com caderno,
  página e trecho.

O significado de cada coluna e as regras de escopo estão em [`funcionamento.md`](funcionamento.md#catálogo).

## Ferramentas (fora do pipeline)

```bash
python -m ferramentas.localizar_exemplos            # acha erratas, republicações e formatos de edital/portaria
python -m ferramentas.regressao_separador main      # a separação dos decretos mudou em relação ao commit main?
python -m ferramentas.regressao_separador main --detalhes  # e o que mudou, decreto a decreto
python -m ferramentas.medir_criterio dados/cadernos  # quantos decretos, por ano, reprovam no critério da cascata
python -m ferramentas.lote_historico --de 2020-03 --ate 2020-12 [--executar]  # vários meses do lote histórico
```

- `localizar_exemplos` só lê `dados/cadernos`. Copia cada página achada, uma por PDF, para `dados/exemplos` (sem
  sobrescrever) e grava um `relatorio.txt`. Use `--sem-paginas` para só o relatório e `--cabecalhos
  EDITAL,PORTARIA,RESOLUÇÃO` para escolher as espécies. Os critérios são aproximados de propósito: é melhor olhar uma
  página a mais do que perder um exemplo.
- `regressao_separador` extrai um commit antigo e compara, caderno a caderno, os decretos separados. Sai com código 1 se
  algum diferir.
- `medir_criterio` só lê. Procura PDFs nas pastas (e subpastas) dadas, aplica o critério de aprovação da leitura em
  cascata a cada decreto e mostra, por ano do caderno, em que nível cada decreto foi lido (nível 1 é a leitura
  atual; 2, o fecho de até 2010; 3, a leitura por coluna; "nenhum", reprovado em todos), para todos os decretos e para
  os da DBF (o da DBF em "nenhum" vai para revisão manual), e por que a leitura atual reprovou os "nenhum" (`fecho antigo`, `sem fecho`,
  `fecho repetido`, `sem abertura`, `outro cabeçalho`, `expediente`, `DBF sem tipo`), com alguns exemplos. `--json
  ARQUIVO` grava tudo; `--exemplos N` muda quantos exemplos aparecem por ano. Sai com código 1 se algum caderno não
  puder ser lido. Critério e motivos: [`funcionamento.md`](funcionamento.md#cadernos-antigos-ano-limite).

