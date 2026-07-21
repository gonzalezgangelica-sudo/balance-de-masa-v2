#!/usr/bin/env python3
"""Contraste por lote: Innova (prday, weight, despesque) vs BC (Fecha empaque, Kilos).

Une por proc_packs.number = ILE [Lot No.] (almacenes E/G).
Sirve para validar la premisa hibrida antes de migrar BC a API.

Uso:
  python contrastar_lote_innova_bc.py --start 01/04/2026 --end 30/04/2026
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
from pathlib import Path
from typing import Any

import pymssql

from generar_reporte_biomasa import (
    DEFAULT_DATABASE,
    DEFAULT_SERVER,
    SQL_BC_LOCATION_EG,
    SQL_INNOVA_LOT,
    SQL_SALIDA_CAJA,
    fetch_rows,
    fmt_num,
    load_app_credentials,
    parse_user_date,
    resolve_bc_credentials,
    resolve_db_credentials,
    sql_bc_ile_empaque_from,
    to_float,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Contraste lote Innova (prday/weight) vs BC (Fecha empaque/Kilos)"
    )
    p.add_argument("--start", required=True, help="Inicio dd/mm/aaaa")
    p.add_argument("--end", required=True, help="Fin dd/mm/aaaa")
    p.add_argument("--server", default=os.getenv("DB_SERVER", DEFAULT_SERVER))
    p.add_argument("--database", default=os.getenv("DB_NAME", DEFAULT_DATABASE))
    p.add_argument("--user", default=os.getenv("DB_USER", ""))
    p.add_argument("--password", default=os.getenv("DB_PASSWORD", ""))
    p.add_argument("--bc-server", default=os.getenv("BC_SERVER", ""))
    p.add_argument("--bc-database", default=os.getenv("BC_DATABASE", ""))
    p.add_argument("--bc-user", default=os.getenv("BC_USER", ""))
    p.add_argument("--bc-password", default=os.getenv("BC_PASSWORD", ""))
    p.add_argument("--cred-target", default="biomasa_sql_innova")
    p.add_argument("--bc-cred-target", default="biomasa_sql_bc")
    p.add_argument("--save-creds", action="store_true")
    p.add_argument(
        "--max-detalle",
        type=int,
        default=40,
        help="Filas de detalle en el Markdown (por mayor |dif kg|)",
    )
    return p.parse_args()


def fetch_innova_lotes(
    conn: pymssql.Connection, start: dt.date, end: dt.date
) -> list[dict[str, Any]]:
    q = f"""
    SELECT
      {SQL_INNOVA_LOT} AS lot,
      MIN(CAST(p.prday AS date)) AS prday_min,
      MAX(CAST(p.prday AS date)) AS prday_max,
      SUM(CAST(p.weight AS float)) AS kg_innova,
      COUNT(*) AS packs,
      MAX(NULLIF(LTRIM(RTRIM(CAST(p.material AS varchar(50)))), '')) AS material
    FROM dbo.proc_packs p
    JOIN dbo.proc_materials m ON m.material = p.material
    WHERE p.prday >= %s
      AND p.prday < DATEADD(day, 1, %s)
      AND {SQL_SALIDA_CAJA}
      AND NULLIF(LTRIM(RTRIM({SQL_INNOVA_LOT})), '') IS NOT NULL
    GROUP BY {SQL_INNOVA_LOT}
    ORDER BY lot;
    """
    params = (start.isoformat(), end.isoformat())
    rows = fetch_rows(conn.cursor(), q, params)
    return [
        {
            "lot": str(r["lot"]).strip(),
            "prday_min": r.get("prday_min"),
            "prday_max": r.get("prday_max"),
            "kg_innova": to_float(r.get("kg_innova")),
            "packs": int(r.get("packs") or 0),
            "material": str(r.get("material") or "").strip(),
        }
        for r in rows
    ]


def discover_vw_stolt_lot_column(conn: pymssql.Connection) -> str | None:
    """Busca en vw_stolt una columna tipica de lote/caja."""
    q = """
    SELECT COLUMN_NAME
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'vw_stolt'
    ORDER BY ORDINAL_POSITION;
    """
    try:
        cols = [str(r["COLUMN_NAME"]).strip().lower() for r in fetch_rows(conn.cursor(), q)]
    except Exception:
        return None
    candidates = (
        "number",
        "lote",
        "lot",
        "n_lote",
        "nlote",
        "caja",
        "pack_number",
        "numlote",
    )
    for name in candidates:
        if name in cols:
            # devolver nombre real (case from DB)
            for r in fetch_rows(conn.cursor(), q):
                if str(r["COLUMN_NAME"]).strip().lower() == name:
                    return str(r["COLUMN_NAME"]).strip()
    return None


def fetch_innova_despesque_por_lote(
    conn: pymssql.Connection,
    start: dt.date,
    end: dt.date,
    lot_col: str | None,
) -> dict[str, dt.date]:
    if not lot_col:
        return {}
    # Identificador seguro de columna (solo alfanumerico/underscore)
    if not all(ch.isalnum() or ch == "_" for ch in lot_col):
        return {}
    q = f"""
    SELECT
      CAST(v.[{lot_col}] AS varchar(50)) AS lot,
      MIN(CAST(v.fdespesque AS date)) AS fdespesque_min
    FROM dbo.vw_stolt v
    WHERE v.fdespesque >= %s
      AND v.fdespesque < DATEADD(day, 1, %s)
      AND NULLIF(LTRIM(RTRIM(CAST(v.[{lot_col}] AS varchar(50)))), '') IS NOT NULL
    GROUP BY CAST(v.[{lot_col}] AS varchar(50));
    """
    try:
        rows = fetch_rows(conn.cursor(), q, (start.isoformat(), end.isoformat()))
    except Exception:
        return {}
    out: dict[str, dt.date] = {}
    for r in rows:
        lot = str(r["lot"]).strip()
        fe = r.get("fdespesque_min")
        if isinstance(fe, dt.datetime):
            fe = fe.date()
        if lot and isinstance(fe, dt.date):
            out[lot] = fe
    return out


def fetch_bc_lotes(
    conn: pymssql.Connection, start: dt.date, end: dt.date
) -> list[dict[str, Any]]:
    """Lotes BC E/G con Fecha empaque en el periodo (contraste vs Innova CAJA)."""
    q = f"""
    SELECT
      CAST(ile.[Lot No.] AS varchar(50)) AS lot,
      MIN(CAST(ile.[Fecha empaque] AS date)) AS fe_empaque_min,
      MAX(CAST(ile.[Fecha empaque] AS date)) AS fe_empaque_max,
      MAX(ABS(CAST(ile.[Kilos] AS float))) AS kg_bc,
      SUM(ABS(CAST(ile.[Quantity] AS float))) AS qty_bc,
      MAX(NULLIF(LTRIM(RTRIM(CAST(ile.[Item No.] AS varchar(50)))), '')) AS item_no
    FROM bc.[Item Ledger Entry] ile
    WHERE ile.[Fecha empaque] >= %s
      AND ile.[Fecha empaque] < DATEADD(day, 1, %s)
      AND {sql_bc_ile_empaque_from()}
      AND {SQL_BC_LOCATION_EG}
      AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
    GROUP BY CAST(ile.[Lot No.] AS varchar(50))
    ORDER BY lot;
    """
    params = (start.isoformat(), end.isoformat())
    rows = fetch_rows(conn.cursor(), q, params)
    return [
        {
            "lot": str(r["lot"]).strip(),
            "fe_empaque_min": r.get("fe_empaque_min"),
            "fe_empaque_max": r.get("fe_empaque_max"),
            "kg_bc": to_float(r.get("kg_bc")),
            "qty_bc": to_float(r.get("qty_bc")),
            "item_no": str(r.get("item_no") or "").strip(),
        }
        for r in rows
    ]


def _as_date(value: Any) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return None


def build_contrast(
    innova: list[dict[str, Any]],
    bc: list[dict[str, Any]],
    despesque: dict[str, dt.date],
) -> list[dict[str, Any]]:
    inn = {r["lot"]: r for r in innova}
    bcc = {r["lot"]: r for r in bc}
    lots = sorted(set(inn) | set(bcc))
    rows: list[dict[str, Any]] = []
    for lot in lots:
        i = inn.get(lot)
        b = bcc.get(lot)
        prday = _as_date(i.get("prday_min")) if i else None
        fe_bc = _as_date(b.get("fe_empaque_min")) if b else None
        kg_i = to_float(i.get("kg_innova")) if i else 0.0
        kg_b = to_float(b.get("kg_bc")) if b else 0.0
        dif_kg = kg_i - kg_b if i and b else None
        dif_dias = (prday - fe_bc).days if prday and fe_bc else None
        rows.append(
            {
                "lot": lot,
                "en_innova": bool(i),
                "en_bc": bool(b),
                "prday_innova": prday.isoformat() if prday else "",
                "fe_empaque_bc": fe_bc.isoformat() if fe_bc else "",
                "dif_dias_prday_vs_empaque": dif_dias if dif_dias is not None else "",
                "fdespesque_innova": (
                    despesque[lot].isoformat() if lot in despesque else ""
                ),
                "kg_innova": round(kg_i, 3) if i else "",
                "kg_bc": round(kg_b, 3) if b else "",
                "dif_kg_innova_menos_bc": (
                    round(dif_kg, 3) if dif_kg is not None else ""
                ),
                "packs_innova": int(i["packs"]) if i else "",
                "item_no_bc": b.get("item_no") if b else "",
                "material_innova": i.get("material") if i else "",
            }
        )
    return rows


def write_outputs(
    start: dt.date,
    end: dt.date,
    rows: list[dict[str, Any]],
    meta: dict[str, Any],
    out_dir: Path,
    max_detalle: int,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
    csv_path = out_dir / f"contraste_lote_innova_bc_{stamp}.csv"
    md_path = out_dir / f"contraste_lote_innova_bc_{stamp}.md"

    fieldnames = list(rows[0].keys()) if rows else [
        "lot",
        "en_innova",
        "en_bc",
        "prday_innova",
        "fe_empaque_bc",
        "dif_dias_prday_vs_empaque",
        "fdespesque_innova",
        "kg_innova",
        "kg_bc",
        "dif_kg_innova_menos_bc",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    both = [r for r in rows if r["en_innova"] and r["en_bc"]]
    only_i = [r for r in rows if r["en_innova"] and not r["en_bc"]]
    only_b = [r for r in rows if r["en_bc"] and not r["en_innova"]]
    same_day = [
        r
        for r in both
        if r["dif_dias_prday_vs_empaque"] != "" and int(r["dif_dias_prday_vs_empaque"]) == 0
    ]
    kg_close = [
        r
        for r in both
        if r["dif_kg_innova_menos_bc"] != "" and abs(float(r["dif_kg_innova_menos_bc"])) <= 0.05
    ]
    detalle = sorted(
        [r for r in both if r["dif_kg_innova_menos_bc"] != ""],
        key=lambda r: abs(float(r["dif_kg_innova_menos_bc"])),
        reverse=True,
    )[:max_detalle]

    lines = [
        f"# Contraste lote Innova vs BC ({start.strftime('%d/%m/%Y')} – {end.strftime('%d/%m/%Y')})",
        "",
        "Union: `proc_packs.number` = `Item Ledger Entry.[Lot No.]` (E/G).",
        "",
        "## Resumen",
        "",
        f"| Metrica | Valor |",
        f"|---------|-------|",
        f"| Lotes Innova CAJA (prday periodo) | {meta['n_innova']:,} |",
        f"| Lotes BC E/G (Fecha empaque en periodo) | {meta['n_bc']:,} |",
        f"| En ambos | {len(both):,} |",
        f"| Solo Innova | {len(only_i):,} |",
        f"| Solo BC | {len(only_b):,} |",
        f"| % match Innova→BC | {meta['pct_innova_in_bc']:.2f}% |",
        f"| % match BC→Innova | {meta['pct_bc_in_innova']:.2f}% |",
        f"| Misma fecha prday = Fecha empaque | {len(same_day):,} / {len(both):,} |",
        f"| Kg casi iguales (±0,05) | {len(kg_close):,} / {len(both):,} |",
        f"| Columna despesque en vw_stolt | {meta['vw_lot_col'] or 'no encontrada'} |",
        f"| Lotes con fdespesque | {meta['n_despesque']:,} |",
        "",
        f"CSV completo: `{csv_path.name}`",
        "",
        f"## Top {max_detalle} diferencias de kg (Innova − BC) en lotes comunes",
        "",
        "| Lote | prday | Fecha empaque BC | Δ dias | kg Innova | kg BC | Δ kg |",
        "|------|-------|------------------|--------|-----------|-------|------|",
    ]
    for r in detalle:
        lines.append(
            f"| `{r['lot']}` | {r['prday_innova'] or '—'} | {r['fe_empaque_bc'] or '—'} | "
            f"{r['dif_dias_prday_vs_empaque'] if r['dif_dias_prday_vs_empaque'] != '' else '—'} | "
            f"{r['kg_innova']} | {r['kg_bc']} | {r['dif_kg_innova_menos_bc']} |"
        )
    lines.extend(
        [
            "",
            "## Lectura para premisa hibrida",
            "",
            "- Si **prday ≈ Fecha empaque** y **weight ≈ Kilos** en la mayoria de lotes, "
            "Innova puede sustituir esos campos custom de BC en un escenario API.",
            "- Lotes **solo BC** no tendran enriquecimiento Innova (stock antiguo / sin pack).",
            "- **fdespesque** solo si `vw_stolt` expone columna de lote.",
            "",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return csv_path, md_path


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    load_app_credentials(base_dir)
    args = parse_args()
    start = parse_user_date(args.start)
    end = parse_user_date(args.end)
    if end < start:
        raise SystemExit("La fecha fin debe ser >= inicio")

    db_user, db_password = resolve_db_credentials(args)
    print(f"Periodo: {start.strftime('%d/%m/%Y')} – {end.strftime('%d/%m/%Y')}")
    print("Conectando Innova...")
    inn_conn = pymssql.connect(
        server=args.server,
        user=db_user,
        password=db_password,
        database=args.database,
        login_timeout=8,
        timeout=180,
    )
    try:
        innova = fetch_innova_lotes(inn_conn, start, end)
        print(f"  Innova lotes CAJA: {len(innova):,}")
        lot_col = discover_vw_stolt_lot_column(inn_conn)
        print(f"  vw_stolt columna lote: {lot_col or '(no encontrada)'}")
        despesque = fetch_innova_despesque_por_lote(inn_conn, start, end, lot_col)
        print(f"  Lotes con fdespesque: {len(despesque):,}")
    finally:
        inn_conn.close()

    bc_server, bc_database, bc_user, bc_password = resolve_bc_credentials(args)
    print("Conectando Business Central (SQL)...")
    bc_conn = pymssql.connect(
        server=bc_server,
        user=bc_user,
        password=bc_password,
        database=bc_database,
        login_timeout=60,
        timeout=600,
    )
    try:
        bc = fetch_bc_lotes(bc_conn, start, end)
        print(f"  BC lotes E/G: {len(bc):,}")
    finally:
        bc_conn.close()

    rows = build_contrast(innova, bc, despesque)
    both = sum(1 for r in rows if r["en_innova"] and r["en_bc"])
    n_i = len(innova)
    n_b = len(bc)
    meta = {
        "n_innova": n_i,
        "n_bc": n_b,
        "pct_innova_in_bc": (100.0 * both / n_i) if n_i else 0.0,
        "pct_bc_in_innova": (100.0 * both / n_b) if n_b else 0.0,
        "vw_lot_col": lot_col,
        "n_despesque": len(despesque),
    }
    csv_path, md_path = write_outputs(
        start, end, rows, meta, base_dir / "Reports", args.max_detalle
    )
    print()
    print("Resumen")
    print("=" * 40)
    print(f"En ambos: {both:,} | Solo Innova: {n_i - both:,} | Solo BC: {n_b - both:,}")
    print(f"Match Innova->BC: {fmt_num(meta['pct_innova_in_bc'])}%")
    print(f"Match BC->Innova: {fmt_num(meta['pct_bc_in_innova'])}%")
    print(f"CSV: {csv_path}")
    print(f"MD:  {md_path}")


if __name__ == "__main__":
    main()
