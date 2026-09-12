"""Risco da carteira: VaR e Expected Shortfall por simulação histórica e pelo modelo de fatores.

Simulação histórica: aplica os pesos atuais aos retornos reais dos fundos numa janela comum
(padrão 3 anos), compõe cada fundo por `horizonte` pregões (buy-and-hold a partir dos pesos
iniciais, então o retorno da carteira é a soma ponderada exata) e lê os percentis. A
contribuição de cada fundo ao ES é a média da sua parcela nos dias da cauda (soma = ES).

Paramétrico (fatores): cov dos fundos = B Σ_f Bᵀ + diag(σ_resid²), com B = betas, Σ_f = covariância
diária dos fatores na mesma janela. VaR = z_α · σ_p · √h; ES = σ_p √h · φ(z_α)/(1-α). Contribuições
por fundo e por fator vêm da decomposição da variância (somam o total). Ignora a média (excesso
sobre CDI ≈ 0 no horizonte) e supõe normalidade: subestima cauda, sobretudo em crédito.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy.stats import norm

log = logging.getLogger(__name__)

HORIZONTE_PADRAO = 21
NIVEIS_PADRAO = (0.95, 0.99)
JANELA_ANOS_PADRAO = 3
MIN_OBS_HIST = 252


def _acumulado(r: pd.DataFrame | pd.Series, h: int):
    """Retorno composto em janelas móveis de h pregões (sobrepostas)."""
    if h == 1:
        return r
    return np.exp(np.log1p(r).rolling(h).sum()) - 1


def simulacao_historica(rets: dict[str, pd.Series], pesos: dict[str, float], peso_cdi: float,
                        cdi: pd.Series, horizonte: int = HORIZONTE_PADRAO,
                        niveis: tuple[float, ...] = NIVEIS_PADRAO, janela_anos: int = JANELA_ANOS_PADRAO) -> dict:
    avisos: list[str] = []
    usados = [c for c in pesos if c in rets and pesos[c] > 0]
    if not usados:
        return {"ok": False, "avisos": ["nenhum fundo com histórico para a simulação histórica"]}
    r = pd.concat([rets[c].rename(c) for c in usados], axis=1)
    fim = r.index.max()
    r = r.loc[r.index > fim - pd.DateOffset(years=janela_anos)].dropna()
    n = len(r)
    if n < 60:
        return {"ok": False, "avisos": [f"histórico comum dos fundos tem só {n} pregões; simulação histórica não calculada"]}
    if n < MIN_OBS_HIST:
        avisos.append(f"histórico comum curto na simulação histórica: {n} pregões (desde {r.index.min().date()})")
    w = pd.Series({c: pesos[c] for c in usados})
    peso_fora = 1.0 - w.sum() - peso_cdi
    if peso_fora > 0.005:
        avisos.append(f"{peso_fora:.1%} da carteira sem histórico ficou fora da simulação histórica (tratado como CDI)")
    c = cdi.reindex(r.index).fillna(0.0)
    linhas, contrib = [], {}
    for h in sorted({1, horizonte}):
        rh = _acumulado(r, h)                      # fundo a fundo
        ch = _acumulado(c, h)
        parcelas = rh * w                          # parcela de cada fundo no retorno da carteira
        rp = parcelas.sum(axis=1) + (peso_cdi + max(peso_fora, 0.0)) * ch
        rp = rp.dropna()
        for a in niveis:
            var = -float(np.percentile(rp, 100 * (1 - a)))
            cauda = rp[rp <= -var]
            es = -float(cauda.mean()) if len(cauda) else var
            linhas.append({"metodo": "histórico", "horizonte": h, "nivel": a, "var": var, "es": es, "n_obs": int(len(rp))})
            if h == horizonte:
                contrib[a] = {
                    "contrib_es": (-parcelas.loc[cauda.index].mean()).to_dict(),
                    "contrib_es_cdi": -float(((peso_cdi + max(peso_fora, 0.0)) * ch).loc[cauda.index].mean()),
                    "var_isolado": {k: -float(np.percentile(parcelas[k].dropna(), 100 * (1 - a))) for k in usados},
                }
    corr = r.corr()
    pior = _acumulado(r, horizonte)
    return {"ok": True, "resumo": linhas, "contrib": contrib, "correlacao": corr, "janela_inicio": r.index.min(),
            "janela_fim": fim, "n_obs": n, "fundos": usados, "avisos": avisos,
            "vol_anual_carteira": float((r * w).sum(axis=1).std() * np.sqrt(252)),
            "pior_h": float((pior * w).sum(axis=1).min())}


def parametrico(betas: pd.DataFrame, vol_resid: pd.Series, pesos: dict[str, float], painel: pd.DataFrame,
                fatores: list[str], horizonte: int = HORIZONTE_PADRAO, niveis: tuple[float, ...] = NIVEIS_PADRAO,
                janela_anos: int = JANELA_ANOS_PADRAO) -> dict:
    usados = [c for c in pesos if c in betas.index and pesos[c] > 0]
    if not usados:
        return {"ok": False, "avisos": ["nenhum fundo com betas para o VaR paramétrico"]}
    fim = painel.index.max()
    f = painel.loc[painel.index > fim - pd.DateOffset(years=janela_anos), fatores].dropna()
    sigma_f = f.cov()
    B = betas.loc[usados, fatores].fillna(0.0)
    s2 = (vol_resid.reindex(usados).fillna(0.0) ** 2)
    cov = B.values @ sigma_f.values @ B.values.T + np.diag(s2.values)
    w = np.array([pesos[c] for c in usados])
    var_p = float(w @ cov @ w)
    if var_p <= 0:
        return {"ok": False, "avisos": ["variância paramétrica nula"]}
    sigma_p = np.sqrt(var_p)
    b = B.values.T @ w                                     # betas da carteira
    var_fat = b * (sigma_f.values @ b)                     # contribuição de cada fator à variância
    var_res = float((w ** 2 * s2.values).sum())
    frac_fundo = w * (cov @ w) / var_p                     # soma 1
    frac_fator = {**{fa: float(v / var_p) for fa, v in zip(fatores, var_fat)}, "residual": var_res / var_p}
    linhas, contrib = [], {}
    for h in sorted({1, horizonte}):
        sig_h = sigma_p * np.sqrt(h)
        for a in niveis:
            z = norm.ppf(a)
            var, es = float(z * sig_h), float(sig_h * norm.pdf(z) / (1 - a))
            linhas.append({"metodo": "paramétrico", "horizonte": h, "nivel": a, "var": var, "es": es, "n_obs": int(len(f))})
            if h == horizonte:
                contrib[a] = {"contrib_var": {c: float(fr * var) for c, fr in zip(usados, frac_fundo)},
                              "contrib_fator": {k: v * var for k, v in frac_fator.items()},
                              "var_isolado": {c: float(z * np.sqrt(cov[i, i]) * pesos[c] * np.sqrt(h)) for i, c in enumerate(usados)}}
    return {"ok": True, "resumo": linhas, "contrib": contrib, "fracao_fundo": dict(zip(usados, frac_fundo.tolist())),
            "fracao_fator": frac_fator, "betas_carteira": dict(zip(fatores, b.tolist())),
            "vol_anual_carteira": float(sigma_p * np.sqrt(252)), "cov_fatores": sigma_f, "janela_inicio": f.index.min(),
            "janela_fim": f.index.max(), "n_obs": int(len(f)), "fundos": usados, "avisos": []}


def risco_carteira(rets: dict[str, pd.Series], betas: pd.DataFrame, vol_resid: pd.Series, pesos: dict[str, float],
                   peso_cdi: float, painel: pd.DataFrame, fatores: list[str], nomes: dict[str, str],
                   horizonte: int = HORIZONTE_PADRAO, niveis: tuple[float, ...] = NIVEIS_PADRAO,
                   janela_anos: int = JANELA_ANOS_PADRAO) -> dict:
    """Junta os dois métodos em tabelas prontas para relatório/JSON."""
    hist = simulacao_historica(rets, pesos, peso_cdi, painel["CDI"], horizonte, niveis, janela_anos)
    par = parametrico(betas, vol_resid, pesos, painel, fatores, horizonte, niveis, janela_anos)
    avisos = list(hist.get("avisos", [])) + list(par.get("avisos", []))
    resumo = pd.DataFrame(hist.get("resumo", []) + par.get("resumo", []))
    a0 = niveis[0]
    linhas = []
    for c, w in pesos.items():
        if w <= 0:
            continue
        hc = hist.get("contrib", {}).get(a0, {})
        pc = par.get("contrib", {}).get(a0, {})
        linhas.append({"cnpj": c, "nome": nomes.get(c, c), "peso": w,
                       "var_isolado_hist": hc.get("var_isolado", {}).get(c, np.nan),
                       "contrib_es_hist": hc.get("contrib_es", {}).get(c, np.nan),
                       "var_isolado_param": pc.get("var_isolado", {}).get(c, np.nan),
                       "contrib_var_param": pc.get("contrib_var", {}).get(c, np.nan),
                       "fracao_risco_param": par.get("fracao_fundo", {}).get(c, np.nan)})
    if peso_cdi > 0:
        linhas.append({"cnpj": "CDI", "nome": "Caixa / CDI", "peso": peso_cdi, "var_isolado_hist": 0.0,
                       "contrib_es_hist": hist.get("contrib", {}).get(a0, {}).get("contrib_es_cdi", np.nan),
                       "var_isolado_param": 0.0, "contrib_var_param": 0.0, "fracao_risco_param": 0.0})
    contrib_fundos = pd.DataFrame(linhas)
    contrib_fatores = pd.DataFrame([{"fator": k, "contrib_var_param": v, "fracao": par.get("fracao_fator", {}).get(k, np.nan)}
                                    for k, v in par.get("contrib", {}).get(a0, {}).get("contrib_fator", {}).items()])
    # benefício de diversificação (nível a0, horizonte): soma dos VaR isolados - VaR da carteira
    def _var(metodo):
        if resumo.empty:
            return np.nan
        m = resumo[(resumo["metodo"] == metodo) & (resumo["horizonte"] == horizonte) & (resumo["nivel"] == a0)]
        return float(m["var"].iloc[0]) if len(m) else np.nan
    info = {"horizonte": horizonte, "niveis": list(niveis), "janela_anos": janela_anos,
            "hist_ok": hist.get("ok", False), "param_ok": par.get("ok", False),
            "hist_janela": [str(hist["janela_inicio"].date()), str(hist["janela_fim"].date())] if hist.get("ok") else None,
            "hist_n_obs": hist.get("n_obs"), "param_n_obs": par.get("n_obs"),
            "vol_anual_hist": hist.get("vol_anual_carteira"), "vol_anual_param": par.get("vol_anual_carteira"),
            "pior_h_hist": hist.get("pior_h"), "betas_carteira": par.get("betas_carteira"),
            "var_hist": _var("histórico"), "var_param": _var("paramétrico"),
            "soma_var_isolado_hist": float(contrib_fundos["var_isolado_hist"].sum()) if not contrib_fundos.empty else np.nan,
            "soma_var_isolado_param": float(contrib_fundos["var_isolado_param"].sum()) if not contrib_fundos.empty else np.nan}
    return {"resumo": resumo, "contrib_fundos": contrib_fundos, "contrib_fatores": contrib_fatores,
            "correlacao": hist.get("correlacao", pd.DataFrame()), "cov_fatores": par.get("cov_fatores", pd.DataFrame()),
            "info": info, "avisos": avisos}
