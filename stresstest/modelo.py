"""Modelo de fatores por fundo e estatísticas de risco a partir da série de cotas."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import statsmodels.api as sm

from .config import JANELA_PADRAO_MESES, MIN_OBS_REGRESSAO

log = logging.getLogger(__name__)

LIMITE_RETORNO_DIARIO = 0.25  # acima disso tratamos como erro de dado / evento de cota
MIN_OBS_ABSOLUTO = 60         # abaixo disso não estimamos beta nenhum


@dataclass
class Modelo:
    cnpj: str
    fatores: list[str]
    betas: pd.Series
    alpha_diario: float
    r2: float
    r2_ajustado: float
    tstats: pd.Series
    vol_residual_diaria: float
    n_obs: int
    inicio: pd.Timestamp
    fim: pd.Timestamp
    avisos: list[str] = field(default_factory=list)


def retornos(cotas: pd.Series) -> tuple[pd.Series, list[str]]:
    """Retornos diários da cota, removendo outliers grosseiros."""
    avisos: list[str] = []
    s = cotas.dropna()
    s = s[s > 0]
    r = s.pct_change().dropna()
    ruins = r[r.abs() > LIMITE_RETORNO_DIARIO]
    if not ruins.empty:
        avisos.append(f"{len(ruins)} retorno(s) diário(s) acima de {LIMITE_RETORNO_DIARIO:.0%} removido(s) "
                      f"(ex.: {ruins.index[0].date()} = {ruins.iloc[0]:+.1%})")
        r = r.drop(ruins.index)
    return r, avisos


def estimar(ret: pd.Series, painel: pd.DataFrame, fatores: list[str],
            janela_meses: int = JANELA_PADRAO_MESES, min_obs: int = MIN_OBS_REGRESSAO,
            cnpj: str = "") -> Modelo | None:
    """OLS do excesso de retorno diário (fundo - CDI) sobre os fatores, na janela mais recente."""
    if ret.empty:
        return None
    fim = ret.index.max()
    ini = fim - pd.DateOffset(months=janela_meses)
    dados = pd.concat([ret.rename("r"), painel[fatores + ["CDI"]]], axis=1, join="inner")
    dados = dados.loc[(dados.index > ini) & (dados.index <= fim)].dropna()
    avisos: list[str] = []
    if len(dados) < MIN_OBS_ABSOLUTO:
        return None
    if len(dados) < min_obs:
        avisos.append(f"histórico curto: betas estimados com só {len(dados)} pregões "
                      f"(desde {dados.index.min().date()})")
    y = dados["r"] - dados["CDI"]
    X = sm.add_constant(dados[fatores])
    res = sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": 5})
    betas = res.params.drop("const")
    if res.rsquared < 0.10:
        avisos.append(f"R² baixo ({res.rsquared:.0%}): o modelo de fatores explica pouco; "
                      "prefira o histórico direto quando existir")
    # fundo com cota parada (ex.: marcação mensal) -> muitos zeros
    zeros = float((y.abs() < 1e-9).mean())
    if zeros > 0.30:
        avisos.append(f"{zeros:.0%} dos dias com retorno zero: cota pouco atualizada, betas subestimados")
    return Modelo(cnpj=cnpj, fatores=fatores, betas=betas, alpha_diario=float(res.params["const"]),
                  r2=float(res.rsquared), r2_ajustado=float(res.rsquared_adj),
                  tstats=res.tvalues.drop("const"), vol_residual_diaria=float(np.sqrt(res.scale)),
                  n_obs=int(res.nobs), inicio=dados.index.min(), fim=fim, avisos=avisos)


def estatisticas(cotas: pd.Series, ret: pd.Series, cdi: pd.Series | None = None) -> dict:
    """Vol, drawdown, piores janelas e VaR histórico a partir do histórico completo do fundo."""
    s = cotas.dropna()
    s = s[s > 0]
    out = {"inicio_serie": s.index.min(), "fim_serie": s.index.max(), "n_obs": int(len(ret))}
    if len(ret) < 21:
        return out
    out["vol_anual"] = float(ret.std() * np.sqrt(252))
    pico = s.cummax()
    dd = s / pico - 1
    out["max_drawdown"] = float(dd.min())
    out["data_max_drawdown"] = dd.idxmin()
    out["pior_dia"] = float(ret.min())
    out["data_pior_dia"] = ret.idxmin()
    r21 = (s / s.shift(21) - 1).dropna()
    out["pior_21d"] = float(r21.min())
    out["data_pior_21d"] = r21.idxmin()
    out["var95_21d"] = float(np.percentile(r21, 5))
    out["es95_21d"] = float(r21[r21 <= np.percentile(r21, 5)].mean())
    ult = s.index.max()
    for meses, chave in ((12, "ret_12m"), (36, "ret_36m")):
        base = s[s.index <= ult - pd.DateOffset(months=meses)]
        out[chave] = float(s.iloc[-1] / base.iloc[-1] - 1) if not base.empty else np.nan
    if cdi is not None:
        c = cdi.reindex(ret.index).fillna(0)
        ex = ret - c
        out["sharpe_12m"] = float(np.sqrt(252) * ex.iloc[-252:].mean() / ex.iloc[-252:].std()) if len(ex) >= 60 else np.nan
    return out
