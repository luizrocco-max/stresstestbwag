"""IDEX-CDI (JGP / Idex Analytics): spread médio das debêntures indexadas ao CDI sobre o CDI.

Fonte pública, sem chave: o próprio site idexanalytics.com.br expõe, via proxy WordPress, o JSON
dos gráficos da página do índice. A série disponível é MENSAL (média do mês, datada no dia 1º):
- "ex_distressed": spread do Idex-CDI ex empresas em distress (desde ago/2017) — PADRÃO, porque
  a série "geral" é dominada pelas marcações de emissores em default (jan/2023 registra 54%
  a.a. por causa da Americanas; ago/2026, 9,6% por um único emissor).
- "geral": spread do Idex-CDI geral (desde jan/2019).

Não existe série diária gratuita: a API idex-api.creditapps.com.br exige autenticação e o
arquivo público de base de dados (S3) cobre só o último mês.
"""
from __future__ import annotations

import json
import logging

import pandas as pd

from ..config import CACHE
from .http import cache_valido, get_json

log = logging.getLogger(__name__)

URL_PROXY = "https://idexanalytics.com.br/wp-json/idex/v1/proxy"
ENDPOINTS = {
    "ex_distressed": "https://idex-api.creditapps.com.br/graficos/idex_cdi_geral_chart_spreads_evolution_ex_distressed_companies_pt",
    "geral": "https://idex-api.creditapps.com.br/graficos/idex_cdi_geral_chart_spreads_evolution_pt",
}
DIR = CACHE / "idex"


def spread_mensal(serie: str = "ex_distressed") -> pd.Series:
    """Spread mensal (% a.a., média do mês) indexado pelo 1º dia do mês. Cache de 20 h; se a
    fonte falhar e houver cache antigo, usa o cache com aviso; sem cache, propaga o erro."""
    if serie not in ENDPOINTS:
        raise ValueError(f"série desconhecida: {serie!r}; use {list(ENDPOINTS)}")
    parquet = DIR / f"spread_{serie}.parquet"
    if cache_valido(parquet, max_idade_horas=20):
        return pd.read_parquet(parquet)["spread"]
    try:
        js = get_json(URL_PROXY, params={"url": ENDPOINTS[serie]}, tentativas=2)
        fig = json.loads(js["json"]) if isinstance(js.get("json"), str) else js["json"]
        tr = fig["data"][0]
        s = pd.Series([100.0 * float(v) for v in tr["y"]], index=pd.to_datetime(tr["x"]), name="spread")
        s = s[~s.index.duplicated(keep="last")].sort_index().dropna()
        if s.empty:
            raise RuntimeError("série vazia")
        DIR.mkdir(parents=True, exist_ok=True)
        s.to_frame().to_parquet(parquet)
        return s
    except Exception as e:  # noqa: BLE001
        if parquet.exists():
            log.warning("IDEX indisponível (%s); usando cache antigo", e)
            return pd.read_parquet(parquet)["spread"]
        raise
