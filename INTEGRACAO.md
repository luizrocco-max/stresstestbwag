# Integrando o stress test em outro sistema

Este documento é para quem vai plugar o stress test de fundos em outro app (por exemplo, a aba
"Stress Test" de um sistema de comitê de investimentos). Leia inteiro antes de começar.

## O que o pacote faz

Dado um conjunto de fundos (CNPJ) e pesos, o pacote:

1. baixa as cotas diárias da CVM e monta a série de cada fundo (cache local em parquet);
2. estima, por fundo, betas do excesso de retorno sobre o CDI contra 5 fatores de mercado
   (Ibovespa, S&P 500, dólar PTAX, juro pré ~2 anos, juro real ~5 anos), janela de 24 meses;
3. aplica cenários históricos (2008, COVID, Joesley, fiscal 2021/2024, ...) e hipotéticos
   (bolsa -20%, juros +200 bps, ...), usando o retorno real da cota quando o fundo já existia;
4. devolve P&L por fundo e por carteira, contribuição por fator, estatísticas de risco e avisos;
5. opcionalmente gera um Excel e uma página HTML interativa autocontida (sem servidor).

Fontes: CVM (`dados.cvm.gov.br`), Banco Central (`api.bcb.gov.br`), Tesouro Direto
(`tesourotransparente.gov.br`) e Yahoo Finance (`query2.finance.yahoo.com`). O ambiente
que roda o cálculo precisa alcançar esses hosts.

## Instalação

```bash
pip install "git+https://github.com/luizrocco-max/stresstestbwag@claude/fundos-stress-test-system-rjjtos"
# ou, clonando: pip install -e .
```

Python 3.10+. Dependências: pandas, numpy, statsmodels, pyarrow, openpyxl, requests, pyyaml.

Variáveis de ambiente úteis:

| Variável | Uso |
|---|---|
| `STRESSTEST_CACHE` | pasta do cache de dados (padrão: `cache/` ao lado do pacote). Aponte para um volume persistente. |
| `STRESSTEST_SAIDA` | pasta padrão de saída de relatórios |

Primeira execução: baixa ~1,5 GB de informes diários da CVM (2007 em diante), 5 a 15 minutos.
Depois, só os dois meses mais recentes são reverificados (a cada 20 h). Para aquecer o cache
num deploy: `python -m stresstest atualizar --cotas`. Para um cache menor, use
`inicio_dados="2015-01"` (perde o retorno real dos cenários anteriores; o modelo continua cobrindo todos).

## API Python (`stresstest.api`)

```python
from stresstest import api

# 1) resolver fundos da sua base para CNPJ (se ela não tiver CNPJ)
api.buscar_fundos("spx nimitz")          # -> [{"cnpj": "12.798.221/0001-36", "nome": ..., "gestor": ..., "pl": ...}, ...]
api.info_fundo("12.798.221/0001-36")     # -> cadastro; "encontrado": False se não estiver na CVM

# 2) stress de uma carteira (pesos em %)
res = api.stress_carteira(
    posicoes=[{"cnpj": "12.798.221/0001-36", "peso": 30, "nome": "SPX Nimitz"},
              {"cnpj": "73.232.530/0001-39", "peso": 30, "nome": "Dynamo Cougar"}],
    caixa_cdi=40,
    metodo="melhor",          # "melhor" | "modelo" | "historico"
    janela_meses=24,
    inicio_dados="2007-06",   # primeiro mês de cotas carregado
    cenarios=None,            # caminho de um YAML próprio, ou None para a biblioteca embutida
)
```

`res` é um dict JSON-serializável com estas chaves:

| Chave | Conteúdo |
|---|---|
| `carteira` | nome e posições normalizadas (peso em fração) |
| `fatores` | id, nome, tipo (`retorno`/`taxa`) e unidade de cada fator |
| `cenarios` | uma linha por cenário: `id`, `cenario`, `tipo`, `inicio`, `fim`, `n_dias`, choque de cada fator, `CDI` do período |
| `resultado_carteira` | por cenário: `pl_carteira` (usado), `pl_modelo`, `cdi_periodo`, `peso_com_historico_real`, `pior_posicao`, `pl_pior_posicao` |
| `contribuicoes` | por cenário: P&L da carteira atribuído a cada fator + CDI (modelo) |
| `fundos` | cadastro de cada fundo (nome, gestor, classe) e peso |
| `betas` | por fundo: betas, `t_<fator>`, `r2`, `alpha_anual`, `vol_residual_anual`, `n_obs`, janela |
| `estatisticas` | por fundo: `vol_anual`, `max_drawdown`, `pior_dia`, `pior_21d`, `var95_21d`, `es95_21d`, `ret_12m`, `ret_36m`, `sharpe_12m`, início/fim da série |
| `risco` | VaR/ES da carteira (ver abaixo) |
| `detalhe` | uma linha por fundo x cenário: `modelo_excesso`, `cdi`, `modelo_total`, `historico`, `usado`, `fonte` (`histórico`/`modelo`/`cdi`) |
| `avisos` | strings: R² baixo, histórico curto, fundo sem cotas, pesos normalizados... |

Unidades: retornos e P&L em fração (-0.127 = -12,7%). Beta de fator de retorno = retorno do
fundo por 1% do fator. Beta de fator de taxa = retorno (fração) por +1 p.p.; multiplique por 100
para ler "% por +100 bps" (um prefixado de duration 2 dá ~-2).

```python
# 3) Excel com o mesmo conteúdo (abas Resumo, Fundos x Cenarios, Detalhe, Betas, ...)
api.excel(posicoes, "saida/stress_cliente.xlsx", caixa_cdi=40)

# 4) painel interativo para um universo de fundos (a página que vira a "aba")
dados = api.painel_universo(
    fundos=[{"cnpj": "12.798.221/0001-36", "nome": "SPX Nimitz", "grupo": "Multimercado"},
            {"cnpj": "73.232.530/0001-39", "nome": "Dynamo Cougar", "grupo": "Ações"}],
    exemplo={"caixa_cdi": 40, "fundos": [{"cnpj": "12.798.221/0001-36", "peso": 30},
                                        {"cnpj": "73.232.530/0001-39", "peso": 30}]},
)
api.painel_html(dados, "static/stress.html")
```

Linha de comando equivalente: `python -m stresstest rodar carteira.yaml`, `python -m stresstest
exportar universo.yaml`, `python -m stresstest buscar "nome"`.

## Como virar uma aba

Três jeitos, do mais simples ao mais integrado:

**A. Página pronta (iframe).** Um job (diário ou sob demanda) chama `painel_universo` com os
fundos da sua base e `painel_html` grava o arquivo em `static/`. A aba é um `<iframe>` ou
uma rota que serve esse HTML. A carteira montada na página fica no `localStorage` do navegador.
Zero acoplamento; recalcula betas só quando o job roda.

**B. Painel + dados do seu app.** O template `stresstest/painel_template.html` lê um JSON de um
`<script id="dados" type="application/json">`. Você pode servir o JSON por uma rota
(`GET /api/stress/universo`) e injetá-lo no template no servidor ou no cliente; e pode
pré-carregar a carteira do cliente selecionado gravando `localStorage["stress-carteira"] =
{"fundos":[{"cnpj":"12.798.221/0001-36","peso":30}], "caixa":40}` antes de abrir a página,
ou trocando o bloco `exemplo` do JSON pela carteira do cliente.

**C. Backend próprio.** Rota `POST /api/stress` que recebe `{posicoes, caixa_cdi}` e devolve
`api.stress_carteira(...)`; o front do seu app desenha o que quiser. É o caminho se a UI já
existe e você quer só os números. O cálculo por carteira leva ~10 s a frio (leitura do cache
de cotas) e < 1 s quando `painel` e `cotas` já estão em memória; para volume, mantenha um
processo com o painel de fatores carregado (`stresstest.fatores.painel()`) e passe-o para
`stresstest.motor.rodar(..., painel=painel, cotas=cotas)`.

Se o front for JavaScript e não houver Python no servidor, use A ou B: rode o exportador como
CLI num job e sirva o HTML/JSON estático.

## Mapeando a sua base de fundos

- A chave é o **CNPJ do fundo/classe** (14 dígitos; pontuação é ignorada). Fundos que mudaram de
  nome mantêm o CNPJ, e a série histórica é contínua antes e depois da Resolução CVM 175.
- Se sua base só tem nome, use `api.buscar_fundos(nome)` uma vez, guarde o CNPJ escolhido e
  revise manualmente: nomes ambíguos (master, feeder, previdência, "advisory") devolvem vários
  candidatos. Prefira o veículo que o cliente de fato investe; masters têm histórico mais longo.
- Fundos fora da CVM (offshore, FIDC fechado sem cota diária, previdência de seguradora sem
  informe) não têm cotas: o pacote avisa e trata a posição como CDI. Para eles, ou se aceita a
  aproximação ou se passa um proxy (outro CNPJ com o mesmo mandato).
- FIIs e ETFs listados não estão no informe diário; ficam fora deste modelo.

## Limitações que precisam aparecer na UI

- Crédito privado e FIDC têm cota suavizada: betas ~0 e o modelo subestima a perda. Mostre o
  aviso de R² baixo e prefira cenários históricos com retorno real (Americanas 2023).
- Betas são lineares e da janela recente (24 meses); gestor macro muda de posição.
- Não há fator de spread de crédito nem de FII (sem série pública diária). Se o app tiver
  acesso a Economatica/ANBIMA, esses fatores podem ser adicionados em `stresstest/fatores.py`
  (uma coluna a mais no painel e uma entrada em `FATORES`).
- É simulação quantitativa, não recomendação de investimento; o rodapé do painel já diz isso.

## Risco da carteira: `res["risco"]`

Calculado em `stress_carteira` (e em `motor.rodar`, campo `Resultado.risco`). Parâmetros de `rodar`:
`risco_horizonte=21`, `risco_niveis=(0.95, 0.99)`, `risco_janela_anos=3`.

| Chave | Conteúdo |
|---|---|
| `resumo` | uma linha por método (`histórico` / `paramétrico`) x horizonte (1 e 21 pregões) x nível: `var`, `es` (fração, positivo = perda), `n_obs` |
| `contrib_fundos` | por posição (inclui `Caixa / CDI`): `peso`, `var_isolado_hist`, `contrib_es_hist` (soma = ES histórico), `var_isolado_param`, `contrib_var_param` (soma = VaR paramétrico), `fracao_risco_param` |
| `contrib_fatores` | paramétrico: `contrib_var_param` e `fracao` da variância por fator + `residual` |
| `correlacao` | matriz de correlação dos retornos diários dos fundos na janela histórica |
| `info` | `hist_ok`, `param_ok`, `hist_janela`, `hist_n_obs`, `vol_anual_hist`, `vol_anual_param`, `pior_h_hist`, `var_hist`, `var_param`, `soma_var_isolado_*`, `betas_carteira` |
| `avisos` | histórico comum curto, peso sem histórico tratado como CDI etc. |

Métodos: **histórico** = pesos atuais aplicados aos retornos reais dos fundos numa janela comum de 3 anos,
retorno de h pregões composto fundo a fundo (buy-and-hold), VaR = percentil, ES = média da cauda;
**paramétrico** = covariância dos fundos `B Σ_f Bᵀ + diag(σ_resid²)` com os betas do stress e a
covariância diária dos fatores nos mesmos 3 anos, VaR = z·σ·√h, ES = σ√h·φ(z)/(1-α), contribuições pela
decomposição da variância. O painel interativo (`painel_template.html`) calcula os dois no navegador a
partir do JSON (`risco.cov_fatores`, `risco.cdi`, `risco.datas` e, por fundo, `vol_resid` e `ret3a`).

Limitações: normalidade no paramétrico (subestima cauda); crédito privado com cota suavizada tem
risco baixo nos dois métodos; a janela histórica exclui fundos mais jovens que ela.

## Fator opcional `SPREAD_CRED` (spread de crédito privado)

Declarado em `FATORES` mas fora de `FATORES_PADRAO`: os betas e choques dos fatores padrão não
mudam quando ele não é pedido. Para usá-lo: `stress_carteira(..., fatores=FATORES_PADRAO + ["SPREAD_CRED"])`
ou `--fatores IBOV,SPX,USDBRL,JURO_PRE,JURO_REAL,SPREAD_CRED` na CLI.

| Item | Valor |
|---|---|
| Fonte | IDEX-CDI **ex-distressed** da JGP (Idex Analytics), JSON público do gráfico "Evolução dos spreads de carrego", via o proxy do próprio site (`idexanalytics.com.br/wp-json/idex/v1/proxy`), sem chave |
| Série | spread médio ponderado das debêntures %CDI/DI+ sobre o CDI, em % a.a.; **média mensal**, datada no dia 1º do mês |
| Início | ago/2017 (a série "geral" começa em jan/2019 e é contaminada por emissores em default) |
| Coluna do painel | nível preenchido para frente dentro do mês; `diff` diário em p.p. (só varia na troca de mês); NaN antes de ago/2017 |
| Conversão | nenhuma: o spread já vem em p.p. (não foi preciso o caminho IDA-DI/duration) |
| Choque | +1 p.p. de spread em papel de duration D ≈ -D% no preço, mesma regra dos juros |
| Sem série na janela | `choques` traz NaN, o P&L usa 0 e `resultado_carteira[*].fatores_sem_dado` lista o fator |
| Regressão | aceita o fator, mas com série mensal o beta diário é pouco confiável; o aviso vem em `avisos` |
| Fonte fora do ar | coluna NaN com warning no log; o painel não cai (cache de 20 h, reaproveitado se a fonte falhar) |

## Cenários próprios

YAML com a mesma estrutura de `stresstest/cenarios.yaml`:

```yaml
historicos:
  - {id: covid_2020, nome: COVID-19, inicio: 2020-02-19, fim: 2020-03-23, descricao: ...}
hipoteticos:
  - {id: juros_300, nome: Curva +300 bps, choques: {JURO_PRE: 3.0, JURO_REAL: 2.0}}
```

Retorno em % (`IBOV: -20`), taxa em pontos percentuais (`JURO_PRE: 2`). Passe o caminho em
`cenarios=` ou `--cenarios`.

## Estrutura do pacote

```
stresstest/api.py            <- comece por aqui
stresstest/motor.py          carteira, P&L por fundo/cenário, agregação
stresstest/modelo.py         regressão dos betas, estatísticas de risco
stresstest/fatores.py        painel diário de fatores
stresstest/cenarios.py       leitura dos cenários (cenarios.yaml)
stresstest/dados/            cvm.py, bcb.py, yahoo.py, tesouro.py (todos com cache)
stresstest/painel_web.py     exporta o painel interativo (painel_template.html)
stresstest/relatorio.py      Excel
```
