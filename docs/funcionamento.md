# Funcionamento, decisões e limitações

Como cada parte funciona por dentro, por que foi feita assim e o que ainda não trata. Decisões tomadas pelo responsável
pelo projeto aparecem com a data.

## Escopo

- O **pipeline** (etapas 1b a 5) trata só **decretos**. Editais, portarias e erratas são reconhecidos pelo catálogo, mas
  só entram no pipeline em fase futura, se forem dos programas de estímulos da DBF.
- Dentro dos decretos, só interessam os de **programas de benefício fiscal da DBF** (`regras/programas.py`). Crédito
  suplementar, utilidade pública e outros assuntos são ignorados.
- Cada decreto que a DBF não tem ganha o próprio PDF; os decretos não são agrupados como no Diário.

### Programas da DBF

Um decreto é da DBF se a ementa ou os considerandos (do início até o `DECRETA:`) citam um programa da lista
`PROGRAMAS_DBF`: PRODEPE, PROIND, PEAP, PRODINPE, PRODEAUTO e outros.

- Cada programa é reconhecido pela sigla, pelo nome oficial, pela lei ou decreto de origem e pelo anexo do RICMS-PE. O
  "Anexo 33" só vale com o Decreto nº 44.650 ou o RICMS ao lado.
- Termos genéricos soltos ("setor automotivo", "indústria naval") não marcam programa.
- Programa citado só no corpo: o decreto fica fora do escopo, mas o log traz um `AVISO` para conferir.
- Os programas ainda não decididos (COOPERA, fios, tecidos e confecções, monofásica, Inovar-PE e FEEF) já têm padrão em
  `PROGRAMAS_OPCIONAIS`; para incluí-los, mova-os para `PROGRAMAS_DBF`.
- Para o credenciamento por edital vale `PROGRAMAS_DE_CREDENCIAMENTO`: os programas da DBF mais a Lei de Incentivo ao
  Esporte (Lei nº 15.706/2015). Ela fica fora de `PROGRAMAS_DBF` para não mudar o escopo dos decretos.

## Captura (etapa 1)

- Só o caderno completo do dia é baixado, não os arquivos por órgão: os decretos sempre estão no caderno, e assim nenhum
  chega duas vezes.
- Uso, problemas de rede e limitações (endereços, 403, download truncado): [`uso.md`](uso.md#etapa-1-baixar-os-cadernos---baixar).

## Separação dos decretos (etapa 1b)

O caderno é separado pelo **texto**, não por páginas, porque as páginas do Diário têm colunas e os decretos começam e
terminam no meio delas.

- Cada decreto vai do cabeçalho `DECRETO Nº ...` (em maiúsculas, no início da linha) até as assinaturas que seguem o
  fecho, ou até o fim do anexo, se houver.
- Citações a outros decretos no corpo do texto nunca são confundidas com cabeçalho.
- Cabeçalhos e rodapés de página do Diário são removidos (nas 4 primeiras linhas de cada página: "Recife, 26 de junho de
  2026", "Ano CX Ć NÀ 114 - 9", "Diário Oficial do Estado de Pernambuco..."; desde 09/11/2026 também as variantes do dia
  1º e da edição Extra, "Recife, 1À de outubro de 2026" e "CX Ć NÀ 31 3", e as dos anos antigos, com dia da semana,
  travessão, sem o "Ano" ou com o título na mesma linha), e as ligaduras (`ﬁ cam`) são convertidas (`ficam`). Com isso,
  o texto do decreto 60929 extraído do caderno de 26/06/2026 é idêntico ao do PDF avulso da DBF.
- O **expediente do Diário** (o quadro com governador(a), vice, secretários, preços e endereço da CEPE, impresso uma vez
  por caderno) é removido pela posição: o quadro é localizado pelas linhas típicas dele (`MARCAS_DO_EXPEDIENTE`; pelo
  menos 4 tipos, entre eles o nome da CEPE e o par "GOVERNADOR(A)"/"VICE-GOVERNADOR(A)"), e toda linha da página que
  cai no retângulo dos blocos dessas linhas sai do texto. O "GOVERNADOR(A)" só conta seguido de "VICE-GOVERNADOR(A)"
  (sozinho, pode ser a assinatura de outro ato), e o quadro cresce para baixo com os blocos de cargo ("SECRETÁRIO DE
  DEFESA SOCIAL") alinhados à lista de secretários, que às vezes desce além da última marca. Antes, quando ele dividia a página com um decreto, entrava no
  meio do texto (ex.: Decreto 57.317/2024, da DBF). Nos cadernos de referência de 2026, isso só muda os decretos 61.563
  e 61.566, fora da DBF. *Decisão de 09/11/2026.*
- Cada decreto leva a **data de publicação**, que é a data do caderno tirada do nome do arquivo (`AAAAMMDD_...`), e o
  nome do caderno. Com `--gravar`, os dois vão nos metadados do PDF de pendentes (campo Keywords) e acompanham o decreto
  até a etapa 5. O `manifesto.json` não é usado para isso.
- Como o recorte de página não serve, o PDF de cada decreto é gerado de novo a partir do texto, em Liberation Sans, só
  com título e linhas. Antes de ser guardado, ele é relido, e o texto e o número precisam bater, senão nada é gravado.
  Esse PDF é intermediário; o layout da DBF vem na etapa 4.

**Limitação, decreto republicado com anexo:** o `(REPUBLICADO POR HAVER SAÍDO COM INCORREÇÃO NO ORIGINAL)` vem depois das
assinaturas e antes do `ANEXO I`. O separador encerra o ato nas assinaturas (o marcador não é assinatura) e deixa os
anexos de fora. No Decreto 61.055 (caderno de 15/07/2026), o texto separado tem 1.670 caracteres e o ato inteiro, 14.787.
Não foi alterado, para a comparação com a base não regredir; o catálogo marca o decreto como republicado. *Decisão de
04/11/2026: fica assim, sem os anexos.*

## Lote (etapas 2 a 5)

### Etapa 2: identificação

| Situação do PDF | Destino |
|---|---|
| Não abre, vazio, sem páginas ou com senha | `dados/erros` |
| Digitalizado (total ou parcialmente) | `dados/leitura tesseract`, com o nome original |
| Começa por Lei, Portaria, Edital, Errata... | descartado |
| Sem cabeçalho reconhecível, ou com cabeçalhos de mais de um ato | `dados/erros` (revisão manual) |
| Decreto | segue para a etapa 3 |

- O cabeçalho `DECRETO Nº ...` precisa estar no início do texto e em maiúsculas, para não confundir o ato com decretos
  citados.
- PDF com mais de um ato, ou sem cabeçalho reconhecível, nunca é descartado: vai para revisão manual.
- O **tipo** do decreto é lido só pelo **início** da ementa, porque "prorrogação" aparece também na ementa da revogação e
  no corpo da renovação. Tipos: concessão, alteração, alteração da norma do programa, prorrogação, renovação, segunda
  prorrogação/renovação, transferência, revogação, terceirização e PROIND. Decreto de tipo não reconhecido vai para
  `dados/erros`.
- **Página digitalizada:** menos de 20 caracteres (sem espaços) e uma imagem cobrindo 10% ou mais da área. O brasão da
  CEPE cobre cerca de 3% e não dispara; o Decreto 35945, que é uma imagem, cobre 29%. PDF sem nenhum texto extraível
  também vai para OCR.

**Limitações:**

- O OCR em si ainda não existe: os PDFs de imagem só são separados na pasta `leitura tesseract`.
- PDF com cabeçalho em texto (20 caracteres ou mais) e corpo em imagem não é detectado como digitalizado: é lido como
  texto, não casa com nenhum cabeçalho e vai para `dados/erros`.

### Etapa 3: duplicidade e republicação

- PDF **com** data de publicação (veio de `--separar --gravar` com caderno datado): vale a regra de republicação (seção
  [Republicação](#republicação)). Uma republicação nunca é descartada só por ter o número de um arquivo da base.
- PDF **sem** data (posto à mão em pendentes): regra antiga. O nome `Decreto {numero}.pdf` (número sem separador de
  milhar) é comparado com a pasta da DBF; se já existir, o PDF é descartado. Um PDF repetido no mesmo lote vai para
  revisão manual.
- A base é tratada como pasta plana (sem subpastas). Maiúsculas e minúsculas não diferenciam nomes, porque a base fica
  no Windows.

**Limitações:**

- Errata continua fora: tem o número do ato referido, mas não é separada como decreto.
- A checagem de pastas sobrepostas compara caminhos já resolvidos, sem diferenciar maiúsculas. A mesma pasta acessada por
  dois caminhos de natureza diferente (por exemplo, `G:` e um caminho de rede UNC) não é detectada.

### Etapa 4: normalização

Cada decreto inédito é recriado em `dados/normalizados/` (com o nome final, que numa republicação tem sufixo) com o
layout dos decretos da DBF:

- fonte de 12 pontos, Liberation Serif (mesmas medidas da Times New Roman, que é proprietária e não pode ser
  versionada);
- texto justificado, sem negrito, itálico, sublinhado ou link;
- título e assinaturas centralizados, ementa no bloco da metade direita;
- recuo na primeira linha de artigos, incisos, alíneas e do fecho; trechos citados de decretos de alteração recuados dos
  dois lados; uma linha em branco entre parágrafos;
- sem logomarca e sem assinaturas. Os seis modelos têm o mesmo layout, então há um gerador só, para qualquer tipo.

O texto chega em linhas fixas, sem marca de parágrafo, e a estrutura é recuperada: uma linha abre parágrafo novo se
começa como dispositivo (`Art. 1º`, `I -`, `a)`, `1.1.`, `Parágrafo único`), considerando ou `DECRETA:` e a linha
anterior terminou uma frase. Essas regras ficam na `Especie` (`inicios_com_recuo` e `inicios_sem_recuo`). Nos seis
modelos, a divisão em parágrafos é idêntica à do Word.

- O PDF gerado é relido: texto, número e tipo precisam bater, senão ele é descartado e o original vai para `dados/erros`
  com o motivo (por exemplo, um caractere que a fonte não tem).
- O `--simular` também gera e confere o PDF, numa pasta temporária, sem gravar nada.
- Pontos de atenção viram `AVISO` no log: ementa não encontrada, fecho ausente, citação sem aspas de fechamento, anexo.
- Um PDF normalizado que sobrou de uma execução anterior é substituído: é cópia derivada, não a base.

**Limitação:** a estrutura é inferida do texto. Um erro de inferência (parágrafo não separado, ou separado a mais) só muda
o espaço em branco, nunca o texto, que é conferido. Atos com anexo mantêm as linhas do anexo como estão, sem reorganizar
tabelas.

### Etapa 5: gravação na base

O PDF normalizado vai para a pasta da DBF como `Decreto {numero}.pdf`:

1. O programa confirma que o PDF se identifica como o decreto esperado e que o nome ainda não existe na base (alguém pode
   ter salvado o mesmo decreto depois da etapa 3).
2. Copia para um `.part` na própria pasta da DBF e compara o SHA-256 do que foi gravado com o do PDF gerado.
3. Só então dá o nome final. No Windows, o renomear recusa um nome que já existe, então um decreto da base nunca é
   sobrescrito. O arquivo final é relido.
4. O original de `dados/pendentes` e a cópia de `dados/normalizados` só são apagados depois dessa conferência.

- Numa republicação, o nome final pode ter sufixo (`Decreto N (republicação).pdf`), e a conferência do passo 1 compara
  o número lido do texto com o nome sem sufixo. Se for preciso renomear arquivos da base antes (publicação mais
  antiga que a gravada), as renomeações vêm antes do passo 2 e são desfeitas se qualquer passo falhar.
- Se alguém salvou o mesmo decreto durante o lote, o original é descartado como duplicado (PDF sem data). Com data, o
  PDF fica em pendentes para o próximo lote, porque o nome tomado não prova que é a mesma publicação.
- Se o conteúdo gravado não conferir, o arquivo sai da base e o original vai para `dados/erros`.
- O `.part` é o único arquivo temporário na pasta sincronizada com o Drive e só existe durante a cópia.
- Falhas de acesso e o que fazer: [`uso.md`](uso.md#lote-etapas-2-a-5).

## Republicação

Um decreto pode sair mais de uma vez com o mesmo número ("republicado por haver saído com incorreção", ou repetido na
Extra). *Decisões de 06/11/2026:*

| Situação | Resultado |
|---|---|
| Número que a base não tem | `Decreto N.pdf` |
| Publicação mais nova de um número já gravado, texto diferente | `Decreto N (republicação).pdf`; a próxima, `Decreto N (republicação 2).pdf`, e assim por diante |
| Publicação mais antiga que a já gravada | os arquivos da base são renomeados um passo adiante e a que chega fica com `Decreto N.pdf` |
| Texto idêntico ao de uma publicação já gravada (qualquer data) | duplicata: não grava, só anota no registro |
| Mesmo dia, edição normal e Extra | a normal conta como a mais antiga; no mesmo caderno, vale a página |
| Arquivo da base sem registro, texto idêntico (critério de `comparacao.py`) | duplicata |
| Arquivo da base sem registro, texto diferente | PDF normalizado em `dados/conferir duplicata/Decreto N (conferir duplicata).pdf`; nada muda na base |
| Arquivo da base sem registro, digitalizado (imagem, sem texto para comparar) | conta como já existente e o registro anota `base é imagem (não comparado)`; vai para conferência só com sinal de republicação: a marca "(REPUBLICADO ...)" no caderno, outro texto do mesmo número no mesmo lote ou no registro. *Decisão de 10/11/2026* |
| A mesma publicação (data, caderno e página) já no registro | ignorada: rodar o lote de novo não muda nada |

- **Duas passagens no lote.** A primeira lê a data de publicação de cada PDF de pendentes e ordena o lote; a segunda
  decide o nome (`pipeline/republicacao.py`) antes de gerar e gravar.
- **Texto idêntico** = mesmo hash do texto normalizado (`pipeline/publicacao.py`), que usa os mesmos elementos da
  comparação: espaços, quebras de linha, ligaduras e aspas curvas não contam.
- **Arquivo "do número" na base** inclui nomes fora do padrão que começam pelo nome padrão (`Decreto 61055 (1).pdf`,
  `Decreto 61055-A.pdf`, mas não `Decreto 610551.pdf`). Como não constam do registro, caem na conferência manual.
- **Nomes parametrizáveis:** os sufixos ficam na `Especie` (`sufixo_republicacao`, `sufixo_republicacao_n`,
  `sufixo_conferir`).

### Registro de publicações

`dados/registro_publicacoes.json` (fora do Git; `REGISTRO_PUBLICACOES` no `.env`) guarda, para cada publicação que passou
pela etapa 3 com data: espécie, número, data de publicação, caderno, página, hash do texto, nome do arquivo e situação
(`gravado`, `duplicata`, `conferir duplicata` ou `base é imagem (não comparado)`). É com ele que o lote sabe a data de cada arquivo da base.

- Escrita atômica (arquivo `.tmp`, gravação forçada no disco e troca de nome): uma queda no meio deixa o registro
  anterior.
- Registro ilegível interrompe o programa sem tocar em nada, até alguém restaurar ou apagar o arquivo.
- Antes de renomear ou gravar na base, a operação é anotada no registro (`operacao_em_andamento`). Na próxima execução,
  o programa compara os arquivos da base com cada passo da operação: se tudo foi feito, conclui o registro; se parou no
  meio, desfaz as renomeações; se nada confere, pede conferência manual.

**Limitações:**

- Um texto idêntico mais antigo que chega depois de um mais novo é descartado, e o arquivo gravado fica com a data da
  publicação mais nova.
- Texto cortado ou com o expediente do Diário misturado (seção seguinte) muda o hash e pode gerar uma republicação ou um
  "conferir" falso.
- O registro é local: duas máquinas rodando lotes teriam registros diferentes.
- Duplicata idêntica ao arquivo da base, barrada já no `--separar --gravar`, não entra no registro (só as descartadas
  pelo lote entram).
- O mesmo número duas vezes na mesma página de um caderno colide em pendentes.

## Cadernos antigos (ano-limite)

Sondagem de 06/11/2026: amostra do S3 (Executivo e Extra), duas janelas de 10 dias úteis por ano, de 2010 a 2025. O
pipeline lê corretamente os cadernos de **2020 em diante**; de julho a novembro de 2019 a amostra também saiu limpa, mas
é pequena.

| Período | Problema | Na amostra |
|---|---|---|
| 2012 a jun/2019 | **Ordem das colunas:** o PyMuPDF entrega o texto fora da ordem de leitura; um decreto é cortado no meio e o final cai no bloco do vizinho | 10% a 20% dos decretos; 2 a 5 da DBF por ano (ex.: Decreto 47.225, 22/03/2019) |
| até 2010 (mudou entre set/2010 e mar/2012) | **Fecho antigo** `PALÁCIO DO CAMPO DAS PRINCESAS, em 18 de março de 2010.`, sem "Independência do Brasil." | todos os decretos de 2010 |
| 2010 | **Ementas antigas:** "Introduz modificações no Decreto ..., que concede incentivo do PRODEPE" e "Dispõe sobre a fruição de estímulo ... PRODEPE" | 4 de 36 decretos da DBF sem tipo |
| todos os anos, até 2026 | **Expediente do Diário** (lista de secretários, preços, endereço da CEPE) entrava no texto do decreto da mesma página | resolvido em 09/11/2026 (ver "Separação"); sobram 6 casos em 2014, 2018 e 2019, junto com o problema das colunas |
| 2012, 2019 e 2025 | Abertura de quem substitui o titular: **"A VICE GOVERNADORA, NO EXERCÍCIO DO CARGO..."**, **"O PRESIDENTE DA ASSEMBLÉIA LEGISLATIVA ..., NO EXERCÍCIO DO CARGO DE GOVERNADOR"** | aceita desde 09/11/2026 |

Os buracos na numeração das amostras não são do separador: os números que faltam não aparecem em nenhum caderno
baixado, o que bate com Extras ausentes do S3.

*Decisões de 06/11/2026:* "Introduz modificações no Decreto ..., que concede incentivo" conta como **alteração**, e
"Dispõe sobre a fruição de estímulo" (Decreto 34.733/2010, que no artigo 1º concede o estímulo) como **concessão**.
Ainda não aplicadas.

*Decisões de 09/11/2026:* a abertura de quem substitui o titular é aceita (`ABERTURA_DO_CORPO_DECRETO`, em
`regras_atos.py`). Um bloco de outra espécie que começa como decreto ("DECRETO Nº 30.257, DE 12 DE MARÇO DE 2007, BAIXOU
AS SEGUINTES PORTARIAS:") vai para revisão manual. O decreto reprovado em todos os níveis da cascata vai para revisão
manual.

### Leitura em cascata

Cada decreto passa por um **critério de aprovação** (`leitura/criterio.py`); se reprovar, é relido no nível seguinte
(`leitura/cascata.py`), até algum aprovar. O decreto aprovado no nível 1 sai exatamente como o separador o entrega.

Critério (um decreto só passa se cumprir tudo):

- o fecho da espécie aparece exatamente uma vez. Sem ele, o motivo é `fecho antigo` se houver o fecho de até 2010
  (`Especie.fechos_anteriores`), senão `sem fecho`; duas ou mais vezes, `fecho repetido`;
- a abertura do corpo vem nos primeiros 1.800 caracteres;
- não há outro cabeçalho da espécie no início de uma linha do texto (`outro cabeçalho`);
- não sobra trecho do expediente do Diário (`expediente`: linhas inteiras em maiúsculas do quadro, como "COMPANHIA
  EDITORA DE PERNAMBUCO" ou "SECRETÁRIOS DE ESTADO"; "Secretários de Estado" no corpo do ato não conta);
- se for de programa da DBF, o tipo é reconhecido (`DBF sem tipo`).

| Nível | Leitura | Situação |
|---|---|---|
| 1 | leitura atual do separador | implementado (etapa B) |
| 2 | fecho de até 2010 | etapa C |
| 3 | leitura por coluna, só nas páginas do decreto | etapa D |
| — | nenhum aprovou | o decreto fica com `nivel_de_leitura=None`, os motivos em `reprovacao` e o aviso "reprovado no critério de leitura (...) em todos os níveis; revisão manual" |

Hoje (etapa B) a cascata só marca: o `--separar` mostra o aviso, mas o reprovado ainda segue para pendentes com
`--gravar`. A etapa E muda isso.

`python -m ferramentas.medir_criterio` aplica o critério e os níveis e mostra, por ano, quantos decretos foram
recuperados e quantos ficam reprovados. Medição de 09/11/2026 nas amostras da sondagem:

| Ano | Decretos | Reprovados | Da DBF reprovados | Motivos |
|---|---|---|---|---|
| 2020 a 2026 | 1.195 | 0 | 0 | — |
| 2019 | 263 | 16 | 5 | sem fecho 11, fecho repetido 3, sem abertura 1, expediente 1 |
| 2014 a 2018 | 408 | 73 | 13 | sem fecho 51, fecho repetido 20, expediente 5, sem abertura 2 |
| 2012 | 104 | 22 | 3 | sem fecho 20, fecho repetido 2 |
| 2010 | 145 | 145 | 36 | fecho antigo 143, DBF sem tipo 4, sem fecho 2, sem abertura 1 |

A leitura por coluna sozinha corrige o corte (2016 a 2019: de 12 a 14 decretos cortados por ano para 0), mas embaralha
ementas e muda o texto dos anos bons, então só pode entrar como nível de recuperação.

### Modelos e evidência dos tipos

- O modelo de cada tipo é o decreto indicado em `regras/especies.py`: concessão 60929, alteração 60951, prorrogação
  60950, renovação 60945, transferência 60505 e terceirização 60233. Segunda prorrogação/renovação usa o modelo da
  prorrogação/renovação.
- Alteração da norma do programa ("Introduz modificações no Decreto nº 21.959", "Modifica o Decreto nº 44.650",
  "Modifica o Decreto nº 44.766", o regulamento do PROIND, incluído em 11/11/2026),
  revogação e PROIND ainda não têm modelo.
- Cada tipo e cada programa tem o campo `evidencia`: `amostra` (ato real da DBF em mãos), `publicado` (ato real publicado
  na Alepe ou na SEFAZ) ou `presumido` (deduzido da legislação, a confirmar). O padrão da ementa de segunda
  prorrogação/renovação é presumido: não há amostra real.

## Catálogo

Reconhece decretos, editais, portarias, erratas e republicações no texto dos cadernos. As RegEx ficam em
`regras/regras_atos.py`, a fonte única das regras de reconhecimento: `especies.py`, `separador.py`, `programas.py` e a
ferramenta de levantamento importam dele. A tabela de exemplos (entrada -> resultado esperado) fica em
`tests/test_regras_atos.py`.

*Decisão de 11/11/2026:* não há mais envio de arquivos à CEPE. O `regras_atos.py` deixou de ser mantido só com a
biblioteca padrão para poder ir sozinho a terceiros.

**Dívida técnica:** o separador (pipeline) e o `identificar` do `regras_atos.py` (catálogo) reconhecem o decreto cada um
do seu jeito. Eram dois porque o `regras_atos.py` precisava rodar sozinho; hoje poderiam ser unificados, mas o separador
acaba de ser validado contra a base e a mudança fica para depois das etapas da leitura em cascata.

### Colunas

- `data_de_publicacao`: do **nome** do caderno (`AAAAMMDD_...`).
- `data_do_ato`: do cabeçalho. O edital da DBF não a traz no cabeçalho; ela vem da linha `Recife, 21 de outubro de
  2026.` do fecho.
- Errata: `numero` e `ano` são os do **ato referido** (a errata não tem número próprio), e `tipo` é o destino (`decreto`,
  `edital`, `portaria` ou `outro`).
- `republicacao`: `sim` (o `(REPUBLICADO ...)` vem logo depois das assinaturas, ou em título acima do edital),
  `provavel` ou `nao`. Rodapé de republicação de outra espécie (instrução normativa, lista de atos do Governador) não se
  liga a nenhum ato e sai como linha `Republicação` a conferir.
- `situacao`: `no_escopo`, `fora_do_escopo` ou `a_confirmar`, seguida do motivo.

### Escopo no catálogo

| Espécie | No escopo quando |
|---|---|
| Decreto | cita programa da DBF no cabeçalho |
| Edital | órgão `DBF` ou `DBM` **e** programa reconhecido no texto (a situação traz o programa, ex.: `no_escopo: órgão DBF; PRODEAUTO`); DBF sem programa reconhecido fica `a_confirmar` |
| Portaria | órgão `SF` **e** programa da DBF citado no texto; SF que só designa ou dispensa servidor fica `fora_do_escopo` |
| Errata | o ato referido é da DBF (ver abaixo) |

- **Errata segue o ato referido.** Entra no escopo se o ato estiver na base (`Decreto 58509.pdf`, `Edital 064-2025.pdf`;
  só com `--com-base`), se estiver no escopo nos cadernos lidos na mesma execução ou se a própria errata citar programa
  da DBF. Sem nenhum desses sinais, fica `a_confirmar`, nunca `fora`, porque o original pode faltar na base. Portaria não
  tem arquivo na base. Se `Decreto 58509 Errata.pdf` já existir, a situação avisa. Errata de ato não identificado também
  fica `a_confirmar`.
- **Órgão sem sigla no cabeçalho:** vale o título em maiúsculas logo acima (até 8 linhas; `SECRETARIA DE
  ADMINISTRAÇÃO`), e ele vale também para os atos seguintes do mesmo bloco, com a marca "lido do título da seção". Sem
  título, o ato fica `a_confirmar`.
- **Nome de arquivo do edital:** `Edital {numero}-{ano}.pdf`, como na base (`Edital 064-2025.pdf`). O da portaria
  continua provisório, porque não há portaria na base.

**Decisões:**

- *03/11/2026:* edital SEFAZ = órgão `DBF` (`DBM` é aceito como outro nome da DBF, sem exemplo nos cadernos); portaria
  SEFAZ = órgão `SF` pela sigla do cabeçalho, e só se citar programa da DBF (a SF 76 é só a designação do Gestor de
  Telemática). A DPC não faz parte da demanda.
- *04/11/2026:* edital da DBF só entra se citar programa (PRODEAUTO, Lei de Incentivo ao Esporte etc.); o título da seção
  vale para os atos seguintes do mesmo bloco; o `EDITAL DE NÃO CONCORRÊNCIA` (publicação particular do PRODEPE) não
  entra; a errata segue o ato referido, com os lotes rodados em ordem cronológica; o CSV continua com `;` e BOM.

**Limitações:**

- O órgão lido do título é uma pista de baixa confiança. Quando o título está longe, o rótulo do órgão pode sair errado,
  mas o escopo não muda, porque um ato que cita programa da DBF nunca sai como fora.
- Cabeçalhos sem número (`EDITAL DE CONVOCAÇÃO`, `PORTARIA SEE/SEGP DE 20 DE OUTUBRO...`) saem com aviso.
- Uma citação em maiúsculas que quebra de linha (`... designada, por meio da` + `PORTARIA Nº 2224/2026, de ...`) é
  sinalizada, não descartada.
- O tipo da portaria é o primeiro verbo operativo; os verbos não vistos nos cadernos saem sem tipo.

## Nova espécie

Para tratar uma espécie nova, defina uma `Especie` em `regras/especies.py` e inclua-a em `ESPECIES_ATIVAS`; as demais
etapas não precisam ser reescritas.

- A `Especie` reúne o que muda de um ato para outro: cabeçalho com o número, padrão do nome do arquivo, tipos, fecho,
  cargo de quem assina, frase que abre o corpo depois da ementa, ponto onde termina o cabeçalho (no decreto, o
  `DECRETA:`), lista de programas do escopo e inícios de parágrafo do PDF normalizado.
- Os campos opcionais podem ficar vazios: sem fecho, o separador não o procura; sem lista de programas, todo ato da
  espécie entra no escopo.
- As RegEx de cabeçalho e tipos ficam em `regras/regras_atos.py`, não em `especies.py`.
- `EDITAL` e `PORTARIA` já estão definidos (cabeçalho, órgão, tipo, `orgaos_do_escopo`), mas só entram em
  `ESPECIES_ATIVAS` por decisão da DBF. Hoje nenhum edital ou portaria é separado, gravado ou comparado.
- `tests/test_especie_generica.py` usa uma espécie fictícia para garantir que nenhuma etapa dependa de regra de decreto.

## Amostras de teste

Os PDFs de `tests/amostras/` são decretos já publicados pela CEPE (publicidade confirmada pelo responsável pelo projeto),
por isso podem ficar no repositório. Não adicione ali PDFs que não sejam públicos. Antes de incluir uma amostra nova,
confira as propriedades do PDF: arquivos antigos da CEPE (Word 2010) podem trazer o nome do autor nos metadados.
