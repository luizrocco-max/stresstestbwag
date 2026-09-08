"""Dados abertos da CVM: cadastro de fundos/classes e informe diário (cotas).

Fontes:
- Cadastro (pós Resolução CVM 175): registro_fundo_classe.zip
- Cotas diárias: INF_DIARIO (arquivos anuais até 2020 e mensais a partir de 2021)

O CNPJ da classe é o mesmo CNPJ que o fundo tinha antes da adaptação à RCVM 175,
portanto a série histórica é contínua quando filtramos pelo CNPJ.
"""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

from ..cnpj import normalizar
from ..config import CACHE
from .http import baixar, cache_valido

log = logging.getLogger(__name__)

BASE = "https://dados.cvm.gov.br/dados/FI"
URL_CADASTRO = f"{BASE}/CAD/DADOS/registro_fundo_classe.zip"
URL_INF_MENSAL = f"{BASE}/DOC/INF_DIARIO/DADOS/inf_diario_fi_{{ano}}{{mes:02d}}.zip"
URL_INF_ANUAL = f"{BASE}/DOC/INF_DIARIO/DADOS/HIST/inf_diario_fi_{{ano}}.zip"
ULTIMO_ANO_HIST = 2020  # até este ano os arquivos são anuais

DIR = CACHE / "cvm"
DIR_INF = DIR / "inf_diario"


# --------------------------------------------------------------------------- cadastro
def cadastro(forcar: bool = False) -> pd.DataFrame:
    """Cadastro de classes (uma linha por CNPJ de classe) com gestor do fundo."""
    parquet = DIR / "cadastro.parquet"
    if not forcar and cache_valido(parquet, max_idade_horas=7 * 24):
        return pd.read_parquet(parquet)

    zip_path = baixar(URL_CADASTRO, DIR / "registro_fundo_classe.zip")
    with zipfile.ZipFile(zip_path) as z:
        classes = pd.read_csv(z.open("registro_classe.csv"), sep=";", encoding="latin1",
                              dtype=str, keep_default_na=False)
        fundos = pd.read_csv(z.open("registro_fundo.csv"), sep=";", encoding="latin1",
                             dtype=str, keep_default_na=False)

    fundos = fundos[["ID_Registro_Fundo", "CNPJ_Fundo", "Denominacao_Social", "Gestor",
                     "Administrador"]].rename(columns={"Denominacao_Social": "nome_fundo"})
    cad = classes.merge(fundos, on="ID_Registro_Fundo", how="left")
    cad = pd.DataFrame({
        "cnpj": cad["CNPJ_Classe"].map(normalizar),
        "nome": cad["Denominacao_Social"].str.strip(),
        "situacao": cad["Situacao"],
        "tipo": cad["Tipo_Classe"],
        "classe_cvm": cad["Classificacao"],
        "classe_anbima": cad["Classificacao_Anbima"],
        "gestor": cad["Gestor"],
        "administrador": cad["Administrador"],
        "pl": pd.to_numeric(cad["Patrimonio_Liquido"], errors="coerce"),
        "data_pl": cad["Data_Patrimonio_Liquido"],
        "inicio_classe": cad["Data_Inicio"],
        "cnpj_fundo": cad["CNPJ_Fundo"].map(lambda x: normalizar(x) if x else ""),
        "nome_fundo": cad["nome_fundo"],
    })
    cad = cad.drop_duplicates("cnpj").reset_index(drop=True)
    parquet.parent.mkdir(parents=True, exist_ok=True)
    cad.to_parquet(parquet, index=False)
    return cad


def buscar(texto: str, somente_ativos: bool = True, limite: int = 20) -> pd.DataFrame:
    """Busca por nome (todas as palavras precisam aparecer), ordenada por PL."""
    cad = cadastro()
    nome = cad["nome"].str.upper()
    mask = pd.Series(True, index=cad.index)
    for termo in texto.upper().split():
        mask &= nome.str.contains(termo, regex=False)
    if somente_ativos:
        mask &= cad["situacao"].str.startswith("Em Funcionamento")
    res = cad[mask].sort_values("pl", ascending=False).head(limite)
    return res[["cnpj", "nome", "classe_cvm", "classe_anbima", "gestor", "pl", "situacao"]]


def info(cnpj: str) -> dict:
    cad = cadastro()
    linha = cad[cad["cnpj"] == normalizar(cnpj)]
    if linha.empty:
        return {"cnpj": normalizar(cnpj), "nome": "", "gestor": "", "classe_cvm": "", "classe_anbima": ""}
    return linha.iloc[0].to_dict()


# --------------------------------------------------------------------------- cotas
_COLS = {
    "CNPJ_FUNDO": "cnpj", "CNPJ_FUNDO_CLASSE": "cnpj", "ID_SUBCLASSE": "subclasse",
    "DT_COMPTC": "data", "VL_QUOTA": "cota", "VL_PATRIM_LIQ": "pl", "NR_COTST": "cotistas",
}


def _parse_csv(conteudo: bytes) -> pd.DataFrame:
    cab = conteudo[:2000].decode("latin1").splitlines()[0].split(";")
    usecols = [c for c in cab if c in _COLS]
    df = pd.read_csv(io.BytesIO(conteudo), sep=";", encoding="latin1", usecols=usecols,
                     dtype={c: str for c in usecols})
    df = df.rename(columns=_COLS)
    if "subclasse" in df.columns:
        # Subclasses repetem a cota da classe; fica com a linha sem subclasse quando existir.
        df["subclasse"] = df["subclasse"].fillna("")
        df = df.sort_values("subclasse").drop_duplicates(["cnpj", "data"], keep="first")
        df = df.drop(columns="subclasse")
    df["cnpj"] = df["cnpj"].str.replace(r"\D", "", regex=True).str.zfill(14)
    df["data"] = pd.to_datetime(df["data"], format="%Y-%m-%d", errors="coerce")
    for c in ("cota", "pl", "cotistas"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["data", "cota"])
    df = df[df["cota"] > 0]
    return df[["cnpj", "data", "cota", "pl", "cotistas"]].reset_index(drop=True)


def _parquet_mes(ano: int, mes: int) -> Path:
    return DIR_INF / f"{ano}{mes:02d}.parquet"


def _mes_recente(ano: int, mes: int) -> bool:
    hoje = date.today()
    return (hoje.year - ano) * 12 + (hoje.month - mes) <= 1


def _garantir_mes(ano: int, mes: int) -> None:
    p = _parquet_mes(ano, mes)
    max_idade = 20 if _mes_recente(ano, mes) else None
    if cache_valido(p, max_idade):
        return
    DIR_INF.mkdir(parents=True, exist_ok=True)
    if ano <= ULTIMO_ANO_HIST:
        log.info("CVM: baixando informe diário anual %s", ano)
        zip_path = baixar(URL_INF_ANUAL.format(ano=ano), DIR_INF / f"inf_diario_fi_{ano}.zip")
        with zipfile.ZipFile(zip_path) as z:
            for nome in z.namelist():
                if not nome.endswith(".csv"):
                    continue
                aamm = nome[-10:-4]
                df = _parse_csv(z.read(nome))
                df.to_parquet(_parquet_mes(int(aamm[:4]), int(aamm[4:])), index=False)
        zip_path.unlink(missing_ok=True)
        if not p.exists():  # mês sem arquivo no zip anual
            pd.DataFrame(columns=["cnpj", "data", "cota", "pl", "cotistas"]).to_parquet(p, index=False)
    else:
        log.info("CVM: baixando informe diário %s-%02d", ano, mes)
        zip_path = baixar(URL_INF_MENSAL.format(ano=ano, mes=mes), DIR_INF / f"inf_{ano}{mes:02d}.zip")
        with zipfile.ZipFile(zip_path) as z:
            nome = [n for n in z.namelist() if n.endswith(".csv")][0]
            df = _parse_csv(z.read(nome))
        df.to_parquet(p, index=False)
        zip_path.unlink(missing_ok=True)


def _meses(inicio: str, fim: str | None = None):
    ini = pd.Period(inicio, freq="M")
    end = pd.Period(fim, freq="M") if fim else pd.Period(date.today(), freq="M")
    p = ini
    while p <= end:
        yield p.year, p.month
        p += 1


def cotas(cnpjs: list[str], inicio: str, fim: str | None = None) -> pd.DataFrame:
    """Cotas diárias em formato largo (índice = data, colunas = CNPJ com 14 dígitos)."""
    alvo = {normalizar(c) for c in cnpjs}
    partes = []
    for ano, mes in _meses(inicio, fim):
        try:
            _garantir_mes(ano, mes)
        except RuntimeError as e:
            if _mes_recente(ano, mes):
                log.warning("CVM: %s-%02d ainda indisponível (%s)", ano, mes, e)
                continue
            raise
        df = pd.read_parquet(_parquet_mes(ano, mes))
        df = df[df["cnpj"].isin(alvo)]
        if not df.empty:
            partes.append(df)
    if not partes:
        return pd.DataFrame()
    longo = pd.concat(partes, ignore_index=True).drop_duplicates(["cnpj", "data"], keep="last")
    largo = longo.pivot(index="data", columns="cnpj", values="cota").sort_index()
    return largo


def patrimonio(cnpjs: list[str], inicio: str, fim: str | None = None) -> pd.DataFrame:
    alvo = {normalizar(c) for c in cnpjs}
    partes = []
    for ano, mes in _meses(inicio, fim):
        p = _parquet_mes(ano, mes)
        if p.exists():
            df = pd.read_parquet(p)
            partes.append(df[df["cnpj"].isin(alvo)])
    if not partes:
        return pd.DataFrame()
    longo = pd.concat(partes, ignore_index=True)
    return longo.pivot(index="data", columns="cnpj", values="pl").sort_index()
