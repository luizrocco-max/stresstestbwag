# Stress test de fundos

Sisteminha em Python para estimar quanto uma carteira de fundos (e cada fundo) perderia em
cenários de stress, usando só dados públicos: cotas diárias da CVM, CDI e PTAX do Banco Central,
Ibovespa e S&P 500 do Yahoo Finance e as curvas do Tesouro Direto.

## Como funciona

1. **Cotas**: baixa o informe diário da CVM (todos os fundos, um arquivo por mês, com cache local em
   `cache/`) e monta a série de cota de cada fundo da carteira pelo CNPJ.
2. **Fatores de risco** (painel diário, calendário de pregões do Ibovespa):

   | Fator | O que é | Choque em |
   |---|---|---|
   | `IBOV` | Ibovespa | % |
   | `SMLL` | Small caps (SMAL11, desde 2008; opcional) | % |
   | `SPX` | S&P 500 em USD | % |
   | `USDBRL` | Dólar PTAX | % |
   | `JURO_PRE` | Taxa do Tesouro Prefixado com vencimento ~2 anos | pontos percentuais |
   | `JURO_REAL` | Taxa do Tesouro IPCA+ com vencimento ~5 anos | pontos percentuais |
   | `SPREAD_CRED` | Spread de crédito privado sobre o CDI (IDEX-CDI ex-distressed, JGP; **mensal**, desde ago/2017; fora do padrão, entra só com `--fatores ...,SPREAD_CRED`) | pontos percentuais |

3. **Betas**: para cada fundo, regressão (OLS, erros HAC) do excesso de retorno diário sobre o CDI
   contra os fatores, na janela mais recente (padrão 24 meses). O beta de um fator de taxa é lido
   como "retorno do fundo por +100 bps" (um fundo prefixado de duration 2 tem beta ≈ -2%).
4. **Cenários** (`stresstest/cenarios.yaml`, ou um YAML seu via `--cenarios`):
   - **históricos**: janela de datas (ex.: COVID, 19/02 a 23/03/2020). Os choques dos fatores vêm dos
     dados reais. Se o fundo já existia, o sistema também apura o **retorno real da cota** no período.
   - **hipotéticos**: choques definidos à mão (ex.: `JURO_PRE: 2` = +200 bps, `IBOV: -20` = -20%).
5. **P&L**: fundo = Σ beta × choque (+ CDI do período). Carteira = soma ponderada pelos pesos.
   Com `--metodo melhor` (padrão), o fundo usa o retorno real quando ele existia no cenário e o modelo
   caso contrário; a aba `Resumo` mostra que fração da carteira tem histórico real em cada cenário.
6. **Saída**: resumo no terminal e um Excel com as abas `Resumo`, `Fundos x Cenarios`, `Detalhe`,
   `Betas`, `Estatisticas` (vol, drawdown, pior 21 dias, VaR histórico), `Cenarios` (choques),
   `Contribuicoes` (P&L da carteira por fator), `Fatores` e `Avisos`.

## Instalação

```bash
pip install -r requirements.txt
```

Python 3.10+. Precisa de acesso à internet para `dados.cvm.gov.br`, `api.bcb.gov.br`,
`query2.finance.yahoo.com` e `tesourotransparente.gov.br`.

## Uso

```bash
# 1) achar o CNPJ dos fundos (cadastro CVM pós-Resolução 175)
python -m stresstest buscar "spx nimitz"
python -m stresstest buscar "capitania premium"

# 2) montar a carteira (veja carteiras/exemplo.yaml)
# 3) rodar
python -m stresstest rodar carteiras/exemplo.yaml
python -m stresstest rodar carteiras/minha.yaml --saida saida/minha.xlsx --janela 36 --metodo modelo

# utilidades
python -m stresstest cenarios                              # choques de cada cenário nos fatores
python -m stresstest fatores --de 2020-02-19 --ate 2020-03-23   # movimento dos fatores numa janela
python -m stresstest atualizar --cotas                     # pré-baixa tudo (demorado na 1ª vez)
```

Formato da carteira (`carteiras/*.yaml`):

```yaml
nome: Carteira do cliente X
# data_base: 2026-08-29      # opcional: ignora dados após esta data
fundos:
  - {cnpj: 12.798.221/0001-36, nome: SPX Nimitz, peso: 15}
  - {cnpj: 20.146.318/0001-92, nome: Capitânia Premium, peso: 25}
caixa_cdi: 60               # peso em caixa / CDI
```

A primeira execução baixa os informes diários da CVM desde 2007 (~1,5 GB, alguns minutos); depois
fica em cache e só os meses recentes são atualizados. Para uma rodada rápida com histórico curto:
`--inicio 2019-01` (perde os cenários anteriores a essa data no modo histórico direto, mas o
modelo de fatores continua cobrindo todos).

## Painel interativo

`python -m stresstest exportar` calcula betas, estatísticas e retornos
históricos de todos os fundos de `stresstest/universo.yaml` (ou de um YAML seu passado como argumento) e gera `saida/painel.html`: uma página
única, sem servidor, em que você monta a carteira (fundos, pesos e caixa), vê o P&L por cenário,
clica num cenário para ver a quebra por fundo e por fator, e cria cenários próprios com sliders
de bolsa, S&P, dólar e juros. Para incluir um fundo novo, acrescente o CNPJ ao YAML e exporte de novo.

## Testes

```bash
python -m pytest -q
```

## Limitações e cuidados

- **Crédito privado / FIDC / fundos com cota suavizada**: a cota quase não reage a mercado, então os
  betas ficam perto de zero e o modelo subestima a perda. O cenário `americanas_2023` existe justamente
  para pegar isso pelo histórico direto. Não há fator de spread de crédito (não existe série pública).
- **Betas são lineares e da janela recente**: um gestor macro muda de posição; o beta de 24 meses é
  uma média. Compare sempre `modelo_total` com `historico` nos cenários em que o fundo existia.
- **Cenários históricos multi-fatoriais**: o P&L do modelo soma choques que na vida real ocorrem com
  correlação; para janelas longas (2021, 2022) a aproximação linear piora.
- Fundos exclusivos/fechados e fundos que trocaram de CNPJ na adaptação à RCVM 175 (raro) podem ter
  série incompleta; o relatório avisa quando a série não cobre um cenário.
- **`SPREAD_CRED` é mensal**: a JGP só publica de graça a média mensal do spread do IDEX-CDI (a série
  diária exige assinatura). A coluna diária do painel só varia na virada do mês, então serve para
  choques de cenário (soma das variações na janela) e para o proxy de debêntures por duration, mas
  **não** para regressão de betas diários: o motor avisa "beta pouco confiável" quando o fator é
  incluído. Janelas dentro de um mesmo mês (ex.: tarifas abr/2025) dão variação 0; janelas antes de
  ago/2017 dão NaN (o P&L trata como 0 e o resumo marca `fatores_sem_dado`). Usa-se a série
  ex-distressed porque a "geral" é dominada por emissores em default (jan/2023 marca 54% a.a.).
- As taxas do Tesouro Direto são as "da manhã" (refletem o fechamento do dia anterior); o painel já as
  desloca um pregão para trás, então o último dia do painel não tem variação de juros.
- Dados de mercado são de fontes públicas gratuitas, sujeitas a indisponibilidade. O cache local em
  `cache/` pode ser apagado a qualquer momento.

## Estrutura

```
stresstest/
  dados/cvm.py       cadastro + informe diário (cotas) com cache parquet
  dados/bcb.py       CDI e PTAX (SGS)
  dados/yahoo.py     Ibovespa, S&P 500, SMAL11
  dados/tesouro.py   taxas do Tesouro Direto -> juro pré/real de prazo constante
  fatores.py         painel de fatores e choque acumulado numa janela
  modelo.py          regressão dos betas e estatísticas de risco
  cenarios.py        leitura do YAML de cenários
  motor.py           carteira, P&L por fundo/cenário, agregação
  relatorio.py       resumo no terminal e Excel
  painel_web.py      exporta o painel interativo (usa painel_template.html)
  cli.py             comandos buscar / rodar / cenarios / fatores / atualizar
  cenarios.yaml      biblioteca de cenários (edite ou passe outro YAML)
  universo.yaml      universo de fundos do painel interativo
  api.py             API programática para outros sistemas (ver INTEGRACAO.md)
carteiras/exemplo.yaml   carteira de exemplo
```
