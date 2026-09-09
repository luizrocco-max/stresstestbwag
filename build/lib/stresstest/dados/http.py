"""Download com retries e cache simples em disco."""
from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

from ..config import TIMEOUT, USER_AGENT

log = logging.getLogger(__name__)


def baixar(url: str, destino: Path, tentativas: int = 4, params: dict | None = None) -> Path:
    """Baixa `url` para `destino` (streaming). Retenta com backoff exponencial."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(destino.suffix + ".part")
    erro: Exception | None = None
    for i in range(tentativas):
        try:
            with requests.get(url, params=params, stream=True, timeout=TIMEOUT,
                              headers={"User-Agent": USER_AGENT}) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for bloco in r.iter_content(chunk_size=1 << 20):
                        f.write(bloco)
            tmp.replace(destino)
            return destino
        except Exception as e:  # noqa: BLE001
            erro = e
            espera = 2 ** i
            log.warning("falha ao baixar %s (%s); nova tentativa em %ss", url, e, espera)
            time.sleep(espera)
    raise RuntimeError(f"não foi possível baixar {url}: {erro}")


def get_json(url: str, params: dict | None = None, tentativas: int = 4):
    erro: Exception | None = None
    for i in range(tentativas):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            erro = e
            time.sleep(2 ** i)
    raise RuntimeError(f"falha em {url}: {erro}")


def cache_valido(caminho: Path, max_idade_horas: float | None) -> bool:
    """True se o arquivo existe e (quando `max_idade_horas` é dado) é recente."""
    if not caminho.exists():
        return False
    if max_idade_horas is None:
        return True
    idade_h = (time.time() - caminho.stat().st_mtime) / 3600
    return idade_h < max_idade_horas
