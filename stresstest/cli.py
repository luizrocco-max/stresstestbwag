"""Linha de comando: python -m stresstest <comando>."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import cenarios as mod_cenarios
from . import motor, relatorio
from .config import INICIO_PADRAO, JANELA_PADRAO_MESES, SAIDA
from .dados import cvm
from .fatores import FATORES, FATORES_PADRAO, choque_janela, painel


def cmd_buscar(args):
    res = cvm.buscar(args.texto, somente_ativos=not args.todos, limite=args.limite)
    if res.empty:
        print("nenhum fundo encontrado")
        return
    res = res.copy()
    res["pl_mm"] = (res["pl"] / 1e6).round(0)
    res["cnpj"] = res["cnpj"].map(lambda c: f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}")
    print(res[["cnpj", "nome", "classe_anbima", "gestor", "pl_mm"]].to_string(index=False, max_colwidth=55))


def cmd_fatores(args):
    p = painel(args.inicio)
    if args.de and args.ate:
        ch = choque_janela(p, args.de, args.ate)
        print(f"Movimento dos fatores de {args.de} a {args.ate} ({ch['n_dias']} pregões):")
        for f, meta in FATORES.items():
            v = ch.get(f)
            if v is None or pd.isna(v):
                continue
            print(f"  {f:<10} {v:+.2%}" if meta["tipo"] == "retorno" else f"  {f:<10} {v:+.2f} p.p.")
        print(f"  {'CDI':<10} {ch['CDI']:+.2%}")
    else:
        print(p.tail(10).to_string())
        print(f"\n{len(p)} pregões de {p.index.min().date()} a {p.index.max().date()}")


def cmd_cenarios(args):
    p = painel(args.inicio)
    lista = mod_cenarios.carregar(args.cenarios)
    fat = args.fatores.split(",") if args.fatores else FATORES_PADRAO
    tab = mod_cenarios.tabela_choques(lista, p, fat)
    with pd.option_context("display.width", 200, "display.float_format", "{:+.3f}".format):
        print(tab.drop(columns=["descricao"]).to_string())


def cmd_atualizar(args):
    logging.info("atualizando painel de fatores")
    p = painel(args.inicio)
    logging.info("painel: %d pregões até %s", len(p), p.index.max().date())
    cad = cvm.cadastro(forcar=True)
    logging.info("cadastro CVM: %d classes", len(cad))
    if args.cotas:
        cvm.cotas([], args.inicio[:7])
        logging.info("cache de cotas CVM atualizado desde %s", args.inicio[:7])


def cmd_rodar(args):
    carteira = motor.carregar_carteira(args.carteira)
    lista = mod_cenarios.carregar(args.cenarios)
    fat = args.fatores.split(",") if args.fatores else FATORES_PADRAO
    res = motor.rodar(carteira, lista, fatores=fat, janela_meses=args.janela,
                      inicio_dados=args.inicio, metodo=args.metodo)
    print(relatorio.resumo_terminal(res))
    saida = Path(args.saida) if args.saida else SAIDA / f"stress_{Path(args.carteira).stem}_{datetime.now():%Y%m%d}.xlsx"
    relatorio.excel(res, saida)
    print(f"\nRelatório salvo em: {saida}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="stresstest", description="Stress test de fundos (CVM + fatores de mercado)")
    ap.add_argument("-v", "--verbose", action="store_true", help="log detalhado")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("buscar", help="busca fundos no cadastro da CVM")
    b.add_argument("texto")
    b.add_argument("--todos", action="store_true", help="inclui fundos cancelados/em liquidação")
    b.add_argument("--limite", type=int, default=15)
    b.set_defaults(func=cmd_buscar)

    f = sub.add_parser("fatores", help="mostra o painel de fatores ou o movimento numa janela")
    f.add_argument("--de"); f.add_argument("--ate")
    f.add_argument("--inicio", default=f"{INICIO_PADRAO}-01")
    f.set_defaults(func=cmd_fatores)

    c = sub.add_parser("cenarios", help="lista os choques de cada cenário")
    c.add_argument("--cenarios", default=None)
    c.add_argument("--fatores", default=None, help="lista separada por vírgula (padrão: %s)" % ",".join(FATORES_PADRAO))
    c.add_argument("--inicio", default=f"{INICIO_PADRAO}-01")
    c.set_defaults(func=cmd_cenarios)

    a = sub.add_parser("atualizar", help="baixa/atualiza os dados de mercado e cadastro")
    a.add_argument("--inicio", default=f"{INICIO_PADRAO}-01")
    a.add_argument("--cotas", action="store_true", help="também baixa todos os informes diários da CVM (demorado)")
    a.set_defaults(func=cmd_atualizar)

    r = sub.add_parser("rodar", help="roda o stress test de uma carteira (YAML)")
    r.add_argument("carteira")
    r.add_argument("--cenarios", default=None, help="YAML de cenários (padrão: cenarios/cenarios.yaml)")
    r.add_argument("--saida", default=None, help="arquivo .xlsx de saída")
    r.add_argument("--janela", type=int, default=JANELA_PADRAO_MESES, help="janela de estimação dos betas (meses)")
    r.add_argument("--inicio", default=INICIO_PADRAO, help="primeiro mês de dados (AAAA-MM)")
    r.add_argument("--metodo", choices=["melhor", "modelo", "historico"], default="melhor")
    r.add_argument("--fatores", default=None, help="fatores separados por vírgula")
    r.set_defaults(func=cmd_rodar)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
