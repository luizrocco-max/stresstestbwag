"""Painel interativo: exporta betas, choques e retornos históricos de um universo de fundos
para um HTML autocontido (template + JSON embutido)."""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import cenarios as mod_cenarios
from . import motor
from .cnpj import formatar, normalizar
from .config import INICIO_PADRAO, JANELA_PADRAO_MESES
from .fatores import FATORES, FATORES_PADRAO, painel as painel_fatores

log = logging.getLogger(__name__)
TEMPLATE = Path(__file__).resolve().parent / "painel_template.html"


def _num(x):
    if x is None:
        return None
    if isinstance(x, (pd.Timestamp, date)):
        return str(pd.Timestamp(x).date())
    if isinstance(x, (float, np.floating)):
        return None if np.isnan(x) else round(float(x), 6)
    if isinstance(x, (int, np.integer)):
        return int(x)
    return x


def dados_universo(universo: str | Path, janela_meses: int = JANELA_PADRAO_MESES,
                   inicio_dados: str = INICIO_PADRAO, cenarios_yaml: str | None = None) -> dict:
    doc = yaml.safe_load(Path(universo).read_text(encoding="utf-8"))
    grupo_de: dict[str, str] = {}
    posicoes = []
    for g in doc["grupos"]:
        for f in g["fundos"]:
            c = normalizar(f["cnpj"])
            grupo_de[c] = g["nome"]
            posicoes.append(motor.Posicao(cnpj=c, nome=f.get("nome", ""), peso=1.0))
    for p in posicoes:
        p.peso = 1.0 / len(posicoes)
    carteira = motor.Carteira(nome=doc.get("nome", "Universo"), posicoes=posicoes)
    lista = mod_cenarios.carregar(cenarios_yaml)
    fat = FATORES_PADRAO
    painel = painel_fatores(f"{inicio_dados}-01" if len(inicio_dados) == 7 else inicio_dados)
    res = motor.rodar(carteira, lista, fatores=fat, janela_meses=janela_meses,
                      inicio_dados=inicio_dados, metodo="melhor", painel=painel)

    cdi_dia = float(painel["CDI"].dropna().iloc[-1])
    cen_out = []
    for c in lista:
        ch = res.choques.loc[c.id]
        cen_out.append({"id": c.id, "nome": c.nome, "rotulo": c.rotulo, "tipo": c.tipo, "inicio": c.inicio,
                        "fim": c.fim, "descricao": c.descricao, "n_dias": int(ch["n_dias"]), "cdi": _num(ch["CDI"]),
                        "choques": {f: _num(ch[f]) for f in fat}})

    betas = res.betas.set_index("cnpj") if not res.betas.empty else pd.DataFrame()
    stats = res.estatisticas.set_index("cnpj") if not res.estatisticas.empty else pd.DataFrame()
    avisos_por_fundo: dict[str, list[str]] = {}
    for a in res.avisos:
        nome, _, texto = a.partition(": ")
        avisos_por_fundo.setdefault(nome, []).append(texto)

    fundos_out = []
    for pos in carteira.fundos:
        cnpj_f = formatar(pos.cnpj)
        cad = res.fundos[res.fundos["cnpj"] == cnpj_f].iloc[0]
        det = res.detalhe[(res.detalhe["cnpj"] == cnpj_f)]
        hist = {r["cenario_id"]: _num(r["historico"]) for _, r in det.iterrows() if r["tipo_cenario"] == "historico"}
        item = {"cnpj": cnpj_f, "nome": pos.nome, "grupo": grupo_de[pos.cnpj], "gestor": cad["gestor"],
                "classe_anbima": cad["classe_anbima"], "hist": hist, "avisos": avisos_por_fundo.get(pos.nome, [])}
        if cnpj_f in betas.index:
            b = betas.loc[cnpj_f]
            item["betas"] = {f: _num(b[f]) for f in fat}
            item["r2"] = _num(b["r2"]); item["n_obs"] = _num(b["n_obs"])
            item["janela"] = [str(b["janela_inicio"]), str(b["janela_fim"])]
        else:
            item["betas"] = None
        if cnpj_f in stats.index:
            s = stats.loc[cnpj_f]
            item["stats"] = {k: _num(s.get(k)) for k in ("inicio_serie", "fim_serie", "vol_anual", "max_drawdown",
                                                         "pior_21d", "var95_21d", "ret_12m", "pior_dia")}
        fundos_out.append(item)

    ex = doc.get("exemplo") or {}
    exemplo = {"caixa_cdi": float(ex.get("caixa_cdi", 0) or 0),
               "fundos": [{"cnpj": formatar(f["cnpj"]), "peso": float(f["peso"])} for f in ex.get("fundos", [])]}
    return {
        "gerado_em": str(date.today()), "cotas_ate": str(res.estatisticas["fim_serie"].max().date()),
        "janela_meses": janela_meses, "cdi_dia": round(cdi_dia, 8), "horizonte_dias": 21,
        "fatores": [{"id": f, **FATORES[f]} for f in fat],
        "grupos": [g["nome"] for g in doc["grupos"]],
        "cenarios": cen_out, "fundos": fundos_out, "exemplo": exemplo,
    }


def gerar_html(dados: dict, saida: str | Path) -> Path:
    js = json.dumps(dados, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("__DADOS__", js)
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(html, encoding="utf-8")
    return saida
