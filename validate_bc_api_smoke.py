"""Smoke / validacion rapida BC API + enrich (reconecta Innova tras OData)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import pymssql

from bc_ile_hybrid import build_bundle_from_ile, download_ile_eg_api
from generar_reporte_biomasa import (
    fmt_num,
    load_app_credentials,
    parse_user_date,
    resolve_db_credentials,
)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="01/04/2026")
    p.add_argument("--end", default="01/04/2026")
    args = p.parse_args()
    base = Path(__file__).resolve().parent
    load_app_credentials(base)
    start = parse_user_date(args.start)
    end = parse_user_date(args.end)
    ns = argparse.Namespace(
        user=os.getenv("DB_USER", ""),
        password=os.getenv("DB_PASSWORD", ""),
        cred_target="biomasa_sql_innova",
        save_creds=False,
    )
    user, password = resolve_db_credentials(ns)
    raw, transport = download_ile_eg_api(start, end, verbose=True)
    print("Reconectando Innova...")
    conn = pymssql.connect(
        server=os.getenv("DB_SERVER"),
        user=user,
        password=password,
        database=os.getenv("DB_NAME", "Innova"),
        login_timeout=8,
        timeout=600,
    )
    try:
        cruce, balance = build_bundle_from_ile(
            conn, raw, start, end, transport=transport, verbose=True
        )
    finally:
        conn.close()
    t = cruce["totals"]
    print("\n=== Cruce ===")
    print(f"lotes={t['lotes_bc']:,} qty={fmt_num(t['qty_total'])} kg={fmt_num(t['kg_total'])}")
    print("=== Balance ===")
    print(
        f"kg_ventas={fmt_num(balance['kg_ventas'])} lotes_ventas={balance['lotes_ventas']:,} "
        f"empaque_kg={fmt_num(balance['kg_empaque_mes'])} stock_final_kg={fmt_num(balance['kg_stock_final'])}"
    )
    print(f"integridad={balance['movimientos_integridad']}")
    print(f"transport={transport}")


if __name__ == "__main__":
    main()
