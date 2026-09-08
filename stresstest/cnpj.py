"""Normalização de CNPJ."""
from __future__ import annotations

import re


def normalizar(cnpj: str | int) -> str:
    """Devolve só os 14 dígitos (zeros à esquerda preservados)."""
    digitos = re.sub(r"\D", "", str(cnpj))
    if not digitos:
        raise ValueError(f"CNPJ inválido: {cnpj!r}")
    return digitos.zfill(14)


def formatar(cnpj: str | int) -> str:
    d = normalizar(cnpj)
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
