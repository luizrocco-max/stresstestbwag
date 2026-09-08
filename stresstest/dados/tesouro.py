"""Taxas do Tesouro Direto (histórico completo desde 2005).

Usadas para construir fatores de juros de prazo constante:
- JURO_PRE: taxa do Tesouro Prefixado com vencimento mais próximo de N anos
- JURO_REAL: taxa do Tesouro IPCA+ com vencimento mais próximo de N anos
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ..config import CACHE
from .http import baixar, cache_valido

log = logging.getLogger(__name__)

URL = ("https://www.tesourotransparente.gov.br/ckan/dataset/df56aa42-484a-4a59-8184-7676580c81e3/"
       "resource/796d2059-14e9-44e3-80c9-2d9e30b405c1/download/PrecoTaxaTesouroDireto.csv")
DIR = CACHE / "tesouro"

TIPOS_PRE = ("Tesouro Prefixado", "Tesouro Prefixado com Juros Semestrais")
TIPOS_REAL = ("Tesouro IPCA+", "Tesouro IPCA+ com Juros Semestrais")


def precos_taxas() -> pd.DataFrame:
    parquet = DIR / "taxas.parquet"
    if cache_valido(parquet, max_idade_horas=20):
        return pd.read_parquet(parquet)
    csv = baixar(URL, DIR / "PrecoTaxaTesouroDireto.csv")
    df = pd.read_csv(csv, sep=";", encoding="utf-8", decimal=",", dtype={"Tipo Titulo": str})
    df = df.rename(columns={
        "Tipo Titulo": "tipo", "Data Vencimento": "vencimento", "Data Base": "data",
        "Taxa Compra Manha": "taxa_compra", "Taxa Venda Manha": "taxa_venda",
        "PU Base Manha": "pu_base",
    })
    df["vencimento"] = pd.to_datetime(df["vencimento"], format="%d/%m/%Y")
    df["data"] = pd.to_datetime(df["data"], format="%d/%m/%Y")
    for c in ("taxa_compra", "taxa_venda", "pu_base"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[["tipo", "vencimento", "data", "taxa_compra", "taxa_venda", "pu_base"]]
    df = df.sort_values(["tipo", "vencimento", "data"]).reset_index(drop=True)
    parquet.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(parquet, index=False)
    return df


def taxa_prazo_constante(tipos: tuple[str, ...], prazo_anos: float) -> pd.DataFrame:
    """Para cada data escolhe o título (entre `tipos`) com vencimento mais próximo de
    data + prazo_anos e devolve nível da taxa (% a.a.) e variação diária (p.p.),
    calculada dentro do mesmo título para não gerar saltos na troca de papel."""
    df = precos_taxas()
    df = df[df["tipo"].isin(tipos)].copy()
    taxa = df["taxa_venda"].where(df["taxa_venda"] > 0, df["taxa_compra"])
    df["taxa"] = taxa
    df = df[df["taxa"] > 0]
    df = df.sort_values(["tipo", "vencimento", "data"])
    df["dtaxa"] = df.groupby(["tipo", "vencimento"])["taxa"].diff()
    # prazo remanescente e distância ao alvo
    df["prazo"] = (df["vencimento"] - df["data"]).dt.days / 365.25
    df["dist"] = (df["prazo"] - prazo_anos).abs()
    # só títulos com pelo menos 6 meses de vida para evitar ruído de vencimento
    df = df[df["prazo"] > 0.5]
    escolhido = df.sort_values(["data", "dist"]).drop_duplicates("data", keep="first")
    out = escolhido.set_index("data")[["taxa", "dtaxa", "vencimento", "tipo"]].sort_index()
    out["dtaxa"] = out["dtaxa"].fillna(0.0)
    return out
