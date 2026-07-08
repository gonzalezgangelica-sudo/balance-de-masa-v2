"""Compara ventas BC (kg) por Posting Date vs kg enlazados del reporte (por prday Innova)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pymssql

import generar_reporte_biomasa as gr

BC_REFERENCIA = {
    "02/03/2026": 14041.60,
    "03/03/2026": 12285.60,
    "04/03/2026": 15153.30,
    "05/03/2026": 27073.30,
    "06/03/2026": 26129.70,
    "09/03/2026": 14012.20,
    "10/03/2026": 10573.90,
    "11/03/2026": 15975.80,
    "12/03/2026": 30078.50,
    "13/03/2026": 27004.60,
    "16/03/2026": 11875.50,
    "17/03/2026": 20731.30,
    "18/03/2026": 29161.00,
    "19/03/2026": 16227.50,
    "20/03/2026": 16318.60,
    "23/03/2026": 15570.30,
    "24/03/2026": 16013.70,
    "25/03/2026": 27107.20,
    "26/03/2026": 31166.30,
    "27/03/2026": 30214.20,
    "30/03/2026": 33733.70,
    "31/03/2026": 23572.70,
}


def fetch_bc_kg_por_posting(conn, start: dt.date, end: dt.date) -> dict[str, float]:
    cursor = conn.cursor()
    q = f"""
    SELECT
      CAST(ile.[Posting Date] AS date) AS fecha,
      SUM(ABS(CAST(ile.[Kilos] AS float))) AS kg
    FROM bc.[Item Ledger Entry] ile
    WHERE ile.[Posting Date] >= %s
      AND ile.[Posting Date] < DATEADD(day, 1, %s)
      AND {gr.SQL_BC_ILE_SALE}
      AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
    GROUP BY CAST(ile.[Posting Date] AS date)
    ORDER BY fecha;
    """
    rows = gr.fetch_rows(cursor, q, (start.isoformat(), end.isoformat()))
    return {r["fecha"].strftime("%d/%m/%Y"): gr.to_float(r["kg"]) for r in rows}


def main() -> None:
    base = Path(__file__).resolve().parent
    gr.load_dotenv_file(base / ".env")
    start = dt.date(2026, 3, 1)
    end = dt.date(2026, 3, 31)

    args = gr.parse_args()
    db_user, db_password = gr.resolve_db_credentials(args)
    bc_server, bc_database, bc_user, bc_password = gr.resolve_bc_credentials(args)

    innova = pymssql.connect(
        server=args.server,
        user=db_user,
        password=db_password,
        database=args.database,
        login_timeout=8,
        timeout=180,
    )
    bc = pymssql.connect(
        server=bc_server,
        user=bc_user,
        password=bc_password,
        database=bc_database,
        login_timeout=30,
        timeout=600,
    )
    try:
        report = gr.build_report_data(innova, start, end, "legacy")
        innova_lotes = gr.fetch_innova_salidas_lotes(innova, start, end)
        bc_data = gr.fetch_bc_salidas_pedido(bc, start, end)
        gr.attach_bc_cruce_to_report(report, bc_data, innova_lotes)
        gr.finalize_balance_kpis(report)

        bc_por_posting = fetch_bc_kg_por_posting(bc, start, end)
        report_por_prday = {
            d["fecha"]: gr.to_float(d.get("bc_kg_bc_enlazado", 0))
            for d in report["bc_cruce_detalle"]
        }

        # Lotes enlazados cuya contabilización BC cae en día distinto al prday Innova
        bc_by_lot = {str(r["lot"]).strip(): r for r in bc_data["by_lot"]}
        innova_by_date: dict[str, list] = {}
        for row in innova_lotes:
            innova_by_date.setdefault(row["fecha"].strftime("%d/%m/%Y"), []).append(row)

        desfase: dict[str, dict[str, float]] = {}
        for fecha_prday, lotes in innova_by_date.items():
            for lot_row in lotes:
                lot = str(lot_row["lot"]).strip()
                bc_row = bc_by_lot.get(lot)
                if not bc_row:
                    continue
                for order_row in bc_data.get("by_lot_order", []):
                    if str(order_row["lot"]).strip() != lot:
                        continue
                    post = order_row.get("posting_date_min")
                    if not post:
                        continue
                    post_str = post.strftime("%d/%m/%Y")
                    if post_str == fecha_prday:
                        continue
                    kg = gr.to_float(order_row["kg"])
                    desfase.setdefault(fecha_prday, {})
                    desfase[fecha_prday][post_str] = desfase[fecha_prday].get(post_str, 0) + kg

        print("COMPARATIVA BC kg — marzo 2026")
        print("=" * 110)
        print(
            f"{'Fecha':<12} {'BC ref (user)':>14} {'BC ILE posting':>14} "
            f"{'Ref-ILE':>10} {'Rpt enlazado':>14} {'Ref-Rpt':>10} {'ILE-Rpt':>10}"
        )
        print("-" * 110)

        tot_ref = tot_ile = tot_rpt = 0.0
        diffs_ref_ile: list[tuple[str, float]] = []
        diffs_ref_rpt: list[tuple[str, float]] = []

        for fecha in sorted(BC_REFERENCIA, key=lambda x: dt.datetime.strptime(x, "%d/%m/%Y")):
            ref = BC_REFERENCIA[fecha]
            ile = bc_por_posting.get(fecha, 0.0)
            rpt = report_por_prday.get(fecha, 0.0)
            d_ref_ile = ref - ile
            d_ref_rpt = ref - rpt
            d_ile_rpt = ile - rpt
            tot_ref += ref
            tot_ile += ile
            tot_rpt += rpt
            if abs(d_ref_ile) > 0.05:
                diffs_ref_ile.append((fecha, d_ref_ile))
            if abs(d_ref_rpt) > 0.05:
                diffs_ref_rpt.append((fecha, d_ref_rpt))
            print(
                f"{fecha:<12} {ref:>14,.2f} {ile:>14,.2f} {d_ref_ile:>10,.2f} "
                f"{rpt:>14,.2f} {d_ref_rpt:>10,.2f} {d_ile_rpt:>10,.2f}"
            )

        print("-" * 110)
        print(
            f"{'TOTAL':<12} {tot_ref:>14,.2f} {tot_ile:>14,.2f} {tot_ref - tot_ile:>10,.2f} "
            f"{tot_rpt:>14,.2f} {tot_ref - tot_rpt:>10,.2f} {tot_ile - tot_rpt:>10,.2f}"
        )

        print("\nDías con diferencia Ref vs BC ILE (posting) > 0,05 kg:")
        for fecha, d in diffs_ref_ile:
            print(f"  {fecha}: {d:+,.2f} kg")

        print("\nDías con diferencia Ref vs Reporte enlazado (prday) > 0,05 kg:")
        for fecha, d in diffs_ref_rpt:
            print(f"  {fecha}: {d:+,.2f} kg  (reporte atribuye kg BC al prday Innova, no al posting BC)")

        print("\nPrincipales desfases prday Innova -> posting BC (kg BC contabilizados otro día):")
        for prday in sorted(desfase, key=lambda x: dt.datetime.strptime(x, "%d/%m/%Y")):
            movs = desfase[prday]
            if not movs:
                continue
            top = sorted(movs.items(), key=lambda x: -x[1])[:3]
            det = ", ".join(f"{d}: {k:,.2f} kg" for d, k in top)
            print(f"  Salida Innova {prday} -> BC posting: {det}")

        # Lotes sin enlace
        sin_enlace = 0
        kg_sin = 0.0
        for day in report["bc_cruce_detalle"]:
            for lot in day.get("lotes", []):
                if not lot.get("enlazado"):
                    sin_enlace += 1
                    kg_sin += gr.to_float(lot.get("kg_innova", 0))
        print(f"\nLotes Innova sin enlace BC en el mes: {sin_enlace:,} ({kg_sin:,.2f} kg Innova)")

    finally:
        innova.close()
        bc.close()


if __name__ == "__main__":
    main()
