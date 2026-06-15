#!/usr/bin/env python3
"""Genera un reporte HTML de biomasa para un rango de fechas.

Premisas de negocio (canon): PREMISAS.md

Uso:
  python generar_reporte_biomasa.py --start 2026-04-01 --end 2026-04-30

Tambien admite parametros de conexion por CLI o variables de entorno:
  DB_SERVER, DB_NAME, DB_USER, DB_PASSWORD
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import json
import os
import traceback
from pathlib import Path
from typing import Any

import pymssql

try:
  import keyring
except Exception:
  keyring = None


DEFAULT_SERVER = "192.168.14.236"
DEFAULT_DATABASE = "Innova"
DEFAULT_USER = ""
DEFAULT_PASSWORD = ""
DEFAULT_BC_SERVER = ""
DEFAULT_BC_DATABASE = ""
DEFAULT_BC_USER = ""
DEFAULT_BC_PASSWORD = ""


def load_dotenv_file(env_path: Path) -> None:
  if not env_path.exists():
    return

  for raw_line in env_path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#"):
      continue

    if "=" in line:
      key, value = line.split("=", 1)
      key = key.strip()
      value = value.strip().strip('"').strip("'")
      if key and key not in os.environ:
        os.environ[key] = value
      continue

    if ":" not in line:
      continue

    label, value = line.split(":", 1)
    label_key = label.strip().lower()
    value = value.strip()
    if not value:
      continue

    informal_map = {
      "server name": "BC_SERVER",
      "database": "BC_DATABASE",
      "user": "BC_USER",
      "password": "BC_PASSWORD",
    }
    env_key = informal_map.get(label_key)
    if env_key and env_key not in os.environ:
      os.environ[env_key] = value


def load_logo_data_uri(base_dir: Path) -> str | None:
  logo_path = base_dir / "stolt_logo.svg"
  if not logo_path.exists():
    return None

  logo_base64 = base64.b64encode(logo_path.read_bytes()).decode("ascii")
  return f"data:image/svg+xml;base64,{logo_base64}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Genera reporte HTML de biomasa")
    parser.add_argument("--start", required=False, help="Fecha inicio dd/mm/aaaa")
    parser.add_argument("--end", required=False, help="Fecha fin dd/mm/aaaa")
    parser.add_argument("--server", default=os.getenv("DB_SERVER", DEFAULT_SERVER))
    parser.add_argument("--database", default=os.getenv("DB_NAME", DEFAULT_DATABASE))
    parser.add_argument("--user", default=os.getenv("DB_USER", DEFAULT_USER))
    parser.add_argument("--password", default=os.getenv("DB_PASSWORD", DEFAULT_PASSWORD))
    parser.add_argument(
      "--cred-target",
      default="biomasa_sql_innova",
      help="Identificador para guardar/leer credenciales seguras del sistema.",
    )
    parser.add_argument(
      "--save-creds",
      action="store_true",
      help="Guarda user/pass en el almacen seguro del sistema para proximas ejecuciones.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Ruta del HTML de salida (opcional).",
    )
    parser.add_argument(
        "--title",
        default="Reporte de Biomasa",
        help="Titulo del reporte.",
    )
    parser.add_argument(
      "--stock-inicial",
      type=float,
      default=None,
      help="Stock inicial del periodo (kg) para validar stock teorico.",
    )
    parser.add_argument(
      "--stock-final-fisico",
      type=float,
      default=None,
      help="Stock final fisico medido (kg) para conciliacion y merma.",
    )
    parser.add_argument(
      "--data-source",
      choices=["legacy", "vw_stolt_despesque"],
      default="legacy",
      help="Fuente de datos para el calculo: legacy (proc_packs/proc_matxacts) o vw_stolt_despesque.",
    )
    parser.add_argument(
      "--skip-bc",
      action="store_true",
      help="No consultar Business Central (cruce salidas con/sin pedido).",
    )
    parser.add_argument("--bc-server", default=os.getenv("BC_SERVER", DEFAULT_BC_SERVER))
    parser.add_argument("--bc-database", default=os.getenv("BC_DATABASE", DEFAULT_BC_DATABASE))
    parser.add_argument("--bc-user", default=os.getenv("BC_USER", DEFAULT_BC_USER))
    parser.add_argument("--bc-password", default=os.getenv("BC_PASSWORD", DEFAULT_BC_PASSWORD))
    parser.add_argument(
      "--bc-timeout",
      type=int,
      default=int(os.getenv("BC_TIMEOUT", "600")),
      help="Timeout de consulta BC en segundos (default 600).",
    )
    parser.add_argument(
      "--bc-login-timeout",
      type=int,
      default=int(os.getenv("BC_LOGIN_TIMEOUT", "60")),
      help="Timeout de login BC en segundos (default 60).",
    )
    return parser.parse_args()


def prompt_with_default(label: str, default: str | None) -> str:
    if default:
        value = input(f"{label} [{default}]: ").strip()
        return value or default
    return input(f"{label}: ").strip()


def format_date_es(value: dt.date) -> str:
    return value.strftime("%d/%m/%Y")


def parse_user_date(value: str) -> dt.date:
    value = value.strip()
    formats = ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"]
    for fmt in formats:
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Fecha invalida: {value}. Use formato dd/mm/aaaa")


def resolve_dates(args: argparse.Namespace) -> tuple[dt.date, dt.date]:
  if args.start and args.end:
    start = parse_user_date(args.start)
    end = parse_user_date(args.end)
    if end < start:
      raise ValueError("La fecha fin no puede ser menor que la fecha inicio")
    return start, end

  start_default = format_date_es(parse_user_date(args.start)) if args.start else None
  end_default = format_date_es(parse_user_date(args.end)) if args.end else None

  print("Ingrese el rango de fechas para el reporte")
  start_raw = prompt_with_default("Fecha inicio (dd/mm/aaaa)", start_default)
  end_raw = prompt_with_default("Fecha fin (dd/mm/aaaa)", end_default)
  start = parse_user_date(start_raw)
  end = parse_user_date(end_raw)
  if end < start:
      raise ValueError("La fecha fin no puede ser menor que la fecha inicio")
  return start, end


def resolve_db_credentials(args: argparse.Namespace) -> tuple[str, str]:
    user = (args.user or "").strip()
    password = (args.password or "").strip()

    if keyring is not None:
        if not user:
            saved_user = keyring.get_password(args.cred_target, "user")
            if saved_user:
                user = saved_user
        if not password:
            saved_pass = keyring.get_password(args.cred_target, "password")
            if saved_pass:
                password = saved_pass

    if not user or not password:
      raise RuntimeError(
        "No hay credenciales disponibles de forma automatica. "
        "Configure keyring (recomendado) o variables de entorno DB_USER/DB_PASSWORD, "
        "o pase --user y --password por CLI."
      )

    if args.save_creds:
        if keyring is None:
            print("Aviso: keyring no esta disponible; no se pueden guardar credenciales cifradas.")
        else:
            keyring.set_password(args.cred_target, "user", user)
            keyring.set_password(args.cred_target, "password", password)
            print(f"Credenciales guardadas de forma segura en keyring con target: {args.cred_target}")

    return user, password


def resolve_bc_credentials(args: argparse.Namespace) -> tuple[str, str, str, str]:
    server = (args.bc_server or "").strip()
    database = (args.bc_database or "").strip()
    user = (args.bc_user or "").strip()
    password = (args.bc_password or "").strip()
    if not all([server, database, user, password]):
        raise RuntimeError(
            "Credenciales Business Central incompletas. Configure BC_SERVER, BC_DATABASE, "
            "BC_USER y BC_PASSWORD en .env (o parametros --bc-*)."
        )
    return server, database, user, password


def write_error_log(error: Exception, context: dict[str, Any] | None = None) -> Path:
    base_dir = Path(__file__).resolve().parent
    log_dir = base_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"error_reporte_biomasa_{stamp}.log"

    lines = [
        f"timestamp={dt.datetime.now().isoformat()}",
        f"error_type={type(error).__name__}",
        f"error_message={error}",
    ]
    if context:
        for key, value in context.items():
          lines.append(f"{key}={value}")
    lines.extend([
        "",
        "--- traceback ---",
        traceback.format_exc(),
    ])

    log_path.write_text("\n".join(lines), encoding="utf-8")
    return log_path


def print_console_summary(start: dt.date, end: dt.date, output_path: Path, report_data: dict[str, Any]) -> None:
    k = report_data["kpis"]
    print("\nResumen del reporte")
    print("=" * 40)
    print(f"Periodo: {format_date_es(start)} a {format_date_es(end)}")
    print(f"Entradas de biomasa (kg): {fmt_num(k['kg_entrada_tina'])}")
    print(f"Cajas (kg): {fmt_num(k['kg_consumo_tina'])}")
    print(f"Salidas totales kg (todos los graders y basculas): {fmt_num(k['kg_salida_no_tina'])}")
    print(f"Stock en entradas (rtype<>12, pkpackaging=3): {fmt_num(k.get('kg_stock_entrada', 0))}")
    print(f"Merma en entradas (rtype=12, pkpackaging=3): {fmt_num(k.get('kg_merma', 0))}")
    print(f"Diferencia (kg): {fmt_num(k['kg_diferencia'])}")
    print(f"Stock sin procesar fin de periodo (kg): {fmt_num(k['kg_stock_sin_procesar_fin'])}")
    print(f"% Diferencia: {fmt_pct(k['pct_diferencia'])}")
    print(f"Packs entrada: {k['packs_entrada']}")
    print(f"Packs salida: {k['packs_salida']}")
    print(f"Movimientos de cajas: {k['movs_consumo']}")
    if k.get("bc_lotes_innova"):
      print(
          f"BC lotes enlazados (number=Lot No.): "
          f"{int(k.get('bc_lotes_enlazados', 0)):,} / {int(k['bc_lotes_innova']):,} "
          f"({fmt_pct(k.get('bc_pct_lotes_enlazados'))})"
      )
      print(f"Kg Innova enlazado a BC: {fmt_num(k.get('bc_kg_innova_enlazado', 0))}")
      print(f"Kg BC enlazado (ILE Kilos): {fmt_num(k.get('bc_kg_bc_enlazado', 0))}")
      print(
          f"Diferencia kg enlazados (Innova - BC): "
          f"{fmt_num(k.get('bc_kg_diferencia_enlazado', 0))}"
      )
      print(f"BC con pedido (ud. / kg): {fmt_num(k.get('bc_qty_con_pedido', 0))} / {fmt_num(k.get('bc_kg_con_pedido', 0))}")
      print(f"BC sin pedido (ud. / kg): {fmt_num(k.get('bc_qty_sin_pedido', 0))} / {fmt_num(k.get('bc_kg_sin_pedido', 0))}")
    if k.get("kg_stock_inicial") is not None:
      print(f"Stock inicial (kg): {fmt_num(k['kg_stock_inicial'])}")
      print(f"Stock final teorico (kg): {fmt_num(k['kg_stock_final_teorico'])}")
    if k.get("kg_stock_final_fisico") is not None:
      print(f"Stock final fisico (kg): {fmt_num(k['kg_stock_final_fisico'])}")
      print(f"Ajuste conciliacion (kg): {fmt_num(k['kg_ajuste_conciliacion'])}")
    print(f"HTML generado: {output_path}")


def enrich_stock_validation(
    report_data: dict[str, Any],
    stock_inicial: float | None,
    stock_final_fisico: float | None,
  ) -> None:
    k = report_data["kpis"]
    k["kg_stock_inicial"] = stock_inicial
    k["kg_stock_final_fisico"] = stock_final_fisico
    k["kg_stock_final_teorico"] = None
    k["kg_ajuste_conciliacion"] = None

    if stock_inicial is None:
      return

    stock_teorico = stock_inicial + k["kg_diferencia"]
    k["kg_stock_final_teorico"] = stock_teorico

    if stock_final_fisico is not None:
      # Positivo = faltante/merma a justificar, negativo = sobrante.
      k["kg_ajuste_conciliacion"] = stock_teorico - stock_final_fisico


def fetch_rows(cursor: pymssql.Cursor, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    cursor.execute(query, params)
    columns = [col[0] for col in cursor.description]
    results = []
    for row in cursor.fetchall():
        results.append(dict(zip(columns, row)))
    return results


def to_float(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


# Premisa de entrada/salida/stock/merma (ver PREMISAS.md). Validadas negocio mar-2026.
PREMISA_ENTRADA_REGLAS = (
  "Material con pkpackaging = 3 en dbo.proc_materials.",
)
PREMISA_SALIDA = (
  "Salida = material con pkpackaging distinto de 3 (o sin pkpackaging) en dbo.proc_materials."
)
PREMISA_CAJAS = "Cajas = consumo TINA en proc_matxacts (xactpath=1), solo nombre con 'tina'."
PREMISA_SALIDA_REGLAS = (
  "Material con pkpackaging <> 3 o pkpackaging NULL en dbo.proc_materials.",
)
PREMISA_STOCK_MERMA_REGLAS = (
  "Solo materiales de entrada (pkpackaging = 3) en dbo.proc_packs.",
  "Stock: rtype <> 12 (o NULL).",
  "Merma: rtype = 12.",
)

SQL_LEGACY_ES_ENTRADA = "m.pkpackaging = 3"
SQL_LEGACY_ES_SALIDA = "(m.pkpackaging <> 3 OR m.pkpackaging IS NULL)"
SQL_LEGACY_ES_STOCK = "m.pkpackaging = 3 AND (p.rtype <> 12 OR p.rtype IS NULL)"
SQL_LEGACY_ES_MERMA = "m.pkpackaging = 3 AND p.rtype = 12"

SQL_VW_STOLT_ES_ENTRADA = "m.pkpackaging = 3"
SQL_VW_STOLT_ES_SALIDA = "(m.pkpackaging <> 3 OR m.pkpackaging IS NULL)"

PREMISA_BC_PEDIDO_REGLAS = (
  "Clave de enlace: dbo.proc_packs.number (codigo de lote/caja) = bc.[Item Ledger Entry].[Lot No.].",
  "Ventas BC: Item Ledger Entry (Entry Type = 1); kilos BC = campo [Kilos] (valor absoluto).",
  "Pedido desde Sales Shipment Line ([Order No.]) del mismo [Document No.].",
  "Con pedido: [Order No.] informado en el albaran BC.",
  "Sin pedido: [Order No.] vacio o NULL.",
  "Cruce kg: peso salida Innova (proc_packs.weight) vs [Kilos] BC en lotes enlazados.",
)
SQL_BC_SALIDA_CON_PEDIDO = "NULLIF(LTRIM(RTRIM(sl.[Order No.])), '') IS NOT NULL"
SQL_BC_SALIDA_SIN_PEDIDO = "NULLIF(LTRIM(RTRIM(sl.[Order No.])), '') IS NULL"
SQL_BC_ILE_SALE = "ile.[Entry Type] = 1"
SQL_INNOVA_LOT = "CAST(p.number AS varchar(50))"


def build_premisa_entrada_html() -> str:
    items = "".join(f"<li>{html.escape(rule)}</li>" for rule in PREMISA_ENTRADA_REGLAS)
    salida_items = "".join(f"<li>{html.escape(rule)}</li>" for rule in PREMISA_SALIDA_REGLAS)
    stock_merma_items = "".join(
        f"<li>{html.escape(rule)}</li>" for rule in PREMISA_STOCK_MERMA_REGLAS
    )
    bc_items = "".join(f"<li>{html.escape(rule)}</li>" for rule in PREMISA_BC_PEDIDO_REGLAS)
    return (
        "<section class='premisa-box'>"
        "<h3 class='premisa-head'>Premisa de entradas de biomasa</h3>"
        f"<ul class='premisa-list'>{items}</ul>"
        f"<p class='premisa-note'>{html.escape(PREMISA_CAJAS)}</p>"
        "<h3 class='premisa-head'>Premisa de salidas de biomasa</h3>"
        f"<ul class='premisa-list'>{salida_items}</ul>"
        "<h3 class='premisa-head'>Premisa stock / merma (proc_packs)</h3>"
        f"<ul class='premisa-list'>{stock_merma_items}</ul>"
        "<h3 class='premisa-head'>Premisa cruce BC (salidas con/sin pedido)</h3>"
        f"<ul class='premisa-list'>{bc_items}</ul>"
        "<p class='premisa-note muted'>Documento canon: PREMISAS.md</p>"
        "</section>"
    )


def fetch_innova_salidas_lotes(
    conn: pymssql.Connection,
    start: dt.date,
    end: dt.date,
) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    params = (start.isoformat(), end.isoformat())
    query = f"""
    SELECT
      CAST(p.regtime AS date) AS fecha,
      {SQL_INNOVA_LOT} AS lot,
      SUM(CAST(p.weight AS float)) AS kg,
      COUNT(*) AS packs
    FROM dbo.proc_packs p
    JOIN dbo.proc_materials m ON m.material = p.material
    WHERE p.regtime >= %s
      AND p.regtime < DATEADD(day, 1, %s)
      AND {SQL_LEGACY_ES_SALIDA}
      AND NULLIF(LTRIM(RTRIM({SQL_INNOVA_LOT})), '') IS NOT NULL
    GROUP BY CAST(p.regtime AS date), {SQL_INNOVA_LOT}
    ORDER BY fecha, lot;
    """
    return fetch_rows(cursor, query, params)


def fetch_bc_salidas_pedido(
    conn: pymssql.Connection,
    start: dt.date,
    end: dt.date,
) -> dict[str, Any]:
    cursor = conn.cursor()
    params = (start.isoformat(), end.isoformat(), start.isoformat(), end.isoformat())
    q_lotes = f"""
    WITH doc_order AS (
      SELECT
        ssl.[Document No.] AS document_no,
        MAX(NULLIF(LTRIM(RTRIM(ssl.[Order No.])), '')) AS [Order No.]
      FROM bc.[Sales Shipment Line] ssl
      WHERE ssl.[Posting Date] >= %s
        AND ssl.[Posting Date] < DATEADD(day, 1, %s)
      GROUP BY ssl.[Document No.]
    )
    SELECT
      CAST(ile.[Lot No.] AS varchar(50)) AS lot,
      MAX(NULLIF(LTRIM(RTRIM(sl.[Order No.])), '')) AS order_no,
      SUM(ABS(CAST(ile.[Quantity] AS float))) AS qty,
      SUM(ABS(CAST(ile.[Kilos] AS float))) AS kg,
      SUM(
        CASE
          WHEN {SQL_BC_SALIDA_CON_PEDIDO}
          THEN ABS(CAST(ile.[Quantity] AS float))
          ELSE 0.0
        END
      ) AS qty_con_pedido,
      SUM(
        CASE
          WHEN {SQL_BC_SALIDA_SIN_PEDIDO}
          THEN ABS(CAST(ile.[Quantity] AS float))
          ELSE 0.0
        END
      ) AS qty_sin_pedido,
      SUM(
        CASE
          WHEN {SQL_BC_SALIDA_CON_PEDIDO}
          THEN ABS(CAST(ile.[Kilos] AS float))
          ELSE 0.0
        END
      ) AS kg_con_pedido,
      SUM(
        CASE
          WHEN {SQL_BC_SALIDA_SIN_PEDIDO}
          THEN ABS(CAST(ile.[Kilos] AS float))
          ELSE 0.0
        END
      ) AS kg_sin_pedido,
      COUNT(*) AS lineas_ile
    FROM bc.[Item Ledger Entry] ile
    LEFT JOIN doc_order sl ON sl.document_no = ile.[Document No.]
    WHERE ile.[Posting Date] >= %s
      AND ile.[Posting Date] < DATEADD(day, 1, %s)
      AND {SQL_BC_ILE_SALE}
      AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
    GROUP BY CAST(ile.[Lot No.] AS varchar(50))
    ORDER BY lot;
    """
    by_lot = fetch_rows(cursor, q_lotes, params)
    totals = {
        "lotes_bc": len(by_lot),
        "qty_total": sum(to_float(r["qty"]) for r in by_lot),
        "qty_con_pedido": sum(to_float(r["qty_con_pedido"]) for r in by_lot),
        "qty_sin_pedido": sum(to_float(r["qty_sin_pedido"]) for r in by_lot),
        "kg_total": sum(to_float(r["kg"]) for r in by_lot),
        "kg_con_pedido": sum(to_float(r["kg_con_pedido"]) for r in by_lot),
        "kg_sin_pedido": sum(to_float(r["kg_sin_pedido"]) for r in by_lot),
        "lineas_ile": sum(int(r["lineas_ile"] or 0) for r in by_lot),
    }
    return {
        "by_lot": by_lot,
        "totals": totals,
        "sql_trace": {
            "view_or_tables": [
                "bc.[Item Ledger Entry]",
                "bc.[Sales Shipment Line]",
            ],
            "params": {"start": start.isoformat(), "end": end.isoformat()},
            "queries": [
                {"name": "bc_lotes_salida_ile", "query": q_lotes.strip()},
            ],
        },
    }


def parse_fecha_es(fecha: str) -> str:
    return dt.datetime.strptime(fecha, "%d/%m/%Y").date().isoformat()


def attach_bc_cruce_to_report(
    report_data: dict[str, Any],
    bc_data: dict[str, Any],
    innova_lotes: list[dict[str, Any]],
) -> None:
    bc_by_lot = {str(row["lot"]).strip(): row for row in bc_data["by_lot"]}
    innova_by_date: dict[str, list[dict[str, Any]]] = {}
    for row in innova_lotes:
        key = row["fecha"].isoformat()
        innova_by_date.setdefault(key, []).append(row)

    tot_lotes_innova = 0
    tot_lotes_enlazados = 0
    tot_kg_innova_enlazado = 0.0
    tot_kg_bc_enlazado = 0.0
    tot_qty_con = 0.0
    tot_qty_sin = 0.0
    tot_kg_con = 0.0
    tot_kg_sin = 0.0

    for detalle_row in report_data["detalle_diario"]:
        date_key = parse_fecha_es(detalle_row["fecha"])
        lotes_dia = innova_by_date.get(date_key, [])
        lotes_innova = len(lotes_dia)
        lotes_enlazados = 0
        kg_innova_enlazado = 0.0
        kg_bc_enlazado = 0.0
        qty_con_pedido = 0.0
        qty_sin_pedido = 0.0
        kg_con_pedido = 0.0
        kg_sin_pedido = 0.0

        for lot_row in lotes_dia:
            lot_key = str(lot_row["lot"]).strip()
            bc_row = bc_by_lot.get(lot_key)
            if not bc_row:
                continue
            lotes_enlazados += 1
            kg_innova_enlazado += to_float(lot_row["kg"])
            kg_bc_enlazado += to_float(bc_row["kg"])
            qty_con_pedido += to_float(bc_row["qty_con_pedido"])
            qty_sin_pedido += to_float(bc_row["qty_sin_pedido"])
            kg_con_pedido += to_float(bc_row["kg_con_pedido"])
            kg_sin_pedido += to_float(bc_row["kg_sin_pedido"])

        detalle_row.update(
            {
                "bc_lotes_innova": lotes_innova,
                "bc_lotes_enlazados": lotes_enlazados,
                "bc_kg_innova_enlazado": kg_innova_enlazado,
                "bc_kg_bc_enlazado": kg_bc_enlazado,
                "bc_kg_diferencia_enlazado": kg_innova_enlazado - kg_bc_enlazado,
                "bc_qty_con_pedido": qty_con_pedido,
                "bc_qty_sin_pedido": qty_sin_pedido,
                "bc_kg_con_pedido": kg_con_pedido,
                "bc_kg_sin_pedido": kg_sin_pedido,
            }
        )

        tot_lotes_innova += lotes_innova
        tot_lotes_enlazados += lotes_enlazados
        tot_kg_innova_enlazado += kg_innova_enlazado
        tot_kg_bc_enlazado += kg_bc_enlazado
        tot_qty_con += qty_con_pedido
        tot_qty_sin += qty_sin_pedido
        tot_kg_con += kg_con_pedido
        tot_kg_sin += kg_sin_pedido

    totals = bc_data["totals"]
    report_data["bc_cruce"] = bc_data
    report_data["kpis"].update(
        {
            "bc_lotes_innova": tot_lotes_innova,
            "bc_lotes_enlazados": tot_lotes_enlazados,
            "bc_lotes_sin_enlace": max(tot_lotes_innova - tot_lotes_enlazados, 0),
            "bc_pct_lotes_enlazados": (
                (tot_lotes_enlazados / tot_lotes_innova * 100.0) if tot_lotes_innova else None
            ),
            "bc_kg_innova_enlazado": tot_kg_innova_enlazado,
            "bc_kg_bc_enlazado": tot_kg_bc_enlazado,
            "bc_kg_diferencia_enlazado": tot_kg_innova_enlazado - tot_kg_bc_enlazado,
            "bc_qty_con_pedido": tot_qty_con,
            "bc_qty_sin_pedido": tot_qty_sin,
            "bc_kg_con_pedido": tot_kg_con,
            "bc_kg_sin_pedido": tot_kg_sin,
            "bc_lotes_bc_periodo": totals["lotes_bc"],
            "bc_qty_total_periodo": totals["qty_total"],
        }
    )
    report_data["sql_trace"]["queries"].extend(bc_data["sql_trace"]["queries"])
    report_data["sql_trace"]["view_or_tables"] = list(
        dict.fromkeys(
            report_data["sql_trace"].get("view_or_tables", [])
            + bc_data["sql_trace"]["view_or_tables"]
        )
    )


def build_source_definition(data_source: str) -> str:
    if data_source == "vw_stolt_despesque":
        return (
            "Fuente: dbo.vw_stolt por fdespesque. "
            + PREMISA_SALIDA
            + " Stock sin procesar = arrastre acumulado (Entradas - Cajas)."
        )
    return (
        "Entradas/salidas/cajas por regtime (proc_packs/proc_matxacts). "
        + PREMISA_SALIDA
        + " Stock/arrastre por fdespesque (vw_stolt)."
    )


def build_report_data(
    conn: pymssql.Connection,
    start: dt.date,
    end: dt.date,
    data_source: str = "legacy",
) -> dict[str, Any]:
    cursor = conn.cursor()
    params = (start.isoformat(), end.isoformat())
    sql_trace: list[dict[str, Any]] = []

    if data_source == "vw_stolt_despesque":
      q_diario = f"""
      SELECT
        CAST(v.fdespesque AS date) AS fecha,
        SUM(
          CASE
            WHEN {SQL_VW_STOLT_ES_ENTRADA}
            THEN CAST(v.peso AS float)
            ELSE 0.0
          END
        ) AS kg_entrada_tina,
        SUM(
          CASE
            WHEN {SQL_VW_STOLT_ES_ENTRADA}
            THEN 1
            ELSE 0
          END
        ) AS packs_entrada,
        SUM(
          CASE
            WHEN {SQL_VW_STOLT_ES_SALIDA}
            THEN CAST(v.peso AS float)
            ELSE 0.0
          END
        ) AS kg_salida_no_tina,
        SUM(
          CASE
            WHEN {SQL_VW_STOLT_ES_ENTRADA}
            THEN 0.0
            ELSE CAST(v.peso AS float)
          END
        ) AS kg_consumo_tina,
        SUM(
          CASE
            WHEN {SQL_VW_STOLT_ES_SALIDA}
            THEN 1
            ELSE 0
          END
        ) AS packs_salida,
        SUM(
          CASE
            WHEN {SQL_VW_STOLT_ES_ENTRADA}
            THEN 0
            ELSE 1
          END
        ) AS movs_consumo
      FROM dbo.vw_stolt v
      JOIN dbo.proc_materials m ON m.material = v.material
      WHERE v.fdespesque >= %s
        AND v.fdespesque < DATEADD(day, 1, %s)
      GROUP BY CAST(v.fdespesque AS date)
      ORDER BY fecha;
      """

      q_top_entrada = f"""
      SELECT TOP 15
        COALESCE(v.material, '-') AS material,
        COALESCE(m.name, v.producto, '(sin producto)') AS material_nombre,
        SUM(CAST(v.peso AS float)) AS kg
      FROM dbo.vw_stolt v
      JOIN dbo.proc_materials m ON m.material = v.material
      WHERE v.fdespesque >= %s
        AND v.fdespesque < DATEADD(day, 1, %s)
        AND {SQL_VW_STOLT_ES_ENTRADA}
      GROUP BY v.material, m.name, v.producto
      ORDER BY kg DESC;
      """

      q_top_salida = f"""
      SELECT TOP 15
        COALESCE(v.material, '-') AS material,
        COALESCE(m.name, v.producto, '(sin producto)') AS material_nombre,
        SUM(CAST(v.peso AS float)) AS kg
      FROM dbo.vw_stolt v
      JOIN dbo.proc_materials m ON m.material = v.material
      WHERE v.fdespesque >= %s
        AND v.fdespesque < DATEADD(day, 1, %s)
        AND {SQL_VW_STOLT_ES_SALIDA}
      GROUP BY v.material, m.name, v.producto
      ORDER BY kg DESC;
      """

      sql_trace.extend(
        [
          {"name": "diario_vw_stolt", "query": q_diario.strip()},
          {"name": "top_entrada_vw_stolt", "query": q_top_entrada.strip()},
          {"name": "top_salida_vw_stolt", "query": q_top_salida.strip()},
        ]
      )

      diarios = fetch_rows(cursor, q_diario, params)
      top_entradas = fetch_rows(cursor, q_top_entrada, params)
      top_salidas = fetch_rows(cursor, q_top_salida, params)

      entradas = []
      salidas = []
      consumos = []
      for row in diarios:
        entradas.append(
          {
            "fecha": row["fecha"],
            "kg_entrada_tina": row["kg_entrada_tina"],
            "packs_entrada": row["packs_entrada"],
          }
        )
        salidas.append(
          {
            "fecha": row["fecha"],
            "kg_salida_no_tina": row["kg_salida_no_tina"],
            "packs_salida": row["packs_salida"],
          }
        )
        consumos.append(
          {
            "fecha": row["fecha"],
            "kg_consumo_tina": row["kg_consumo_tina"],
            "movs_consumo": row["movs_consumo"],
          }
        )
    else:
      q_entrada = f"""
      SELECT
        CAST(p.regtime AS date) AS fecha,
        SUM(CAST(p.weight AS float)) AS kg_entrada_tina,
        COUNT(*) AS packs_entrada
      FROM dbo.proc_packs p
      JOIN dbo.proc_materials m ON m.material = p.material
      WHERE p.regtime >= %s
        AND p.regtime < DATEADD(day, 1, %s)
        AND {SQL_LEGACY_ES_ENTRADA}
      GROUP BY CAST(p.regtime AS date)
      ORDER BY fecha;
      """

      q_salida = f"""
      SELECT
        CAST(p.regtime AS date) AS fecha,
        SUM(CAST(p.weight AS float)) AS kg_salida_no_tina,
        COUNT(*) AS packs_salida
      FROM dbo.proc_packs p
      JOIN dbo.proc_materials m ON m.material = p.material
      WHERE p.regtime >= %s
        AND p.regtime < DATEADD(day, 1, %s)
        AND {SQL_LEGACY_ES_SALIDA}
      GROUP BY CAST(p.regtime AS date)
      ORDER BY fecha;
      """

      q_consumo = """
      SELECT
        CAST(x.regtime AS date) AS fecha,
        SUM(CAST(x.weight AS float)) AS kg_consumo_tina,
        COUNT(*) AS movs_consumo
      FROM dbo.proc_matxacts x
      JOIN dbo.proc_materials m ON m.material = x.material
      WHERE x.regtime >= %s
        AND x.regtime < DATEADD(day, 1, %s)
        AND x.xactpath = 1
        AND LOWER(m.name) LIKE '%tina%'
      GROUP BY CAST(x.regtime AS date)
      ORDER BY fecha;
      """

      q_top_entrada = f"""
      SELECT TOP 15
        m.material,
        m.name AS material_nombre,
        SUM(CAST(p.weight AS float)) AS kg
      FROM dbo.proc_packs p
      JOIN dbo.proc_materials m ON m.material = p.material
      WHERE p.regtime >= %s
        AND p.regtime < DATEADD(day, 1, %s)
        AND {SQL_LEGACY_ES_ENTRADA}
      GROUP BY m.material, m.name
      ORDER BY kg DESC;
      """

      q_top_salida = f"""
      SELECT TOP 15
        m.material,
        m.name AS material_nombre,
        SUM(CAST(p.weight AS float)) AS kg
      FROM dbo.proc_packs p
      JOIN dbo.proc_materials m ON m.material = p.material
      WHERE p.regtime >= %s
        AND p.regtime < DATEADD(day, 1, %s)
        AND {SQL_LEGACY_ES_SALIDA}
      GROUP BY m.material, m.name
      ORDER BY kg DESC;
      """

      sql_trace.extend(
        [
          {"name": "entrada_legacy", "query": q_entrada.strip()},
          {"name": "salida_legacy", "query": q_salida.strip()},
          {"name": "consumo_legacy", "query": q_consumo.strip()},
          {"name": "top_entrada_legacy", "query": q_top_entrada.strip()},
          {"name": "top_salida_legacy", "query": q_top_salida.strip()},
        ]
      )

      entradas = fetch_rows(cursor, q_entrada, params)
      salidas = fetch_rows(cursor, q_salida, params)
      consumos = fetch_rows(cursor, q_consumo, params)
      top_entradas = fetch_rows(cursor, q_top_entrada, params)
      top_salidas = fetch_rows(cursor, q_top_salida, params)

    q_stock_merma = f"""
    SELECT
      CAST(p.regtime AS date) AS fecha,
      SUM(
        CASE
          WHEN {SQL_LEGACY_ES_STOCK}
          THEN CAST(p.weight AS float)
          ELSE 0.0
        END
      ) AS kg_stock,
      SUM(
        CASE
          WHEN {SQL_LEGACY_ES_MERMA}
          THEN CAST(p.weight AS float)
          ELSE 0.0
        END
      ) AS kg_merma
    FROM dbo.proc_packs p
    JOIN dbo.proc_materials m ON m.material = p.material
    WHERE p.regtime >= %s
      AND p.regtime < DATEADD(day, 1, %s)
      AND {SQL_LEGACY_ES_ENTRADA}
    GROUP BY CAST(p.regtime AS date)
    ORDER BY fecha;
    """
    stock_merma_rows = fetch_rows(cursor, q_stock_merma, params)
    sql_trace.append({"name": "stock_merma_proc_packs", "query": q_stock_merma.strip()})

    by_date: dict[str, dict[str, Any]] = {}

    current = start
    while current <= end:
        key = current.isoformat()
        by_date[key] = {
        "fecha": format_date_es(current),
            "kg_entrada_tina": 0.0,
            "packs_entrada": 0,
            "kg_salida_no_tina": 0.0,
            "packs_salida": 0,
            "kg_consumo_tina": 0.0,
            "movs_consumo": 0,
            "kg_stock": 0.0,
            "kg_merma": 0.0,
            "bc_qty_con_pedido": 0.0,
            "bc_qty_sin_pedido": 0.0,
            "bc_kg_con_pedido": 0.0,
            "bc_kg_sin_pedido": 0.0,
            "bc_lotes_innova": 0,
            "bc_lotes_enlazados": 0,
            "bc_lotes_sin_enlace": 0,
            "bc_pct_lotes_enlazados": None,
            "bc_kg_innova_enlazado": 0.0,
            "bc_kg_bc_enlazado": 0.0,
            "bc_kg_diferencia_enlazado": 0.0,
        }
        current += dt.timedelta(days=1)

    for row in entradas:
        key = row["fecha"].isoformat()
        by_date[key]["kg_entrada_tina"] = to_float(row["kg_entrada_tina"])
        by_date[key]["packs_entrada"] = int(row["packs_entrada"] or 0)

    for row in salidas:
        key = row["fecha"].isoformat()
        by_date[key]["kg_salida_no_tina"] = to_float(row["kg_salida_no_tina"])
        by_date[key]["packs_salida"] = int(row["packs_salida"] or 0)

    for row in consumos:
        key = row["fecha"].isoformat()
        by_date[key]["kg_consumo_tina"] = to_float(row["kg_consumo_tina"])
        by_date[key]["movs_consumo"] = int(row["movs_consumo"] or 0)

    for row in stock_merma_rows:
        key = row["fecha"].isoformat()
        by_date[key]["kg_stock"] = to_float(row["kg_stock"])
        by_date[key]["kg_merma"] = to_float(row["kg_merma"])

    stock_by_despesque: dict[str, dict[str, float]] = {}
    if data_source == "legacy":
      q_stock_despesque = f"""
      SELECT
        CAST(v.fdespesque AS date) AS fecha,
        SUM(
          CASE
            WHEN {SQL_VW_STOLT_ES_ENTRADA}
            THEN CAST(v.peso AS float)
            ELSE 0.0
          END
        ) AS kg_entrada_tina_stock,
        SUM(
          CASE
            WHEN {SQL_VW_STOLT_ES_SALIDA}
            THEN CAST(v.peso AS float)
            ELSE 0.0
          END
        ) AS kg_cajas_stock
      FROM dbo.vw_stolt v
      JOIN dbo.proc_materials m ON m.material = v.material
      WHERE v.fdespesque >= %s
        AND v.fdespesque < DATEADD(day, 1, %s)
      GROUP BY CAST(v.fdespesque AS date)
      ORDER BY fecha;
      """
      sql_trace.append({"name": "stock_arrastre_despesque", "query": q_stock_despesque.strip()})
      stock_rows = fetch_rows(cursor, q_stock_despesque, params)
      for row in stock_rows:
        key = row["fecha"].isoformat()
        stock_by_despesque[key] = {
          "kg_entrada_tina_stock": to_float(row["kg_entrada_tina_stock"]),
          "kg_cajas_stock": to_float(row["kg_cajas_stock"]),
        }

    detalle = []
    acumulado_dif = 0.0
    acumulado_balance = 0.0
    acumulado_stock_despesque = 0.0

    for key in sorted(by_date.keys()):
        entry = by_date[key]
        dif = entry["kg_entrada_tina"] - entry["kg_consumo_tina"]
        balance = entry["kg_entrada_tina"] - entry["kg_salida_no_tina"]
        acumulado_dif += dif
        acumulado_balance += balance
        pct_dif = (dif / entry["kg_entrada_tina"] * 100.0) if entry["kg_entrada_tina"] else None

        stock_row = stock_by_despesque.get(
          key,
          {"kg_entrada_tina_stock": 0.0, "kg_cajas_stock": 0.0},
        )
        stock_dif = stock_row["kg_entrada_tina_stock"] - stock_row["kg_cajas_stock"]
        if data_source == "legacy":
          acumulado_stock_despesque += stock_dif
          stock_sin_procesar = acumulado_stock_despesque
        else:
          stock_sin_procesar = acumulado_dif

        detalle.append(
            {
                **entry,
                "diferencia_kg": dif,
                "acumulado_diferencia_kg": acumulado_dif,
                "balance_entrada_salida_kg": balance,
                "acumulado_balance_kg": acumulado_balance,
                "porcentaje_diferencia": pct_dif,
                "kg_entrada_tina_stock": stock_row["kg_entrada_tina_stock"],
                "kg_cajas_stock": stock_row["kg_cajas_stock"],
                "diferencia_stock_kg": stock_dif,
                # En legacy: stock/arrastre por despesque. En vw_stolt: por el propio detalle diario.
                "stock_sin_procesar_kg": stock_sin_procesar,
            }
        )

    tot_entrada = sum(r["kg_entrada_tina"] for r in detalle)
    tot_salida = sum(r["kg_salida_no_tina"] for r in detalle)
    tot_consumo = sum(r["kg_consumo_tina"] for r in detalle)
    tot_stock = sum(r["kg_stock"] for r in detalle)
    tot_merma = sum(r["kg_merma"] for r in detalle)
    tot_dif = sum(r["diferencia_kg"] for r in detalle)
    tot_balance = sum(r["balance_entrada_salida_kg"] for r in detalle)
    tot_stock_fin = detalle[-1]["stock_sin_procesar_kg"] if detalle else 0.0

    kpis = {
        "kg_entrada_tina": tot_entrada,
        "kg_salida_no_tina": tot_salida,
        "kg_consumo_tina": tot_consumo,
        "kg_stock_entrada": tot_stock,
        "kg_merma": tot_merma,
        "kg_diferencia": tot_dif,
        "kg_balance_entrada_salida": tot_balance,
        "kg_stock_sin_procesar_fin": tot_stock_fin,
        "pct_diferencia": (tot_dif / tot_entrada * 100.0) if tot_entrada else None,
        "packs_entrada": sum(r["packs_entrada"] for r in detalle),
        "packs_salida": sum(r["packs_salida"] for r in detalle),
        "movs_consumo": sum(r["movs_consumo"] for r in detalle),
        "bc_qty_con_pedido": 0.0,
        "bc_qty_sin_pedido": 0.0,
        "bc_kg_con_pedido": 0.0,
        "bc_kg_sin_pedido": 0.0,
        "bc_lotes_innova": 0,
        "bc_lotes_enlazados": 0,
        "bc_lotes_sin_enlace": 0,
        "bc_pct_lotes_enlazados": None,
        "bc_kg_innova_enlazado": 0.0,
        "bc_kg_bc_enlazado": 0.0,
        "bc_kg_diferencia_enlazado": 0.0,
    }

    return {
        "detalle_diario": detalle,
        "kpis": kpis,
        "top_entradas": top_entradas,
        "top_salidas": top_salidas,
        "sql_trace": {
          "data_source": data_source,
          "view_or_tables": (
            ["dbo.vw_stolt"] if data_source == "vw_stolt_despesque" else [
              "dbo.vw_stolt",
              "dbo.proc_packs",
              "dbo.proc_matxacts",
              "dbo.proc_materials",
            ]
          ),
          "params": {
            "start": start.isoformat(),
            "end": end.isoformat(),
          },
          "queries": sql_trace,
        },
    }


def fmt_num(value: float | int | None, decimals: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:,.{decimals}f}"


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}%"


def build_table_rows(detalle: list[dict[str, Any]]) -> str:
    rows = []
    for r in detalle:
        rows.append(
            "<tr>"
            f"<td>{html.escape(r['fecha'])}</td>"
            f"<td class='num'>{fmt_num(r['kg_entrada_tina'])}</td>"
            f"<td class='num'>{fmt_num(r['kg_consumo_tina'])}</td>"
            f"<td class='num'>{fmt_num(r['diferencia_kg'])}</td>"
            f"<td class='num'>{fmt_pct(r['porcentaje_diferencia'])}</td>"
            f"<td class='num'>{fmt_num(r['kg_salida_no_tina'])}</td>"
            f"<td class='num'>{fmt_num(r['balance_entrada_salida_kg'])}</td>"
            f"<td class='num'>{fmt_num(r['acumulado_diferencia_kg'])}</td>"
            f"<td class='num'>{fmt_num(r['stock_sin_procesar_kg'])}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def build_stock_merma_table_rows(detalle: list[dict[str, Any]]) -> str:
    rows = []
    for r in detalle:
        if (
            r["kg_entrada_tina"] == 0
            and r["kg_salida_no_tina"] == 0
            and r["kg_stock"] == 0
            and r["kg_merma"] == 0
        ):
            continue
        rows.append(
            "<tr>"
            f"<td>{html.escape(r['fecha'])}</td>"
            f"<td class='num'>{fmt_num(r['kg_entrada_tina'])}</td>"
            f"<td class='num'>{fmt_num(r['kg_salida_no_tina'])}</td>"
            f"<td class='num'>{fmt_num(r['kg_stock'])}</td>"
            f"<td class='num'>{fmt_num(r['kg_merma'])}</td>"
            f"<td class='num'>{fmt_num(r['balance_entrada_salida_kg'])}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def build_bc_cruce_table_rows(detalle: list[dict[str, Any]]) -> str:
    rows = []
    for r in detalle:
        if not any(
            (
                r.get("kg_salida_no_tina"),
                r.get("bc_lotes_innova"),
                r.get("bc_lotes_enlazados"),
                r.get("bc_qty_con_pedido"),
                r.get("bc_qty_sin_pedido"),
            )
        ):
            continue
        rows.append(
            "<tr>"
            f"<td>{html.escape(r['fecha'])}</td>"
            f"<td class='num'>{fmt_num(r['kg_salida_no_tina'])}</td>"
            f"<td class='num'>{int(r.get('bc_lotes_innova', 0))}</td>"
            f"<td class='num'>{int(r.get('bc_lotes_enlazados', 0))}</td>"
            f"<td class='num'>{fmt_num(r.get('bc_kg_innova_enlazado', 0))}</td>"
            f"<td class='num'>{fmt_num(r.get('bc_kg_bc_enlazado', 0))}</td>"
            f"<td class='num'>{fmt_num(r.get('bc_kg_diferencia_enlazado', 0))}</td>"
            f"<td class='num'>{fmt_num(r.get('bc_qty_con_pedido', 0))}</td>"
            f"<td class='num'>{fmt_num(r.get('bc_qty_sin_pedido', 0))}</td>"
            f"<td class='num'>{fmt_num(r.get('bc_kg_con_pedido', 0))}</td>"
            f"<td class='num'>{fmt_num(r.get('bc_kg_sin_pedido', 0))}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def build_top_material_rows(items: list[dict[str, Any]]) -> str:
    rows = []
    for i, item in enumerate(items, start=1):
        rows.append(
            "<tr>"
            f"<td>{i}</td>"
            f"<td>{item['material']}</td>"
            f"<td>{html.escape(str(item['material_nombre']))}</td>"
            f"<td class='num'>{fmt_num(to_float(item['kg']))}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def render_html(
    title: str,
    start: dt.date,
    end: dt.date,
    data: dict[str, Any],
    logo_data_uri: str | None,
    data_source: str,
) -> str:
    detalle = data["detalle_diario"]
    k = data["kpis"]
    sql_trace = data.get("sql_trace", {})

    labels = [r["fecha"] for r in detalle]
    entrada = [round(r["kg_entrada_tina"], 2) for r in detalle]
    consumo = [round(r["kg_consumo_tina"], 2) for r in detalle]
    salida = [round(r["kg_salida_no_tina"], 2) for r in detalle]
    diferencia = [round(r["diferencia_kg"], 2) for r in detalle]
    acumulado = [round(r["acumulado_diferencia_kg"], 2) for r in detalle]
    stock_entrada = [round(r.get("kg_stock", 0), 2) for r in detalle]
    merma = [round(r.get("kg_merma", 0), 2) for r in detalle]

    detail_rows_html = build_table_rows(detalle)
    stock_merma_rows_html = build_stock_merma_table_rows(detalle)
    bc_cruce_rows_html = build_bc_cruce_table_rows(detalle)
    bc_loaded = bool(data.get("bc_cruce"))
    bc_note = (
        "Enlace por proc_packs.number = BC Item Ledger Entry [Lot No.]. Pedido desde Sales Shipment Line."
        if bc_loaded
        else "BC no disponible (--skip-bc o error de conexion). Valores a cero."
    )
    bc_cruce_section_html = f"""
      <article class="chart-card">
        <div class="section-head">
          <h3>Cruce Innova / Business Central por lote (number / Lot No.)</h3>
          <button type="button" class="btn-export" data-table-id="bcCruceTable" data-file-name="cruce_bc_salidas_pedido">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <p class="muted" style="margin-top:0;">
          Salidas Innova agrupadas por fecha de regtime. Cada pack de salida enlaza con BC por
          <strong>proc_packs.number</strong> = <strong>[Lot No.]</strong> (codigo de lote/caja).
          Con pedido = [Order No.] informado en el albaran; sin pedido = vacio.
          Unidades BC = Quantity; kilos BC = [Kilos] en Item Ledger Entry (valor absoluto).
          {html.escape(bc_note)}
        </p>
        <table id="bcCruceTable">
          <thead>
            <tr>
              <th>Fecha</th>
              <th class="num">Salidas Innova (kg)</th>
              <th class="num">Lotes Innova</th>
              <th class="num">Lotes enlazados BC</th>
              <th class="num">Kg Innova enlazado</th>
              <th class="num">BC Kilos enlazados</th>
              <th class="num">Dif. kg (I-BC)</th>
              <th class="num">BC con pedido (ud.)</th>
              <th class="num">BC sin pedido (ud.)</th>
              <th class="num">BC con pedido (kg)</th>
              <th class="num">BC sin pedido (kg)</th>
            </tr>
          </thead>
          <tbody>
            {bc_cruce_rows_html}
            <tr>
              <td><strong>TOTAL</strong></td>
              <td class="num"><strong>{fmt_num(k['kg_salida_no_tina'])}</strong></td>
              <td class="num"><strong>{int(k.get('bc_lotes_innova', 0))}</strong></td>
              <td class="num"><strong>{int(k.get('bc_lotes_enlazados', 0))}</strong></td>
              <td class="num"><strong>{fmt_num(k.get('bc_kg_innova_enlazado', 0))}</strong></td>
              <td class="num"><strong>{fmt_num(k.get('bc_kg_bc_enlazado', 0))}</strong></td>
              <td class="num"><strong>{fmt_num(k.get('bc_kg_diferencia_enlazado', 0))}</strong></td>
              <td class="num"><strong>{fmt_num(k.get('bc_qty_con_pedido', 0))}</strong></td>
              <td class="num"><strong>{fmt_num(k.get('bc_qty_sin_pedido', 0))}</strong></td>
              <td class="num"><strong>{fmt_num(k.get('bc_kg_con_pedido', 0))}</strong></td>
              <td class="num"><strong>{fmt_num(k.get('bc_kg_sin_pedido', 0))}</strong></td>
            </tr>
          </tbody>
        </table>
      </article>
"""
    top_entradas_rows = build_top_material_rows(data["top_entradas"])
    top_salidas_rows = build_top_material_rows(data["top_salidas"])
    logo_html = (
      f'<img class="brand-logo" src="{logo_data_uri}" alt="SSF" />'
      if logo_data_uri
      else ""
    )
    stock_cards_html = ""
    source_definition = build_source_definition(data_source)
    premisa_entrada_html = build_premisa_entrada_html()
    trace_tables = ", ".join(sql_trace.get("view_or_tables", []))
    trace_params = sql_trace.get("params", {})
    trace_query_items = []
    for item in sql_trace.get("queries", []):
      name = html.escape(str(item.get("name", "query")))
      query_txt = html.escape(str(item.get("query", "")))
      trace_query_items.append(
        "<details class='trace-query'>"
        f"<summary>{name}</summary>"
        f"<pre>{query_txt}</pre>"
        "</details>"
      )
    trace_queries_html = "\n".join(trace_query_items)
    if k.get("kg_stock_inicial") is not None:
      stock_cards_html += (
        f"<article class='card'><div class='kpi-title'>Stock inicial (kg)</div>"
        f"<div class='kpi-value'>{fmt_num(k['kg_stock_inicial'])}</div>"
        "<div class='kpi-sub'>Dato ingresado por usuario</div></article>"
      )
      stock_cards_html += (
        f"<article class='card'><div class='kpi-title'>Stock final teorico sin procesar (kg)</div>"
        f"<div class='kpi-value'>{fmt_num(k['kg_stock_final_teorico'])}</div>"
        "<div class='kpi-sub'>Stock inicial + Entradas - Consumo</div></article>"
      )
    if k.get("kg_stock_final_fisico") is not None:
      stock_cards_html += (
        f"<article class='card'><div class='kpi-title'>Stock final fisico (kg)</div>"
        f"<div class='kpi-value'>{fmt_num(k['kg_stock_final_fisico'])}</div>"
        "<div class='kpi-sub'>Medicion de planta</div></article>"
      )
      stock_cards_html += (
        f"<article class='card'><div class='kpi-title'>Ajuste conciliacion (kg)</div>"
        f"<div class='kpi-value'>{fmt_num(k['kg_ajuste_conciliacion'])}</div>"
        "<div class='kpi-sub'>Teorico - Fisico</div></article>"
      )

    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/xlsx@0.18.5/dist/xlsx.full.min.js"></script>
  <style>
    :root {{
      --bg: #f3f7f7;
      --card: #ffffff;
      --ink: #1e293b;
      --muted: #5b677a;
      --brand: #0b6e4f;
      --accent: #f59e0b;
      --danger: #b42318;
      --line: #dbe3ea;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at 20% 0%, #dff7ec 0%, transparent 35%),
        radial-gradient(circle at 90% 10%, #fff1d6 0%, transparent 30%),
        var(--bg);
    }}
    .wrap {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}
    .head {{
      background: linear-gradient(135deg, #0f766e, #0b6e4f);
      color: #fff;
      border-radius: 18px;
      padding: 24px;
      box-shadow: 0 10px 26px rgba(11, 110, 79, 0.22);
    }}
    .head-top {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
    }}
    .head h1 {{ margin: 0 0 8px 0; font-size: 30px; }}
    .head p {{ margin: 0; opacity: 0.95; }}
    .head-main {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .brand-logo {{
      height: 42px;
      width: auto;
      background: #ffffff;
      border-radius: 8px;
      padding: 4px 6px;
      box-shadow: 0 2px 10px rgba(2, 6, 23, 0.18);
    }}
    .grid {{
      margin-top: 16px;
      display: grid;
      grid-template-columns: repeat(4, minmax(220px, 1fr));
      gap: 12px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      box-shadow: 0 4px 12px rgba(2, 6, 23, 0.04);
    }}
    .kpi-title {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }}
    .kpi-value {{ font-size: 26px; font-weight: 700; margin-top: 6px; }}
    .kpi-sub {{ color: var(--muted); font-size: 12px; margin-top: 4px; }}
    .charts {{
      margin-top: 18px;
      display: grid;
      grid-template-columns: repeat(2, minmax(320px, 1fr));
      gap: 14px;
    }}
    .chart-card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      min-height: 320px;
      position: relative;
    }}
    .chart-card h3 {{ margin: 4px 0 12px 0; font-size: 16px; }}
    .section-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 10px;
    }}
    .section-head h3 {{
      margin: 0;
    }}
    .btn-max {{
      position: absolute;
      right: 14px;
      top: 10px;
      border: 1px solid var(--line);
      background: #ffffff;
      color: var(--ink);
      border-radius: 8px;
      padding: 6px 10px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
    }}
    .btn-max:hover {{
      background: #f8fafc;
    }}
    .btn-export {{
      border: 1px solid #1f7a4d;
      background: #e8f6ee;
      color: #0f5132;
      border-radius: 8px;
      padding: 6px 10px;
      font-size: 12px;
      font-weight: 700;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      white-space: nowrap;
    }}
    .btn-export:hover {{
      background: #d8f0e2;
    }}
    .btn-export-top {{
      flex-shrink: 0;
    }}
    .excel-icon {{
      width: 16px;
      height: 16px;
      border-radius: 3px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: #1f7a4d;
      color: #ffffff;
      font-size: 9px;
      font-weight: 800;
      line-height: 1;
    }}
    .chart-panel {{
      min-height: 0;
      height: 360px;
      position: relative;
      overflow: hidden;
    }}
    .chart-panel canvas {{
      position: absolute;
      left: 14px;
      right: 14px;
      top: 46px;
      bottom: 14px;
      width: auto !important;
      height: auto !important;
    }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 10px; font-size: 13px; }}
    th {{ text-align: left; background: #f8fafc; }}
    .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .tables {{
      margin-top: 18px;
      display: grid;
      grid-template-columns: 2fr 1fr;
      gap: 14px;
    }}
    .muted {{ color: var(--muted); }}
    .premisa-box {{
      margin-top: 14px;
      background: #f0faf6;
      border: 1px solid #b8e0cf;
      border-radius: 14px;
      padding: 14px 16px;
    }}
    .premisa-head {{
      margin: 0 0 8px 0;
      font-size: 16px;
      color: #0f5132;
    }}
    .premisa-list {{
      margin: 0 0 10px 0;
      padding-left: 20px;
      font-size: 13px;
      color: #1e293b;
    }}
    .premisa-list li {{ margin: 4px 0; }}
    .premisa-note {{
      margin: 4px 0;
      font-size: 13px;
      color: #334155;
    }}
    .trace-box {{
      margin-top: 18px;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
    }}
    .trace-head {{
      margin: 0 0 8px 0;
      font-size: 16px;
    }}
    .trace-meta {{
      font-size: 13px;
      color: var(--muted);
      margin: 4px 0;
    }}
    .trace-query {{
      margin-top: 10px;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 8px 10px;
      background: #fbfdff;
    }}
    .trace-query summary {{
      cursor: pointer;
      font-weight: 600;
      font-size: 13px;
      color: #0f172a;
    }}
    .trace-query pre {{
      margin: 10px 0 0 0;
      white-space: pre-wrap;
      font-size: 12px;
      color: #0f172a;
      background: #f8fafc;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      overflow-x: auto;
    }}
    .chart-modal {{
      position: fixed;
      inset: 0;
      background: rgba(15, 23, 42, 0.75);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 9999;
      padding: 24px;
    }}
    .chart-modal.is-open {{
      display: flex;
    }}
    .chart-modal-content {{
      width: min(1200px, 100%);
      height: min(88vh, 860px);
      background: #ffffff;
      border-radius: 14px;
      border: 1px solid var(--line);
      box-shadow: 0 20px 70px rgba(2, 6, 23, 0.5);
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }}
    .chart-modal-head {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      background: #f8fafc;
    }}
    .chart-modal-head h3 {{
      margin: 0;
      font-size: 16px;
    }}
    .chart-modal-close {{
      border: 1px solid var(--line);
      background: #ffffff;
      border-radius: 8px;
      padding: 6px 10px;
      cursor: pointer;
      font-weight: 600;
    }}
    .chart-modal-body {{
      flex: 1;
      min-height: 0;
      padding: 12px 16px 16px;
    }}
    .chart-modal-body canvas {{
      width: 100% !important;
      height: 100% !important;
    }}
    @media (max-width: 1080px) {{
      .grid {{ grid-template-columns: repeat(2, minmax(220px, 1fr)); }}
      .charts, .tables {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 640px) {{
      .grid {{ grid-template-columns: 1fr; }}
      .wrap {{ padding: 12px; }}
      .head h1 {{ font-size: 24px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="head">
      <div class="head-top">
        <div class="head-main">
          {logo_html}
          <h1>{html.escape(title)}</h1>
        </div>
        <button type="button" class="btn-export btn-export-top" id="btnExportAll" data-file-name="reporte_biomasa_completo">
          <span class="excel-icon">X</span>
          Exportar todo en Excel (5 hojas)
        </button>
      </div>
      <p>Periodo: <strong>{format_date_es(start)}</strong> a <strong>{format_date_es(end)}</strong></p>
      <p class="muted">{html.escape(source_definition)}</p>
    </section>

    {premisa_entrada_html}

    <section class="grid">
      <article class="card"><div class="kpi-title">Entradas de biomasa (kg)</div><div class="kpi-value">{fmt_num(k['kg_entrada_tina'])}</div><div class="kpi-sub">{k['packs_entrada']} packs · Stock+Merma</div></article>
      <article class="card"><div class="kpi-title">Stock en entradas (kg)</div><div class="kpi-value">{fmt_num(k.get('kg_stock_entrada', 0))}</div><div class="kpi-sub">pkpackaging=3, rtype distinto de 12</div></article>
      <article class="card"><div class="kpi-title">Merma en entradas (kg)</div><div class="kpi-value">{fmt_num(k.get('kg_merma', 0))}</div><div class="kpi-sub">pkpackaging=3, rtype=12</div></article>
      <article class="card"><div class="kpi-title">Cajas (kg)</div><div class="kpi-value">{fmt_num(k['kg_consumo_tina'])}</div><div class="kpi-sub">{k['movs_consumo']} movimientos</div></article>
      <article class="card"><div class="kpi-title">Diferencia (kg)</div><div class="kpi-value">{fmt_num(k['kg_diferencia'])}</div><div class="kpi-sub">% diferencia: {fmt_pct(k['pct_diferencia'])} · Entradas - Cajas</div></article>
      <article class="card"><div class="kpi-title">Salidas totales kg (todos los graders y basculas)</div><div class="kpi-value">{fmt_num(k['kg_salida_no_tina'])}</div><div class="kpi-sub">{k['packs_salida']} packs</div></article>
      <article class="card"><div class="kpi-title">Balance entradas - salidas (kg)</div><div class="kpi-value">{fmt_num(k['kg_balance_entrada_salida'])}</div><div class="kpi-sub">Diferencia E-S del periodo</div></article>
      <article class="card"><div class="kpi-title">Stock sin procesar fin de periodo (kg)</div><div class="kpi-value">{fmt_num(k['kg_stock_sin_procesar_fin'])}</div><div class="kpi-sub">Arrastre acumulado de Entradas - Cajas</div></article>
      <article class="card"><div class="kpi-title">BC lotes enlazados</div><div class="kpi-value">{int(k.get('bc_lotes_enlazados', 0)):,} / {int(k.get('bc_lotes_innova', 0)):,}</div><div class="kpi-sub">{fmt_pct(k.get('bc_pct_lotes_enlazados'))} · number = Lot No.</div></article>
      <article class="card"><div class="kpi-title">Kg Innova enlazado</div><div class="kpi-value">{fmt_num(k.get('bc_kg_innova_enlazado', 0))}</div><div class="kpi-sub">Salidas Innova con lote en BC</div></article>
      <article class="card"><div class="kpi-title">BC Kilos enlazados (ILE)</div><div class="kpi-value">{fmt_num(k.get('bc_kg_bc_enlazado', 0))}</div><div class="kpi-sub">Campo [Kilos] · dif.: {fmt_num(k.get('bc_kg_diferencia_enlazado', 0))} kg</div></article>
      <article class="card"><div class="kpi-title">BC con pedido</div><div class="kpi-value">{fmt_num(k.get('bc_qty_con_pedido', 0))} ud.</div><div class="kpi-sub">{fmt_num(k.get('bc_kg_con_pedido', 0))} kg · sin pedido: {fmt_num(k.get('bc_qty_sin_pedido', 0))} ud.</div></article>
      {stock_cards_html}
    </section>

    <section class="trace-box">
      <h3 class="trace-head">Trazabilidad SQL</h3>
      <p class="trace-meta">Origen: <strong>{html.escape(str(sql_trace.get('data_source', data_source)))}</strong></p>
      <p class="trace-meta">Tablas/Vistas: <strong>{html.escape(trace_tables)}</strong></p>
      <p class="trace-meta">Parametros: <strong>start={html.escape(str(trace_params.get('start', '-')))}</strong>, <strong>end={html.escape(str(trace_params.get('end', '-')))}</strong></p>
      {trace_queries_html}
    </section>

    <section class="charts">
      <article class="chart-card chart-panel">
        <h3>Evolucion diaria de biomasa (kg)</h3>
        <button type="button" class="btn-max" data-chart="lineKg">Maximizar</button>
        <canvas id="lineKg"></canvas>
      </article>
      <article class="chart-card chart-panel">
        <h3>Diferencia diaria y acumulada</h3>
        <button type="button" class="btn-max" data-chart="comboDiff">Maximizar</button>
        <canvas id="comboDiff"></canvas>
      </article>
      <article class="chart-card chart-panel">
        <h3>Composicion total del periodo (kg)</h3>
        <button type="button" class="btn-max" data-chart="donutTotals">Maximizar</button>
        <canvas id="donutTotals"></canvas>
      </article>
      <article class="chart-card chart-panel">
        <h3>Stock y merma diarios en entradas (kg)</h3>
        <button type="button" class="btn-max" data-chart="lineStockMerma">Maximizar</button>
        <canvas id="lineStockMerma"></canvas>
      </article>
      <article class="chart-card chart-panel">
        <h3>Diferencia entre entradas y salidas (kg)</h3>
        <button type="button" class="btn-max" data-chart="barBalance">Maximizar</button>
        <canvas id="barBalance"></canvas>
      </article>
    </section>

    <section class="tables">
      <article class="chart-card">
        <div class="section-head">
          <h3>Detalle diario de produccion</h3>
          <button type="button" class="btn-export" data-table-id="detalleTable" data-file-name="detalle_biomasa">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <table id="detalleTable">
          <thead>
            <tr>
              <th>Fecha</th>
              <th class="num">Entradas de biomasa</th>
              <th class="num">Cajas (kg)</th>
              <th class="num">Diferencia</th>
              <th class="num">% Diferencia</th>
              <th class="num">Salidas totales kg</th>
              <th class="num">Balance E-S</th>
              <th class="num">Acum. Diferencia</th>
              <th class="num">Stock sin procesar</th>
            </tr>
          </thead>
          <tbody>
            {detail_rows_html}
            <tr>
              <td><strong>TOTAL</strong></td>
              <td class="num"><strong>{fmt_num(k['kg_entrada_tina'])}</strong></td>
              <td class="num"><strong>{fmt_num(k['kg_consumo_tina'])}</strong></td>
              <td class="num"><strong>{fmt_num(k['kg_diferencia'])}</strong></td>
              <td class="num"><strong>{fmt_pct(k['pct_diferencia'])}</strong></td>
              <td class="num"><strong>{fmt_num(k['kg_salida_no_tina'])}</strong></td>
              <td class="num"><strong>{fmt_num(k['kg_balance_entrada_salida'])}</strong></td>
              <td class="num"><strong>{fmt_num(k['kg_diferencia'])}</strong></td>
              <td class="num"><strong>{fmt_num(k['kg_stock_sin_procesar_fin'])}</strong></td>
            </tr>
          </tbody>
        </table>
      </article>

      <article class="chart-card">
        <div class="section-head">
          <h3>Entradas, salidas, stock y merma</h3>
          <button type="button" class="btn-export" data-table-id="stockMermaTable" data-file-name="entradas_salidas_stock_merma">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <p class="muted" style="margin-top:0;">
          Stock y merma desglosan las entradas (pkpackaging=3) por proc_packs.rtype:
          rtype=12 es merma; rtype distinto de 12 es stock. Entradas = Stock + Merma.
        </p>
        <table id="stockMermaTable">
          <thead>
            <tr>
              <th>Fecha</th>
              <th class="num">Entradas (kg)</th>
              <th class="num">Salidas (kg)</th>
              <th class="num">Stock (kg)</th>
              <th class="num">Merma (kg)</th>
              <th class="num">Diferencia E-S (kg)</th>
            </tr>
          </thead>
          <tbody>
            {stock_merma_rows_html}
            <tr>
              <td><strong>TOTAL</strong></td>
              <td class="num"><strong>{fmt_num(k['kg_entrada_tina'])}</strong></td>
              <td class="num"><strong>{fmt_num(k['kg_salida_no_tina'])}</strong></td>
              <td class="num"><strong>{fmt_num(k.get('kg_stock_entrada', 0))}</strong></td>
              <td class="num"><strong>{fmt_num(k.get('kg_merma', 0))}</strong></td>
              <td class="num"><strong>{fmt_num(k['kg_balance_entrada_salida'])}</strong></td>
            </tr>
          </tbody>
        </table>
      </article>

      {bc_cruce_section_html}

      <article class="chart-card">
        <div class="section-head">
          <h3>Top materiales de entrada</h3>
          <button type="button" class="btn-export" data-table-id="topEntradasTable" data-file-name="top_materiales_entrada_tina_e">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <table id="topEntradasTable">
          <thead><tr><th>#</th><th>ID</th><th>Material</th><th class="num">kg</th></tr></thead>
          <tbody>{top_entradas_rows}</tbody>
        </table>
        <div class="section-head" style="margin-top:18px;">
          <h3>Top materiales de salida (resto)</h3>
          <button type="button" class="btn-export" data-table-id="topSalidasTable" data-file-name="top_materiales_salida_no_tina">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <table id="topSalidasTable">
          <thead><tr><th>#</th><th>ID</th><th>Material</th><th class="num">kg</th></tr></thead>
          <tbody>{top_salidas_rows}</tbody>
        </table>
      </article>
    </section>

    <div id="chartModal" class="chart-modal" aria-hidden="true">
      <div class="chart-modal-content" role="dialog" aria-modal="true" aria-labelledby="chartModalTitle">
        <div class="chart-modal-head">
          <h3 id="chartModalTitle">Grafica ampliada</h3>
          <button id="chartModalClose" type="button" class="chart-modal-close">Cerrar</button>
        </div>
        <div class="chart-modal-body">
          <canvas id="chartModalCanvas"></canvas>
        </div>
      </div>
    </div>
  </div>

<script>
const labels = {json.dumps(labels)};
const entrada = {json.dumps(entrada)};
const consumo = {json.dumps(consumo)};
const salida = {json.dumps(salida)};
const diferencia = {json.dumps(diferencia)};
const acumulado = {json.dumps(acumulado)};
const stockEntrada = {json.dumps(stock_entrada)};
const merma = {json.dumps(merma)};
const balance = entrada.map((v, i) => Number((v - salida[i]).toFixed(2)));

const chartConfigs = {{
  lineKg: {{
    type: 'line',
    data: {{
      labels,
      datasets: [
        {{ label: 'Entradas de biomasa', data: entrada, borderColor: '#0b6e4f', tension: 0.25, fill: false }},
        {{ label: 'Cajas (kg)', data: consumo, borderColor: '#dc2626', tension: 0.25, fill: false }},
        {{ label: 'Salidas totales kg', data: salida, borderColor: '#2563eb', tension: 0.25, fill: false }}
      ]
    }},
    options: {{ responsive: true, maintainAspectRatio: false }}
  }},
  comboDiff: {{
    data: {{
      labels,
      datasets: [
        {{ type: 'bar', label: 'Diferencia diaria', data: diferencia, backgroundColor: diferencia.map(v => v >= 0 ? 'rgba(11,110,79,.7)' : 'rgba(180,35,24,.7)') }},
        {{ type: 'line', label: 'Acumulado diferencia', data: acumulado, borderColor: '#7c3aed', tension: 0.2, yAxisID: 'y1' }}
      ]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      scales: {{
        y: {{ position: 'left' }},
        y1: {{ position: 'right', grid: {{ drawOnChartArea: false }} }}
      }}
    }}
  }},
  donutTotals: {{
    type: 'doughnut',
    data: {{
      labels: ['Stock en entradas', 'Merma', 'Salidas', 'Cajas (kg)'],
      datasets: [{{
        data: [
          {k.get('kg_stock_entrada', 0):.2f},
          {k.get('kg_merma', 0):.2f},
          {k['kg_salida_no_tina']:.2f},
          {k['kg_consumo_tina']:.2f}
        ],
        backgroundColor: ['#0b6e4f', '#f59e0b', '#2563eb', '#dc2626']
      }}]
    }},
    options: {{ responsive: true, maintainAspectRatio: false }}
  }},
  lineStockMerma: {{
    type: 'line',
    data: {{
      labels,
      datasets: [
        {{ label: 'Stock en entradas', data: stockEntrada, borderColor: '#0b6e4f', tension: 0.25, fill: false }},
        {{ label: 'Merma', data: merma, borderColor: '#f59e0b', tension: 0.25, fill: false }}
      ]
    }},
    options: {{ responsive: true, maintainAspectRatio: false }}
  }},
  barBalance: {{
    type: 'bar',
    data: {{
      labels,
      datasets: [{{
        label: 'Diferencia entre entradas y salidas',
        data: balance,
        backgroundColor: balance.map(v => v >= 0 ? 'rgba(37,99,235,.75)' : 'rgba(245,158,11,.75)')
      }}]
    }},
    options: {{ responsive: true, maintainAspectRatio: false }}
  }}
}};

for (const [chartId, chartConfig] of Object.entries(chartConfigs)) {{
  new Chart(document.getElementById(chartId), chartConfig);
}}

const chartModal = document.getElementById('chartModal');
const chartModalClose = document.getElementById('chartModalClose');
const chartModalTitle = document.getElementById('chartModalTitle');
const chartModalCanvas = document.getElementById('chartModalCanvas');
let chartModalInstance = null;

function openChartModal(chartId, title) {{
  if (!(chartId in chartConfigs)) return;
  chartModalTitle.textContent = title || 'Grafica ampliada';
  chartModal.classList.add('is-open');
  chartModal.setAttribute('aria-hidden', 'false');

  if (chartModalInstance) {{
    chartModalInstance.destroy();
  }}

  const modalConfig = JSON.parse(JSON.stringify(chartConfigs[chartId]));
  chartModalInstance = new Chart(chartModalCanvas, modalConfig);
}}

function closeChartModal() {{
  chartModal.classList.remove('is-open');
  chartModal.setAttribute('aria-hidden', 'true');
  if (chartModalInstance) {{
    chartModalInstance.destroy();
    chartModalInstance = null;
  }}
}}

document.querySelectorAll('.btn-max').forEach((btn) => {{
  btn.addEventListener('click', () => {{
    const chartId = btn.getAttribute('data-chart');
    const title = btn.closest('.chart-card')?.querySelector('h3')?.textContent?.trim();
    openChartModal(chartId, title);
  }});
}});

chartModalClose.addEventListener('click', closeChartModal);
chartModal.addEventListener('click', (event) => {{
  if (event.target === chartModal) {{
    closeChartModal();
  }}
}});

document.addEventListener('keydown', (event) => {{
  if (event.key === 'Escape' && chartModal.classList.contains('is-open')) {{
    closeChartModal();
  }}
}});

function exportTableToExcel(tableId, fileName) {{
  const table = document.getElementById(tableId);
  if (!table) return;

  const workbook = [
    '<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel" xmlns="http://www.w3.org/TR/REC-html40">',
    '<head><meta charset="UTF-8"></head>',
    '<body>',
    table.outerHTML,
    '</body>',
    '</html>'
  ].join('');

  const blob = new Blob(['\ufeff' + workbook], {{ type: 'application/vnd.ms-excel' }});
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${{fileName || 'tabla'}}.xls`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}}

function exportAllTablesToExcel(fileName) {{
  const sections = [
    {{ sheetName: 'Detalle diario', tableId: 'detalleTable' }},
    {{ sheetName: 'Stock y merma', tableId: 'stockMermaTable' }},
    {{ sheetName: 'Cruce BC', tableId: 'bcCruceTable' }},
    {{ sheetName: 'Top entrada', tableId: 'topEntradasTable' }},
    {{ sheetName: 'Top salida', tableId: 'topSalidasTable' }}
  ];

  if (typeof XLSX === 'undefined') {{
    alert('No se pudo cargar la libreria de Excel. Revisa conexion a internet.');
    return;
  }}

  const workbook = XLSX.utils.book_new();

  for (const section of sections) {{
    const table = document.getElementById(section.tableId);
    if (!table) continue;
    const worksheet = XLSX.utils.table_to_sheet(table, {{ raw: true }});
    XLSX.utils.book_append_sheet(workbook, worksheet, section.sheetName);
  }}

  XLSX.writeFile(workbook, `${{fileName || 'reporte_biomasa_completo'}}.xlsx`);
}}

document.querySelectorAll('.btn-export').forEach((btn) => {{
  btn.addEventListener('click', () => {{
    const tableId = btn.getAttribute('data-table-id');
    const fileName = btn.getAttribute('data-file-name');
    if (tableId) {{
      exportTableToExcel(tableId, fileName);
    }}
  }});
}});

const btnExportAll = document.getElementById('btnExportAll');
if (btnExportAll) {{
  btnExportAll.addEventListener('click', () => {{
    const fileName = btnExportAll.getAttribute('data-file-name');
    exportAllTablesToExcel(fileName);
  }});
}}
</script>
</body>
</html>
"""


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    env_path = base_dir / ".env"
    load_dotenv_file(env_path)
    logo_data_uri = load_logo_data_uri(base_dir)
    args = parse_args()
    if args.stock_final_fisico is not None and args.stock_inicial is None:
      raise ValueError("Si informa --stock-final-fisico tambien debe informar --stock-inicial")
    start, end = resolve_dates(args)
    db_user, db_password = resolve_db_credentials(args)

    if args.output:
        output_path = Path(args.output)
    else:
        out_dir = base_dir / "Reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"reporte_biomasa_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.html"

    print("\nConectando a base de datos y generando reporte...")

    conn = pymssql.connect(
        server=args.server,
        user=db_user,
        password=db_password,
        database=args.database,
        login_timeout=8,
        timeout=180,
    )

    try:
        report_data = build_report_data(conn, start, end, args.data_source)
        enrich_stock_validation(report_data, args.stock_inicial, args.stock_final_fisico)
        innova_lotes = fetch_innova_salidas_lotes(conn, start, end)
    finally:
        conn.close()

    if not args.skip_bc:
        try:
            bc_server, bc_database, bc_user, bc_password = resolve_bc_credentials(args)
            print("Conectando a Business Central (cruce por lote)...")
            bc_conn = pymssql.connect(
                server=bc_server,
                user=bc_user,
                password=bc_password,
                database=bc_database,
                login_timeout=args.bc_login_timeout,
                timeout=args.bc_timeout,
            )
            try:
                print(
                    f"Consultando lotes BC (timeout {args.bc_timeout}s; "
                    "puede tardar 1-3 min desde red corporativa)..."
                )
                bc_data = fetch_bc_salidas_pedido(bc_conn, start, end)
                attach_bc_cruce_to_report(report_data, bc_data, innova_lotes)
                print("Business Central cargado correctamente.")
            finally:
                bc_conn.close()
        except Exception as bc_exc:
            print(f"Aviso: no se pudo cargar Business Central ({bc_exc}).")
            print("El reporte se genera sin cruce BC. Use --skip-bc para omitir BC a proposito.")

    html_report = render_html(args.title, start, end, report_data, logo_data_uri, args.data_source)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_report, encoding="utf-8")

    print_console_summary(start, end, output_path, report_data)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log = write_error_log(exc)
        print("\nSe produjo un error al generar el reporte.")
        print(f"Detalle guardado en log: {log}")
        print(f"Error: {exc}")
        raise
