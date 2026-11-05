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
- Cabeçalhos e rodapés de página do Diário são removidos, e as ligaduras (`ﬁ cam`) são convertidas (`ficam`). Com isso,
  o texto do decreto 60929 extraído do caderno de 26/06/2026 é idêntico ao do PDF avulso da DBF.
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

### Etapa 3: duplicidade

- O arquivo é comparado com a pasta da DBF pelo nome `Decreto {numero}.pdf` (número sem separador de milhar). Se já
  existir, o PDF temporário é descartado.
- A base é tratada como pasta plana (sem subpastas). Maiúsculas e minúsculas não diferenciam nomes, porque a base fica
  no Windows.
- Um PDF inédito repetido no mesmo lote vai para revisão manual.

**Limitações:**

- Decretos republicados ou com errata têm o mesmo número do original. Pela regra atual (comparação estrita por número),
  a republicação seria tratada como duplicada e descartada.
- A checagem de pastas sobrepostas compara caminhos já resolvidos, sem diferenciar maiúsculas. A mesma pasta acessada por
  dois caminhos de natureza diferente (por exemplo, `G:` e um caminho de rede UNC) não é detectada.

### Etapa 4: normalização

Cada decreto inédito é recriado em `dados/normalizados/Decreto {numero}.pdf` com o layout dos decretos da DBF:

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

- Se alguém salvou o mesmo decreto durante o lote, o original é descartado como duplicado.
- Se o conteúdo gravado não conferir, o arquivo sai da base e o original vai para `dados/erros`.
- O `.part` é o único arquivo temporário na pasta sincronizada com o Drive e só existe durante a cópia.
- Falhas de acesso e o que fazer: [`uso.md`](uso.md#lote-etapas-2-a-5).

### Modelos e evidência dos tipos

- O modelo de cada tipo é o decreto indicado em `regras/especies.py`: concessão 60929, alteração 60951, prorrogação
  60950, renovação 60945, transferência 60505 e terceirização 60233. Segunda prorrogação/renovação usa o modelo da
  prorrogação/renovação.
- Alteração da norma do programa ("Introduz modificações no Decreto nº 21.959", "Modifica o Decreto nº 44.650"),
  revogação e PROIND ainda não têm modelo.
- Cada tipo e cada programa tem o campo `evidencia`: `amostra` (ato real da DBF em mãos), `publicado` (ato real publicado
  na Alepe ou na SEFAZ) ou `presumido` (deduzido da legislação, a confirmar). O padrão da ementa de segunda
  prorrogação/renovação é presumido: não há amostra real.

## Catálogo

Reconhece decretos, editais, portarias, erratas e republicações no texto dos cadernos. As RegEx ficam em
`regras/regras_atos.py`, só com a biblioteca padrão, para poder ir sozinho à equipe da CEPE: o cabeçalho do arquivo
explica o formato de texto esperado e `python regras_atos.py` confere a tabela de exemplos. `especies.py`, `separador.py`,
`programas.py` e a ferramenta de levantamento importam dele.

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
