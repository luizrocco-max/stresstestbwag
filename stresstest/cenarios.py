"""Cenários de stress: históricos (janelas de datas) e hipotéticos (choques definidos)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .fatores import FATORES, choque_janela

CENARIOS_PADRAO = Path(__file__).resolve().parent.parent / "cenarios" / "cenarios.yaml"


@dataclass
class Cenario:
    id: str
    nome: str
    tipo: str  # "historico" | "hipotetico"
    descricao: str = ""
    inicio: str | None = None
    fim: str | None = None
    choques: dict = field(default_factory=dict)
    horizonte_dias: int = 21

    @property
    def rotulo(self) -> str:
        if self.tipo == "historico":
            return f"{self.nome} ({self.inicio} a {self.fim})"
        return self.nome


def carregar(caminho: str | Path | None = None) -> list[Cenario]:
    caminho = Path(caminho) if caminho else CENARIOS_PADRAO
    doc = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    lista: list[Cenario] = []
    for c in doc.get("historicos", []) or []:
        lista.append(Cenario(id=c["id"], nome=c["nome"], tipo="historico",
                             descricao=c.get("descricao", ""), inicio=str(c["inicio"]), fim=str(c["fim"])))
    for c in doc.get("hipoteticos", []) or []:
        choques = {}
        for k, v in (c.get("choques") or {}).items():
            if k not in FATORES:
                raise ValueError(f"cenário {c['id']}: fator desconhecido {k!r}; válidos: {list(FATORES)}")
            # retorno vem em % no YAML (-25 => -25%); taxa vem em p.p. (2 => +200 bps)
            choques[k] = float(v) / 100.0 if FATORES[k]["tipo"] == "retorno" else float(v)
        lista.append(Cenario(id=c["id"], nome=c["nome"], tipo="hipotetico",
                             descricao=c.get("descricao", ""), choques=choques,
                             horizonte_dias=int(c.get("horizonte_dias", 21))))
    ids = [c.id for c in lista]
    if len(ids) != len(set(ids)):
        raise ValueError("ids de cenário repetidos")
    return lista


def choques(cenario: Cenario, painel: pd.DataFrame, fatores: list[str]) -> dict:
    """Vetor de choques do cenário nos `fatores` + CDI do período + n_dias."""
    if cenario.tipo == "historico":
        ch = choque_janela(painel, cenario.inicio, cenario.fim, fatores)
        if ch["n_dias"] == 0:
            raise ValueError(f"cenário {cenario.id}: janela sem pregões no painel")
        return ch
    out = {f: float(cenario.choques.get(f, 0.0)) for f in fatores}
    cdi_dia = float(painel["CDI"].dropna().iloc[-1])
    out["CDI"] = (1 + cdi_dia) ** cenario.horizonte_dias - 1
    out["n_dias"] = cenario.horizonte_dias
    return out


def tabela_choques(cenarios: list[Cenario], painel: pd.DataFrame, fatores: list[str]) -> pd.DataFrame:
    linhas = []
    for c in cenarios:
        ch = choques(c, painel, fatores)
        linhas.append({"id": c.id, "cenario": c.nome, "tipo": c.tipo, "inicio": c.inicio, "fim": c.fim,
                       **{f: ch[f] for f in fatores}, "CDI": ch["CDI"], "n_dias": ch["n_dias"],
                       "descricao": c.descricao})
    return pd.DataFrame(linhas).set_index("id")
