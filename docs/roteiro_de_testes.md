# Roteiro de testes — o que conferir antes de aceitar uma mudança

Comandos em PowerShell, na raiz do repositório, com a `.venv` ativa (`.\.venv\Scripts\Activate.ps1`) e,
para não ter erro de acentuação no console do Windows:

```powershell
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = "src"   # para o python -c abaixo achar o pacote sem instalar
```

Os totais de referência (seção 6) valem para os **5 cadernos de referência**: 25/04, 26/06, 15/07, 21/10
e 22/10/2026. Com outros cadernos em `dados\cadernos`, os totais mudam; compare por caderno.

| Bloco | Se falhar |
|---|---|
| 1 a 4 | não aceitar a mudança |
| 5 | não enviar nada à CEPE |
| 6 e 7 | conferir se a diferença é esperada pela mudança |

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
Select-String -Path src\dbf_cepe\regras\regras_atos.py -Pattern 'G:\\', '@', 'sefaz\.pe', 'senha', 'password', 'proxy'
```

- [ ] A primeira linha não mostra nada (nenhum `.env`, `dados/`, checklist, `.zip` ou `.venv` versionado).
- [ ] As espécies ativas são só `['Decreto']`, a menos que a mudança seja justamente ativar outra.
- [ ] No `Select-String`, só aparecem `@dataclass` e `@property` em `regras_atos.py`.

## 3. Regressão do separador (o texto que vai para a comparação com a base)

```powershell
python -m ferramentas.regressao_separador main
```

- [ ] Todas as linhas dizem `IDÊNTICO` e o final é `tudo idêntico ao commit main: True`. Troque `main`
      pelo commit de antes da mudança, se estiver comparando com outro ponto.

## 4. Comparação com a base real (precisa do `.env` e da base da DBF)

```powershell
python main.py --separar dados\cadernos --comparar
```

- [ ] O mesmo resultado de antes da mudança (nos 5 cadernos de referência, 30/30 idênticos).

## 5. Módulo que vai para a CEPE, sozinho

```powershell
$d = Join-Path $env:TEMP "cepe_teste"
New-Item -ItemType Directory -Force $d | Out-Null
Copy-Item src\dbf_cepe\regras\regras_atos.py $d -Force
python -I "$d\regras_atos.py"
```

- [ ] Imprime `N exemplo(s) conferido(s): tudo certo.` (só com o arquivo copiado: sem PyMuPDF nem o
      resto do projeto).

## 6. Catálogo

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

## 7. Levantamento de formatos

```powershell
python -m ferramentas.localizar_exemplos
```

- [ ] `dados\exemplos\relatorio.txt` abre com `200 página(s)/trecho(s) para olhar.` e
      `Erratas por ato referido: Despacho 4, Portaria 3, ? 3, Edital 1, Decreto 1, Termo de fomento 1`.

**Se algo falhar:** guarde o comando, a saída completa e o número do item.
