"""Séries de preço do Yahoo Finance (Ibovespa, S&P 500, small caps, câmbio de fallback)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from ..config import CACHE
from .http import cache_valido, get_json

log = logging.getLogger(__name__)

URL = "https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
DIR = CACHE / "yahoo"

TICKERS = {
    "IBOV": "^BVSP",
    "SPX": "^GSPC",
    "SMLL": "SMAL11.SA",
    "USDBRL_YAHOO": "BRL=X",
}


def historico(ticker: str, inicio: str = "1990-01-01") -> pd.Series:
    """Fechamento ajustado diário. Cache de 1 dia."""
    seguro = ticker.replace("^", "_").replace("=", "_")
    parquet = DIR / f"{seguro}.parquet"
    if cache_valido(parquet, max_idade_horas=20):
        return pd.read_parquet(parquet)["close"]

    p1 = int(pd.Timestamp(inicio).timestamp())
    p2 = int(datetime.now(timezone.utc).timestamp()) + 86400
    js = get_json(URL.format(ticker=ticker), params={
        "period1": p1, "period2": p2, "interval": "1d", "events": "div",
    })
    res = js["chart"]["result"][0]
    ts = res["timestamp"]
    quote = res["indicators"]["quote"][0]["close"]
    adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose") or quote
    idx = pd.to_datetime(ts, unit="s", utc=True).tz_convert("America/Sao_Paulo").normalize().tz_localize(None)
    s = pd.Series(adj, index=idx, name="close", dtype="float64").dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    DIR.mkdir(parents=True, exist_ok=True)
    s.to_frame().to_parquet(parquet)
    return s
