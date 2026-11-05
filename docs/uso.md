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
- **Caderno republicado no mesmo endereço** não é baixado de novo, porque o manifesto só guarda o que já veio. Para
  rebaixar, apague a entrada dele no manifesto.

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

Sem `--gravar`, só lê. Com `--gravar`, cada decreto de programa da DBF que a base não tem vira um PDF em
`dados/pendentes/` (`INÉDITO (gravado em pendentes)`), pronto para o lote. O comando:

- exige `PASTA_DBF`;
- nunca sobrescreve um arquivo, e rodar de novo não duplica nada;
- termina com código 1 se algum decreto não puder ser gravado.

Os pontos de atenção aparecem como `AVISO` no log: cabeçalho no meio da linha, decreto sem o preâmbulo `A GOVERNADORA`,
fecho não encontrado (caderno cortado), mesmo número repetido no caderno (republicação), página digitalizada e programa
citado só no corpo do decreto.

### `--comparar`

Compara o texto de cada decreto que "já existe na DBF" com o do PDF da base. Serve para saber se um decreto repetido é a
mesma publicação ou uma possível republicação. Só lê.

- A comparação é elemento por elemento: palavras, números e cada sinal de pontuação. Maiúsculas, acentos e pontuação
  contam.
- Espaços e quebras de linha não contam. Ligaduras, aspas e apóstrofos curvos e travessões são padronizados: "Recife/ PE"
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
```

- `localizar_exemplos` só lê `dados/cadernos`. Copia cada página achada, uma por PDF, para `dados/exemplos` (sem
  sobrescrever) e grava um `relatorio.txt`. Use `--sem-paginas` para só o relatório e `--cabecalhos
  EDITAL,PORTARIA,RESOLUÇÃO` para escolher as espécies. Os critérios são aproximados de propósito: é melhor olhar uma
  página a mais do que perder um exemplo.
- `regressao_separador` extrai um commit antigo e compara, caderno a caderno, os decretos separados. Sai com código 1 se
  algum diferir.
