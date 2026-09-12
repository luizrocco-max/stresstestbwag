"""API programática para outros sistemas (ex.: a aba de stress test de um app de comitê).

Entrada mínima: lista de posições com CNPJ e peso. Saída: dicionários JSON-serializáveis.

    from stresstest import api
    res = api.stress_carteira([{"cnpj": "12.798.221/0001-36", "peso": 60},
                               {"cnpj": "73.232.530/0001-39", "peso": 40}], caixa_cdi=0)
    res["carteira"]      # P&L da carteira por cenário
    res["fundos"]        # betas, estatísticas e P&L de cada fundo em cada cenário

    dados = api.painel_universo(fundos=[{"cnpj": ..., "nome": ..., "grupo": ...}, ...])
    api.painel_html(dados, "static/stress.html")   # página interativa autocontida

Tudo usa cache local em `stresstest.config.CACHE` (variável STRESSTEST_CACHE). A primeira
chamada baixa os informes diários da CVM (demorado); depois só os meses recentes.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from . import cenarios as mod_cenarios
from . import motor, painel_web
from .cnpj import formatar, normalizar
from .config import INICIO_PADRAO, JANELA_PADRAO_MESES
from .dados import cvm
from .fatores import FATORES, FATORES_PADRAO

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------------- cadastro
def buscar_fundos(texto: str, limite: int = 10) -> list[dict]:
    """Busca no cadastro CVM por nome. Devolve cnpj (formatado), nome, gestor, classe, PL."""
    df = cvm.buscar(texto, limite=limite)
    out = []
    for _, r in df.iterrows():
        out.append({"cnpj": formatar(r["cnpj"]), "nome": r["nome"], "gestor": r["gestor"],
                    "classe_cvm": r["classe_cvm"], "classe_anbima": r["classe_anbima"],
                    "pl": None if pd.isna(r["pl"]) else float(r["pl"])})
    return out


def info_fundo(cnpj: str) -> dict:
    """Cadastro de um CNPJ (nome, gestor, classe). Campos vazios se não estiver na CVM."""
    i = cvm.info(cnpj)
    return {"cnpj": formatar(cnpj), "nome": i.get("nome", ""), "gestor": i.get("gestor", ""),
            "classe_cvm": i.get("classe_cvm", ""), "classe_anbima": i.get("classe_anbima", ""),
            "situacao": i.get("situacao", ""), "encontrado": bool(i.get("nome"))}


def cotas(cnpjs: list[str], inicio: str = "2015-01") -> pd.DataFrame:
    """Cotas diárias (colunas = CNPJ com 14 dígitos)."""
    return cvm.cotas(cnpjs, inicio)


# ----------------------------------------------------------------------------- stress
def _carteira(posicoes: list[dict], caixa_cdi: float, nome: str, data_base: str | None) -> motor.Carteira:
    pos = [motor.Posicao(cnpj=normalizar(p["cnpj"]), nome=str(p.get("nome") or ""), peso=float(p["peso"]) / 100.0)
           for p in posicoes]
    if caixa_cdi:
        pos.append(motor.Posicao(cnpj="CDI", nome="Caixa / CDI", peso=float(caixa_cdi) / 100.0, tipo="cdi"))
    if not pos:
        raise ValueError("carteira vazia")
    total = sum(p.peso for p in pos)
    avisos = []
    if abs(total - 1) > 0.005:
        avisos.append(f"pesos somam {total:.1%}; normalizados para 100%")
        for p in pos:
            p.peso /= total
    return motor.Carteira(nome=nome, posicoes=pos, data_base=data_base, avisos=avisos)


def _df(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    d = df.reset_index() if df.index.name else df
    return [{k: painel_web._num(v) for k, v in row.items()} for row in d.to_dict("records")]


def stress_carteira(posicoes: list[dict], caixa_cdi: float = 0.0, nome: str = "Carteira",
                    cenarios: str | Path | None = None, janela_meses: int = JANELA_PADRAO_MESES,
                    inicio_dados: str = INICIO_PADRAO, metodo: str = "melhor",
                    data_base: str | None = None, fatores: list[str] | None = None) -> dict:
    """Roda o stress test de uma carteira.

    posicoes: [{"cnpj": "12.798.221/0001-36", "peso": 60, "nome": "opcional"}, ...] (pesos em %)
    caixa_cdi: peso (%) em caixa remunerado a CDI
    cenarios: caminho de um YAML de cenários (padrão: biblioteca embutida)
    metodo: "melhor" (retorno real quando o fundo existia, senão modelo) | "modelo" | "historico"
    """
    cart = _carteira(posicoes, caixa_cdi, nome, data_base)
    lista = mod_cenarios.carregar(cenarios)
    res = motor.rodar(cart, lista, fatores=fatores, janela_meses=janela_meses,
                      inicio_dados=inicio_dados, metodo=metodo)
    fat = res.fatores
    return {
        "carteira": {"nome": cart.nome, "posicoes": [{"cnpj": p.cnpj if p.tipo == "cdi" else formatar(p.cnpj),
                                                      "nome": p.nome, "peso": p.peso, "tipo": p.tipo} for p in cart.posicoes]},
        "fatores": [{"id": f, **FATORES[f]} for f in fat],
        "cenarios": _df(res.choques),
        "resultado_carteira": _df(res.carteira_cenarios),
        "contribuicoes": _df(res.contribuicoes),
        "fundos": _df(res.fundos),
        "betas": _df(res.betas),
        "estatisticas": _df(res.estatisticas),
        "detalhe": _df(res.detalhe),
        "risco": risco_json(res.risco),
        "avisos": res.avisos,
    }


def risco_json(rk: dict) -> dict:
    """Bloco de risco (VaR/ES) em formato JSON-serializável."""
    if not rk:
        return {}
    corr = rk.get("correlacao")
    return {
        "info": {k: painel_web._num(v) if not isinstance(v, (list, dict)) else v for k, v in rk["info"].items()},
        "resumo": _df(rk["resumo"]),
        "contrib_fundos": _df(rk["contrib_fundos"]),
        "contrib_fatores": _df(rk["contrib_fatores"]),
        "correlacao": {c: {k: painel_web._num(v) for k, v in corr[c].items()} for c in corr.columns} if corr is not None and not corr.empty else {},
        "avisos": rk.get("avisos", []),
    }


def excel(resultado_ou_posicoes, saida: str | Path, **kw) -> Path:
    """Gera o relatório Excel. Aceita a lista de posições (roda o stress) ou nada mais simples:
    use `stresstest.relatorio.excel` diretamente se já tiver o objeto Resultado."""
    from . import relatorio
    cart = _carteira(resultado_ou_posicoes, kw.pop("caixa_cdi", 0.0), kw.pop("nome", "Carteira"), kw.pop("data_base", None))
    lista = mod_cenarios.carregar(kw.pop("cenarios", None))
    res = motor.rodar(cart, lista, **kw)
    return relatorio.excel(res, saida)


# ----------------------------------------------------------------------------- painel interativo
def painel_universo(fundos: list[dict], exemplo: dict | None = None, grupos: list[str] | None = None,
                    janela_meses: int = JANELA_PADRAO_MESES, inicio_dados: str = INICIO_PADRAO,
                    cenarios: str | Path | None = None, nome: str = "Universo") -> dict:
    """Calcula os dados do painel interativo para um universo de fundos.

    fundos: [{"cnpj": ..., "nome": ..., "grupo": "Multimercado"}, ...]
    exemplo: {"caixa_cdi": 20, "fundos": [{"cnpj": ..., "peso": 12}, ...]} (carteira inicial da página)
    Devolve o dict JSON que o template consome (veja painel_html)."""
    import tempfile

    import yaml
    por_grupo: dict[str, list] = {}
    for f in fundos:
        por_grupo.setdefault(f.get("grupo") or "Fundos", []).append({"cnpj": f["cnpj"], "nome": f.get("nome", "")})
    ordem = grupos or list(por_grupo)
    doc = {"nome": nome, "grupos": [{"nome": g, "fundos": por_grupo[g]} for g in ordem if g in por_grupo],
           "exemplo": exemplo or {"caixa_cdi": 0, "fundos": []}}
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as tmp:
        yaml.safe_dump(doc, tmp, allow_unicode=True)
        caminho = tmp.name
    try:
        return painel_web.dados_universo(caminho, janela_meses=janela_meses, inicio_dados=inicio_dados,
                                         cenarios_yaml=cenarios)
    finally:
        Path(caminho).unlink(missing_ok=True)


def painel_html(dados: dict, saida: str | Path) -> Path:
    """Escreve a página interativa autocontida (HTML + JSON embutido)."""
    return painel_web.gerar_html(dados, saida)
