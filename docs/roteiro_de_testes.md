# Roteiro de testes — o que conferir antes de aceitar uma mudança

Comandos em PowerShell, na raiz do repositório, com a `.venv` ativa (`.\.venv\Scripts\Activate.ps1`) e,
para não ter erro de acentuação no console do Windows:

```powershell
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = "src"   # para o python -c abaixo achar o pacote sem instalar
```

Os totais de referência (seção 5) valem para os **5 cadernos de referência**: 25/04, 26/06, 15/07, 21/10
e 22/10/2026. Com outros cadernos em `dados\cadernos`, os totais mudam; compare por caderno.

| Bloco | Se falhar |
|---|---|
| 1 a 4 | não aceitar a mudança |
| 5 a 7 | conferir se a diferença é esperada pela mudança |
| 8 | não rodar lote com republicação na base real |

## 1. Suíte e lint

```powershell
pytest -q
ruff check .
```

- [ ] Nenhum teste falhando; os únicos `skipped` são os que dependem do sistema operacional.
- [ ] `ruff check .` diz `All checks passed!`.

## 2. Segurança e escopo

```powershell
git ls-files | Select-String -Pattern "\.env$|^dados/|checklist|\.zip$|\.venv"
python -c "from dbf_cepe.regras.especies import ESPECIES_ATIVAS; print([e.nome for e in ESPECIES_ATIVAS])"
```

- [ ] A primeira linha não mostra nada (nenhum `.env`, `dados/`, checklist, `.zip` ou `.venv` versionado).
- [ ] As espécies ativas são só `['Decreto']`, a menos que a mudança seja justamente ativar outra.

## 3. Regressão do separador (o texto que vai para a comparação com a base)

```powershell
python -m ferramentas.regressao_separador main
```

- [ ] Todas as linhas dizem `IDÊNTICO` e o final é `tudo idêntico ao commit main: True`. Troque `main`
      pelo commit de antes da mudança, se estiver comparando com outro ponto.
- [ ] Se a mudança devia alterar algum decreto, rode de novo com `--detalhes` e confira, decreto a decreto, as linhas
      que saíram e entraram e os avisos que mudaram. O resumo final (`Mudanças: ...`) tem de bater com o esperado.

## 4. Comparação com a base real (precisa do `.env` e da base da DBF)

```powershell
python main.py --separar dados\cadernos --comparar
```

- [ ] O mesmo resultado de antes da mudança (nos 5 cadernos de referência, 30/30 idênticos).

## 5. Catálogo

```powershell
python main.py --catalogar dados\cadernos
$csv = Import-Csv dados\catalogo_atos.csv -Delimiter ';' -Encoding UTF8
$csv | Group-Object especie | Select-Object Name, Count
$csv | Group-Object { $_.situacao.Split(':')[0] } | Select-Object Name, Count
```

Referência (5 cadernos):

- [ ] 264 linhas: Decreto 73, Edital 38, Portaria 131, Errata 13, Republicação 9.
- [ ] Situação: `no_escopo` 45 (30 decretos, 14 editais da DBF e 1 portaria da SF), `fora_do_escopo`
      185 e `a_confirmar` 34.
- [ ] Editais da DBF: 13 `no_escopo: órgão DBF; LEI_ESPORTE` e o 017 `no_escopo: órgão DBF; PRODEAUTO`.
- [ ] Portarias da SF: só a 200 `no_escopo: órgão SF; PRODEPE`; as outras seis `fora_do_escopo`.
- [ ] Com `--com-base`, as erratas de decreto e edital deixam de dizer `(base não consultada: use --com-base)`.
- [ ] Somente leitura: em `dados\` a única novidade é o `catalogo_atos.csv`.

## 6. Levantamento de formatos

```powershell
python -m ferramentas.localizar_exemplos
```

- [ ] `dados\exemplos\relatorio.txt` abre com `200 página(s)/trecho(s) para olhar.` e
      `Erratas por ato referido: Despacho 4, Portaria 3, ? 3, Edital 1, Decreto 1, Termo de fomento 1`.

## 7. Critério da leitura em cascata

```powershell
python -m ferramentas.medir_criterio dados\cadernos
```

- [ ] Nos 5 cadernos de referência, a linha `2026` mostra `73` decretos, `0` reprovados e `0` da DBF reprovados.

## 8. Republicação, em pastas de teste (nunca no G:)

Os decretos 47.206 e 47.207 (PRODEPE) saíram em 19/03/2019 e de novo em 22/03/2019. As variáveis abaixo valem só para
esta janela do PowerShell e apontam tudo para `dados\validacao`; feche a janela no fim.

```powershell
$v = "dados\validacao"
Remove-Item -Recurse -Force $v -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$v\dbf" | Out-Null
Copy-Item "tests\amostras\Decreto 60233.pdf" "$v\dbf"
$env:PASTA_DBF = "$PWD\$v\dbf"; $env:PASTA_TEMP = "$v\pendentes"; $env:PASTA_ERROS = "$v\erros"
$env:PASTA_OCR = "$v\ocr"; $env:PASTA_NORMALIZADOS = "$v\normalizados"; $env:PASTA_LOGS = "$v\logs"
$env:PASTA_CONFERIR_DUPLICATA = "$v\conferir duplicata"; $env:REGISTRO_PUBLICACOES = "$v\registro_publicacoes.json"
$env:PASTA_CADERNOS = "$v\cad19"; python main.py --baixar --fonte s3 --de 2019-03-19 --ate 2019-03-19
$env:PASTA_CADERNOS = "$v\cad22"; python main.py --baixar --fonte s3 --de 2019-03-22 --ate 2019-03-22
python main.py --separar "$v\cad22" --gravar; python main.py
python main.py --separar "$v\cad19" --gravar; python main.py
Get-ChildItem "$v\dbf" | Select-Object Name
python main.py --separar "$v\cad19" --gravar
```

- [ ] O segundo `--separar` mostra `ANTERIOR À DA BASE` para 47206 e 47207, e o lote seguinte registra
      `renomeado na DBF: Decreto 47206.pdf -> Decreto 47206 (republicação).pdf` (o mesmo para o 47207).
- [ ] A pasta `dbf` termina com `Decreto 47206.pdf`, `Decreto 47206 (republicação).pdf`, `Decreto 47207.pdf` e
      `Decreto 47207 (republicação).pdf`, além dos outros decretos da DBF desses dois dias.
- [ ] No `registro_publicacoes.json`, `Decreto 47206.pdf` tem `data_publicacao` 2019-03-19, o `(republicação)` tem
      2019-03-22, e `operacao_em_andamento` é `null`.
- [ ] O último `--separar` diz `já registrado (mesma publicação)` para os dois.
- [ ] Nada mudou no G: nem em `dados\cadernos`.

Os textos de 22/03/2019 vêm cortados (problema de leitura dos cadernos anteriores a 2020), por isso eles diferem do
original e viram republicação; o teste serve para conferir os nomes, não o conteúdo.

**Se algo falhar:** guarde o comando, a saída completa e o número do item.
