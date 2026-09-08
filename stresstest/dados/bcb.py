"""Séries do SGS (Banco Central): CDI diário e PTAX."""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from ..config import CACHE
from .http import cache_valido, get_json

log = logging.getLogger(__name__)

SGS_CDI = 12      # CDI, taxa % ao dia
SGS_PTAX = 1      # Dólar PTAX venda
URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
DIR = CACHE / "bcb"


def sgs(codigo: int, inicio: str = "1995-01-01") -> pd.Series:
    """Série diária do SGS como pd.Series (índice datetime). Consulta em blocos de 5 anos
    (a API limita séries diárias a 10 anos por chamada). Cache de 1 dia."""
    parquet = DIR / f"sgs_{codigo}.parquet"
    if cache_valido(parquet, max_idade_horas=20):
        s = pd.read_parquet(parquet)["valor"]
        if s.index.min() <= pd.Timestamp(inicio):
            return s[s.index >= pd.Timestamp(inicio)]

    ini = pd.Timestamp(inicio)
    fim = pd.Timestamp(date.today())
    partes = []
    while ini <= fim:
        bloco_fim = min(ini + pd.DateOffset(years=5) - pd.Timedelta(days=1), fim)
        dados = get_json(URL.format(codigo=codigo), params={
            "formato": "json",
            "dataInicial": ini.strftime("%d/%m/%Y"),
            "dataFinal": bloco_fim.strftime("%d/%m/%Y"),
        })
        if dados:
            df = pd.DataFrame(dados)
            df["data"] = pd.to_datetime(df["data"], format="%d/%m/%Y")
            df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
            partes.append(df.set_index("data")["valor"])
        ini = bloco_fim + pd.Timedelta(days=1)
    s = pd.concat(partes).sort_index()
    s = s[~s.index.duplicated(keep="last")].dropna()
    DIR.mkdir(parents=True, exist_ok=True)
    s.rename("valor").to_frame().to_parquet(parquet)
    return s


def cdi_diario(inicio: str = "1995-01-01") -> pd.Series:
    """CDI como fração ao dia (0.0005 = 0,05% a.d.)."""
    return (sgs(SGS_CDI, inicio) / 100.0).rename("CDI")


def ptax(inicio: str = "1995-01-01") -> pd.Series:
    return sgs(SGS_PTAX, inicio).rename("PTAX")
