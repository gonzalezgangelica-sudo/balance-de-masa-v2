#!/usr/bin/env python3
"""Validacion diaria: salidas Innova por regtime enlazadas a venta BC por lote."""
from __future__ import annotations

import argparse
import calendar
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

import pymssql

from generar_reporte_biomasa import (
    DEFAULT_DATABASE,
    DEFAULT_SERVER,
    SQL_INNOVA_LOT,
    SQL_LEGACY_ES_SALIDA,
    fetch_bc_salidas_pedido,
    fetch_rows,
    fmt_num,
    load_dotenv_file,
    parse_user_date,
    resolve_bc_credentials,
    to_float,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validacion diaria salidas Innova vs BC por lote")
    p.add_argument("--fecha", required=True, help="Fecha salida Innova dd/mm/aaaa")
    p.add_argument("--max-detalle", type=int, default=30, help="Filas de detalle por lote")
    p.add_argument("--bc-server", default=os.getenv("BC_SERVER", ""))
    p.add_argument("--bc-database", default=os.getenv("BC_DATABASE", ""))
    p.add_argument("--bc-user", default=os.getenv("BC_USER", ""))
    p.add_argument("--bc-password", default=os.getenv("BC_PASSWORD", ""))
    return p.parse_args()


def month_range(fecha) -> tuple:
    start = fecha.replace(day=1)
    last = calendar.monthrange(fecha.year, fecha.month)[1]
    end = fecha.replace(day=last)
    return start, end


def fetch_innova_salidas_dia(conn: pymssql.Connection, day_iso: str) -> list[dict[str, Any]]:
    q = f"""
    SELECT
      {SQL_INNOVA_LOT} AS lot,
      m.material,
      m.name AS material_nombre,
      SUM(CAST(p.weight AS float)) AS kg_innova,
      COUNT(*) AS packs,
      MIN(p.regtime) AS primera_hora,
      MAX(p.regtime) AS ultima_hora
    FROM dbo.proc_packs p
    JOIN dbo.proc_materials m ON m.material = p.material
    WHERE CAST(p.regtime AS date) = %s
      AND {SQL_LEGACY_ES_SALIDA}
      AND NULLIF(LTRIM(RTRIM({SQL_INNOVA_LOT})), '') IS NOT NULL
    GROUP BY {SQL_INNOVA_LOT}, m.material, m.name
    ORDER BY kg_innova DESC, lot;
    """
    return fetch_rows(conn.cursor(), q, (day_iso,))


def fetch_innova_totales_dia(conn: pymssql.Connection, day_iso: str) -> dict[str, Any]:
    q = f"""
    SELECT
      COUNT(*) AS packs,
      COUNT(DISTINCT {SQL_INNOVA_LOT}) AS lotes,
      SUM(CAST(p.weight AS float)) AS kg
    FROM dbo.proc_packs p
    JOIN dbo.proc_materials m ON m.material = p.material
    WHERE CAST(p.regtime AS date) = %s
      AND {SQL_LEGACY_ES_SALIDA}
    """
    row = fetch_rows(conn.cursor(), q, (day_iso,))[0]
    return {
        "packs": int(row["packs"] or 0),
        "lotes": int(row["lotes"] or 0),
        "kg": to_float(row["kg"]),
    }


def fetch_bc_lot_posting_dates(
    conn: pymssql.Connection,
    lots: list[str],
    month_start: str,
    month_end: str,
) -> dict[str, dict[str, Any]]:
    """Fechas de contabilizacion BC por lote (solo ventas del mes)."""
    if not lots:
        return {}
    out: dict[str, dict[str, Any]] = {}
    cursor = conn.cursor()
    for i in range(0, len(lots), 400):
        batch = lots[i : i + 400]
        ph = ",".join(["%s"] * len(batch))
        q = f"""
        SELECT
          CAST(ile.[Lot No.] AS varchar(50)) AS lot,
          MIN(CAST(ile.[Posting Date] AS date)) AS bc_fecha_min,
          MAX(CAST(ile.[Posting Date] AS date)) AS bc_fecha_max,
          MAX(ile.[Document No.]) AS bc_documento
        FROM bc.[Item Ledger Entry] ile
        WHERE ile.[Entry Type] = 1
          AND ile.[Posting Date] >= %s
          AND ile.[Posting Date] < DATEADD(day, 1, %s)
          AND CAST(ile.[Lot No.] AS varchar(50)) IN ({ph})
        GROUP BY CAST(ile.[Lot No.] AS varchar(50))
        """
        params = (month_start, month_end) + tuple(batch)
        for row in fetch_rows(cursor, q, params):
            out[str(row["lot"]).strip()] = row
    return out


def main() -> None:
    load_dotenv_file(Path(".env"))
    args = parse_args()
    fecha = parse_user_date(args.fecha)
    day_iso = fecha.isoformat()
    mes_ini, mes_fin = month_range(fecha)

    innova = pymssql.connect(
        server=os.getenv("DB_SERVER", DEFAULT_SERVER),
        user=os.getenv("DB_USER", ""),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", DEFAULT_DATABASE),
        timeout=120,
    )
    try:
        totales = fetch_innova_totales_dia(innova, day_iso)
        filas = fetch_innova_salidas_dia(innova, day_iso)
    finally:
        innova.close()

    lot_kg: dict[str, float] = defaultdict(float)
    lot_packs: dict[str, int] = defaultdict(int)
    lot_materiales: dict[str, set[str]] = defaultdict(set)
    for r in filas:
        lot = str(r["lot"]).strip()
        lot_kg[lot] += to_float(r["kg_innova"])
        lot_packs[lot] += int(r["packs"] or 0)
        lot_materiales[lot].add(str(r["material_nombre"]))

    lots_unicos = sorted(lot_kg.keys())
    if not lots_unicos:
        print(f"No hay salidas Innova el {fecha.strftime('%d/%m/%Y')}.")
        return

    print("Consultando BC del mes (ventas por lote)...", flush=True)
    bc_server, bc_db, bc_user, bc_pw = resolve_bc_credentials(args)
    bc = pymssql.connect(
        server=bc_server,
        user=bc_user,
        password=bc_pw,
        database=bc_db,
        login_timeout=60,
        timeout=600,
    )
    try:
        bc_mes = fetch_bc_salidas_pedido(bc, mes_ini, mes_fin)
        bc_by_lot = {str(r["lot"]).strip(): r for r in bc_mes["by_lot"]}
        bc_fechas = fetch_bc_lot_posting_dates(
            bc,
            lots_unicos,
            mes_ini.isoformat(),
            mes_fin.isoformat(),
        )
    finally:
        bc.close()

    enlazados: list[tuple[str, float, dict[str, Any]]] = []
    sin_bc: list[tuple[str, float]] = []
    for lot in lots_unicos:
        kg_i = lot_kg[lot]
        bc_row = bc_by_lot.get(lot)
        if bc_row:
            enlazados.append((lot, kg_i, bc_row))
        else:
            sin_bc.append((lot, kg_i))

    tot_kg_innova_lotes = sum(lot_kg.values())
    tot_kg_innova_enl = sum(x[1] for x in enlazados)
    tot_kg_bc = sum(to_float(x[2]["kg"]) for x in enlazados)
    tot_qty_con = sum(to_float(x[2]["qty_con_pedido"]) for x in enlazados)
    tot_qty_sin = sum(to_float(x[2]["qty_sin_pedido"]) for x in enlazados)
    tot_kg_con = sum(to_float(x[2]["kg_con_pedido"]) for x in enlazados)
    tot_kg_sin = sum(to_float(x[2]["kg_sin_pedido"]) for x in enlazados)

    mismo_dia = 0
    otro_dia = 0
    for lot, _, _ in enlazados:
        fe = bc_fechas.get(lot, {})
        fmin = fe.get("bc_fecha_min")
        if fmin == fecha:
            mismo_dia += 1
        elif fmin:
            otro_dia += 1

    print("=" * 78)
    print(f"VALIDACION DIARIA SALIDAS — {fecha.strftime('%d/%m/%Y')}")
    print("=" * 78)
    print()
    print("1. CRITERIO DE VALIDACION")
    print("   - Dia Innova: salidas en proc_packs por regtime (pkpackaging <> 3).")
    print("   - Lote/caja: proc_packs.number.")
    print("   - Venta BC asociada: mismo valor en [Item Ledger Entry].[Lot No.], Entry Type = 1.")
    print("   - Kg Innova = proc_packs.weight | Kg BC = ABS([Kilos]) en ILE.")
    print("   - Pedido BC = [Order No.] del albaran (Sales Shipment Line).")
    print("   - La contabilizacion BC puede ser el mismo dia u otro dia del mes.")
    print()
    print("2. SALIDAS INNOVA DEL DIA")
    print(f"   Packs:  {totales['packs']:,}")
    print(f"   Lotes:  {totales['lotes']:,}")
    print(f"   Kg:     {fmt_num(totales['kg'])}")
    print()
    print("3. CRUCE CON VENTA BC (solo lotes que salieron este dia)")
    pct_enl = len(enlazados) / len(lots_unicos) * 100 if lots_unicos else 0
    print(f"   Lotes del dia:              {len(lots_unicos):,}")
    print(f"   Con venta BC enlazada:      {len(enlazados):,} ({pct_enl:.1f}%)")
    print(f"   Sin venta BC:               {len(sin_bc):,}")
    print(f"   Kg Innova (lotes):          {fmt_num(tot_kg_innova_lotes)}")
    print(f"   Kg Innova enlazados:        {fmt_num(tot_kg_innova_enl)}")
    print(f"   Kg BC ([Kilos]):            {fmt_num(tot_kg_bc)}")
    print(f"   Diferencia kg (I - BC):     {fmt_num(tot_kg_innova_enl - tot_kg_bc)}")
    print()
    print("4. VENTA BC EN ESOS LOTES")
    print(f"   Con pedido:  {fmt_num(tot_qty_con)} ud / {fmt_num(tot_kg_con)} kg")
    print(f"   Sin pedido:  {fmt_num(tot_qty_sin)} ud / {fmt_num(tot_kg_sin)} kg")
    print(f"   Lotes contabilizados el mismo dia: {mismo_dia:,}")
    print(f"   Lotes contabilizados otro dia:     {otro_dia:,}")
    print()
    print("5. DETALLE POR LOTE (ordenado por kg Innova)")
    print("-" * 78)
    print(
        f"{'Lote':<12} {'Kg Inn.':>9} {'Kg BC':>9} {'Dif kg':>8} "
        f"{'Ud':>5} {'Pedido':<13} {'F.BC':<11} Material"
    )
    print("-" * 78)

    for lot in sorted(lots_unicos, key=lambda l: lot_kg[l], reverse=True)[: args.max_detalle]:
        kg_i = lot_kg[lot]
        bc_row = bc_by_lot.get(lot)
        mat = ", ".join(sorted(lot_materiales[lot]))[:32]
        if bc_row:
            kg_b = to_float(bc_row["kg"])
            qty = to_float(bc_row["qty"])
            order = bc_row.get("order_no") or "(sin pedido)"
            fe = bc_fechas.get(lot, {})
            fmin = fe.get("bc_fecha_min")
            fmax = fe.get("bc_fecha_max")
            if fmin and fmax:
                fbc = fmin.strftime("%d/%m") if fmin == fmax else f"{fmin:%d/%m}-{fmax:%d/%m}"
            else:
                fbc = "—"
            print(
                f"{lot:<12} {kg_i:>9.2f} {kg_b:>9.2f} {kg_i-kg_b:>8.2f} "
                f"{qty:>5.0f} {str(order)[:13]:<13} {fbc:<11} {mat}"
            )
        else:
            print(f"{lot:<12} {kg_i:>9.2f} {'—':>9} {'—':>8} {'—':>5} {'SIN BC':<13} {'—':<11} {mat}")

    if len(lots_unicos) > args.max_detalle:
        print(f"... +{len(lots_unicos) - args.max_detalle} lotes (usa --max-detalle N)")

    if sin_bc:
        print()
        print(f"6. LOTES SIN VENTA BC ({len(sin_bc)}) — muestra")
        for lot, kg in sorted(sin_bc, key=lambda x: x[1], reverse=True)[:12]:
            print(f"   {lot}: {fmt_num(kg)} kg — {', '.join(sorted(lot_materiales[lot]))[:50]}")

    print()
    print("7. CONCLUSION")
    if totales["kg"]:
        print(
            f"   - {tot_kg_innova_enl/totales['kg']*100:.1f}% del kg del dia tiene venta BC por lote."
        )
    if tot_kg_innova_enl and tot_kg_bc:
        dif = abs(tot_kg_innova_enl - tot_kg_bc)
        print(f"   - Desviacion kg en enlazados: {fmt_num(dif)} ({dif/tot_kg_innova_enl*100:.2f}%).")
    if sin_bc:
        print(f"   - {len(sin_bc)} lotes sin BC: revisar si aun no contabilizados o lote distinto en ERP.")
    if otro_dia:
        print(f"   - {otro_dia} lotes se vendieron en BC en fecha distinta a la salida Innova.")


if __name__ == "__main__":
    main()
