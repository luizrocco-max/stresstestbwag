"""Painel diário de fatores de risco.

Fatores de RETORNO (choque em %): IBOV, SMLL, SPX (em USD), USDBRL (PTAX).
Fatores de TAXA (choque em pontos percentuais): JURO_PRE (~2 anos), JURO_REAL (~5 anos) e
SPREAD_CRED (spread de crédito privado sobre o CDI, IDEX-CDI ex-distressed da JGP; série MENSAL
desde ago/2017 — a coluna diária só varia na troca de mês; NaN antes do início; fora do padrão).
Coluna CDI: taxa diária (fração) usada como taxa livre de risco.

O calendário do painel é o de pregões do Ibovespa. Séries de outros calendários
(S&P 500, PTAX, Tesouro) são preenchidas para frente e convertidas em retorno/variação
nesse calendário, de modo que um feriado brasileiro absorve o movimento acumulado lá fora.
As taxas do Tesouro Direto (publicadas de manhã) são deslocadas um pregão para trás, para
casar com o fechamento; por isso o último pregão do painel fica sem variação de juros.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .dados import bcb, idex, tesouro, yahoo

log = logging.getLogger(__name__)

FATORES = {
    "IBOV": dict(tipo="retorno", nome="Ibovespa", unidade="%"),
    "SMLL": dict(tipo="retorno", nome="Small caps (SMAL11, desde 2008)", unidade="%"),
    "SPX": dict(tipo="retorno", nome="S&P 500 em USD", unidade="%"),
    "USDBRL": dict(tipo="retorno", nome="Dólar PTAX (BRL por USD)", unidade="%"),
    "JURO_PRE": dict(tipo="taxa", nome="Juro pré ~2 anos (Tesouro Prefixado)", unidade="p.p."),
    "JURO_REAL": dict(tipo="taxa", nome="Juro real ~5 anos (Tesouro IPCA+)", unidade="p.p."),
    "SPREAD_CRED": dict(tipo="taxa", nome="Spread de crédito privado sobre o CDI", unidade="p.p."),
}
# SPREAD_CRED fica fora do padrão de propósito: só entra quando pedido (fatores=[..., "SPREAD_CRED"]).
FATORES_PADRAO = ["IBOV", "SPX", "USDBRL", "JURO_PRE", "JURO_REAL"]
PRAZO_PRE_ANOS = 2.0
PRAZO_REAL_ANOS = 5.0


def _no_calendario(s: pd.Series, cal: pd.DatetimeIndex) -> pd.Series:
    """Reindexa `s` (nível) no calendário `cal` preenchendo para frente."""
    return s.reindex(cal.union(s.index)).ffill().reindex(cal)


def niveis(inicio: str = "2004-01-01") -> pd.DataFrame:
    """Níveis diários (índices, câmbio e taxas) no calendário do Ibovespa."""
    ibov = yahoo.historico(yahoo.TICKERS["IBOV"], inicio)
    ibov = ibov[ibov.index >= pd.Timestamp(inicio)]
    cal = ibov.index
    spx = yahoo.historico(yahoo.TICKERS["SPX"], inicio)
    smll = yahoo.historico(yahoo.TICKERS["SMLL"], inicio)
    try:
        usd = bcb.ptax(inicio)
    except Exception as e:  # noqa: BLE001
        log.warning("PTAX indisponível no BCB (%s); usando BRL=X do Yahoo", e)
        usd = yahoo.historico(yahoo.TICKERS["USDBRL_YAHOO"], inicio)
    cdi = bcb.cdi_diario(inicio)
    pre = tesouro.taxa_prazo_constante(tesouro.TIPOS_PRE, PRAZO_PRE_ANOS)
    real = tesouro.taxa_prazo_constante(tesouro.TIPOS_REAL, PRAZO_REAL_ANOS)

    df = pd.DataFrame(index=cal)
    df.index.name = "data"
    df["IBOV"] = ibov
    df["SMLL"] = _no_calendario(smll, cal)
    df["SPX"] = _no_calendario(spx, cal)
    df["USDBRL"] = _no_calendario(usd, cal)
    df["CDI"] = _no_calendario(cdi, cal)
    # Taxas do Tesouro Direto são "da manhã": a taxa publicada no dia t reflete o fechamento
    # de t-1. Deslocamos um pregão para trás para alinhar com a cota (fechamento) do fundo.
    # Nível "sintético": soma das variações dentro de cada papel (sem saltos na troca de título).
    df["JURO_PRE"] = _no_calendario(pre["taxa"], cal).shift(-1)
    df["JURO_REAL"] = _no_calendario(real["taxa"], cal).shift(-1)
    df["_JURO_PRE_SINT"] = _no_calendario(pre["dtaxa"].cumsum(), cal).shift(-1)
    df["_JURO_REAL_SINT"] = _no_calendario(real["dtaxa"].cumsum(), cal).shift(-1)
    # Spread de crédito (IDEX-CDI, mensal): nível em % a.a. preenchido para frente dentro do mês;
    # NaN antes do início da série. Fonte fora do ar => coluna NaN com aviso, sem derrubar o painel.
    try:
        df["SPREAD_CRED"] = _no_calendario(idex.spread_mensal(), cal)
    except Exception as e:  # noqa: BLE001
        log.warning("SPREAD_CRED indisponível (%s); coluna fica NaN", e)
        df["SPREAD_CRED"] = np.nan
    return df


def painel(inicio: str = "2004-01-01") -> pd.DataFrame:
    """Retornos diários (fatores de retorno), variações em p.p. (fatores de taxa) e CDI diário."""
    nv = niveis(inicio)
    p = pd.DataFrame(index=nv.index)
    for f in ("IBOV", "SMLL", "SPX", "USDBRL"):
        p[f] = nv[f].pct_change()
    p["JURO_PRE"] = nv["_JURO_PRE_SINT"].diff()
    p["JURO_REAL"] = nv["_JURO_REAL_SINT"].diff()
    p["SPREAD_CRED"] = nv["SPREAD_CRED"].diff()
    p["CDI"] = nv["CDI"]
    p = p.iloc[1:]
    p = p.replace([np.inf, -np.inf], np.nan)
    return p


def choque_janela(painel_: pd.DataFrame, inicio, fim, fatores: list[str] | None = None) -> dict:
    """Movimento acumulado dos fatores do fechamento de `inicio` ao fechamento de `fim`.

    Retorno: dict {fator: choque} (fração para retorno, p.p. para taxa), mais
    'CDI' (retorno acumulado do CDI) e 'n_dias' (pregões na janela)."""
    ini, fm = pd.Timestamp(inicio), pd.Timestamp(fim)
    jan = painel_[(painel_.index > ini) & (painel_.index <= fm)]
    fatores = fatores or [f for f in FATORES if f in painel_.columns]
    out = {}
    for f in fatores:
        col = jan[f].dropna()
        # sem dado no início da janela (série ainda não existia) => choque indefinido, nunca zero
        if col.empty or (f in jan.columns and len(jan) and pd.isna(jan[f].iloc[0])):
            out[f] = np.nan
        elif FATORES[f]["tipo"] == "retorno":
            out[f] = float((1 + col).prod() - 1)
        else:
            out[f] = float(col.sum())
    out["CDI"] = float((1 + jan["CDI"].fillna(0)).prod() - 1)
    out["n_dias"] = int(len(jan))
    return out
