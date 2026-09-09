"""Testes sem rede: modelo, choques de janela, carteira e motor com dados sintéticos."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stresstest import cenarios, cnpj, fatores, modelo, motor
from stresstest.dados import cvm


@pytest.fixture
def painel_sintetico():
    rng = np.random.default_rng(42)
    idx = pd.bdate_range("2021-01-04", periods=800)
    p = pd.DataFrame(index=idx)
    p["IBOV"] = rng.normal(0, 0.012, len(idx))
    p["SPX"] = rng.normal(0, 0.010, len(idx))
    p["USDBRL"] = rng.normal(0, 0.008, len(idx))
    p["JURO_PRE"] = rng.normal(0, 0.06, len(idx))
    p["JURO_REAL"] = rng.normal(0, 0.04, len(idx))
    p["CDI"] = 0.0004
    return p


def cotas_de(painel, beta_ibov=0.5, beta_pre=-0.02, ruido=0.002, seed=1):
    rng = np.random.default_rng(seed)
    r = painel["CDI"] + beta_ibov * painel["IBOV"] + beta_pre * painel["JURO_PRE"] + rng.normal(0, ruido, len(painel))
    return (1 + r).cumprod() * 100


def test_cnpj():
    assert cnpj.normalizar("12.798.221/0001-36") == "12798221000136"
    assert cnpj.formatar("12798221000136") == "12.798.221/0001-36"
    assert cnpj.normalizar(7455507000189) == "07455507000189"


def test_estimar_recupera_betas(painel_sintetico):
    cot = cotas_de(painel_sintetico)
    ret, avisos = modelo.retornos(cot)
    assert not avisos
    m = modelo.estimar(ret, painel_sintetico, fatores.FATORES_PADRAO, janela_meses=24)
    assert m is not None
    assert abs(m.betas["IBOV"] - 0.5) < 0.05
    assert abs(m.betas["JURO_PRE"] + 0.02) < 0.005  # -2% por +100 bps (duration ~2)
    assert abs(m.betas["SPX"]) < 0.1
    assert m.r2 > 0.8


def test_retornos_remove_outlier(painel_sintetico):
    cot = cotas_de(painel_sintetico)
    cot.iloc[100] *= 2  # erro de dado
    ret, avisos = modelo.retornos(cot)
    assert len(avisos) == 1 and "removido" in avisos[0]


def test_choque_janela(painel_sintetico):
    p = painel_sintetico
    ini, fim = p.index[10], p.index[30]
    ch = fatores.choque_janela(p, ini, fim)
    esperado = (1 + p["IBOV"].iloc[11:31]).prod() - 1
    assert ch["IBOV"] == pytest.approx(esperado)
    assert ch["JURO_PRE"] == pytest.approx(p["JURO_PRE"].iloc[11:31].sum())
    assert ch["n_dias"] == 20


def test_cenarios_padrao_carregam():
    lista = cenarios.carregar()
    ids = {c.id for c in lista}
    assert "covid_2020" in ids and "juros_+200" in ids
    hip = next(c for c in lista if c.id == "juros_+200")
    assert hip.choques["JURO_PRE"] == 2.0
    bolsa = next(c for c in lista if c.id == "bolsa_-20")
    assert bolsa.choques["IBOV"] == pytest.approx(-0.20)


def test_carregar_carteira_normaliza(tmp_path):
    y = tmp_path / "c.yaml"
    y.write_text("nome: Teste\nfundos:\n  - {cnpj: 12.798.221/0001-36, nome: A, peso: 30}\n"
                 "  - {cnpj: 07.455.507/0001-89, nome: B, peso: 30}\ncaixa_cdi: 20\n", encoding="utf-8")
    c = motor.carregar_carteira(y)
    assert c.avisos and "normalizados" in c.avisos[0]
    assert sum(p.peso for p in c.posicoes) == pytest.approx(1.0)
    assert c.posicoes[-1].tipo == "cdi"


def test_motor_rodar_sintetico(painel_sintetico, monkeypatch):
    monkeypatch.setattr(cvm, "info", lambda c: {"nome": "F", "gestor": "G", "classe_cvm": "", "classe_anbima": "", "situacao": ""})
    p = painel_sintetico
    cotas = pd.DataFrame({
        "11111111000111": cotas_de(p, beta_ibov=1.0, beta_pre=0.0, seed=2),
        "22222222000122": cotas_de(p, beta_ibov=0.0, beta_pre=-0.03, seed=3),
    })
    cart = motor.Carteira(nome="T", posicoes=[
        motor.Posicao("11111111000111", "Acoes", 0.5),
        motor.Posicao("22222222000122", "Pre", 0.3),
        motor.Posicao("CDI", "Caixa", 0.2, tipo="cdi"),
    ])
    ini, fim = p.index[300], p.index[320]
    lista = [
        cenarios.Cenario(id="h", nome="Hist", tipo="historico", inicio=str(ini.date()), fim=str(fim.date())),
        cenarios.Cenario(id="b", nome="Bolsa -20", tipo="hipotetico", choques={"IBOV": -0.20}),
        cenarios.Cenario(id="j", nome="Juros +200", tipo="hipotetico", choques={"JURO_PRE": 2.0}),
    ]
    res = motor.rodar(cart, lista, painel=p, cotas=cotas)
    cc = res.carteira_cenarios
    # bolsa -20%: só o fundo de ações (peso 50%, beta 1) sofre => ~ -10% + CDI
    assert cc.loc["b", "pl_carteira"] == pytest.approx(-0.10 + 0.0085, abs=0.005)
    # juros +200bps: fundo pré (peso 30%, -3% por +100 bps) => -1.8% + CDI
    assert cc.loc["j", "pl_carteira"] == pytest.approx(0.3 * -0.03 * 2.0 + 0.0085, abs=0.005)
    # cenário histórico usa o retorno real das cotas
    det = res.detalhe[(res.detalhe["cenario_id"] == "h") & (res.detalhe["nome"] == "Acoes")].iloc[0]
    assert det["fonte"] == "histórico"
    real = cotas["11111111000111"].loc[fim] / cotas["11111111000111"].loc[ini] - 1
    assert det["usado"] == pytest.approx(real)
    assert cc.loc["h", "peso_com_historico_real"] == pytest.approx(1.0)
    assert set(res.matriz.index) == {"Acoes", "Pre", "Caixa"}


def test_api_stress_carteira(painel_sintetico, monkeypatch):
    from stresstest import api
    monkeypatch.setattr(cvm, "info", lambda c: {"nome": "F", "gestor": "G", "classe_cvm": "", "classe_anbima": "", "situacao": ""})
    p = painel_sintetico
    cot = pd.DataFrame({"11111111000111": cotas_de(p, beta_ibov=1.0, beta_pre=0.0, seed=2)})
    monkeypatch.setattr(cvm, "cotas", lambda cnpjs, inicio, fim=None: cot)
    monkeypatch.setattr(motor, "painel_fatores", lambda inicio: p)
    lista_yaml = __import__("pathlib").Path(__file__).parent / "_cen.yaml"
    lista_yaml.write_text("hipoteticos:\n  - {id: b, nome: Bolsa -20, choques: {IBOV: -20}}\n", encoding="utf-8")
    try:
        res = api.stress_carteira([{"cnpj": "11.111.111/0001-11", "peso": 50}], caixa_cdi=50, cenarios=lista_yaml)
    finally:
        lista_yaml.unlink()
    import json
    json.dumps(res)  # serializável
    assert res["resultado_carteira"][0]["pl_carteira"] == pytest.approx(0.5 * -0.20 + 0.0085, abs=0.005)
    assert res["betas"][0]["IBOV"] == pytest.approx(1.0, abs=0.05)
    assert res["carteira"]["posicoes"][-1]["tipo"] == "cdi"


# ----------------------------------------------------------------------------- SPREAD_CRED
def _painel_real_ou_skip():
    from stresstest.config import CACHE
    if not (CACHE / "cvm" / "inf_diario").exists():
        pytest.skip("cache de dados não disponível (teste precisa dos dados reais)")
    return fatores.painel("2007-06-01")


def test_spread_declarado_fora_do_padrao():
    assert fatores.FATORES["SPREAD_CRED"]["tipo"] == "taxa"
    assert "SPREAD_CRED" not in fatores.FATORES_PADRAO
    assert fatores.FATORES_PADRAO == ["IBOV", "SPX", "USDBRL", "JURO_PRE", "JURO_REAL"]


def test_painel_paridade_colunas_antigas():
    """(i) o painel com a coluna nova mantém as colunas antigas valor a valor."""
    p = _painel_real_ou_skip()
    antigas = ["IBOV", "SMLL", "SPX", "USDBRL", "JURO_PRE", "JURO_REAL", "CDI"]
    assert "SPREAD_CRED" in p.columns and set(antigas) <= set(p.columns)
    sem = p[antigas]
    com = p[antigas + ["SPREAD_CRED"]][antigas]
    pd.testing.assert_frame_equal(sem, com)
    # NaN antes do início da série (ago/2017), nada de zero
    assert p.loc[:"2017-07-31", "SPREAD_CRED"].isna().all()
    assert p["SPREAD_CRED"].notna().sum() > 100


def test_rodar_paridade_com_golden():
    """(ii) betas e choques com os fatores padrão idênticos aos de antes da mudança."""
    import json
    from pathlib import Path
    _painel_real_ou_skip()
    g = json.loads((Path(__file__).parent / "golden_exemplo.json").read_text(encoding="utf-8"))
    cart = motor.carregar_carteira(Path(__file__).parent.parent / "carteiras" / "exemplo.yaml")
    cart.data_base = g["data_base"]
    res = motor.rodar(cart, cenarios.carregar())
    assert res.fatores == g["fatores"]
    for r in res.betas.to_dict("records"):
        esperado = g["betas"][r["cnpj"]]
        for f in g["fatores"]:
            assert r[f] == pytest.approx(esperado[f], abs=1e-9), (r["cnpj"], f)
        assert r["r2"] == pytest.approx(esperado["r2"], abs=1e-9)
    # cenários novos (ex.: credito_+200) podem existir; os antigos têm que bater valor a valor
    assert set(g["choques"]) <= set(res.choques.index)
    for cid in g["choques"]:
        for f in g["fatores"] + ["CDI"]:
            assert res.choques.loc[cid, f] == pytest.approx(g["choques"][cid][f], abs=1e-9), (cid, f)
        assert res.carteira_cenarios.loc[cid, "pl_carteira"] == pytest.approx(g["pl_carteira"][cid], abs=1e-9), cid


def test_choque_spread_janelas():
    """(iii) NaN antes da série; abertura positiva na janela da Americanas."""
    p = _painel_real_ou_skip()
    ch = fatores.choque_janela(p, "2008-09-12", "2008-10-27", ["IBOV", "SPREAD_CRED"])
    assert np.isnan(ch["SPREAD_CRED"]) and not np.isnan(ch["IBOV"])
    ch = fatores.choque_janela(p, "2023-01-11", "2023-03-23", ["SPREAD_CRED"])
    assert 0.3 < ch["SPREAD_CRED"] < 3.0
    ch = fatores.choque_janela(p, "2020-02-19", "2020-03-23", ["SPREAD_CRED"])
    assert ch["SPREAD_CRED"] > 0


def test_choque_janela_nan_no_inicio(painel_sintetico):
    p = painel_sintetico.copy()
    p["SPREAD_CRED"] = np.nan
    p.loc[p.index[400]:, "SPREAD_CRED"] = 0.01
    assert np.isnan(fatores.choque_janela(p, p.index[300], p.index[450], ["SPREAD_CRED"])["SPREAD_CRED"])
    assert fatores.choque_janela(p, p.index[401], p.index[450], ["SPREAD_CRED"])["SPREAD_CRED"] == pytest.approx(0.49)


def test_motor_fator_sem_dado_vira_zero_no_pl(painel_sintetico, monkeypatch):
    monkeypatch.setattr(cvm, "info", lambda c: {"nome": "F", "gestor": "G", "classe_cvm": "", "classe_anbima": "", "situacao": ""})
    p = painel_sintetico.copy()
    p["SPREAD_CRED"] = np.nan
    p.loc[p.index[500]:, "SPREAD_CRED"] = 0.0
    cotas = pd.DataFrame({"11111111000111": cotas_de(p, beta_ibov=1.0, beta_pre=0.0, seed=2)})
    cart = motor.Carteira(nome="T", posicoes=[motor.Posicao("11111111000111", "A", 1.0)])
    lista = [cenarios.Cenario(id="h", nome="Antes", tipo="historico", inicio=str(p.index[100].date()), fim=str(p.index[120].date()))]
    fat = fatores.FATORES_PADRAO + ["SPREAD_CRED"]
    res = motor.rodar(cart, lista, fatores=fat, painel=p, cotas=cotas, metodo="modelo")
    assert np.isnan(res.choques.loc["h", "SPREAD_CRED"])
    assert res.carteira_cenarios.loc["h", "fatores_sem_dado"] == "SPREAD_CRED"
    assert not np.isnan(res.carteira_cenarios.loc["h", "pl_carteira"])
    assert any("SPREAD_CRED" in a for a in res.avisos)
