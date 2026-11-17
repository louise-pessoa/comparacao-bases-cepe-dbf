# Plano: leitura por OCR (Tesseract) dos cadernos da CEPE e da base da DBF

Situação: **em implementação** na branch `claude/plano-ocr-tesseract`. Escrito em 16/11/2026.

| Fase | Situação |
|---|---|
| 0 — ambiente e levantamento | pronta e rodada na máquina da DBF (17/11/2026); resultado abaixo |
| 1 — `leitura/ocr.py`, configuração e cache | pronta |
| 2 — calibração | ferramenta pronta (`ferramentas/medir_ocr.py`); falta rodar nos cadernos de 2010 e 2020 |
| 3 a 6 | a fazer (ordem: 5, depois 3, 4 e 6) |

*Decisão de 16/11/2026:* as fases são feitas e testadas nesta branch, um commit por fase; o merge na `main` só acontece
depois de todas testadas (não há merge só do plano).

## Levantamento (fase 0), 17/11/2026

Cadernos baixados na máquina da DBF (`dados/historico` e `dados/cadernos`; os já apagados pela limpeza não entram):

| ano | cadernos | páginas | cadernos com página sem texto | digitalizadas | texto ilegível |
|---|---|---|---|---|---|
| 2010 | 346 | 6.746 | 1 | 0 | 1 |
| 2020 | 491 | 7.598 | 10 | 0 | 30 |
| 2021 | 35 | 372 | 0 | 0 | 0 |
| 2026 | 53 | 786 | 0 | 0 | 0 |

Base da DBF: 9.163 PDFs, dos quais 6.331 com texto, 2.152 digitalizados e 680 com texto ilegível (31% sem texto).

Conclusões:

- Nos cadernos, **nenhuma página é digitalizada**: o OCR atua nas páginas com **texto ilegível** (fonte sem conversão
  para Unicode). A página é desenhada normalmente, então a renderização + OCR funciona do mesmo jeito.
- **Lacuna atual:** o separador só avisa das páginas digitalizadas. Os atos das páginas ilegíveis somem sem aviso, e
  a limpeza (que confere só as digitalizadas) pode apagar esses cadernos. Ver a questão em aberto 4.
- Com `--ocr`, as 31 páginas foram lidas com confiança de 86 a 95 (800 a 3.100 palavras cada): são balanços e
  publicações de empresas (CHESF, COMPESA, JCPM), sem cabeçalho de decreto e longe dos decretos do caderno. **Nos
  cadernos baixados, o OCR dos cadernos (fase 3) não recupera nenhum decreto.**
- O volume que importa está na base. Por faixa de número, os PDFs sem texto ficam onde os cadernos processados
  caem: de 29.000 a 45.999 (≈2010 a 2018), 30 a 70 com texto ilegível por milhar; de 45.000 a 49.999 (≈2018 a 2020),
  23 a 145 em imagem por milhar. Abaixo de 29.000 (antes de 2010) quase tudo é imagem, mas esses números não
  aparecem nos cadernos. É aqui que hoje o decreto do caderno vira "já existe; não comparado" (fase 5).
- *Decisão de 17/11/2026 (questão 4):* a página com texto ilegível passou a contar como página sem texto no separador
  (aviso no `--separar` e no catálogo; a limpeza guarda o caderno).

## Objetivo e escopo

Hoje o projeto só **detecta** texto que falta e não o lê:

| Onde | O que acontece hoje | Com este plano |
|---|---|---|
| Páginas digitalizadas dentro do caderno | o separador avisa "páginas digitalizadas [...]: atos nelas não foram vistos" e o caderno nunca é apagado | as páginas são lidas por OCR e os atos delas passam por uma **cascata de OCR** própria; o aprovado segue o pipeline normal |
| PDF da base da DBF em imagem ou ilegível | `BASE_IMAGEM`: conta como já existente, sem comparar | o PDF da base é lido por OCR **só em memória** e comparado com o texto do caderno; similaridade alta = duplicata, abaixo vai para `conferir duplicata` |
| PDFs avulsos em `dados/leitura tesseract` | ficam parados para revisão manual | **fora do escopo** (decisão de 16/11/2026); continua como está |

Fora do escopo: OCR na nuvem (nenhum texto sai da máquina), análise semântica, mudar o layout do PDF normalizado.

## Decisões já tomadas (16/11/2026)

1. Escopo: páginas digitalizadas dos cadernos e PDFs em imagem da base da DBF.
2. Biblioteca: **pytesseract + PyMuPDF**. O PyMuPDF (já usado) renderiza a página; o pytesseract chama o Tesseract
   e devolve texto e confiança por palavra (`image_to_data`).
3. Ambiente: Windows, com o Tesseract provavelmente já instalado (a fase 0 confere).
4. Destino do ato lido por OCR: **segue o pipeline normal** (normalizado e gravado na base), desde que passe em todas as
   travas abaixo. Quem falha em qualquer trava vai para revisão manual.
5. Travas: cascata própria do OCR; confiança mínima; número conferido em duas leituras; marca "lido por OCR" no registro
   e no log; **lote piloto** em simulação antes de liberar a gravação automática.
6. Base em imagem: similaridade alta conta como duplicata; abaixo, `conferir duplicata`. O OCR **nunca** renomeia nem
   altera arquivo da base.
7. Amostras: ainda não se sabe em que anos há páginas digitalizadas; a fase 0 faz esse levantamento.

## Arquitetura

```
leitura/ocr.py           NOVO  renderiza a página (PyMuPDF), chama o Tesseract, devolve texto + confiança;
                               cache em disco; nenhuma regra de domínio
leitura/cascata_ocr.py   NOVO  níveis O1..O3 e as travas do OCR (usa criterio.py)
leitura/separador.py     muda  _montar_texto recebe um "leitor de página digitalizada" opcional
leitura/cascata.py       muda  ato com página de OCR passa pela cascata de OCR, não pelos níveis 2 e 3
pipeline/comparacao.py   muda  comparar_com_base faz OCR do PDF da base em imagem (opcional)
pipeline/republicacao.py muda  identico_ao_da_base passa a distinguir "igual por OCR"
pipeline/publicacao.py   muda  metadados do PDF de pendentes levam a origem da leitura (ocr, confiança, versão)
pipeline/registro.py     muda  campo "leitura" na entrada; VERSAO 2, lendo também a 1
pipeline/limpeza.py      muda  caderno com páginas digitalizadas pode ser apagado se todas foram lidas e resolvidas
config.py                muda  variáveis OCR_*, pasta do cache na checagem de pastas sobrepostas
cli.py                   muda  --verificar-ocr
ferramentas/levantar_digitalizadas.py  NOVO  fase 0
ferramentas/medir_ocr.py               NOVO  calibração dos limiares
```

O OCR é **opcional em tempo de execução**: sem Tesseract (ou com `OCR_ATIVO=nao`), o pipeline se comporta exatamente
como hoje. Isso mantém o CI e quem não tem o Tesseract funcionando, e permite desligar o OCR se algo der errado.

### `leitura/ocr.py`

- `ler_pagina(pagina, ajustes) -> PaginaOcr(texto, palavras[(texto, confiança, caixa)], confianca_media, versao)`.
- Renderização: `pagina.get_pixmap(dpi=OCR_DPI, colorspace=GRAY)`; imagem entregue ao pytesseract sem passar por
  arquivo do projeto.
- Tesseract: idioma `por`, `--oem 1` (LSTM), `--psm` conforme o nível da cascata (padrão 3: o 1 exigiria também o
  `osd.traineddata`). *Decisão de 17/11/2026:* `OCR_TESSDATA` permite usar uma pasta de idiomas fora da instalação,
  porque gravar em Program Files exige administrador. Versão do Tesseract e do `por.traineddata`
  lidas uma vez (`get_tesseract_version`) e guardadas em cada resultado.
- **Cache** em `dados/cache ocr/`: um JSON por (SHA-256 do PDF, página, nível, DPI, versão do Tesseract). O OCR de um
  caderno de 2010 leva segundos por página e a cascata relê o caderno; sem cache, o lote histórico ficaria lento. Gravação
  atômica (`.tmp` + `os.replace`, como o registro). Cache ilegível é apagado e refeito (é derivado, não é fonte de verdade).
- Erros do Tesseract (`TesseractNotFoundError`, `TesseractError`, timeout) viram `ErroOcr` com mensagem clara; a página
  fica como "digitalizada, não lida", igual a hoje.

### Integração com o separador (cadernos)

`_montar_texto` hoje pula a página digitalizada. Com o OCR, o texto dela entra **no lugar da página**, preservando
`inicios` (o início de cada página no texto). Assim um decreto que começa numa página de texto e termina numa página
digitalizada é separado inteiro, e a página do ato continua certa.

`AtoSeparado` ganha `paginas_ocr: tuple[int, ...]` e `confianca_ocr: float | None`. O separador continua sem regra do
OCR: só recebe o texto pronto.

### Cascata de OCR

O ato com qualquer página lida por OCR não passa pelos níveis 2 e 3 de hoje (eles releem o texto do PDF, que naquela
página não existe). Passa por níveis próprios, cada um uma nova leitura OCR da(s) página(s) do ato:

| Nível | Leitura | Para quê |
|---|---|---|
| O1 | 300 dpi, `--psm 3` (segmentação automática; detecta colunas) | caso comum |
| O2 | 400 dpi, pré-processamento: tons de cinza, binarização (Otsu), correção de inclinação | digitalização ruim ou torta |
| O3 | página cortada em colunas (faixas verticais em branco), cada coluna com `--psm 4`, em ordem | colunas misturadas pelo O1 |

Em cada nível o ato precisa passar no **critério de hoje** (`criterio.motivos_de_reprovacao`, aceitando os fechos
anteriores) **e** nas travas do OCR:

1. **Confiança:** média das palavras do ato ≥ `OCR_CONFIANCA_MEDIA` e nenhuma palavra do cabeçalho, da data e dos números
   citados na ementa abaixo de `OCR_CONFIANCA_CRITICA`. Valores iniciais propostos 85 e 70; definitivos na fase 2.
2. **Número confere 2x:** o número e a data do cabeçalho têm de sair iguais em duas leituras independentes (o nível que
   aprovou e o seguinte, ou O2 quando aprovou no O3), e serem plausíveis: data do ato ≤ data do caderno e número dentro
   da faixa dos decretos vizinhos do mesmo caderno (lidos por texto). Ver a questão em aberto 1.
3. **Caracteres suspeitos:** sem confusões típicas no número (`O`/`0`, `l`/`1`, `S`/`5`) e sem caracteres fora do
   alfabeto do Diário.

Aprovado: segue com `nivel_de_leitura = "O1"`/`"O2"`/`"O3"` e um aviso "lido por OCR (nível X, confiança Y); confira o
texto". Reprovado em todos: revisão manual, como hoje, com os motivos.

### Lote, registro e piloto

- `--separar --gravar` grava o ato aprovado em pendentes como hoje; os metadados do PDF (`publicacao.py`) ganham
  `"leitura": {"origem": "ocr", "nivel": ..., "confianca": ..., "tesseract": ...}`.
- O lote lê esses metadados e grava a mesma informação no registro (`Entrada.leitura`) e no `logs/pipeline.log`. Assim dá
  para listar e refazer tudo o que foi lido por OCR.
- **Piloto:** `OCR_GRAVAR_NA_BASE=nao` (padrão). Com `nao`, o ato lido por OCR que seria gravado vai para `dados/erros`
  com o motivo "lido por OCR; gravação automática desligada (piloto)". O `lote_historico` também para no mês que tiver
  ato de OCR. Depois do piloto conferido à mão, a variável vira `sim`.
- Relatório (`--separar`): coluna/indicação "OCR" e a confiança de cada ato.

### Base da DBF em imagem

- `comparar_com_base`: se a leitura do PDF da base der `IMAGEM` e o OCR estiver ativo, faz OCR (nível O1; O2 se a
  confiança for baixa) e compara com `comparar_textos`. O resultado ganha `por_ocr=True`.
- `planejar` (republicação), para arquivo da base sem registro:
  - OCR com similaridade ≥ `OCR_SIMILARIDADE_DUPLICATA` → **duplicata** (situação `duplicata`, `leitura: ocr da base`);
  - OCR abaixo do limiar → **conferir duplicata** (nunca grava nem renomeia por causa do OCR);
  - OCR falhou → regra de hoje (`BASE_IMAGEM`).
- O limiar não pode ser 100%: o OCR erra letras. Começa em 0,95 e é calibrado na fase 2 com os pares
  (texto do caderno, OCR do PDF da base) dos decretos de 2010 (34529, 34530, 34532, 34534).
- O `--conferir` mostra as diferenças também para a base lida por OCR.

### Limpeza dos cadernos

A condição 3 ("não tem página digitalizada") passa a ser: toda página digitalizada foi lida por OCR **e** nenhum ato
dela ficou em revisão manual. O manifesto anota as páginas lidas por OCR e a versão do Tesseract.

## Segurança

- **Base da DBF só leitura:** o OCR da base abre o PDF para leitura, faz tudo em memória e grava o cache **fora** da
  `PASTA_DBF`. `validar_configuracao` passa a incluir `PASTA_CACHE_OCR` na checagem de pastas iguais ou aninhadas.
- **Execução do Tesseract:** o pytesseract chama o binário por `subprocess` com lista de argumentos (sem shell). O
  caminho vem de `TESSERACT_CMD` no `.env`; o programa confere que é um arquivo existente chamado `tesseract`/
  `tesseract.exe`, e nunca monta argumentos com texto vindo do PDF.
- **Limites contra PDF malicioso ou defeituoso:** DPI máximo (600), número máximo de páginas de OCR por caderno, tamanho
  máximo da imagem (`Image.MAX_IMAGE_PIXELS` do Pillow mantido), `timeout` por página no pytesseract.
- **Arquivos temporários:** o pytesseract usa o diretório temporário do sistema; não deixa nada no projeto. Nada de dado
  da base é gravado em temporário compartilhado além do que o próprio Tesseract faz.
- **Dependências:** `pytesseract` e `Pillow` com versão fixada em `requirements.txt` (o Dependabot já cobre). O
  `por.traineddata` vem do repositório oficial `tesseract-ocr/tessdata_best` (ou `tessdata`), e o `--verificar-ocr`
  mostra o SHA-256 dele para conferência.
- **Logs:** registram número, página, nível e confiança; não o texto inteiro do ato.
- **Integridade:** a regra atual continua — o original só é descartado depois de gravado, conferido por SHA-256 e relido.

## Rede

O OCR é **todo local**: não há chamada de rede nova no pipeline. A rede só entra na instalação:

- `pip install` do `pytesseract` e do `Pillow`, pelo mesmo caminho de hoje (proxy em `docs/uso.md`);
- se o idioma português não estiver instalado, baixar o `por.traineddata` uma vez (pelo navegador ou com o proxy da
  rede), conferir o SHA-256 e copiar para a pasta `tessdata`.

Nenhum serviço de OCR externo: os textos da base da DBF não saem da máquina.

## Configuração (`.env.example`)

```
OCR_ATIVO=auto                  # auto: liga se achar o Tesseract; sim: exige; nao: desliga
TESSERACT_CMD=                  # ex.: C:\Program Files\Tesseract-OCR\tesseract.exe (em branco: procura no PATH)
OCR_IDIOMA=por
OCR_TESSDATA=                   # pasta com o por.traineddata, se não der para gravar na do Tesseract (sem admin)
OCR_DPI=300
OCR_CONFIANCA_MEDIA=85          # provisórios; definidos na fase 2
OCR_CONFIANCA_CRITICA=70
OCR_SIMILARIDADE_DUPLICATA=0.95
OCR_GRAVAR_NA_BASE=nao          # piloto: só vira "sim" depois da fase 6
PASTA_CACHE_OCR=dados/cache ocr
```

## Fases

Cada fase é um commit na branch `claude/plano-ocr-tesseract`, com testes e docs atualizados; a branch inteira vai
para a `main` num PR só, depois de testada.

### Fase 0 — ambiente e levantamento

- `python main.py --verificar-ocr`: acha o Tesseract, mostra versão, idiomas disponíveis (exige `por`), SHA-256 do
  `por.traineddata` e faz OCR de uma página de teste embutida. Sai com código ≠ 0 se faltar algo.
- `ferramentas/levantar_digitalizadas.py PASTA [--base]`: conta, por ano, cadernos e páginas digitalizadas (usando
  `_pagina_digitalizada`) e PDFs da base em imagem ou ilegíveis; lista os candidatos a amostra.
- **Entrega:** a tabela por ano entra em `funcionamento.md` e define se o OCR vale para todos os anos ou só para alguns.

### Fase 1 — `leitura/ocr.py`, configuração e cache

- Módulo, `ErroOcr`, cache, variáveis `OCR_*`, validação de `TESSERACT_CMD` e da pasta do cache.
- Testes unitários com o pytesseract **simulado** (monkeypatch), rodando sem Tesseract: chaves do cache, cache ilegível,
  timeout, Tesseract ausente com `OCR_ATIVO=auto` (desliga) e `sim` (erro).

### Fase 2 — calibração (`ferramentas/medir_ocr.py`)

- Gabarito sem trabalho manual: as páginas de **texto** dos cadernos já têm o texto certo. A ferramenta renderiza essas
  páginas, faz OCR e compara com o texto extraído, por ano, medindo acerto por palavra, acerto nos números e a relação
  entre confiança e erro. Isso define `OCR_CONFIANCA_MEDIA`, `OCR_CONFIANCA_CRITICA` e qual nível resolve o quê.
- Com as amostras da fase 0 (base em imagem), mede a similaridade de pares que são o mesmo decreto e de pares diferentes
  para escolher `OCR_SIMILARIDADE_DUPLICATA` com margem.

### Fase 3 — cadernos: separador e cascata de OCR

- `_montar_texto` com o leitor de página digitalizada; `AtoSeparado.paginas_ocr`; `leitura/cascata_ocr.py` com os níveis
  e as travas; avisos e relatório.
- Testes: caderno sintético (página de texto + página em imagem gerada a partir de um decreto real em `tests/amostras`)
  com o OCR simulado; ato atravessando a fronteira texto/imagem; cada trava reprovando sozinha.

### Fase 4 — pendentes, lote e registro

- Metadados de leitura no PDF de pendentes; `Entrada.leitura`; registro `VERSAO = 2` lendo a 1 sem perder nada;
  `OCR_GRAVAR_NA_BASE`; parada do `lote_historico`.
- Testes: registro v1 lido pela versão nova; ato de OCR com a gravação desligada vai para erros com o motivo.

### Fase 5 — base em imagem

- `comparar_com_base` com OCR; `planejar` com "igual por OCR"; `--conferir` mostrando as diferenças.
- Testes: PDF da base em imagem gerado a partir de amostra, acima e abaixo do limiar; garantia de que nada é renomeado
  nem gravado por causa do OCR da base.

### Fase 6 — limpeza, docs e piloto

- Limpeza, `README.md`, `uso.md`, `funcionamento.md`, `roteiro_de_testes.md`.
- **Piloto:** um mês com páginas digitalizadas (escolhido na fase 0), com `--simular` e `OCR_GRAVAR_NA_BASE=nao`;
  conferir cada ato lido por OCR contra a imagem. Só depois mudar para `sim` e anotar a decisão em `funcionamento.md`.

## Testes e CI

- Testes com OCR simulado rodam sempre (sem Tesseract).
- Testes com o Tesseract real ficam marcados `@pytest.mark.ocr` e são pulados quando o binário não existe.
- CI: um passo instala `tesseract-ocr tesseract-ocr-por` (apt) no job do Ubuntu e roda os testes `ocr`. O resultado do
  OCR muda com a versão do Tesseract, então esses testes conferem **campos** (número, data, tipo, fecho) e similaridade
  mínima, não o texto exato.
- Amostras novas (página digitalizada real, PDF da base em imagem) em `tests/amostras_ocr/`, só documentos já
  publicados, como as amostras atuais.

## Riscos

| Risco | Mitigação |
|---|---|
| Número ou data errados gravados na base | travas de confiança, número conferido em duas leituras, plausibilidade, piloto, marca no registro para refazer |
| Lentidão no lote histórico | cache por SHA-256, OCR só das páginas digitalizadas e só da base em imagem |
| Resultado diferente entre Windows e CI | versão do Tesseract no cache e no registro; testes por campos, não por texto exato |
| OCR da base dá "diferente" para o mesmo decreto | vai para `conferir duplicata` (decisão humana), nunca grava sozinho |
| Tesseract ausente ou quebrado | `OCR_ATIVO=auto` volta ao comportamento atual; `--verificar-ocr` |

## Questões em aberto

1. **Segunda fonte do número.** O plano usa duas leituras OCR independentes + plausibilidade pelos vizinhos do caderno.
   Se o Diário tiver um sumário/índice em texto com os números dos decretos, ele seria uma fonte melhor; confirmar na
   fase 0.
2. **Anos cobertos.** Depende do levantamento: se as páginas digitalizadas forem todas de antes de 2010, o OCR também
   depende da cascata antiga (fecho, colunas) e o piloto deve ser num desses anos.
3. **Onde fica o reprovado no piloto:** o plano usa `dados/erros`. Uma pasta própria (`dados/conferir ocr`) deixaria a
   revisão mais fácil, ao custo de mais uma pasta na limpeza e na validação.
4. ~~Páginas ilegíveis no separador~~: feito em 17/11/2026 (ver o levantamento).
5. **Ordem das fases:** com o levantamento, a fase 5 (base em imagem) traz o ganho real e a fase 3 (cadernos) quase
   nenhum nos cadernos baixados. Proposta: fazer a 5 antes da 3.
