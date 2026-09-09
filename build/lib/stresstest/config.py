"""Caminhos e constantes globais."""
from __future__ import annotations

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("STRESSTEST_CACHE", RAIZ / "cache"))
SAIDA = Path(os.environ.get("STRESSTEST_SAIDA", RAIZ / "saida"))

USER_AGENT = "Mozilla/5.0 (compatible; stresstest-bwag/0.1)"
TIMEOUT = 120

# Primeiro mês com dados carregados por padrão (cobre a crise de 2008).
INICIO_PADRAO = "2007-06"

# Janela de estimação dos betas, em meses.
JANELA_PADRAO_MESES = 24
MIN_OBS_REGRESSAO = 120
