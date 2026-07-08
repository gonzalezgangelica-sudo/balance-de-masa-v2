#!/usr/bin/env python3
"""Exporta datos de un mes para contraste con negocio (mes de referencia: marzo 2026).

Genera CSVs y un informe Markdown en Reports/<periodo>/ con KPIs, detalle diario,
encadenamiento de arrastre y comprobaciones de balance.

Uso:
  python exportar_contraste_mes.py --start 01/03/2026 --end 31/03/2026 --arrastre-mensual
  python exportar_contraste_mes.py --start 01/03/2026 --end 31/03/2026 --arrastre-mensual --skip-bc
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
    attach_bc_cruce_to_report,
    build_report_data,
    compute_stock_apertura_arrastre,
    enrich_arrastre_mensual,
    enrich_stock_validation,
    fetch_bc_salidas_pedido,
    fetch_innova_salidas_lotes,
    fetch_tinas_arrastradas,
    finalize_balance_kpis,
    format_date_es,
    fmt_num,
    fmt_pct,
    load_dotenv_file,
    parse_stock_inicial_arg,
    parse_user_date,
    resolve_bc_credentials,
    resolve_db_credentials,
    resolve_stock_ancla,
)


def csv_num(value: float | int | None) -> str:
    if value is None:
        return ""
    return f"{float(value):.2f}".replace(".", ",")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Exporta contraste mensual de biomasa")
    parser.add_argument("--start", required=True, help="Fecha inicio dd/mm/aaaa")
    parser.add_argument("--end", required=True, help="Fecha fin dd/mm/aaaa")
    parser.add_argument("--server", default=os.getenv("DB_SERVER", DEFAULT_SERVER))
    parser.add_argument("--database", default=os.getenv("DB_NAME", DEFAULT_DATABASE))
    parser.add_argument("--user", default=os.getenv("DB_USER", ""))
    parser.add_argument("--password", default=os.getenv("DB_PASSWORD", ""))
    parser.add_argument("--cred-target", default="biomasa_sql_innova")
    parser.add_argument("--save-creds", action="store_true")
    parser.add_argument("--arrastre-mensual", action="store_true")
    parser.add_argument("--stock-inicial", default=None)
    parser.add_argument("--stock-ancla", default=None)
    parser.add_argument("--stock-ancla-kg", type=float, default=0.0)
    parser.add_argument("--stock-final-fisico", type=float, default=None)
    parser.add_argument("--data-source", choices=["legacy", "vw_stolt_despesque"], default="legacy")
    parser.add_argument("--skip-bc", action="store_true")
    parser.add_argument("--bc-server", default=os.getenv("BC_SERVER", ""))
    parser.add_argument("--bc-database", default=os.getenv("BC_DATABASE", ""))
    parser.add_argument("--bc-user", default=os.getenv("BC_USER", ""))
    parser.add_argument("--bc-password", default=os.getenv("BC_PASSWORD", ""))
    parser.add_argument("--bc-timeout", type=int, default=int(os.getenv("BC_TIMEOUT", "600")))
    parser.add_argument("--bc-login-timeout", type=int, default=int(os.getenv("BC_LOGIN_TIMEOUT", "60")))
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Carpeta de salida (default: Reports/contraste_YYYYMMDD_YYYYMMDD)",
    )
    return parser.parse_args()


def write_csv(path: Path, headers: list[str], rows: list[list[Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(headers)
        writer.writerows(rows)


def build_comprobaciones(k: dict[str, Any]) -> list[dict[str, Any]]:
    e = k["kg_entrada_tina"]
    s = k["kg_salida_no_tina"]
    p = k["kg_consumo_tina"]
    stock_entrada = k.get("kg_stock_entrada") or k["kg_diferencia"]
    merma = k.get("kg_merma") or 0.0
    stock_ini = k.get("kg_stock_inicial") or 0.0
    stock_inventario = k.get("kg_stock_inventario") or k.get("kg_stock_cierre_teorico") or 0.0

    checks = [
        {
            "comprobacion": "Balance masa: Entrada = Salida + Stock entrada + Merma",
            "formula": f"{e:.2f} = {s:.2f} + {stock_entrada:.2f} + {merma:.2f}",
            "residual_kg": e - (s + stock_entrada + merma),
        },
        {
            "comprobacion": "Stock de entrada = Entradas - TINA procesada",
            "formula": f"{stock_entrada:.2f} = {e:.2f} - {p:.2f}",
            "residual_kg": stock_entrada - (e - p),
        },
        {
            "comprobacion": "Merma = Entradas - Salidas - Stock de entrada",
            "formula": f"{merma:.2f} = {e:.2f} - {s:.2f} - {stock_entrada:.2f}",
            "residual_kg": merma - (e - s - stock_entrada),
        },
        {
            "comprobacion": "Merma = TINA procesada - Salidas CAJA",
            "formula": f"{merma:.2f} = {p:.2f} - {s:.2f}",
            "residual_kg": merma - (p - s),
        },
        {
            "comprobacion": "Stock inventario = Stock inicial + Entradas - TINA procesada",
            "formula": f"{stock_inventario:.2f} = {stock_ini:.2f} + {e:.2f} - {p:.2f}",
            "residual_kg": stock_inventario - (stock_ini + e - p),
        },
        {
            "comprobacion": "Balance TINA-CAJA = Entradas - Salidas",
            "formula": f"{k['kg_balance_entrada_salida']:.2f} = {e:.2f} - {s:.2f}",
            "residual_kg": k["kg_balance_entrada_salida"] - (e - s),
        },
    ]
    return checks


def write_contraste_md(
    path: Path,
    start: dt.date,
    end: dt.date,
    k: dict[str, Any],
    checks: list[dict[str, Any]],
    trail: list[dict[str, Any]],
    tinas: dict[str, Any] | None,
    bc_loaded: bool,
) -> None:
    lines = [
        f"# Contraste biomasa — {format_date_es(start)} a {format_date_es(end)}",
        "",
        "Mes de referencia para validacion con negocio. Premisas: `PREMISAS.md`.",
        "",
        "## Resumen",
        "",
        "| Metrica | kg |",
        "|---------|---:|",
        f"| Entradas TINA | {fmt_num(k['kg_entrada_tina'])} |",
        f"| Salidas CAJA | {fmt_num(k['kg_salida_no_tina'])} |",
        f"| TINA procesada | {fmt_num(k['kg_consumo_tina'])} |",
        f"| Stock inicial | {fmt_num(k.get('kg_stock_inicial', 0))} |",
        f"| Stock de entrada | {fmt_num(k.get('kg_stock_entrada', k['kg_diferencia']))} |",
        f"| Stock inventario cierre | {fmt_num(k.get('kg_stock_inventario', 0))} |",
        f"| Merma (E - S - Stock entrada) | {fmt_num(k.get('kg_merma', 0))} |",
        f"| % Merma / entradas | {fmt_pct(k.get('pct_merma'))} |",
        f"| Balance TINA - CAJA | {fmt_num(k['kg_balance_entrada_salida'])} |",
        "",
    ]
    if tinas:
        lines.extend([
            "## Arrastre",
            "",
            f"- Tinas arrastradas (mes anterior): **{int(tinas['packs']):,}** Nº de Tinas / "
            f"**{fmt_num(tinas['kg'])}** kg",
            f"- Periodo origen: {format_date_es(tinas['mes_origen_inicio'])} a "
            f"{format_date_es(tinas['mes_origen_fin'])}",
            "",
        ])
    if trail:
        lines.extend(["## Encadenamiento mensual", "", "| Mes | Apertura | Entradas | Procesada | Salidas | Cierre |", "|-----|----------:|----------:|----------:|----------:|----------:|"])
        for row in trail:
            lines.append(
                f"| {row['mes']} | {fmt_num(row['kg_stock_apertura'])} | "
                f"{fmt_num(row['kg_entrada_tina'])} | {fmt_num(row['kg_consumo_tina'])} | "
                f"{fmt_num(row['kg_salida_no_tina'])} | {fmt_num(row['kg_stock_cierre'])} |"
            )
        lines.append("")
    if bc_loaded:
        lines.extend([
            "## Business Central",
            "",
            f"- Lotes enlazados: {int(k.get('bc_lotes_enlazados', 0)):,} / "
            f"{int(k.get('bc_lotes_innova', 0)):,} ({fmt_pct(k.get('bc_pct_lotes_enlazados'))})",
            f"- Kg Innova enlazado: {fmt_num(k.get('bc_kg_innova_enlazado', 0))}",
            f"- Kg BC enlazado: {fmt_num(k.get('bc_kg_bc_enlazado', 0))}",
            "",
        ])
    lines.extend(["## Comprobaciones", "", "| Comprobacion | Residual (kg) | OK |", "|--------------|-------------:|:--:|"])
    for check in checks:
        ok = "Si" if abs(check["residual_kg"]) < 0.05 else "No"
        lines.append(f"| {check['comprobacion']} | {fmt_num(check['residual_kg'])} | {ok} |")
    lines.extend([
        "",
        "## Pendiente validacion negocio",
        "",
        "- [ ] Confirmar stock inicial de apertura (arrastre desde enero)",
        "- [ ] Confirmar stock inventario cierre con planta",
        "- [ ] Interpretar merma negativa vs stock arrastrado",
        "- [ ] Firmar mes como referencia antes de replicar a otros meses",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    base = Path(__file__).resolve().parent
    load_dotenv_file(base / ".env")
    args = parse_args()
    start = parse_user_date(args.start)
    end = parse_user_date(args.end)
    stock_inicial_manual, stock_inicial_auto = parse_stock_inicial_arg(args.stock_inicial)
    use_arrastre = args.arrastre_mensual or stock_inicial_auto

    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        out_dir = base / "Reports" / f"contraste_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    db_user, db_password = resolve_db_credentials(args)
    print(f"Extrayendo datos {format_date_es(start)} - {format_date_es(end)}...")

    conn = pymssql.connect(
        server=args.server,
        user=db_user,
        password=db_password,
        database=args.database,
        login_timeout=30,
        timeout=180,
    )
    stock_inicial = stock_inicial_manual
    trail: list[dict[str, Any]] = []
    tinas: dict[str, Any] | None = None
    try:
        report_data = build_report_data(conn, start, end, args.data_source)
        if use_arrastre:
            ancla, ancla_kg = resolve_stock_ancla(args, start)
            print(f"Arrastre: ancla {format_date_es(ancla)} = {fmt_num(ancla_kg)} kg")
            stock_inicial, trail = compute_stock_apertura_arrastre(
                conn, start, args.data_source, ancla, ancla_kg
            )
            tinas = fetch_tinas_arrastradas(conn, start, end)
            enrich_arrastre_mensual(report_data, stock_inicial, trail, tinas)
        enrich_stock_validation(report_data, stock_inicial, args.stock_final_fisico)
        finalize_balance_kpis(report_data)
        innova_lotes = fetch_innova_salidas_lotes(conn, start, end)
    finally:
        conn.close()

    bc_loaded = False
    if not args.skip_bc:
        try:
            bc_server, bc_database, bc_user, bc_password = resolve_bc_credentials(args)
            print("Consultando Business Central...")
            bc_conn = pymssql.connect(
                server=bc_server,
                user=bc_user,
                password=bc_password,
                database=bc_database,
                login_timeout=args.bc_login_timeout,
                timeout=args.bc_timeout,
            )
            try:
                bc_data = fetch_bc_salidas_pedido(bc_conn, start, end)
                attach_bc_cruce_to_report(report_data, bc_data, innova_lotes)
                bc_loaded = True
            finally:
                bc_conn.close()
        except Exception as exc:
            print(f"Aviso: BC no disponible ({exc})")

    k = report_data["kpis"]
    detalle = report_data["detalle_diario"]
    checks = build_comprobaciones(k)

    write_csv(
        out_dir / "01_resumen_kpis.csv",
        ["Metrica", "Valor", "Unidad"],
        [
            ["Entradas TINA", csv_num(k["kg_entrada_tina"]), "kg"],
            ["Salidas CAJA", csv_num(k["kg_salida_no_tina"]), "kg"],
            ["TINA procesada", csv_num(k["kg_consumo_tina"]), "kg"],
            ["Stock inicial", csv_num(k.get("kg_stock_inicial")), "kg"],
            ["Stock de entrada", csv_num(k.get("kg_stock_entrada")), "kg"],
            ["Stock inventario cierre", csv_num(k.get("kg_stock_inventario")), "kg"],
            ["Merma", csv_num(k.get("kg_merma")), "kg"],
            ["% Merma", csv_num(k.get("pct_merma")), "%"],
            ["Balance TINA-CAJA", csv_num(k["kg_balance_entrada_salida"]), "kg"],
            ["Nº de Tinas (entrada)", str(k["packs_entrada"]), "ud"],
            ["Packs salida", str(k["packs_salida"]), "ud"],
        ],
    )

    daily_rows: list[list[Any]] = []
    for row in detalle:
        if (
            row["kg_entrada_tina"] == 0
            and row["kg_salida_no_tina"] == 0
            and row.get("kg_consumo_tina", 0) == 0
        ):
            continue
        daily_rows.append([
            row["fecha"],
            csv_num(row["kg_entrada_tina"]),
            csv_num(row["kg_salida_no_tina"]),
            csv_num(row["kg_consumo_tina"]),
            csv_num(row.get("kg_stock_entrada", row["diferencia_kg"])),
            csv_num(row.get("kg_merma", 0)),
            csv_num(row["balance_entrada_salida_kg"]),
            csv_num(row.get("kg_stock_inventario", 0)),
        ])
    daily_rows.append([
        "TOTAL",
        csv_num(k["kg_entrada_tina"]),
        csv_num(k["kg_salida_no_tina"]),
        csv_num(k["kg_consumo_tina"]),
        csv_num(k.get("kg_stock_entrada")),
        csv_num(k.get("kg_merma", 0)),
        csv_num(k["kg_balance_entrada_salida"]),
        csv_num(k.get("kg_stock_inventario", 0)),
    ])
    write_csv(
        out_dir / "02_detalle_diario.csv",
        [
            "Fecha", "Entradas TINA", "Salidas CAJA", "TINA procesada",
            "Stock entrada", "Merma", "Balance T-C", "Stock inventario",
        ],
        daily_rows,
    )

    if trail:
        write_csv(
            out_dir / "03_encadenamiento_mensual.csv",
            ["Mes", "Apertura", "Entradas TINA", "TINA procesada", "Salidas CAJA", "Cierre"],
            [
                [
                    row["mes"],
                    csv_num(row["kg_stock_apertura"]),
                    csv_num(row["kg_entrada_tina"]),
                    csv_num(row["kg_consumo_tina"]),
                    csv_num(row["kg_salida_no_tina"]),
                    csv_num(row["kg_stock_cierre"]),
                ]
                for row in trail
            ],
        )

    if tinas:
        write_csv(
            out_dir / "04_tinas_arrastradas.csv",
            ["Concepto", "Valor"],
            [
                ["Mes origen desde", format_date_es(tinas["mes_origen_inicio"])],
                ["Mes origen hasta", format_date_es(tinas["mes_origen_fin"])],
                ["Nº de Tinas", str(tinas["packs"])],
                ["Kg", csv_num(tinas["kg"])],
            ],
        )

    write_csv(
        out_dir / "05_comprobaciones_balance.csv",
        ["Comprobacion", "Formula", "Residual kg", "OK"],
        [
            [
                check["comprobacion"],
                check["formula"],
                csv_num(check["residual_kg"]),
                "Si" if abs(check["residual_kg"]) < 0.05 else "No",
            ]
            for check in checks
        ],
    )

    write_contraste_md(
        out_dir / "CONTRASTE.md",
        start,
        end,
        k,
        checks,
        trail,
        tinas,
        bc_loaded,
    )

    print(f"\nExportacion completada: {out_dir}")
    print(f"  Stock entrada: {fmt_num(k.get('kg_stock_entrada'))} kg")
    print(f"  Merma: {fmt_num(k.get('kg_merma'))} kg ({fmt_pct(k.get('pct_merma'))})")
    print(f"  Stock inventario: {fmt_num(k.get('kg_stock_inventario'))} kg")
    for check in checks:
        flag = "OK" if abs(check["residual_kg"]) < 0.05 else "REVISAR"
        print(f"  [{flag}] {check['comprobacion']}")


if __name__ == "__main__":
    main()
