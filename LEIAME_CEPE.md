# regras_atos.py — reconhecimento de atos no texto do Diário Oficial

Um único arquivo Python, **só com a biblioteca padrão** (Python 3.10 ou mais novo, nenhuma instalação).
Reconhece, no texto de um caderno do Diário Oficial de Pernambuco, **decretos, editais, portarias,
erratas e republicações**. Cada ato sai com espécie, número, ano, órgão, tipo, data do ato e página.

## Como rodar

```bash
python regras_atos.py          # confere a tabela EXEMPLOS; diz "tudo certo" ou lista as divergências
```

```python
from regras_atos import identificar

for a in identificar(texto_do_caderno, inicios_de_pagina):   # inicios_de_pagina é opcional
    print(a.especie, a.numero, a.ano, a.orgao, a.tipo, a.data_do_ato, a.pagina, a.avisos)
```

## Formato de texto esperado

- Texto **extraído do PDF** (não imagem), com **uma linha do Diário por linha** do texto. As quebras do PDF ficam como estão.
- O **cabeçalho do ato** (`DECRETO Nº 60.929, DE 25 DE JUNHO DE 2026`, `EDITAL DBF Nº 063/2026`,
  `PORTARIA SF Nº 76, DE 24.04.2026`) está **no início de uma linha e em MAIÚSCULAS**. Citações no corpo do texto
  ("o Decreto nº 21.959, de ...") nunca contam.
- As ligaduras do Diário ("beneﬁ ciário") são convertidas por `limpar_texto`, que `identificar` já chama.

## O que cada achado traz

| Espécie | Chave | Observações |
|---|---|---|
| Decreto | número, ano, tipo (11 tipos) | tipo pelo **início da ementa**; a ordem dos tipos importa ("segunda prorrogação" antes de "prorrogação") |
| Edital | número/ano, órgão (`DBF`, `DBM`...), tipo (credenciamento, intimação...) | o cabeçalho da DBF **não traz data**: ela é lida da linha `Recife, 21 de outubro de 2026.` do fecho (vira aviso) |
| Portaria | número/ano, órgão (`SF` = Secretaria da Fazenda), tipo | tipo = primeiro verbo operativo depois de `RESOLVE:` |
| Errata | **ato referido** (espécie, número, ano) e **destino**: `decreto`, `edital`, `portaria` ou `outro` | não tem número próprio |
| Republicação | marcador `(REPUBLICADO POR HAVER SAÍDO COM INCORREÇÃO NO ORIGINAL)` ligado ao ato certo | `republicacao` = `sim` (logo após o fecho, ou em título acima do edital) ou `provavel`; sem ato associado, sai como achado `Republicação` com aviso |

Nada some em silêncio: cabeçalho sem número, órgão ausente, data que não está no cabeçalho, possível citação quebrada em
duas linhas e marcador de republicação sem ato aparecem em `avisos`. `sefaz` é `True`/`False` pela sigla do órgão
(edital `DBF`/`DBM`, portaria `SF`) e `None` quando o cabeçalho não diz o órgão. Nesse caso, o título em maiúsculas
logo acima (ex.: `SECRETARIA DE ADMINISTRAÇÃO`) é lido como `orgao_do_titulo`: pista de baixa confiança, que só
decide `sefaz`; o campo `orgao` continua vazio.

## O que ainda falta (pendências conhecidas)

- **Amostra real:** `DBM` foi informado como outro nome da DBF, mas não apareceu em nenhum caderno; edital de
  **descredenciamento** da DBF também não (tipo marcado como `presumido`).
- **Portaria:** só os tipos vistos nos cadernos (designação, dispensa, exoneração, concessão, prorrogação, substituição,
  alteração, perda de incentivo); os demais saem com `tipo = None` ("a confirmar").
- **Órgão pelo título da seção**: vale até 8 linhas acima e para os atos seguintes do mesmo bloco. Quando o título está
  longe, o rótulo pode sair errado (há aviso no achado); por isso ele nunca entra em `orgao`.
- **Páginas digitalizadas** (imagem, sem texto) não são cobertas: precisam de OCR antes.
- A ligação de republicação só alcança decreto, edital e portaria; rodapés de outras espécies (instrução normativa, lista de
  atos do Governador, resolução) voltam como achados `Republicação` com aviso.

## O que devolver

1. Se vocês já têm uma **fonte estruturada** (API, exportação com espécie, número, data e texto de cada ato), avisem: isso
   dispensa as RegEx.
2. A saída de `python regras_atos.py` e uma lista dos **cabeçalhos que não foram reconhecidos** (página e trecho curto).
3. **Casos novos** para a tabela `EXEMPLOS` do arquivo (trecho curto de entrada e o resultado esperado).
4. Se existir, o **código de reconhecimento** que vocês usam hoje ou um **catálogo** dos atos da Secretaria da Fazenda no
   período (espécie, número, ano, órgão, tipo, data do ato, data de publicação, caderno, página).
