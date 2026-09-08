"""Motor do stress test: carrega a carteira, estima betas, aplica cenários e agrega."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import cenarios as mod_cenarios
from . import modelo as mod_modelo
from .cnpj import formatar, normalizar
from .config import INICIO_PADRAO, JANELA_PADRAO_MESES
from .dados import cvm
from .fatores import FATORES, FATORES_PADRAO, painel as painel_fatores

log = logging.getLogger(__name__)

TOLERANCIA_INICIO_DIAS = 7  # cota de referência pode estar até 7 dias corridos antes da data


@dataclass
class Posicao:
    cnpj: str            # 14 dígitos; "CDI" para caixa
    nome: str
    peso: float          # fração (0.15 = 15%)
    tipo: str = "fundo"  # "fundo" | "cdi"


@dataclass
class Carteira:
    nome: str
    posicoes: list[Posicao]
    data_base: str | None = None
    avisos: list[str] = field(default_factory=list)

    @property
    def fundos(self) -> list[Posicao]:
        return [p for p in self.posicoes if p.tipo == "fundo"]


def carregar_carteira(caminho: str | Path) -> Carteira:
    doc = yaml.safe_load(Path(caminho).read_text(encoding="utf-8"))
    posicoes: list[Posicao] = []
    for f in doc.get("fundos", []) or []:
        posicoes.append(Posicao(cnpj=normalizar(f["cnpj"]), nome=str(f.get("nome") or ""),
                                peso=float(f["peso"]) / 100.0))
    caixa = float(doc.get("caixa_cdi", 0) or 0)
    if caixa:
        posicoes.append(Posicao(cnpj="CDI", nome="Caixa / CDI", peso=caixa / 100.0, tipo="cdi"))
    if not posicoes:
        raise ValueError("carteira sem posições")
    vistos: dict[str, int] = {}
    for p in posicoes:  # nomes precisam ser únicos (viram linhas da matriz)
        base = p.nome or formatar(p.cnpj) if p.tipo == "fundo" else p.nome
        n = vistos.get(base, 0)
        vistos[base] = n + 1
        p.nome = base if n == 0 else f"{base} ({formatar(p.cnpj)})"
    total = sum(p.peso for p in posicoes)
    avisos = []
    if abs(total - 1.0) > 0.005:
        avisos.append(f"pesos somam {total:.1%}; foram normalizados para 100%")
        for p in posicoes:
            p.peso /= total
    return Carteira(nome=str(doc.get("nome", Path(caminho).stem)), posicoes=posicoes,
                    data_base=str(doc["data_base"]) if doc.get("data_base") else None, avisos=avisos)


@dataclass
class Resultado:
    carteira: Carteira
    fatores: list[str]
    fundos: pd.DataFrame            # cadastro + pesos
    betas: pd.DataFrame             # fundo x fator (+ alpha, r2, n_obs)
    estatisticas: pd.DataFrame      # risco histórico por fundo
    choques: pd.DataFrame           # cenário x fator
    detalhe: pd.DataFrame           # fundo x cenário (longo)
    matriz: pd.DataFrame            # fundo x cenário (P&L usado)
    carteira_cenarios: pd.DataFrame # cenário -> P&L da carteira
    contribuicoes: pd.DataFrame     # cenário x fator (carteira)
    avisos: list[str]


def _retorno_janela(cota: pd.Series, inicio, fim) -> float:
    """Retorno da cota do fechamento de `inicio` ao de `fim` (NaN se a série não cobre)."""
    s = cota.dropna()
    ini, fm = pd.Timestamp(inicio), pd.Timestamp(fim)
    base = s[(s.index <= ini) & (s.index >= ini - pd.Timedelta(days=TOLERANCIA_INICIO_DIAS))]
    topo = s[(s.index <= fm) & (s.index >= fm - pd.Timedelta(days=TOLERANCIA_INICIO_DIAS))]
    if base.empty or topo.empty:
        return np.nan
    return float(topo.iloc[-1] / base.iloc[-1] - 1)


def rodar(carteira: Carteira, lista_cenarios: list[mod_cenarios.Cenario] | None = None,
          fatores: list[str] | None = None, janela_meses: int = JANELA_PADRAO_MESES,
          inicio_dados: str = INICIO_PADRAO, metodo: str = "melhor",
          painel: pd.DataFrame | None = None, cotas: pd.DataFrame | None = None) -> Resultado:
    """Executa o stress test.

    metodo: "melhor" usa o retorno histórico real do fundo quando ele existia no cenário e o
    modelo de fatores caso contrário; "modelo" usa sempre o modelo; "historico" usa só o real.
    """
    fatores = list(fatores or FATORES_PADRAO)
    lista_cenarios = lista_cenarios or mod_cenarios.carregar()
    avisos: list[str] = list(carteira.avisos)

    if painel is None:
        log.info("montando painel de fatores desde %s", inicio_dados)
        painel = painel_fatores(f"{inicio_dados}-01" if len(inicio_dados) == 7 else inicio_dados)
    if carteira.data_base:
        painel = painel[painel.index <= pd.Timestamp(carteira.data_base)]

    cnpjs = [p.cnpj for p in carteira.fundos]
    if cotas is None:
        log.info("carregando cotas CVM de %d fundo(s) desde %s", len(cnpjs), inicio_dados)
        cotas = cvm.cotas(cnpjs, inicio_dados)
    if carteira.data_base and not cotas.empty:
        cotas = cotas[cotas.index <= pd.Timestamp(carteira.data_base)]

    # ----------------------------------------------------------------- cadastro e betas
    linhas_fundos, linhas_betas, linhas_stats = [], [], []
    modelos: dict[str, mod_modelo.Modelo | None] = {}
    rets: dict[str, pd.Series] = {}
    for pos in carteira.fundos:
        inf = cvm.info(pos.cnpj)
        nome = pos.nome or inf.get("nome") or formatar(pos.cnpj)
        pos.nome = nome
        linhas_fundos.append({"cnpj": formatar(pos.cnpj), "nome": nome, "peso": pos.peso,
                              "gestor": inf.get("gestor", ""), "classe_cvm": inf.get("classe_cvm", ""),
                              "classe_anbima": inf.get("classe_anbima", ""), "situacao": inf.get("situacao", "")})
        if cotas.empty or pos.cnpj not in cotas.columns:
            avisos.append(f"{nome}: sem cotas na CVM desde {inicio_dados}; posição tratada como CDI")
            modelos[pos.cnpj] = None
            continue
        ret, av = mod_modelo.retornos(cotas[pos.cnpj])
        avisos += [f"{nome}: {a}" for a in av]
        rets[pos.cnpj] = ret
        m = mod_modelo.estimar(ret, painel, fatores, janela_meses, cnpj=pos.cnpj)
        modelos[pos.cnpj] = m
        if m is None:
            avisos.append(f"{nome}: histórico insuficiente para estimar betas (menos de "
                          f"{mod_modelo.MIN_OBS_ABSOLUTO} pregões); só o histórico direto será usado")
        else:
            avisos += [f"{nome}: {a}" for a in m.avisos]
            linhas_betas.append({"cnpj": formatar(pos.cnpj), "nome": nome, **m.betas.to_dict(),
                                 **{f"t_{k}": v for k, v in m.tstats.to_dict().items()},
                                 "alpha_anual": (1 + m.alpha_diario) ** 252 - 1, "r2": m.r2,
                                 "r2_ajustado": m.r2_ajustado, "vol_residual_anual": m.vol_residual_diaria * np.sqrt(252),
                                 "n_obs": m.n_obs, "janela_inicio": m.inicio.date(), "janela_fim": m.fim.date()})
        st = mod_modelo.estatisticas(cotas[pos.cnpj], ret, painel["CDI"])
        linhas_stats.append({"cnpj": formatar(pos.cnpj), "nome": nome, **st})

    fundos_df = pd.DataFrame(linhas_fundos)
    betas_df = pd.DataFrame(linhas_betas)
    stats_df = pd.DataFrame(linhas_stats)

    # ----------------------------------------------------------------- choques
    choques_df = mod_cenarios.tabela_choques(lista_cenarios, painel, fatores)
    for c in lista_cenarios:
        faltando = [f for f in fatores if pd.isna(choques_df.loc[c.id, f])]
        if faltando:
            avisos.append(f"cenário {c.nome}: sem dados dos fatores {faltando} na janela; tratados como 0")
            choques_df.loc[c.id, faltando] = 0.0

    # ----------------------------------------------------------------- fundo x cenário
    linhas = []
    for pos in carteira.posicoes:
        for c in lista_cenarios:
            ch = choques_df.loc[c.id]
            cdi = float(ch["CDI"])
            if pos.tipo == "cdi" or modelos.get(pos.cnpj) is None and pos.cnpj not in rets:
                linhas.append({"cnpj": pos.cnpj if pos.tipo == "cdi" else formatar(pos.cnpj), "nome": pos.nome,
                               "peso": pos.peso, "cenario_id": c.id, "cenario": c.rotulo, "tipo_cenario": c.tipo,
                               "modelo_excesso": 0.0, "cdi": cdi, "modelo_total": cdi, "historico": cdi,
                               "usado": cdi, "fonte": "cdi"})
                continue
            m = modelos.get(pos.cnpj)
            if m is not None:
                excesso = float(sum(m.betas[f] * ch[f] for f in fatores))
                modelo_total = (1 + excesso) * (1 + cdi) - 1
            else:
                excesso, modelo_total = np.nan, np.nan
            hist = _retorno_janela(cotas[pos.cnpj], c.inicio, c.fim) if c.tipo == "historico" else np.nan
            if metodo == "modelo":
                usado, fonte = modelo_total, "modelo"
            elif metodo == "historico":
                usado, fonte = hist, "histórico"
            else:
                usado, fonte = (hist, "histórico") if not np.isnan(hist) else (modelo_total, "modelo")
            linhas.append({"cnpj": formatar(pos.cnpj), "nome": pos.nome, "peso": pos.peso, "cenario_id": c.id,
                           "cenario": c.rotulo, "tipo_cenario": c.tipo, "modelo_excesso": excesso, "cdi": cdi,
                           "modelo_total": modelo_total, "historico": hist, "usado": usado, "fonte": fonte})
    detalhe = pd.DataFrame(linhas)
    matriz = detalhe.pivot(index="nome", columns="cenario", values="usado")
    ordem = [c.rotulo for c in lista_cenarios]
    matriz = matriz.reindex(index=[p.nome for p in carteira.posicoes], columns=ordem)

    # ----------------------------------------------------------------- carteira
    linhas_c, linhas_contrib = [], []
    pesos = {p.cnpj: p.peso for p in carteira.posicoes}
    for c in lista_cenarios:
        d = detalhe[detalhe["cenario_id"] == c.id]
        usado = float((d["peso"] * d["usado"]).sum(skipna=False)) if d["usado"].notna().all() else np.nan
        peso_hist = float(d.loc[d["fonte"].isin(["histórico", "cdi"]), "peso"].sum())
        modelo_tot = float((d["peso"] * d["modelo_total"].fillna(d["cdi"])).sum())
        pior_fundo = d.loc[d["usado"].idxmin()] if d["usado"].notna().any() else None
        linhas_c.append({"cenario_id": c.id, "cenario": c.rotulo, "tipo": c.tipo, "n_dias": int(choques_df.loc[c.id, "n_dias"]),
                         "pl_carteira": usado, "pl_modelo": modelo_tot, "cdi_periodo": float(choques_df.loc[c.id, "CDI"]),
                         "peso_com_historico_real": peso_hist,
                         "pior_posicao": pior_fundo["nome"] if pior_fundo is not None else "",
                         "pl_pior_posicao": float(pior_fundo["usado"]) if pior_fundo is not None else np.nan,
                         "descricao": c.descricao})
        contrib = {"cenario_id": c.id, "cenario": c.rotulo}
        for f in fatores:
            beta_cart = sum(pesos[k] * m.betas[f] for k, m in modelos.items() if m is not None)
            contrib[f] = float(beta_cart * choques_df.loc[c.id, f])
        contrib["CDI"] = float(choques_df.loc[c.id, "CDI"])
        linhas_contrib.append(contrib)
    carteira_df = pd.DataFrame(linhas_c).set_index("cenario_id")
    contrib_df = pd.DataFrame(linhas_contrib).set_index("cenario_id")

    return Resultado(carteira=carteira, fatores=fatores, fundos=fundos_df, betas=betas_df,
                     estatisticas=stats_df, choques=choques_df, detalhe=detalhe, matriz=matriz,
                     carteira_cenarios=carteira_df, contribuicoes=contrib_df, avisos=avisos)
