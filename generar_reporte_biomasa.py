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
import calendar
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
DEFAULT_INNOVA_CRED_TARGET = "biomasa_sql_innova"
DEFAULT_BC_CRED_TARGET = "biomasa_sql_bc"


def project_root() -> Path:
    """Carpeta del proyecto (donde estan los scripts)."""
    return Path(__file__).resolve().parent


def project_env_path(base_dir: Path | None = None) -> Path:
    """Credenciales locales del proyecto: <carpeta_scripts>/.env"""
    return (base_dir or project_root()) / ".env"


# Compatibilidad con imports antiguos (configurar_credenciales.py).
def user_credentials_env_path() -> Path:
    return project_env_path()


def hide_windows_file(path: Path) -> None:
    """Marca el fichero como oculto en Windows (Explorer). Opcional."""
    if os.name != "nt" or not path.exists():
        return
    try:
        import ctypes

        FILE_ATTRIBUTE_HIDDEN = 0x02
        ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_HIDDEN)
    except Exception:
        pass


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


def load_app_credentials(base_dir: Path) -> list[Path]:
    """Carga credenciales solo desde .env en la carpeta del proyecto."""
    loaded: list[Path] = []
    env_path = project_env_path(base_dir)
    if env_path.exists():
        load_dotenv_file(env_path)
        loaded.append(env_path)
    return loaded


def keyring_get(service: str, username: str) -> str:
    if keyring is None:
        return ""
    try:
        return (keyring.get_password(service, username) or "").strip()
    except Exception:
        return ""


def keyring_set(service: str, username: str, password: str) -> bool:
    if keyring is None:
        return False
    try:
        keyring.set_password(service, username, password)
        return True
    except Exception:
        return False


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
      default=DEFAULT_INNOVA_CRED_TARGET,
      help="Identificador keyring para credenciales Innova.",
    )
    parser.add_argument(
      "--bc-cred-target",
      default=DEFAULT_BC_CRED_TARGET,
      help="Identificador keyring para credenciales Business Central.",
    )
    parser.add_argument(
      "--save-creds",
      action="store_true",
      help="Guarda user/pass Innova y BC en el almacen seguro de Windows (Credential Manager).",
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
      default=None,
      help="Stock inicial del periodo (kg) o 'auto' (equivale a --arrastre-mensual).",
    )
    parser.add_argument(
      "--arrastre-mensual",
      action="store_true",
      help="Calcula stock inicial encadenando cierre mensual del mes anterior.",
    )
    parser.add_argument(
      "--stock-ancla",
      default=None,
      help="Mes ancla para arrastre (dd/mm/aaaa, dia 1). Default: 01/01 del ano del periodo.",
    )
    parser.add_argument(
      "--stock-ancla-kg",
      type=float,
      default=0.0,
      help="Stock de apertura (kg) en el mes ancla (default 0).",
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
    """Parsea dd/mm/aaaa (tambien dd-mm-aaaa / aaaa-mm-dd). Rechaza dias inexistentes (p.ej. 31/06)."""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Fecha vacia. Use formato dd/mm/aaaa (ejemplo: 31/03/2026).")

    formats = ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"]
    calendar_errors: list[str] = []
    for fmt in formats:
        try:
            parsed = dt.datetime.strptime(raw, fmt).date()
            # Comprobacion de ida y vuelta: evita ambiguedades residuales.
            if parsed.strftime(fmt) != dt.datetime.strptime(raw, fmt).strftime(fmt):
                continue
            return parsed
        except ValueError as exc:
            msg = str(exc).lower()
            if "day is out of range" in msg or "month must be in" in msg or "unconverted data" in msg:
                calendar_errors.append(str(exc))
            continue

    # Si el patron parece dd/mm/aaaa pero el dia/mes no existe en el calendario.
    for sep in ("/", "-"):
        parts = raw.split(sep)
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            # dd/mm/yyyy o yyyy-mm-dd
            if len(parts[0]) == 4:
                year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
            else:
                day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
            if not (1 <= month <= 12):
                raise ValueError(
                    f"Fecha invalida: {raw}. El mes {month} no existe. Use dd/mm/aaaa."
                )
            max_day = calendar.monthrange(year, month)[1]
            if not (1 <= day <= max_day):
                raise ValueError(
                    f"Fecha invalida: {raw}. El dia {day:02d} no existe en "
                    f"{month:02d}/{year} (ese mes tiene {max_day} dias)."
                )
            break

    raise ValueError(
        f"Fecha invalida: {raw}. Use formato dd/mm/aaaa "
        f"(ejemplo: 01/03/2026). No se admiten dias inexistentes (31/06)."
    )


def parse_date_range(start_raw: str, end_raw: str) -> tuple[dt.date, dt.date]:
    start = parse_user_date(start_raw)
    end = parse_user_date(end_raw)
    if end < start:
        raise ValueError(
            f"La fecha fin ({format_date_es(end)}) no puede ser menor que "
            f"la fecha inicio ({format_date_es(start)})."
        )
    return start, end


def resolve_dates(args: argparse.Namespace) -> tuple[dt.date, dt.date]:
  if args.start and args.end:
    return parse_date_range(args.start, args.end)

  start_default = None
  end_default = None
  if args.start:
    try:
      start_default = format_date_es(parse_user_date(args.start))
    except ValueError as exc:
      print(f"[AVISO] {exc}")
  if args.end:
    try:
      end_default = format_date_es(parse_user_date(args.end))
    except ValueError as exc:
      print(f"[AVISO] {exc}")

  print("Ingrese el rango de fechas para el reporte")
  print("(formato dd/mm/aaaa; no se admiten dias inexistentes, p.ej. 31/06)")
  while True:
    start_raw = prompt_with_default("Fecha inicio (dd/mm/aaaa)", start_default)
    end_raw = prompt_with_default("Fecha fin (dd/mm/aaaa)", end_default)
    try:
      return parse_date_range(start_raw, end_raw)
    except ValueError as exc:
      print(f"[ERROR] {exc}")
      print("Vuelva a introducir las fechas.\n")
      start_default = None
      end_default = None


def parse_stock_inicial_arg(value: str | None) -> tuple[float | None, bool]:
    if value is None:
        return None, False
    if str(value).strip().lower() == "auto":
        return None, True
    try:
        return float(value), False
    except ValueError as exc:
        raise ValueError(
            f"--stock-inicial invalido: {value}. Use un numero o 'auto'."
        ) from exc


def first_day_of_month(value: dt.date) -> dt.date:
    return value.replace(day=1)


def last_day_of_month(value: dt.date) -> dt.date:
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def add_months(value: dt.date, months: int) -> dt.date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


def previous_calendar_month(value: dt.date) -> tuple[dt.date, dt.date]:
    first = first_day_of_month(value)
    prev_last = first - dt.timedelta(days=1)
    return first_day_of_month(prev_last), prev_last


def resolve_stock_ancla(args: argparse.Namespace, period_start: dt.date) -> tuple[dt.date, float]:
    if args.stock_ancla:
        ancla = parse_user_date(args.stock_ancla)
        if ancla.day != 1:
            print(f"Aviso: --stock-ancla {format_date_es(ancla)} no es dia 1; se usa {format_date_es(first_day_of_month(ancla))}")
            ancla = first_day_of_month(ancla)
    else:
        ancla = dt.date(period_start.year, 1, 1)
    return ancla, float(args.stock_ancla_kg)


def fetch_period_kpi_totals(
    conn: pymssql.Connection,
    start: dt.date,
    end: dt.date,
    data_source: str,
) -> dict[str, float]:
    data = build_report_data(conn, start, end, data_source)
    k = data["kpis"]
    return {
        "kg_entrada_tina": k["kg_entrada_tina"],
        "kg_consumo_tina": k["kg_consumo_tina"],
        "kg_salida_no_tina": k["kg_salida_no_tina"],
        "kg_diferencia": k["kg_diferencia"],
    }


def compute_stock_apertura_arrastre(
    conn: pymssql.Connection,
    as_of: dt.date,
    data_source: str,
    ancla: dt.date,
    ancla_kg: float,
) -> tuple[float, list[dict[str, Any]]]:
    """Stock de apertura en as_of por encadenamiento mensual desde mes ancla."""
    month_start = first_day_of_month(as_of)
    trail: list[dict[str, Any]] = []
    stock = ancla_kg
    current = ancla

    if month_start < ancla:
        raise ValueError(
            f"El periodo ({format_date_es(as_of)}) es anterior al mes ancla ({format_date_es(ancla)})"
        )

    while current < month_start:
        month_end = last_day_of_month(current)
        totals = fetch_period_kpi_totals(conn, current, month_end, data_source)
        cierre = stock + totals["kg_diferencia"]
        trail.append({
            "mes": format_date_es(current),
            "mes_fin": format_date_es(month_end),
            "kg_stock_apertura": stock,
            "kg_entrada_tina": totals["kg_entrada_tina"],
            "kg_consumo_tina": totals["kg_consumo_tina"],
            "kg_salida_no_tina": totals["kg_salida_no_tina"],
            "kg_stock_cierre": cierre,
        })
        stock = cierre
        current = add_months(current, 1)

    if as_of > month_start:
        partial_end = as_of - dt.timedelta(days=1)
        if partial_end >= month_start:
            totals = fetch_period_kpi_totals(conn, month_start, partial_end, data_source)
            stock += totals["kg_diferencia"]

    return stock, trail


def fetch_tinas_arrastradas(
    conn: pymssql.Connection,
    start: dt.date,
    end: dt.date,
) -> dict[str, float | int]:
    """Tinas entrada creadas en M-1 (proc_packs.regtime) consumidas en el periodo (matxacts.regtime)."""
    prev_start, prev_end = previous_calendar_month(start)
    query = f"""
    SELECT
        COUNT(DISTINCT p.id) AS packs,
        SUM(CAST(x.weight AS float)) AS kg
    FROM dbo.proc_matxacts x
    INNER JOIN dbo.proc_packs p ON p.id = x.pack
    INNER JOIN dbo.proc_materials m ON m.material = p.material
    WHERE x.xactpath = 1
      AND LOWER(m.name) LIKE '%tina%'
      AND {SQL_LEGACY_ES_ENTRADA}
      AND p.regtime >= %s AND p.regtime < DATEADD(day, 1, %s)
      AND x.regtime >= %s AND x.regtime < DATEADD(day, 1, %s)
    """
    cur = conn.cursor()
    rows = fetch_rows(cur, query, (prev_start, prev_end, start, end))
    row = rows[0] if rows else {}
    return {
        "packs": int(row.get("packs") or 0),
        "kg": to_float(row.get("kg")),
        "mes_origen_inicio": prev_start,
        "mes_origen_fin": prev_end,
    }


def enrich_arrastre_mensual(
    report_data: dict[str, Any],
    stock_inicial: float,
    trail: list[dict[str, Any]],
    tinas_arrastradas: dict[str, float | int],
) -> None:
    k = report_data["kpis"]
    k["arrastre_activo"] = True
    k["kg_stock_inicial_auto"] = True
    k["arrastre_trail"] = trail
    k["tinas_arrastradas_packs"] = int(tinas_arrastradas["packs"])
    k["kg_tinas_arrastradas"] = float(tinas_arrastradas["kg"])
    k["tinas_arrastradas_desde"] = format_date_es(tinas_arrastradas["mes_origen_inicio"])
    k["tinas_arrastradas_hasta"] = format_date_es(tinas_arrastradas["mes_origen_fin"])
    k["kg_stock_cierre_teorico"] = stock_inicial + k["kg_diferencia"]


def finalize_balance_kpis(report_data: dict[str, Any]) -> None:
    """Merma = Entradas TINA - Salidas CAJA - Stock de tinas (premisa 5). Ver PREMISAS.md."""
    k = report_data["kpis"]
    detalle = report_data["detalle_diario"]
    stock_ini = k.get("kg_stock_inicial") or 0.0

    k["kg_stock_entrada"] = sum(row.get("kg_stock_tina", 0) for row in detalle)

    stock_inventario = k.get("kg_stock_final_fisico")
    if stock_inventario is None:
        stock_inventario = k.get("kg_stock_cierre_teorico")
    if stock_inventario is None:
        if k.get("kg_stock_final_teorico") is not None:
            stock_inventario = k["kg_stock_final_teorico"]
        else:
            # Premisa 7 pendiente: arrastre provisional Entradas - Procesadas
            stock_inventario = stock_ini + k["kg_diferencia"]
        k["kg_stock_cierre_teorico"] = stock_inventario
    k["kg_stock_inventario"] = stock_inventario
    k["kg_stock_balance"] = stock_inventario

    k["kg_merma"] = (
        k["kg_entrada_tina"] - k["kg_salida_no_tina"] - k["kg_stock_entrada"]
    )
    k["pct_merma"] = (
        k["kg_merma"] / k["kg_entrada_tina"] * 100.0 if k["kg_entrada_tina"] else None
    )

    acumulado_inventario = stock_ini
    for row in detalle:
        stock_tinas_dia = row.get("kg_stock_tina", 0)
        row["kg_stock_entrada"] = stock_tinas_dia
        acumulado_inventario += row["diferencia_kg"]
        row["kg_stock_inventario"] = acumulado_inventario
        row["kg_stock_balance"] = acumulado_inventario
        row["kg_merma"] = (
            row["kg_entrada_tina"] - row["kg_salida_no_tina"] - stock_tinas_dia
        )


def resolve_db_credentials(args: argparse.Namespace) -> tuple[str, str]:
    user = (args.user or "").strip()
    password = (args.password or "").strip()

    if not user:
        user = keyring_get(args.cred_target, "user")
    if not password:
        password = keyring_get(args.cred_target, "password")

    if not user or not password:
      raise RuntimeError(
        "No hay credenciales Innova. Edite el fichero .env en la carpeta del proyecto "
        "(vea .env.example): DB_USER y DB_PASSWORD."
      )

    if args.save_creds:
        if keyring_set(args.cred_target, "user", user) and keyring_set(
            args.cred_target, "password", password
        ):
            print(f"Credenciales Innova guardadas en Windows Credential Manager ({args.cred_target}).")
        else:
            print("Aviso: no se pudieron guardar credenciales Innova en keyring.")

    return user, password


def resolve_bc_credentials(args: argparse.Namespace) -> tuple[str, str, str, str]:
    server = (args.bc_server or "").strip()
    database = (args.bc_database or "").strip()
    user = (args.bc_user or "").strip()
    password = (args.bc_password or "").strip()

    if not user:
        user = keyring_get(args.bc_cred_target, "user")
    if not password:
        password = keyring_get(args.bc_cred_target, "password")
    if not server:
        server = keyring_get(args.bc_cred_target, "server")
    if not database:
        database = keyring_get(args.bc_cred_target, "database")

    if not all([server, database, user, password]):
        raise RuntimeError(
            "Credenciales Business Central incompletas. Edite .env en la carpeta del proyecto "
            "(BC_SERVER, BC_DATABASE, BC_USER, BC_PASSWORD; vea .env.example)."
        )

    if args.save_creds:
        ok = all(
            [
                keyring_set(args.bc_cred_target, "server", server),
                keyring_set(args.bc_cred_target, "database", database),
                keyring_set(args.bc_cred_target, "user", user),
                keyring_set(args.bc_cred_target, "password", password),
            ]
        )
        if ok:
            print(f"Credenciales BC guardadas en Windows Credential Manager ({args.bc_cred_target}).")
        else:
            print("Aviso: no se pudieron guardar credenciales BC en keyring.")

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
    print(f"Entradas TINA (kg): {fmt_num(k['kg_entrada_tina'])}")
    print(f"TINA procesada (kg): {fmt_num(k['kg_consumo_tina'])}")
    print(f"Salidas CAJA (kg): {fmt_num(k['kg_salida_no_tina'])}")
    print(f"Stock de tinas (kg): {fmt_num(k.get('kg_stock_entrada', 0))}")
    print(f"Merma (Entradas - Salidas - Stock tinas): {fmt_num(k.get('kg_merma', 0))}")
    if k.get("pct_merma") is not None:
      print(f"% Merma sobre entradas: {fmt_pct(k['pct_merma'])}")
    if k.get("kg_stock_inventario") is not None:
      print(f"Stock inventario cierre (kg): {fmt_num(k['kg_stock_inventario'])}")
    print(f"Stock sin procesar fin de periodo (kg): {fmt_num(k['kg_stock_sin_procesar_fin'])}")
    print(f"% Diferencia: {fmt_pct(k['pct_diferencia'])}")
    print(f"Nº de Tinas (entrada): {k['packs_entrada']}")
    print(f"Packs salida (Nº de Cajas): {k['packs_salida']}")
    print(f"Movimientos TINA procesada: {k['movs_consumo']}")
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
    if k.get("bc_bal_kg_stock_inicial") is not None:
      print(
          f"Balance BC E/G — Stock inicial: {fmt_num(k['bc_bal_kg_stock_inicial'])} | "
          f"Salidas Innova: {fmt_num(k['bc_bal_kg_produccion'])} | "
          f"Ventas: {fmt_num(k['bc_bal_kg_ventas'])} | "
          f"Stock teorico: {fmt_num(k['bc_bal_kg_stock_teorico'])} | "
          f"Stock real: {fmt_num(k['bc_bal_kg_stock_real'])} | "
          f"Check: {fmt_num(k['bc_bal_kg_check'])} kg"
      )
    if k.get("bc_bal_cajas_stock_inicial") is not None:
      print(
          f"Balance por tipo (cajas) — Stock inicial: {int(k['bc_bal_cajas_stock_inicial']):,} | "
          f"Entradas BC (+): {int(k['bc_bal_cajas_entradas']):,} | "
          f"Ventas: {int(k['bc_bal_cajas_ventas']):,} | "
          f"Teorico: {int(k['bc_bal_cajas_stock_teorico']):,} | "
          f"Real: {int(k['bc_bal_cajas_stock_real']):,} | "
          f"Check: {int(k['bc_bal_cajas_check']):,} cajas"
      )
    if k.get("kg_stock_inicial") is not None:
      origen = " (arrastre mensual)" if k.get("kg_stock_inicial_auto") else ""
      print(f"Stock inicial (kg){origen}: {fmt_num(k['kg_stock_inicial'])}")
      print(f"Stock final teorico (kg): {fmt_num(k['kg_stock_final_teorico'])}")
    if k.get("kg_stock_cierre_teorico") is not None:
      print(f"Stock cierre teorico TINA (kg): {fmt_num(k['kg_stock_cierre_teorico'])}")
    if k.get("arrastre_activo"):
      print(
          f"Tinas arrastradas ({k.get('tinas_arrastradas_desde')} a {k.get('tinas_arrastradas_hasta')} "
          f"consumidas en periodo): {int(k.get('tinas_arrastradas_packs', 0)):,} Nº de Tinas / "
          f"{fmt_num(k.get('kg_tinas_arrastradas', 0))} kg"
      )
    if k.get("kg_stock_final_fisico") is not None:
      print(f"Stock final fisico (kg): {fmt_num(k['kg_stock_final_fisico'])}")
      print(f"Ajuste conciliacion (kg): {fmt_num(k['kg_ajuste_conciliacion'])}")
    print(f"HTML generado: {output_path}")
    print(f"\n*** {NOTA_ALERTA_VAP_TITULO} ***")
    print(NOTA_ALERTA_VAP)


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
    if k.get("kg_stock_cierre_teorico") is None:
      k["kg_stock_cierre_teorico"] = stock_teorico

    if stock_final_fisico is not None:
      # Positivo = faltante/merma a justificar, negativo = sobrante.
      k["kg_ajuste_conciliacion"] = stock_teorico - stock_final_fisico


def sql_row_date_key(value: Any) -> str:
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


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


# Premisas canonicas (PREMISAS.md premisas 1-6). Fecha: proc_packs/proc_matxacts.prday.
PREMISA_ENTRADA_REGLAS = (
  "Entrada TINA: proc_packs + proc_materials, pkpackaging = 3, rtype IN ('1','12'), fecha prday.",
)
PREMISA_PROCESADA = (
  "Tinas procesadas: proc_matxacts + proc_materials, pkpackaging = 3, xactpath IN ('1'), fecha prday.",
)
PREMISA_SALIDA_REGLAS = (
  "Salida CAJA: proc_packs + proc_materials, pkpackaging <> 3 o NULL, rtype = 1, fecha prday.",
)
PREMISA_STOCK_TINAS_REGLAS = (
  "Stock de tinas: proc_packs, pkpackaging = 3, rtype IN ('1'), fecha prday; SUM(nregs) y SUM(weight).",
)
PREMISA_STOCK_MERMA_REGLAS = (
  "Stock de tinas: consulta directa premisa 4 (no Entradas - Procesadas).",
  "Merma (kg) = Entrada TINA - Salidas CAJA - Stock de tinas.",
  "Balance masa: Entrada TINA = Salidas CAJA + Stock de tinas + Merma.",
  "Stock inventario / arrastre mensual: premisa 7 pendiente.",
)
PREMISA_SALIDA = "Salida CAJA por prday (premisa 3)."
PREMISA_TINA_PROCESADA = PREMISA_PROCESADA
PREMISA_CAJAS = PREMISA_PROCESADA  # alias historico

SQL_PK_MATERIAL = "m"
SQL_LEGACY_ES_ENTRADA = "m.pkpackaging = 3"
SQL_LEGACY_ES_SALIDA = "(m.pkpackaging <> 3 OR m.pkpackaging IS NULL)"
SQL_ENTRADA_TINA = "m.pkpackaging = 3 AND p.rtype IN ('1', '12')"
SQL_SALIDA_CAJA = "(m.pkpackaging <> 3 OR m.pkpackaging IS NULL) AND p.rtype = 1"
SQL_STOCK_TINA = "mat.pkpackaging = 3 AND pk.rtype IN ('1')"
SQL_PROCESADA_TINA = "mat.pkpackaging = 3 AND pk.xactpath IN ('1')"
SQL_PRDAY_RANGO = "CAST({alias}.prday AS date) >= %s AND {alias}.prday < DATEADD(day, 1, %s)"

SQL_VW_STOLT_ES_ENTRADA = "m.pkpackaging = 3"
SQL_VW_STOLT_ES_SALIDA = "(m.pkpackaging <> 3 OR m.pkpackaging IS NULL)"

PREMISA_BC_PEDIDO_REGLAS = (
  "Clave de enlace: dbo.proc_packs.number (codigo de lote/caja) = bc.[Item Ledger Entry].[Lot No.].",
  "Ventas BC: Item Ledger Entry ([Entry Type] = 1); kilos BC = ABS([Kilos]).",
  "Pedido desde Sales Shipment Line ([Order No.]) del [Document No.] del ILE.",
  "Con pedido: [Order No.] informado en el albaran BC.",
  "Sin pedido: [Order No.] vacio o NULL.",
  "Cruce por lote (no por fecha): contabilizacion BC puede ser otro dia del mes.",
  "Almacenes BC: Location Code E y G unicamente.",
  "Cruce kg: peso salida Innova (proc_packs.weight) vs [Kilos] BC en lotes enlazados.",
  "Ver PREMISAS.md — Premisa 6 (premisa legacy BC).",
)
SQL_BC_SALIDA_CON_PEDIDO = "NULLIF(LTRIM(RTRIM(sl.[Order No.])), '') IS NOT NULL"
SQL_BC_SALIDA_SIN_PEDIDO = "NULLIF(LTRIM(RTRIM(sl.[Order No.])), '') IS NULL"
SQL_BC_ILE_SALE = "ile.[Entry Type] = 1"
SQL_BC_ILE_POS_ADJ = "ile.[Entry Type] = 2"
SQL_BC_ILE_NEG_ADJ = "ile.[Entry Type] = 3"
SQL_BC_ILE_SALE_OR_NEG_ADJ = "ile.[Entry Type] IN (1, 3)"
SQL_BC_LOCATION_EG = "ile.[Location Code] IN ('E', 'G')"
SQL_BC_ILE_OUTPUT = "ile.[Entry Type] = 4"
SQL_INNOVA_LOT = "CAST(p.number AS varchar(50))"
BC_BALANCE_CHECK_TOLERANCE_KG = 5000.0
BC_BALANCE_SKIP_APERTURA = True
# Limite historico ILE para evitar timeout en BC (solo movimientos desde esta fecha).
BC_ILE_HISTORY_FROM = dt.date(2026, 1, 1)
# Solo lotes con empaque o movimiento ILE en el mes del periodo (check mensual).
BC_BALANCE_SCOPE_MONTH = True


def bc_ile_effective_start(start: dt.date) -> dt.date:
    return start if start >= BC_ILE_HISTORY_FROM else BC_ILE_HISTORY_FROM


def sql_bc_ile_posting_from(alias: str = "ile") -> str:
    return f"{alias}.[Posting Date] >= '{BC_ILE_HISTORY_FROM.isoformat()}'"


def sql_bc_ile_empaque_from(alias: str = "ile") -> str:
    return f"{alias}.[Fecha empaque] >= '{BC_ILE_HISTORY_FROM.isoformat()}'"
PREMISA_BC_BALANCE_EG_REGLAS = (
  "Almacenes BC: Location Code E y G unicamente.",
  "Fecha empaque: campo [Fecha empaque] en Item Ledger Entry.",
  "Stock inicial (dia): ILE — [Fecha empaque] anterior al dia y venta o ajuste negativo (Entry Type 1/3) en ese dia o posteriores.",
  "Stock final teorico (dia): Stock inicial + Salidas Innova - Ventas del dia.",
  "Stock final real (dia): empaque acumulado del periodo sin venta hasta ese dia (kg BC por lote).",
  "Encadenamiento: stock final del dia N = stock inicial del dia N+1 (misma regla ILE).",
  "Salidas Innova: salidas CAJA del periodo (proc_packs prday, premisa 3).",
  "Ventas BC: ventas del periodo en almacenes E/G (Posting Date, Entry Type=1).",
  "Stock apertura: empaque anterior al periodo sin venta antes del periodo (E/G).",
  "Check (dia): Stock final teorico - Stock final real.",
  "Check mes: Stock final teorico fin de mes - Stock final real fin de mes.",
  "Alcance check mensual: solo lotes con empaque o movimiento ILE en el mes del periodo.",
  "Historico ILE acotado desde 2026-01-01 para evitar timeout en BC.",
  "Fines de semana: sin Salidas Innova ni Ventas BC (0 kg); stocks se arrastran del dia anterior.",
  "Desglose por tipo de producto BC (Cod. producto via Conversion productos: Cod. bascula = material Innova) y lote.",
  "Balance por tipo en cajas: Entradas = ajustes positivos BC (Entry Type 2); Ventas = ventas BC (Entry Type 1); agregadas por Cod. producto.",
  "Encadenamiento en cajas: stock final teorico del dia N = stock inicial del dia N+1.",
  "Stock inicial BC E/G por producto: corte a fecha inicio; empaque anterior; venta ese dia o despues; cajas y kg.",
  "Stock final BC E/G por producto: empaque del periodo sin venta hasta fecha fin (pendiente mes siguiente); cajas y kg.",
)

# Limitacion conocida — debe mostrarse en todos los resultados (ver PREMISAS.md).
NOTA_ALERTA_VAP = (
  "Nota: El producto VAP entra por tinas pero no se procesa; se acumula en stock de tinas "
  "de forma ficticia y distorsiona entradas, stock y merma. "
  "Limitacion conocida — sin correccion disponible de momento."
)
NOTA_ALERTA_VAP_TITULO = "Alerta — limitacion VAP en stock de tinas"


def build_nota_alerta_vap_html() -> str:
    return (
        "<footer class='nota-alerta-vap' role='note'>"
        f"<p class='nota-alerta-vap-titulo'>{html.escape(NOTA_ALERTA_VAP_TITULO)}</p>"
        f"<p class='nota-alerta-vap-texto'>{html.escape(NOTA_ALERTA_VAP)}</p>"
        "</footer>"
    )


def build_report_intro_html(
    start: dt.date,
    end: dt.date,
    k: dict[str, Any],
    source_definition: str,
    bc_loaded: bool,
) -> str:
    bc_status = (
        f"Cruce BC activo: {int(k.get('bc_lotes_enlazados', 0)):,} de "
        f"{int(k.get('bc_lotes_innova', 0)):,} lotes enlazados "
        f"({fmt_pct(k.get('bc_pct_lotes_enlazados'))})."
        if bc_loaded
        else "Cruce BC no disponible en esta ejecución (--skip-bc o error de conexión)."
    )
    return (
        "<article class='intro-card'>"
        "<h2>Guía del informe</h2>"
        "<p class='intro-lead'>"
        "Informe de seguimiento de biomasa en planta. Consolida datos de "
        "<strong>Innova (SQL Server)</strong> y, cuando está disponible, "
        "<strong>Business Central</strong>. Las reglas de negocio canónicas están en "
        "<strong>PREMISAS.md</strong> del repositorio."
        "</p>"
        "<div class='intro-grid'>"
        "<section>"
        "<h3>Terminología</h3>"
        "<ul class='intro-list'>"
        "<li><strong>TINA</strong> — entrada de biomasa (<code>pkpackaging = 3</code>).</li>"
        "<li><strong>CAJA</strong> — salida de producto (<code>pkpackaging &lt;&gt; 3</code>).</li>"
        "<li><strong>Tinas procesadas</strong> — consumo en <code>proc_matxacts</code>; "
        "no confundir con salida CAJA.</li>"
        "<li><strong>Fecha operativa</strong> — campo <code>prday</code> a medianoche.</li>"
        "</ul>"
        "</section>"
        "<section>"
        "<h3>Capítulos del informe</h3>"
        "<ol class='intro-list'>"
        "<li><strong>Resumen</strong> — KPIs del periodo.</li>"
        "<li><strong>Gráficas</strong> — evolución diaria e indicadores visuales.</li>"
        "<li><strong>Detalle diario</strong> — tabla día a día con exportación Excel.</li>"
        "<li><strong>Balance</strong> — stock de tinas, merma y arrastre mensual.</li>"
        "<li><strong>Cruce BC</strong> — enlace Innova / Business Central por lote.</li>"
        "<li><strong>Balance BC E/G</strong> — stock inicial, salidas Innova, ventas y check teorico (almacenes E y G).</li>"
        "<li><strong>Materiales</strong> — top entradas y salidas.</li>"
        "</ol>"
        "<p class='intro-debug-note muted'>"
        "La pestaña <strong>Debug</strong> (auditoría técnica) incluye premisas SQL y consultas "
        "ejecutadas; no forma parte del informe de negocio."
        "</p>"
        "</section>"
        "</div>"
        "<section class='intro-snapshot'>"
        "<h3>Instantánea del periodo</h3>"
        f"<p class='muted'>{html.escape(source_definition)}</p>"
        f"<p>{html.escape(bc_status)}</p>"
        "<div class='intro-kpis'>"
        f"<div><span class='intro-kpi-label'>Entradas TINA</span>"
        f"<strong>{fmt_num(k['kg_entrada_tina'])} kg</strong></div>"
        f"<div><span class='intro-kpi-label'>Salidas CAJA</span>"
        f"<strong>{fmt_num(k['kg_salida_no_tina'])} kg</strong></div>"
        f"<div><span class='intro-kpi-label'>Stock de tinas</span>"
        f"<strong>{fmt_num(k.get('kg_stock_entrada', 0))} kg</strong></div>"
        f"<div><span class='intro-kpi-label'>Merma</span>"
        f"<strong>{fmt_num(k.get('kg_merma', 0))} kg</strong> "
        f"({fmt_pct(k.get('pct_merma'))})</div>"
        "</div>"
        "</section>"
        "<p class='intro-hint muted'>"
        "Use las pestañas superiores para navegar. El botón "
        "<strong>Exportar todo en Excel</strong> genera un libro con las tablas principales del reporte."
        "</p>"
        "</article>"
    )


def build_premisa_entrada_html() -> str:
    items = "".join(f"<li>{html.escape(rule)}</li>" for rule in PREMISA_ENTRADA_REGLAS)
    procesada_items = "".join(f"<li>{html.escape(rule)}</li>" for rule in PREMISA_PROCESADA)
    salida_items = "".join(f"<li>{html.escape(rule)}</li>" for rule in PREMISA_SALIDA_REGLAS)
    stock_merma_items = "".join(
        f"<li>{html.escape(rule)}</li>" for rule in PREMISA_STOCK_MERMA_REGLAS
    )
    bc_items = "".join(f"<li>{html.escape(rule)}</li>" for rule in PREMISA_BC_PEDIDO_REGLAS)
    return (
        "<section class='premisa-box'>"
        "<h3 class='premisa-head'>Premisa 1 — Entradas TINA</h3>"
        f"<ul class='premisa-list'>{items}</ul>"
        "<h3 class='premisa-head'>Premisa 2 — Tinas procesadas</h3>"
        f"<ul class='premisa-list'>{procesada_items}</ul>"
        "<h3 class='premisa-head'>Premisa 3 — Salidas CAJA</h3>"
        f"<ul class='premisa-list'>{salida_items}</ul>"
        "<h3 class='premisa-head'>Premisas 4 y 5 — Stock de tinas y merma</h3>"
        f"<ul class='premisa-list'>{stock_merma_items}</ul>"
        "<h3 class='premisa-head'>Premisa 6 — Cruce BC (pedidos)</h3>"
        f"<ul class='premisa-list'>{bc_items}</ul>"
        "<p class='premisa-note muted'>Documento canon: PREMISAS.md</p>"
        f"<p class='premisa-note nota-alerta-vap-inline'><strong>{html.escape(NOTA_ALERTA_VAP_TITULO)}:</strong> "
        f"{html.escape(NOTA_ALERTA_VAP)}</p>"
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
      CAST(p.prday AS date) AS fecha,
      {SQL_INNOVA_LOT} AS lot,
      SUM(CAST(p.weight AS float)) AS kg,
      COUNT(*) AS packs
    FROM dbo.proc_packs p
    JOIN dbo.proc_materials m ON m.material = p.material
    WHERE p.prday >= %s
      AND p.prday < DATEADD(day, 1, %s)
      AND {SQL_SALIDA_CAJA}
      AND NULLIF(LTRIM(RTRIM({SQL_INNOVA_LOT})), '') IS NOT NULL
    GROUP BY CAST(p.prday AS date), {SQL_INNOVA_LOT}
    ORDER BY fecha, lot;
    """
    return fetch_rows(cursor, query, params)


def fetch_innova_lotes_por_material(
    conn: pymssql.Connection,
    start: dt.date,
    end: dt.date,
) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    params = (start.isoformat(), end.isoformat())
    query = f"""
    SELECT
      {SQL_INNOVA_LOT} AS lot,
      m.material,
      m.name AS material_nombre,
      MAX(NULLIF(LTRIM(RTRIM(CAST(m.pattern AS varchar(50)))), '')) AS pattern,
      MIN(CAST(p.prday AS date)) AS prday_min,
      MAX(CAST(p.prday AS date)) AS prday_max,
      SUM(CAST(p.weight AS float)) AS kg_innova,
      COUNT(*) AS packs
    FROM dbo.proc_packs p
    JOIN dbo.proc_materials m ON m.material = p.material
    WHERE p.prday >= %s
      AND p.prday < DATEADD(day, 1, %s)
      AND {SQL_SALIDA_CAJA}
      AND NULLIF(LTRIM(RTRIM({SQL_INNOVA_LOT})), '') IS NOT NULL
    GROUP BY {SQL_INNOVA_LOT}, m.material, m.name
    ORDER BY lot;
    """
    rows = fetch_rows(cursor, query, params)
    return [
        {
            "lot": str(row["lot"]).strip(),
            "material": str(row.get("material") or "").strip(),
            "material_nombre": str(row.get("material_nombre") or "").strip(),
            "pattern": str(row.get("pattern") or "").strip(),
            "prday_min": row.get("prday_min"),
            "prday_max": row.get("prday_max"),
            "kg_innova": to_float(row.get("kg_innova")),
            "packs": int(row.get("packs") or 0),
        }
        for row in rows
    ]


def fetch_bc_conversion_productos(conn: pymssql.Connection) -> dict[str, Any]:
    """Mapa Innova (Cod. bascula) -> producto BC (Cod. producto) desde Conversion productos."""
    cursor = conn.cursor()
    query = """
    SELECT
      LTRIM(RTRIM(CAST(cp.[Cod. bascula] AS varchar(50)))) AS cod_bascula,
      LTRIM(RTRIM(CAST(cp.[Cod. producto] AS varchar(50)))) AS cod_producto
    FROM bc.[Conversion productos] cp
    WHERE NULLIF(LTRIM(RTRIM(CAST(cp.[Cod. bascula] AS varchar(50)))), '') IS NOT NULL
      AND NULLIF(LTRIM(RTRIM(CAST(cp.[Cod. producto] AS varchar(50)))), '') IS NOT NULL
    GROUP BY
      LTRIM(RTRIM(CAST(cp.[Cod. bascula] AS varchar(50)))),
      LTRIM(RTRIM(CAST(cp.[Cod. producto] AS varchar(50))))
    ORDER BY cod_bascula;
    """
    rows = fetch_rows(cursor, query, ())
    by_bascula: dict[str, dict[str, str]] = {}
    productos: set[str] = set()
    for row in rows:
        bascula = str(row.get("cod_bascula") or "").strip()
        producto = str(row.get("cod_producto") or "").strip()
        if not bascula or not producto:
            continue
        meta = {
            "cod_bascula": bascula,
            "cod_producto": producto,
            "item_description": "",
        }
        by_bascula[bascula] = meta
        if bascula.isdigit():
            by_bascula[str(int(bascula))] = meta
        productos.add(producto)
    return {
        "by_bascula": by_bascula,
        "productos": sorted(productos),
        "rows": rows,
        "sql_trace": {
            "view_or_tables": ["bc.[Conversion productos]"],
            "queries": [{"name": "bc_conversion_productos", "query": query.strip()}],
        },
    }


def resolve_cod_producto_bc(
    material: str,
    pattern: str,
    item_no: str,
    conversion_by_bascula: dict[str, dict[str, str]] | None,
) -> tuple[str, str, str]:
    """Devuelve (cod_producto, origen_enlace, descripcion_conversion).

    Prioridad:
      1) bc.Conversion productos: Cod. bascula = material Innova → Cod. producto
      2) Innova pattern si existe como Cod. producto en Conversion
      3) Item No. BC del lote
      4) pattern Innova
      5) material Innova
    """
    material = (material or "").strip()
    pattern = (pattern or "").strip()
    item_no = (item_no or "").strip()
    conv_map = conversion_by_bascula or {}
    productos = {
        str(meta.get("cod_producto") or "").strip()
        for meta in conv_map.values()
        if meta.get("cod_producto")
    }

    if material:
        meta = conv_map.get(material)
        if meta is None and material.isdigit():
            meta = conv_map.get(str(int(material)))
        if meta and meta.get("cod_producto"):
            return (
                str(meta["cod_producto"]).strip(),
                "conversion_bascula",
                str(meta.get("item_description") or "").strip(),
            )

    if pattern and pattern in productos:
        return pattern, "pattern_en_conversion", ""

    if item_no:
        return item_no, "item_no_bc", ""

    if pattern:
        return pattern, "pattern_innova", ""

    if material:
        return material, "material_innova", ""

    return "(sin tipo)", "sin_enlace", ""


def fetch_bc_salidas_pedido(
    conn: pymssql.Connection,
    start: dt.date,
    end: dt.date,
) -> dict[str, Any]:
    cursor = conn.cursor()
    ile_start = bc_ile_effective_start(start)
    params = (ile_start.isoformat(), end.isoformat(), ile_start.isoformat(), end.isoformat())
    q_lotes = f"""
    WITH doc_order AS (
      SELECT
        ssl.[Document No.] AS document_no,
        MAX(NULLIF(LTRIM(RTRIM(ssl.[Order No.])), '')) AS [Order No.]
      FROM bc.[Sales Shipment Line] ssl
      WHERE ssl.[Posting Date] >= %s
        AND ssl.[Posting Date] < DATEADD(day, 1, %s)
        AND {sql_bc_ile_posting_from('ssl')}
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
      AND {sql_bc_ile_posting_from()}
      AND {SQL_BC_ILE_SALE}
      AND {SQL_BC_LOCATION_EG}
      AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
    GROUP BY CAST(ile.[Lot No.] AS varchar(50))
    ORDER BY lot;
    """
    by_lot = fetch_rows(cursor, q_lotes, params)
    q_lotes_pedido = f"""
    WITH doc_order AS (
      SELECT
        ssl.[Document No.] AS document_no,
        MAX(NULLIF(LTRIM(RTRIM(ssl.[Order No.])), '')) AS [Order No.]
      FROM bc.[Sales Shipment Line] ssl
      WHERE ssl.[Posting Date] >= %s
        AND ssl.[Posting Date] < DATEADD(day, 1, %s)
        AND {sql_bc_ile_posting_from('ssl')}
      GROUP BY ssl.[Document No.]
    )
    SELECT
      CAST(ile.[Lot No.] AS varchar(50)) AS lot,
      NULLIF(LTRIM(RTRIM(sl.[Order No.])), '') AS order_no,
      SUM(ABS(CAST(ile.[Quantity] AS float))) AS qty,
      SUM(ABS(CAST(ile.[Kilos] AS float))) AS kg,
      MIN(CAST(ile.[Posting Date] AS date)) AS posting_date_min,
      MAX(CAST(ile.[Posting Date] AS date)) AS posting_date_max,
      COUNT(*) AS lineas_ile
    FROM bc.[Item Ledger Entry] ile
    LEFT JOIN doc_order sl ON sl.document_no = ile.[Document No.]
    WHERE ile.[Posting Date] >= %s
      AND ile.[Posting Date] < DATEADD(day, 1, %s)
      AND {sql_bc_ile_posting_from()}
      AND {SQL_BC_ILE_SALE}
      AND {SQL_BC_LOCATION_EG}
      AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
    GROUP BY
      CAST(ile.[Lot No.] AS varchar(50)),
      NULLIF(LTRIM(RTRIM(sl.[Order No.])), '')
    ORDER BY lot, order_no;
    """
    by_lot_order = fetch_rows(cursor, q_lotes_pedido, params)
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
        "by_lot_order": by_lot_order,
        "totals": totals,
        "sql_trace": {
            "view_or_tables": [
                "bc.[Item Ledger Entry]",
                "bc.[Sales Shipment Line]",
            ],
            "params": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "ile_history_from": BC_ILE_HISTORY_FROM.isoformat(),
            },
            "queries": [
                {"name": "bc_lotes_salida_ile", "query": q_lotes.strip()},
                {"name": "bc_lotes_pedido_ile", "query": q_lotes_pedido.strip()},
            ],
        },
    }


def previous_month_range(period_start: dt.date) -> tuple[dt.date, dt.date]:
    first_current = period_start.replace(day=1)
    last_previous = first_current - dt.timedelta(days=1)
    first_previous = last_previous.replace(day=1)
    return first_previous, last_previous


def fetch_bc_balance_eg(
    conn: pymssql.Connection,
    start: dt.date,
    end: dt.date,
    verbose: bool = True,
) -> dict[str, Any]:
    cursor = conn.cursor()
    ile_start = bc_ile_effective_start(start)
    params_period = (ile_start.isoformat(), end.isoformat())
    params_unsold = (
        ile_start.isoformat(),
        end.isoformat(),
        ile_start.isoformat(),
        end.isoformat(),
    )
    params_lot_snapshot = (
        ile_start.isoformat(),
        end.isoformat(),
        ile_start.isoformat(),
        end.isoformat(),
    )

    def run_step(name: str, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        if verbose:
            print(f"  BC balance E/G: {name}...")
        return fetch_rows(cursor, query, params)

    q_ventas_diario = f"""
    SELECT
      CAST(ile.[Posting Date] AS date) AS fecha,
      SUM(ABS(CAST(ile.[Kilos] AS float))) AS kg,
      COUNT(DISTINCT ile.[Lot No.]) AS lotes
    FROM bc.[Item Ledger Entry] ile
    WHERE ile.[Posting Date] >= %s
      AND ile.[Posting Date] < DATEADD(day, 1, %s)
      AND {sql_bc_ile_posting_from()}
      AND {SQL_BC_ILE_SALE}
      AND {SQL_BC_LOCATION_EG}
    GROUP BY CAST(ile.[Posting Date] AS date)
    ORDER BY fecha;
    """
    ventas_diario = run_step("ventas diario", q_ventas_diario, params_period)

    q_lots_stock_antiguo_mes = f"""
    SELECT
      CAST(ile.[Lot No.] AS varchar(50)) AS lot,
      MIN(CAST(ile.[Fecha empaque] AS date)) AS fe_empaque,
      MAX(ABS(CAST(ile.[Kilos] AS float))) AS kg,
      MIN(CAST(ile.[Posting Date] AS date)) AS first_out,
      MAX(NULLIF(LTRIM(RTRIM(CAST(ile.[Item No.] AS varchar(50)))), '')) AS item_no,
      MAX(NULLIF(LTRIM(RTRIM(CAST(ile.[Description] AS varchar(250)))), '')) AS item_description
    FROM bc.[Item Ledger Entry] ile
    WHERE ile.[Posting Date] >= %s
      AND ile.[Posting Date] < DATEADD(day, 1, %s)
      AND {sql_bc_ile_posting_from()}
      AND {SQL_BC_LOCATION_EG}
      AND {SQL_BC_ILE_SALE_OR_NEG_ADJ}
      AND ile.[Fecha empaque] IS NOT NULL
      AND {sql_bc_ile_empaque_from()}
      AND CAST(ile.[Fecha empaque] AS date) < CAST(ile.[Posting Date] AS date)
      AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
    GROUP BY CAST(ile.[Lot No.] AS varchar(50))
    ORDER BY lot;
    """
    lots_stock_antiguo_mes_raw = run_step(
        "lotes stock antiguo activos en mes",
        q_lots_stock_antiguo_mes,
        params_period,
    )
    lots_stock_antiguo_mes = [
        {
            "lot": str(row["lot"]).strip(),
            "fe_empaque": row.get("fe_empaque"),
            "kg": to_float(row.get("kg")),
            "first_out": row.get("first_out"),
            "item_no": str(row.get("item_no") or "").strip(),
            "item_description": str(row.get("item_description") or "").strip(),
        }
        for row in lots_stock_antiguo_mes_raw
    ]

    q_ventas_stock_antiguo_diario = f"""
    SELECT
      CAST(ile.[Posting Date] AS date) AS fecha,
      SUM(ABS(CAST(ile.[Kilos] AS float))) AS kg,
      COUNT(DISTINCT ile.[Lot No.]) AS lotes
    FROM bc.[Item Ledger Entry] ile
    WHERE ile.[Posting Date] >= %s
      AND ile.[Posting Date] < DATEADD(day, 1, %s)
      AND {sql_bc_ile_posting_from()}
      AND {SQL_BC_ILE_SALE}
      AND {SQL_BC_LOCATION_EG}
      AND ile.[Fecha empaque] IS NOT NULL
      AND {sql_bc_ile_empaque_from()}
      AND CAST(ile.[Fecha empaque] AS date) < CAST(ile.[Posting Date] AS date)
    GROUP BY CAST(ile.[Posting Date] AS date)
    ORDER BY fecha;
    """
    ventas_stock_antiguo_diario = run_step(
        "ventas stock antiguo diario", q_ventas_stock_antiguo_diario, params_period
    )

    q_empaque_diario = f"""
    WITH lot_empaque AS (
      SELECT
        CAST(ile.[Lot No.] AS varchar(50)) AS lot,
        MIN(CAST(ile.[Fecha empaque] AS date)) AS fecha,
        MAX(ABS(CAST(ile.[Kilos] AS float))) AS kg
      FROM bc.[Item Ledger Entry] ile
      WHERE ile.[Fecha empaque] >= %s
        AND ile.[Fecha empaque] < DATEADD(day, 1, %s)
        AND {sql_bc_ile_empaque_from()}
        AND {SQL_BC_LOCATION_EG}
        AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
      GROUP BY CAST(ile.[Lot No.] AS varchar(50))
    )
    SELECT
      fecha,
      SUM(kg) AS kg,
      COUNT(*) AS lotes
    FROM lot_empaque
    GROUP BY fecha
    ORDER BY fecha;
    """
    empaque_diario = run_step("empaque diario", q_empaque_diario, params_period)

    q_ventas_stock_antiguo_total = f"""
    SELECT
      SUM(ABS(CAST(ile.[Kilos] AS float))) AS kg,
      COUNT(DISTINCT ile.[Lot No.]) AS lotes
    FROM bc.[Item Ledger Entry] ile
    WHERE ile.[Posting Date] >= %s
      AND ile.[Posting Date] < DATEADD(day, 1, %s)
      AND {sql_bc_ile_posting_from()}
      AND {SQL_BC_ILE_SALE}
      AND {SQL_BC_LOCATION_EG}
      AND ile.[Fecha empaque] IS NOT NULL
      AND {sql_bc_ile_empaque_from()}
      AND CAST(ile.[Fecha empaque] AS date) < CAST(ile.[Posting Date] AS date);
    """
    ventas_stock_antiguo_total = run_step(
        "ventas stock antiguo total mes", q_ventas_stock_antiguo_total, params_period
    )[0]

    q_ventas_por_lote = f"""
    SELECT
      CAST(ile.[Lot No.] AS varchar(50)) AS lot,
      SUM(ABS(CAST(ile.[Kilos] AS float))) AS kg,
      SUM(ABS(CAST(ile.[Quantity] AS float))) AS qty,
      MAX(NULLIF(LTRIM(RTRIM(CAST(ile.[Item No.] AS varchar(50)))), '')) AS item_no,
      MAX(NULLIF(LTRIM(RTRIM(CAST(ile.[Description] AS varchar(250)))), '')) AS item_description,
      MIN(CAST(ile.[Fecha empaque] AS date)) AS fe_empaque,
      MIN(CAST(ile.[Posting Date] AS date)) AS first_sale,
      MAX(CAST(ile.[Posting Date] AS date)) AS last_sale
    FROM bc.[Item Ledger Entry] ile
    WHERE ile.[Posting Date] >= %s
      AND ile.[Posting Date] < DATEADD(day, 1, %s)
      AND {sql_bc_ile_posting_from()}
      AND {SQL_BC_ILE_SALE}
      AND {SQL_BC_LOCATION_EG}
      AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
    GROUP BY CAST(ile.[Lot No.] AS varchar(50))
    ORDER BY lot;
    """
    ventas_por_lote_raw = run_step("ventas por lote", q_ventas_por_lote, params_period)
    ventas_por_lote = [
        {
            "lot": str(row["lot"]).strip(),
            "kg": to_float(row.get("kg")),
            "qty": to_float(row.get("qty")),
            "item_no": str(row.get("item_no") or "").strip(),
            "item_description": str(row.get("item_description") or "").strip(),
            "fe_empaque": row.get("fe_empaque"),
            "first_sale": row.get("first_sale"),
            "last_sale": row.get("last_sale"),
        }
        for row in ventas_por_lote_raw
    ]

    q_entradas_pos_adj_por_lote = f"""
    SELECT
      CAST(ile.[Lot No.] AS varchar(50)) AS lot,
      SUM(ABS(CAST(ile.[Kilos] AS float))) AS kg,
      SUM(ABS(CAST(ile.[Quantity] AS float))) AS qty,
      MAX(NULLIF(LTRIM(RTRIM(CAST(ile.[Item No.] AS varchar(50)))), '')) AS item_no,
      MAX(NULLIF(LTRIM(RTRIM(CAST(ile.[Description] AS varchar(250)))), '')) AS item_description,
      MIN(CAST(ile.[Fecha empaque] AS date)) AS fe_empaque,
      MIN(CAST(ile.[Posting Date] AS date)) AS first_in,
      MAX(CAST(ile.[Posting Date] AS date)) AS last_in
    FROM bc.[Item Ledger Entry] ile
    WHERE ile.[Posting Date] >= %s
      AND ile.[Posting Date] < DATEADD(day, 1, %s)
      AND {sql_bc_ile_posting_from()}
      AND {SQL_BC_ILE_POS_ADJ}
      AND {SQL_BC_LOCATION_EG}
      AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
    GROUP BY CAST(ile.[Lot No.] AS varchar(50))
    ORDER BY lot;
    """
    entradas_pos_adj_raw = run_step(
        "entradas ajustes positivos por lote",
        q_entradas_pos_adj_por_lote,
        params_period,
    )
    entradas_pos_adj_por_lote = [
        {
            "lot": str(row["lot"]).strip(),
            "kg": to_float(row.get("kg")),
            "qty": to_float(row.get("qty")),
            "item_no": str(row.get("item_no") or "").strip(),
            "item_description": str(row.get("item_description") or "").strip(),
            "fe_empaque": row.get("fe_empaque"),
            "first_in": row.get("first_in"),
            "last_in": row.get("last_in"),
        }
        for row in entradas_pos_adj_raw
    ]

    q_entradas_pos_adj_diario = f"""
    SELECT
      CAST(ile.[Posting Date] AS date) AS fecha,
      LTRIM(RTRIM(CAST(ile.[Item No.] AS varchar(50)))) AS item_no,
      SUM(ABS(CAST(ile.[Quantity] AS float))) AS qty,
      SUM(ABS(CAST(ile.[Kilos] AS float))) AS kg,
      COUNT(DISTINCT ile.[Lot No.]) AS lotes
    FROM bc.[Item Ledger Entry] ile
    WHERE ile.[Posting Date] >= %s
      AND ile.[Posting Date] < DATEADD(day, 1, %s)
      AND {sql_bc_ile_posting_from()}
      AND {SQL_BC_ILE_POS_ADJ}
      AND {SQL_BC_LOCATION_EG}
      AND NULLIF(LTRIM(RTRIM(CAST(ile.[Item No.] AS varchar(50)))), '') IS NOT NULL
    GROUP BY CAST(ile.[Posting Date] AS date), LTRIM(RTRIM(CAST(ile.[Item No.] AS varchar(50))))
    ORDER BY fecha, item_no;
    """
    entradas_pos_adj_diario = run_step(
        "entradas ajustes positivos diario por producto",
        q_entradas_pos_adj_diario,
        params_period,
    )

    q_ventas_diario_producto = f"""
    SELECT
      CAST(ile.[Posting Date] AS date) AS fecha,
      LTRIM(RTRIM(CAST(ile.[Item No.] AS varchar(50)))) AS item_no,
      SUM(ABS(CAST(ile.[Quantity] AS float))) AS qty,
      SUM(ABS(CAST(ile.[Kilos] AS float))) AS kg,
      COUNT(DISTINCT ile.[Lot No.]) AS lotes
    FROM bc.[Item Ledger Entry] ile
    WHERE ile.[Posting Date] >= %s
      AND ile.[Posting Date] < DATEADD(day, 1, %s)
      AND {sql_bc_ile_posting_from()}
      AND {SQL_BC_ILE_SALE}
      AND {SQL_BC_LOCATION_EG}
      AND NULLIF(LTRIM(RTRIM(CAST(ile.[Item No.] AS varchar(50)))), '') IS NOT NULL
    GROUP BY CAST(ile.[Posting Date] AS date), LTRIM(RTRIM(CAST(ile.[Item No.] AS varchar(50))))
    ORDER BY fecha, item_no;
    """
    ventas_diario_producto = run_step(
        "ventas diario por producto",
        q_ventas_diario_producto,
        params_period,
    )

    q_ventas_total = f"""
    SELECT
      SUM(ABS(CAST(ile.[Kilos] AS float))) AS kg,
      COUNT(DISTINCT ile.[Lot No.]) AS lotes
    FROM bc.[Item Ledger Entry] ile
    WHERE ile.[Posting Date] >= %s
      AND ile.[Posting Date] < DATEADD(day, 1, %s)
      AND {sql_bc_ile_posting_from()}
      AND {SQL_BC_ILE_SALE}
      AND {SQL_BC_LOCATION_EG};
    """
    ventas_total = run_step("ventas total", q_ventas_total, params_period)[0]

    q_unsold_lots = f"""
    WITH mar_venta AS (
      SELECT DISTINCT CAST(ile.[Lot No.] AS varchar(50)) AS lot
      FROM bc.[Item Ledger Entry] ile
      WHERE ile.[Posting Date] >= %s
        AND ile.[Posting Date] < DATEADD(day, 1, %s)
        AND {sql_bc_ile_posting_from()}
        AND {SQL_BC_ILE_SALE}
        AND {SQL_BC_LOCATION_EG}
        AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
    )
    SELECT DISTINCT CAST(ile.[Lot No.] AS varchar(50)) AS lot
    FROM bc.[Item Ledger Entry] ile
    WHERE ile.[Fecha empaque] >= %s
      AND ile.[Fecha empaque] < DATEADD(day, 1, %s)
      AND {sql_bc_ile_empaque_from()}
      AND {SQL_BC_LOCATION_EG}
      AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
      AND CAST(ile.[Lot No.] AS varchar(50)) NOT IN (SELECT lot FROM mar_venta)
    ORDER BY lot;
    """
    unsold_rows = run_step("lotes sin venta", q_unsold_lots, params_unsold)
    unsold_lots = [str(row["lot"]).strip() for row in unsold_rows]

    q_stock_final_total = f"""
    WITH mar_venta AS (
      SELECT DISTINCT CAST(ile.[Lot No.] AS varchar(50)) AS lot
      FROM bc.[Item Ledger Entry] ile
      WHERE ile.[Posting Date] >= %s
        AND ile.[Posting Date] < DATEADD(day, 1, %s)
        AND {sql_bc_ile_posting_from()}
        AND {SQL_BC_ILE_SALE}
        AND {SQL_BC_LOCATION_EG}
        AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
    ),
    stock_final_lot AS (
      SELECT
        CAST(ile.[Lot No.] AS varchar(50)) AS lot,
        MAX(ABS(CAST(ile.[Kilos] AS float))) AS kg
      FROM bc.[Item Ledger Entry] ile
      WHERE ile.[Fecha empaque] >= %s
        AND ile.[Fecha empaque] < DATEADD(day, 1, %s)
        AND {sql_bc_ile_empaque_from()}
        AND {SQL_BC_LOCATION_EG}
        AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
        AND CAST(ile.[Lot No.] AS varchar(50)) NOT IN (SELECT lot FROM mar_venta)
      GROUP BY CAST(ile.[Lot No.] AS varchar(50))
    )
    SELECT
      COALESCE(SUM(kg), 0) AS kg,
      COUNT(*) AS lotes
    FROM stock_final_lot;
    """
    stock_final_total = run_step("stock final total", q_stock_final_total, params_unsold)[0]

    q_lot_snapshot = f"""
    WITH lot_empaque AS (
      SELECT
        CAST(ile.[Lot No.] AS varchar(50)) AS lot,
        MIN(CAST(ile.[Fecha empaque] AS date)) AS fe_empaque,
        MAX(ABS(CAST(ile.[Kilos] AS float))) AS kg,
        MAX(NULLIF(LTRIM(RTRIM(CAST(ile.[Item No.] AS varchar(50)))), '')) AS item_no,
        MAX(NULLIF(LTRIM(RTRIM(CAST(ile.[Description] AS varchar(250)))), '')) AS item_description
      FROM bc.[Item Ledger Entry] ile
      WHERE ile.[Fecha empaque] >= %s
        AND ile.[Fecha empaque] < DATEADD(day, 1, %s)
        AND {sql_bc_ile_empaque_from()}
        AND {SQL_BC_LOCATION_EG}
        AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
      GROUP BY CAST(ile.[Lot No.] AS varchar(50))
    ),
    lot_first_sale AS (
      SELECT
        CAST(ile.[Lot No.] AS varchar(50)) AS lot,
        MIN(CAST(ile.[Posting Date] AS date)) AS first_sale
      FROM bc.[Item Ledger Entry] ile
      WHERE ile.[Posting Date] >= %s
        AND ile.[Posting Date] < DATEADD(day, 1, %s)
        AND {sql_bc_ile_posting_from()}
        AND {SQL_BC_ILE_SALE}
        AND {SQL_BC_LOCATION_EG}
        AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
      GROUP BY CAST(ile.[Lot No.] AS varchar(50))
    )
    SELECT
      e.lot,
      e.fe_empaque,
      e.kg,
      e.item_no,
      e.item_description,
      s.first_sale
    FROM lot_empaque e
    LEFT JOIN lot_first_sale s ON s.lot = e.lot
    ORDER BY e.lot;
    """
    lot_snapshot_raw = run_step("snapshot lotes", q_lot_snapshot, params_lot_snapshot)
    lot_snapshot = [
        {
            "lot": str(row["lot"]).strip(),
            "fe_empaque": row["fe_empaque"],
            "kg": to_float(row["kg"]),
            "item_no": str(row.get("item_no") or "").strip(),
            "item_description": str(row.get("item_description") or "").strip(),
            "first_sale": row.get("first_sale"),
        }
        for row in lot_snapshot_raw
    ]

    stock_inicial_ile_diario = build_stock_inicial_ile_diario(
        start,
        end,
        merge_lots_for_stock_inicial_ile(lot_snapshot, lots_stock_antiguo_mes),
    )
    kg_stock_inicial_apertura = 0.0
    lotes_stock_inicial_apertura = 0
    if stock_inicial_ile_diario:
        kg_stock_inicial_apertura = to_float(stock_inicial_ile_diario[0].get("kg"))
        lotes_stock_inicial_apertura = int(stock_inicial_ile_diario[0].get("lotes") or 0)

    q_empaque_mes = f"""
    SELECT COUNT(DISTINCT ile.[Lot No.]) AS lotes
    FROM bc.[Item Ledger Entry] ile
    WHERE ile.[Fecha empaque] >= %s
      AND ile.[Fecha empaque] < DATEADD(day, 1, %s)
      AND {sql_bc_ile_empaque_from()}
      AND {SQL_BC_LOCATION_EG}
      AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL;
    """
    empaque_mes = run_step("empaque mes", q_empaque_mes, params_period)[0]

    stock_apertura = {"kg": 0.0, "lotes": 0}
    lotes_apertura: list[dict[str, Any]] = []
    q_stock_apertura = ""
    q_lotes_apertura = ""
    if not BC_BALANCE_SKIP_APERTURA:
        q_stock_apertura = f"""
    WITH lot_pre AS (
      SELECT
        CAST(ile.[Lot No.] AS varchar(50)) AS lot,
        MAX(ABS(CAST(ile.[Kilos] AS float))) AS kg
      FROM bc.[Item Ledger Entry] ile
      WHERE {sql_bc_ile_empaque_from()}
        AND ile.[Fecha empaque] < %s
        AND {SQL_BC_LOCATION_EG}
        AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
      GROUP BY CAST(ile.[Lot No.] AS varchar(50))
    ),
    sold_before AS (
      SELECT DISTINCT CAST(ile.[Lot No.] AS varchar(50)) AS lot
      FROM bc.[Item Ledger Entry] ile
      WHERE {sql_bc_ile_posting_from()}
        AND ile.[Posting Date] < %s
        AND {SQL_BC_ILE_SALE}
        AND {SQL_BC_LOCATION_EG}
        AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
    )
    SELECT
      COALESCE(SUM(l.kg), 0) AS kg,
      COUNT(*) AS lotes
    FROM lot_pre l
    WHERE l.lot NOT IN (SELECT lot FROM sold_before);
    """
        try:
            stock_apertura = run_step(
                "stock apertura (opcional)", q_stock_apertura, (start.isoformat(), start.isoformat())
            )[0]
        except Exception as exc:
            print(f"  Aviso: stock apertura omitido (consulta pesada en BC: {exc}).")
            try:
                cursor.close()
            except Exception:
                pass
            cursor = conn.cursor()

        q_lotes_apertura = f"""
    WITH lot_pre AS (
      SELECT
        CAST(ile.[Lot No.] AS varchar(50)) AS lot,
        MIN(CAST(ile.[Fecha empaque] AS date)) AS fe_empaque,
        MAX(ABS(CAST(ile.[Kilos] AS float))) AS kg,
        MAX(NULLIF(LTRIM(RTRIM(CAST(ile.[Item No.] AS varchar(50)))), '')) AS item_no,
        MAX(NULLIF(LTRIM(RTRIM(CAST(ile.[Description] AS varchar(250)))), '')) AS item_description
      FROM bc.[Item Ledger Entry] ile
      WHERE {sql_bc_ile_empaque_from()}
        AND ile.[Fecha empaque] < %s
        AND {SQL_BC_LOCATION_EG}
        AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
      GROUP BY CAST(ile.[Lot No.] AS varchar(50))
    ),
    sold_before AS (
      SELECT DISTINCT CAST(ile.[Lot No.] AS varchar(50)) AS lot
      FROM bc.[Item Ledger Entry] ile
      WHERE {sql_bc_ile_posting_from()}
        AND ile.[Posting Date] < %s
        AND {SQL_BC_ILE_SALE}
        AND {SQL_BC_LOCATION_EG}
        AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
    )
    SELECT
      l.lot,
      l.fe_empaque,
      l.kg,
      l.item_no,
      l.item_description
    FROM lot_pre l
    WHERE l.lot NOT IN (SELECT lot FROM sold_before)
    ORDER BY l.lot;
    """
        try:
            lotes_apertura_raw = run_step(
                "lotes apertura (opcional)", q_lotes_apertura, (start.isoformat(), start.isoformat())
            )
            lotes_apertura = [
                {
                    "lot": str(row["lot"]).strip(),
                    "fe_empaque": row.get("fe_empaque"),
                    "kg": to_float(row.get("kg")),
                    "item_no": str(row.get("item_no") or "").strip(),
                    "item_description": str(row.get("item_description") or "").strip(),
                }
                for row in lotes_apertura_raw
            ]
        except Exception as exc:
            print(
                "  Aviso: detalle de lotes de apertura omitido "
                f"(consulta pesada en BC: {exc})."
            )
    else:
        print("  BC balance E/G: stock apertura omitido (consulta historica pesada en BC).")

    trace_queries = [
        {"name": "bc_balance_ventas_diario_eg", "query": q_ventas_diario.strip()},
        {"name": "bc_balance_lots_stock_antiguo_mes_eg", "query": q_lots_stock_antiguo_mes.strip()},
        {"name": "bc_balance_ventas_stock_antiguo_diario_eg", "query": q_ventas_stock_antiguo_diario.strip()},
        {"name": "bc_balance_empaque_diario_eg", "query": q_empaque_diario.strip()},
        {"name": "bc_balance_ventas_por_lote_eg", "query": q_ventas_por_lote.strip()},
        {"name": "bc_balance_entradas_pos_adj_por_lote_eg", "query": q_entradas_pos_adj_por_lote.strip()},
        {"name": "bc_balance_entradas_pos_adj_diario_producto_eg", "query": q_entradas_pos_adj_diario.strip()},
        {"name": "bc_balance_ventas_diario_producto_eg", "query": q_ventas_diario_producto.strip()},
        {"name": "bc_balance_lot_snapshot_eg", "query": q_lot_snapshot.strip()},
        {"name": "bc_balance_unsold_lots_eg", "query": q_unsold_lots.strip()},
        {"name": "bc_balance_stock_final_eg", "query": q_stock_final_total.strip()},
    ]
    if q_stock_apertura:
        trace_queries.insert(2, {"name": "bc_balance_stock_apertura_eg", "query": q_stock_apertura.strip()})
    if q_lotes_apertura:
        trace_queries.insert(3, {"name": "bc_balance_lotes_apertura_eg", "query": q_lotes_apertura.strip()})

    kg_empaque_mes = sum(to_float(row.get("kg")) for row in empaque_diario)
    lotes_empaque_mes_kg = sum(int(row.get("lotes") or 0) for row in empaque_diario)

    return {
        "ventas_diario": ventas_diario,
        "stock_inicial_ile_diario": stock_inicial_ile_diario,
        "ventas_stock_antiguo_diario": ventas_stock_antiguo_diario,
        "empaque_diario": empaque_diario,
        "kg_empaque_mes": kg_empaque_mes,
        "lotes_empaque_mes_kg": lotes_empaque_mes_kg,
        "lot_snapshot": lot_snapshot,
        "lots_stock_antiguo_mes": lots_stock_antiguo_mes,
        "lotes_apertura": lotes_apertura,
        "ventas_por_lote": ventas_por_lote,
        "entradas_pos_adj_por_lote": entradas_pos_adj_por_lote,
        "entradas_pos_adj_diario": entradas_pos_adj_diario,
        "ventas_diario_producto": ventas_diario_producto,
        "kg_stock_inicial": kg_stock_inicial_apertura,
        "lotes_stock_inicial": lotes_stock_inicial_apertura,
        "kg_ventas_stock_antiguo_mes": to_float(ventas_stock_antiguo_total.get("kg")),
        "lotes_ventas_stock_antiguo_mes": int(ventas_stock_antiguo_total.get("lotes") or 0),
        "kg_stock_apertura": to_float(stock_apertura.get("kg")),
        "lotes_stock_apertura": int(stock_apertura.get("lotes") or 0),
        "kg_ventas": to_float(ventas_total.get("kg")),
        "lotes_ventas": int(ventas_total.get("lotes") or 0),
        "unsold_lots": unsold_lots,
        "lotes_empaque_mes": int(empaque_mes.get("lotes") or 0),
        "lotes_stock_final": len(unsold_lots),
        "lotes_stock_final_bc": int(stock_final_total.get("lotes") or 0),
        "kg_stock_final": to_float(stock_final_total.get("kg")),
        "sql_trace": {
            "view_or_tables": ["bc.[Item Ledger Entry]"],
            "params": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "ile_history_from": BC_ILE_HISTORY_FROM.isoformat(),
            },
            "queries": trace_queries,
        },
    }


def parse_fecha_es_date(fecha: str) -> dt.date:
    return dt.datetime.strptime(fecha, "%d/%m/%Y").date()


def sql_row_to_date(value: Any) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return None


def merge_lots_for_stock_inicial_ile(
    lot_snapshot: list[dict[str, Any]],
    lots_stock_antiguo_mes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in lots_stock_antiguo_mes:
        lot = str(row.get("lot") or "").strip()
        if not lot:
            continue
        merged[lot] = {
            "lot": lot,
            "fe_empaque": row.get("fe_empaque"),
            "kg": row.get("kg"),
            "first_out": row.get("first_out"),
            "item_no": str(row.get("item_no") or "").strip(),
            "item_description": str(row.get("item_description") or "").strip(),
        }
    for row in lot_snapshot:
        lot = str(row.get("lot") or "").strip()
        if not lot:
            continue
        merged[lot] = {
            "lot": lot,
            "fe_empaque": row.get("fe_empaque"),
            "kg": row.get("kg"),
            "first_out": row.get("first_sale"),
            "item_no": str(row.get("item_no") or "").strip(),
            "item_description": str(row.get("item_description") or "").strip(),
        }
    return list(merged.values())


def build_stock_inicial_ile_diario(
    start: dt.date,
    end: dt.date,
    lot_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    lots: list[dict[str, Any]] = []
    for row in lot_rows:
        fe_empaque = sql_row_to_date(row.get("fe_empaque"))
        if fe_empaque is None:
            continue
        lots.append(
            {
                "fe_empaque": fe_empaque,
                "kg": to_float(row.get("kg")),
                "first_out": sql_row_to_date(row.get("first_out")),
            }
        )

    result: list[dict[str, Any]] = []
    current = start
    while current <= end:
        kg_total = 0.0
        lotes = 0
        for lot in lots:
            if lot["fe_empaque"] >= current:
                continue
            first_out = lot["first_out"]
            if first_out is not None and first_out < current:
                continue
            kg_total += lot["kg"]
            lotes += 1
        result.append({"fecha": current, "kg": kg_total, "lotes": lotes})
        current += dt.timedelta(days=1)
    return result


def compute_bc_stock_real_cierre(
    lot_snapshot: list[dict[str, Any]],
    day: dt.date,
) -> float:
    total = 0.0
    for row in lot_snapshot:
        fe_empaque = row.get("fe_empaque")
        if fe_empaque is None:
            continue
        if isinstance(fe_empaque, dt.datetime):
            fe_empaque = fe_empaque.date()
        if fe_empaque > day:
            continue
        first_sale = row.get("first_sale")
        if first_sale is not None:
            if isinstance(first_sale, dt.datetime):
                first_sale = first_sale.date()
            if first_sale <= day:
                continue
        total += to_float(row.get("kg"))
    return total


def _format_sql_date(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, dt.datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, dt.date):
        return value.strftime("%d/%m/%Y")
    return str(value)


def _tipo_slug(tipo_key: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in tipo_key.strip().lower())
    slug = "-".join(part for part in slug.split("-") if part)
    return slug or "sin-tipo"


def _lot_in_stock_inicial(fe_empaque: Any, first_out: Any, day: dt.date) -> bool:
    """En stock al inicio del dia: empaque anterior al dia y salida ese dia o despues."""
    fe = sql_row_to_date(fe_empaque)
    if fe is None or fe >= day:
        return False
    out = sql_row_to_date(first_out)
    if out is not None and out < day:
        return False
    return True


def _lot_in_stock_final(fe_empaque: Any, first_out: Any, day: dt.date) -> bool:
    """En stock al cierre del dia: empaque hasta el dia y sin venta ese dia (salida posterior)."""
    fe = sql_row_to_date(fe_empaque)
    if fe is None or fe > day:
        return False
    out = sql_row_to_date(first_out)
    if out is not None and out <= day:
        return False
    return True


def build_bc_balance_lot_detalle(
    bc_balance: dict[str, Any],
    innova_lotes_material: list[dict[str, Any]] | None,
    period_start: dt.date | None = None,
    conversion_by_bascula: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    innova_by_lot: dict[str, dict[str, Any]] = {}
    for row in innova_lotes_material or []:
        lot_key = str(row["lot"]).strip()
        existing = innova_by_lot.get(lot_key)
        if existing is None:
            innova_by_lot[lot_key] = dict(row)
            continue
        existing["kg_innova"] = to_float(existing.get("kg_innova")) + to_float(row.get("kg_innova"))
        existing["packs"] = int(existing.get("packs") or 0) + int(row.get("packs") or 0)
        if not existing.get("pattern") and row.get("pattern"):
            existing["pattern"] = row.get("pattern")

    unsold = {str(lot).strip() for lot in (bc_balance.get("unsold_lots") or [])}
    ventas_by_lot = {
        str(row["lot"]).strip(): row for row in (bc_balance.get("ventas_por_lote") or [])
    }
    apertura_by_lot = {
        str(row["lot"]).strip(): row for row in (bc_balance.get("lotes_apertura") or [])
    }
    snapshot_by_lot = {
        str(row["lot"]).strip(): row for row in (bc_balance.get("lot_snapshot") or [])
    }
    antiguo_by_lot = {
        str(row["lot"]).strip(): row for row in (bc_balance.get("lots_stock_antiguo_mes") or [])
    }
    entradas_by_lot = {
        str(row["lot"]).strip(): row for row in (bc_balance.get("entradas_pos_adj_por_lote") or [])
    }

    all_lots: set[str] = set()
    all_lots.update(innova_by_lot)
    all_lots.update(unsold)
    all_lots.update(ventas_by_lot)
    all_lots.update(apertura_by_lot)
    all_lots.update(snapshot_by_lot)
    all_lots.update(antiguo_by_lot)
    all_lots.update(entradas_by_lot)

    # Empaque ficticio (dia anterior al periodo) para ventas sin Fecha empaque ni empaque del mes.
    fe_apertura_inferida = (period_start - dt.timedelta(days=1)) if period_start else None

    detalle: list[dict[str, Any]] = []
    for lot in sorted(all_lots):
        snap = snapshot_by_lot.get(lot, {})
        apertura = apertura_by_lot.get(lot)
        antiguo = antiguo_by_lot.get(lot, {})
        innova = innova_by_lot.get(lot, {})
        venta = ventas_by_lot.get(lot, {})
        entrada = entradas_by_lot.get(lot, {})

        material = str(innova.get("material") or "").strip()
        material_nombre = str(innova.get("material_nombre") or "").strip()
        pattern = str(innova.get("pattern") or "").strip()
        item_no = (
            str(snap.get("item_no") or "").strip()
            or str(entrada.get("item_no") or "").strip()
            or str(antiguo.get("item_no") or "").strip()
            or str(apertura.get("item_no") if apertura else "").strip()
            or str(venta.get("item_no") or "").strip()
        )
        item_description = (
            str(snap.get("item_description") or "").strip()
            or str(entrada.get("item_description") or "").strip()
            or str(antiguo.get("item_description") or "").strip()
            or str(apertura.get("item_description") if apertura else "").strip()
            or str(venta.get("item_description") or "").strip()
        )
        cod_producto, enlace_origen, conv_desc = resolve_cod_producto_bc(
            material, pattern, item_no, conversion_by_bascula
        )
        tipo_key = cod_producto
        tipo_nombre = (
            material_nombre
            or conv_desc
            or item_description
            or cod_producto
            or "(sin tipo)"
        )

        fe_empaque = (
            snap.get("fe_empaque")
            or entrada.get("fe_empaque")
            or antiguo.get("fe_empaque")
            or venta.get("fe_empaque")
            or (apertura.get("fe_empaque") if apertura else None)
        )
        kg_bc = (
            to_float(snap.get("kg"))
            if snap
            else to_float(
                entrada.get("kg")
                if entrada
                else (antiguo.get("kg") if antiguo else (apertura.get("kg") if apertura else 0))
            )
        )
        kg_innova = to_float(innova.get("kg_innova"))
        kg_ventas_bc = to_float(venta.get("kg"))
        qty_ventas_bc = int(round(to_float(venta.get("qty"))))
        qty_entradas_bc = int(round(to_float(entrada.get("qty"))))
        packs_innova = int(innova.get("packs") or 0)
        first_sale = (
            snap.get("first_sale")
            or venta.get("first_sale")
            or antiguo.get("first_out")
        )
        first_in = entrada.get("first_in")

        if lot in unsold:
            estado = "stock_final"
        elif kg_ventas_bc > 0 or first_sale is not None:
            estado = "vendido"
        elif qty_entradas_bc > 0 or first_in is not None:
            estado = "entrada_bc"
        elif apertura:
            estado = "apertura"
        else:
            estado = "empaque_mes"

        stock_apertura_inferido = False
        if (
            fe_empaque is None
            and estado == "vendido"
            and not snap
            and fe_apertura_inferida is not None
        ):
            fe_empaque = fe_apertura_inferida
            stock_apertura_inferido = True

        # Unidad homogenea: 1 lote BC = 1 caja (stock y ventas).
        # Entradas BC (ajuste positivo): ABS(Quantity), fallback 1 caja por lote.
        cajas_entradas = qty_entradas_bc if qty_entradas_bc > 0 else (1 if entrada else 0)
        cajas_ventas = 1 if (estado == "vendido" or qty_ventas_bc > 0 or kg_ventas_bc > 0) else 0
        cajas_stock_final = 1 if lot in unsold else 0

        detalle.append(
            {
                "lot": lot,
                "tipo_key": tipo_key,
                "tipo_nombre": tipo_nombre,
                "material": material,
                "material_nombre": material_nombre,
                "pattern": pattern,
                "cod_bascula": material,
                "cod_producto": cod_producto,
                "enlace_origen": enlace_origen,
                "item_no": item_no,
                "item_description": item_description,
                "fe_empaque": fe_empaque,
                "prday_min": innova.get("prday_min"),
                "prday_max": innova.get("prday_max"),
                "kg_innova": kg_innova,
                "packs_innova": packs_innova,
                "kg_bc": kg_bc,
                "kg_ventas_bc": kg_ventas_bc,
                "qty_ventas_bc": qty_ventas_bc,
                "qty_entradas_bc": qty_entradas_bc,
                "cajas_entradas": cajas_entradas,
                "cajas_ventas": cajas_ventas,
                "cajas_stock_final": cajas_stock_final,
                "kg_stock_final": kg_bc if lot in unsold else 0.0,
                "first_sale": first_sale,
                "first_in": first_in,
                "last_sale": venta.get("last_sale"),
                "estado": estado,
                "enlazado": bool(innova) and bool(snap or apertura or venta or antiguo or entrada),
                "stock_apertura_inferido": stock_apertura_inferido,
            }
        )
    return detalle


def build_bc_balance_por_tipo(lot_detalle: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in lot_detalle:
        key = row["tipo_key"]
        bucket = grouped.setdefault(
            key,
            {
                "tipo_key": key,
                "tipo_nombre": row["tipo_nombre"],
                "material": row.get("material") or "",
                "cod_producto": row.get("cod_producto") or key,
                "pattern": row.get("pattern") or "",
                "item_no": row.get("item_no") or "",
                "lotes": 0,
                "lotes_stock_final": 0,
                "lotes_vendidos": 0,
                "kg_innova": 0.0,
                "kg_bc": 0.0,
                "kg_ventas_bc": 0.0,
                "kg_stock_final": 0.0,
                "packs_innova": 0,
                "lotes_detalle": [],
            },
        )
        bucket["lotes"] += 1
        bucket["kg_innova"] += to_float(row.get("kg_innova"))
        bucket["kg_bc"] += to_float(row.get("kg_bc"))
        bucket["kg_ventas_bc"] += to_float(row.get("kg_ventas_bc"))
        bucket["kg_stock_final"] += to_float(row.get("kg_stock_final"))
        bucket["packs_innova"] += int(row.get("packs_innova") or 0)
        if not bucket.get("material") and row.get("material"):
            bucket["material"] = row.get("material") or ""
        if not bucket.get("pattern") and row.get("pattern"):
            bucket["pattern"] = row.get("pattern") or ""
        if row.get("estado") == "stock_final":
            bucket["lotes_stock_final"] += 1
        if row.get("estado") == "vendido":
            bucket["lotes_vendidos"] += 1
        bucket["lotes_detalle"].append(row)

    result = list(grouped.values())
    result.sort(key=lambda item: (-item["kg_innova"], item["tipo_nombre"]))
    return result


def build_bc_balance_por_tipo_cajas(
    lot_detalle: list[dict[str, Any]],
    start: dt.date,
    end: dt.date,
    bc_balance: dict[str, Any] | None = None,
    detalle_diario_report: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Balance por tipo en cajas + diario consolidado con encadenamiento.

    Entradas = ajustes positivos BC (Entry Type 2) por [Item No.] / Cod. producto.
    Ventas = ventas BC (Entry Type 1) por [Item No.] / Cod. producto.
    Stock final dia N = stock inicial dia N+1 (igual que balance consolidado).
    """
    tipo_meta: dict[str, dict[str, str]] = {}
    lots_by_tipo: dict[str, list[dict[str, Any]]] = {}
    item_to_tipo: dict[str, str] = {}

    def _ensure_tipo(
        key: str,
        *,
        tipo_nombre: str = "",
        material: str = "",
        cod_producto: str = "",
        pattern: str = "",
        item_no: str = "",
    ) -> str:
        key = (key or "(sin tipo)").strip() or "(sin tipo)"
        tipo_meta.setdefault(
            key,
            {
                "tipo_key": key,
                "tipo_nombre": tipo_nombre or key,
                "material": material,
                "cod_producto": cod_producto or key,
                "pattern": pattern,
                "item_no": item_no or key,
            },
        )
        lots_by_tipo.setdefault(key, [])
        return key

    for row in lot_detalle:
        key = _ensure_tipo(
            str(row.get("tipo_key") or "(sin tipo)"),
            tipo_nombre=str(row.get("tipo_nombre") or ""),
            material=str(row.get("material") or ""),
            cod_producto=str(row.get("cod_producto") or ""),
            pattern=str(row.get("pattern") or ""),
            item_no=str(row.get("item_no") or ""),
        )
        lots_by_tipo[key].append(row)
        for alias in (
            str(row.get("cod_producto") or "").strip(),
            str(row.get("item_no") or "").strip(),
            str(row.get("tipo_key") or "").strip(),
        ):
            if alias:
                item_to_tipo.setdefault(alias, key)

    def _tipo_from_item(item_no: str) -> str:
        item = (item_no or "").strip()
        if not item:
            return _ensure_tipo("(sin tipo)")
        if item in item_to_tipo:
            return item_to_tipo[item]
        key = _ensure_tipo(item, tipo_nombre=item, cod_producto=item, item_no=item)
        item_to_tipo[item] = key
        return key

    entradas_dia_tipo: dict[dt.date, dict[str, int]] = {}
    for row in (bc_balance or {}).get("entradas_pos_adj_diario") or []:
        day = sql_row_to_date(row.get("fecha"))
        item_no = str(row.get("item_no") or "").strip()
        if day is None or not item_no:
            continue
        key = _tipo_from_item(item_no)
        qty = int(round(to_float(row.get("qty"))))
        if qty <= 0:
            continue
        entradas_dia_tipo.setdefault(day, {})
        entradas_dia_tipo[day][key] = entradas_dia_tipo[day].get(key, 0) + qty

    ventas_dia_tipo: dict[dt.date, dict[str, int]] = {}
    for row in (bc_balance or {}).get("ventas_diario_producto") or []:
        day = sql_row_to_date(row.get("fecha"))
        item_no = str(row.get("item_no") or "").strip()
        if day is None or not item_no:
            continue
        key = _tipo_from_item(item_no)
        qty = int(round(to_float(row.get("qty"))))
        if qty <= 0:
            continue
        ventas_dia_tipo.setdefault(day, {})
        ventas_dia_tipo[day][key] = ventas_dia_tipo[day].get(key, 0) + qty

    stock_ini_tipo: dict[str, int] = {}
    for key, rows in lots_by_tipo.items():
        stock_ini_tipo[key] = sum(
            1
            for row in rows
            if _lot_in_stock_inicial(row.get("fe_empaque"), row.get("first_sale"), start)
        )

    days: list[dt.date] = []
    if detalle_diario_report:
        for det in detalle_diario_report:
            days.append(parse_fecha_es_date(det["fecha"]))
    else:
        current = start
        while current <= end:
            days.append(current)
            current += dt.timedelta(days=1)

    stock_real_dia_tipo: dict[dt.date, dict[str, int]] = {day: {} for day in days}
    for key, rows in lots_by_tipo.items():
        for row in rows:
            fe = sql_row_to_date(row.get("fe_empaque"))
            out = sql_row_to_date(row.get("first_sale"))
            if fe is None:
                continue
            for day in days:
                if fe > day:
                    continue
                if out is not None and out <= day:
                    continue
                stock_real_dia_tipo[day][key] = stock_real_dia_tipo[day].get(key, 0) + 1

    detalle_diario_cajas: list[dict[str, Any]] = []
    acc_entradas: dict[str, int] = {key: 0 for key in tipo_meta}
    acc_ventas: dict[str, int] = {key: 0 for key in tipo_meta}
    stock_ini_carry: dict[str, int] = dict(stock_ini_tipo)

    for day in days:
        entradas_map = entradas_dia_tipo.get(day, {})
        ventas_map = ventas_dia_tipo.get(day, {})
        real_map = stock_real_dia_tipo.get(day, {})
        cajas_entradas_dia = 0
        cajas_ventas_dia = 0
        cajas_ini_dia = 0
        cajas_teo_dia = 0
        cajas_real_dia = 0

        for key in tipo_meta:
            ini = int(stock_ini_carry.get(key, 0))
            ent = int(entradas_map.get(key, 0))
            ven = int(ventas_map.get(key, 0))
            teorico = ini + ent - ven
            real = int(real_map.get(key, 0))
            acc_entradas[key] = acc_entradas.get(key, 0) + ent
            acc_ventas[key] = acc_ventas.get(key, 0) + ven
            # Encadenamiento: stock final teorico del dia = stock inicial del siguiente.
            stock_ini_carry[key] = teorico
            cajas_ini_dia += ini
            cajas_entradas_dia += ent
            cajas_ventas_dia += ven
            cajas_teo_dia += teorico
            cajas_real_dia += real

        detalle_diario_cajas.append(
            {
                "fecha": day.strftime("%d/%m/%Y"),
                "cajas_stock_inicial": cajas_ini_dia,
                "cajas_entradas": cajas_entradas_dia,
                "cajas_ventas": cajas_ventas_dia,
                "cajas_stock_teorico": cajas_teo_dia,
                "cajas_stock_real": cajas_real_dia,
                "cajas_check": cajas_teo_dia - cajas_real_dia,
            }
        )

    result: list[dict[str, Any]] = []
    for key, meta in tipo_meta.items():
        ini = int(stock_ini_tipo.get(key, 0))
        ent = int(acc_entradas.get(key, 0))
        ven = int(acc_ventas.get(key, 0))
        teorico = ini + ent - ven
        real = int(stock_real_dia_tipo.get(end, {}).get(key, 0))
        result.append(
            {
                **meta,
                "cajas_stock_inicial": ini,
                "cajas_entradas": ent,
                "cajas_ventas": ven,
                "cajas_stock_teorico": teorico,
                "cajas_stock_real": real,
                "cajas_check": teorico - real,
                "lotes": len(lots_by_tipo.get(key, [])),
            }
        )

    result.sort(
        key=lambda item: (
            -(int(item["cajas_entradas"]) + int(item["cajas_ventas"])),
            str(item["tipo_key"]),
        )
    )
    return result, detalle_diario_cajas


def build_bc_stock_snapshot_por_producto(
    lot_detalle: list[dict[str, Any]],
    start: dt.date,
    end: dt.date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Stock inicial (fecha inicio) y stock final pendiente (empaque del periodo) por producto.

    Stock inicial: empaque < start y salida >= start (o sin salida).
    Stock final: empaque en [start, end] y sin venta hasta end (pendiente mes siguiente).
    Almacenes E/G (ya filtrados en ILE). Unidades: 1 lote = 1 caja; kg = kg BC del lote.
    """
    inicial: dict[str, dict[str, Any]] = {}
    final: dict[str, dict[str, Any]] = {}

    def _bucket(store: dict[str, dict[str, Any]], row: dict[str, Any]) -> dict[str, Any]:
        key = str(row.get("tipo_key") or row.get("cod_producto") or row.get("item_no") or "(sin tipo)").strip()
        if not key:
            key = "(sin tipo)"
        bucket = store.setdefault(
            key,
            {
                "tipo_key": key,
                "tipo_nombre": str(row.get("tipo_nombre") or key),
                "cod_producto": str(row.get("cod_producto") or key),
                "material": str(row.get("material") or ""),
                "pattern": str(row.get("pattern") or ""),
                "item_no": str(row.get("item_no") or ""),
                "cajas": 0,
                "kg": 0.0,
                "lotes": 0,
            },
        )
        if not bucket.get("tipo_nombre") and row.get("tipo_nombre"):
            bucket["tipo_nombre"] = str(row.get("tipo_nombre"))
        if not bucket.get("material") and row.get("material"):
            bucket["material"] = str(row.get("material") or "")
        if not bucket.get("pattern") and row.get("pattern"):
            bucket["pattern"] = str(row.get("pattern") or "")
        return bucket

    for row in lot_detalle:
        fe = row.get("fe_empaque")
        out = row.get("first_sale")
        kg = to_float(row.get("kg_bc"))
        if _lot_in_stock_inicial(fe, out, start):
            b = _bucket(inicial, row)
            b["cajas"] += 1
            b["kg"] += kg
            b["lotes"] += 1

        fe_d = sql_row_to_date(fe)
        if fe_d is not None and start <= fe_d <= end and _lot_in_stock_final(fe, out, end):
            b = _bucket(final, row)
            b["cajas"] += 1
            b["kg"] += kg
            b["lotes"] += 1

    lista_inicial = sorted(inicial.values(), key=lambda r: (-r["kg"], r["tipo_key"]))
    lista_final = sorted(final.values(), key=lambda r: (-r["kg"], r["tipo_key"]))
    totals = {
        "inicial_cajas": sum(int(r["cajas"]) for r in lista_inicial),
        "inicial_kg": sum(to_float(r["kg"]) for r in lista_inicial),
        "inicial_productos": len(lista_inicial),
        "final_cajas": sum(int(r["cajas"]) for r in lista_final),
        "final_kg": sum(to_float(r["kg"]) for r in lista_final),
        "final_productos": len(lista_final),
    }
    return lista_inicial, lista_final, totals


def attach_bc_balance_eg_to_report(
    report_data: dict[str, Any],
    bc_balance: dict[str, Any],
    innova_lotes: list[dict[str, Any]] | None = None,
    innova_lotes_material: list[dict[str, Any]] | None = None,
    conversion_productos: dict[str, Any] | None = None,
) -> None:
    kg_stock_inicial = bc_balance["kg_stock_inicial"]
    kg_stock_apertura = bc_balance["kg_stock_apertura"]
    kg_ventas = bc_balance["kg_ventas"]
    kg_produccion = to_float(report_data["kpis"]["kg_salida_no_tina"])

    ventas_by_date: dict[str, float] = {}
    ventas_lotes_by_date: dict[str, int] = {}
    for row in bc_balance["ventas_diario"]:
        key = sql_row_date_key(row["fecha"])
        ventas_by_date[key] = to_float(row["kg"])
        ventas_lotes_by_date[key] = int(row.get("lotes") or 0)

    stock_inicial_ile_by_date: dict[str, float] = {}
    stock_inicial_ile_lotes_by_date: dict[str, int] = {}
    for row in bc_balance.get("stock_inicial_ile_diario") or []:
        key = sql_row_date_key(row["fecha"])
        stock_inicial_ile_by_date[key] = to_float(row["kg"])
        stock_inicial_ile_lotes_by_date[key] = int(row.get("lotes") or 0)

    ventas_stock_antiguo_by_date: dict[str, float] = {}
    ventas_stock_antiguo_lotes_by_date: dict[str, int] = {}
    for row in bc_balance.get("ventas_stock_antiguo_diario") or []:
        key = sql_row_date_key(row["fecha"])
        ventas_stock_antiguo_by_date[key] = to_float(row["kg"])
        ventas_stock_antiguo_lotes_by_date[key] = int(row.get("lotes") or 0)

    empaque_by_date: dict[str, float] = {}
    empaque_lotes_by_date: dict[str, int] = {}
    for row in bc_balance.get("empaque_diario") or []:
        key = sql_row_date_key(row["fecha"])
        empaque_by_date[key] = to_float(row["kg"])
        empaque_lotes_by_date[key] = int(row.get("lotes") or 0)

    lot_snapshot = bc_balance.get("lot_snapshot") or []

    detalle_bc: list[dict[str, Any]] = []
    kg_stock_final_real_prev = kg_stock_apertura
    for det in report_data["detalle_diario"]:
        date_key = parse_fecha_es(det["fecha"])
        day = parse_fecha_es_date(det["fecha"])
        kg_stock_ini_dia = stock_inicial_ile_by_date.get(date_key, 0.0)
        lotes_stock_ini_dia = stock_inicial_ile_lotes_by_date.get(date_key, 0)
        kg_stock_real_ini_dia = kg_stock_final_real_prev
        kg_ventas_dia = ventas_by_date.get(date_key, 0.0)
        lotes_ventas_dia = ventas_lotes_by_date.get(date_key, 0)
        kg_ventas_stock_antiguo = ventas_stock_antiguo_by_date.get(date_key, 0.0)
        lotes_ventas_stock_antiguo = ventas_stock_antiguo_lotes_by_date.get(date_key, 0)
        kg_empaque_dia = empaque_by_date.get(date_key, 0.0)
        lotes_empaque_dia = empaque_lotes_by_date.get(date_key, 0)
        kg_prod_dia = to_float(det["kg_salida_no_tina"])
        kg_stock_final_teorico = kg_stock_ini_dia + kg_prod_dia - kg_ventas_dia
        kg_stock_final_real = compute_bc_stock_real_cierre(lot_snapshot, day)
        kg_real_variacion = kg_stock_final_real - kg_stock_real_ini_dia
        kg_diferencia = kg_stock_final_teorico - kg_stock_final_real
        kg_stock_final_real_prev = kg_stock_final_real
        detalle_bc.append(
            {
                "fecha": det["fecha"],
                "kg_stock_inicial": kg_stock_ini_dia,
                "lotes_stock_inicial": lotes_stock_ini_dia,
                "kg_stock_real_inicial": kg_stock_real_ini_dia,
                "kg_ventas_stock_antiguo": kg_ventas_stock_antiguo,
                "lotes_ventas_stock_antiguo": lotes_ventas_stock_antiguo,
                "kg_empaque": kg_empaque_dia,
                "lotes_empaque": lotes_empaque_dia,
                "kg_produccion": kg_prod_dia,
                "kg_ventas": kg_ventas_dia,
                "lotes_ventas": lotes_ventas_dia,
                "kg_stock_final_teorico": kg_stock_final_teorico,
                "kg_stock_final_real": kg_stock_final_real,
                "kg_stock_teorico": kg_stock_final_teorico,
                "kg_stock_real_cierre": kg_stock_final_real,
                "kg_real_variacion": kg_real_variacion,
                "kg_diferencia": kg_diferencia,
            }
        )

    if detalle_bc:
        kg_stock_real = detalle_bc[-1]["kg_stock_final_real"]
        kg_stock_teorico = detalle_bc[-1]["kg_stock_final_teorico"]
    else:
        kg_stock_real = bc_balance["kg_stock_final"]
        kg_stock_teorico = kg_stock_apertura + kg_produccion - kg_ventas
    kg_check = kg_stock_teorico - kg_stock_real
    check_ok = abs(kg_check) <= BC_BALANCE_CHECK_TOLERANCE_KG

    period_start = parse_fecha_es_date(report_data["detalle_diario"][0]["fecha"]) if report_data.get("detalle_diario") else None
    period_end = parse_fecha_es_date(report_data["detalle_diario"][-1]["fecha"]) if report_data.get("detalle_diario") else None
    if period_start is None:
        period_start = dt.date.fromisoformat(str(bc_balance["sql_trace"]["params"]["start"]))
    if period_end is None:
        period_end = dt.date.fromisoformat(str(bc_balance["sql_trace"]["params"]["end"]))
    lot_detalle = build_bc_balance_lot_detalle(
        bc_balance,
        innova_lotes_material,
        period_start=period_start,
        conversion_by_bascula=(conversion_productos or {}).get("by_bascula"),
    )
    detalle_por_tipo = build_bc_balance_por_tipo(lot_detalle)
    detalle_por_tipo_cajas, detalle_diario_cajas = build_bc_balance_por_tipo_cajas(
        lot_detalle,
        period_start,
        period_end,
        bc_balance=bc_balance,
        detalle_diario_report=report_data.get("detalle_diario"),
    )
    stock_inicial_producto, stock_final_producto, stock_producto_totals = (
        build_bc_stock_snapshot_por_producto(lot_detalle, period_start, period_end)
    )

    cajas_stock_inicial = sum(int(t["cajas_stock_inicial"]) for t in detalle_por_tipo_cajas)
    cajas_entradas = sum(int(t["cajas_entradas"]) for t in detalle_por_tipo_cajas)
    cajas_ventas = sum(int(t["cajas_ventas"]) for t in detalle_por_tipo_cajas)
    cajas_stock_teorico = sum(int(t["cajas_stock_teorico"]) for t in detalle_por_tipo_cajas)
    cajas_stock_real = sum(int(t["cajas_stock_real"]) for t in detalle_por_tipo_cajas)
    cajas_check = cajas_stock_teorico - cajas_stock_real

    report_data["bc_balance_eg"] = {
        "loaded": True,
        "kg_stock_inicial": kg_stock_inicial,
        "lotes_stock_inicial": bc_balance["lotes_stock_inicial"],
        "kg_stock_apertura": kg_stock_apertura,
        "lotes_stock_apertura": bc_balance["lotes_stock_apertura"],
        "kg_produccion": kg_produccion,
        "kg_ventas": kg_ventas,
        "lotes_ventas": bc_balance["lotes_ventas"],
        "kg_stock_teorico": kg_stock_teorico,
        "kg_stock_real": kg_stock_real,
        "kg_stock_final": kg_stock_real,
        "lotes_stock_final": bc_balance["lotes_stock_final"],
        "lotes_stock_final_bc": bc_balance["lotes_stock_final_bc"],
        "kg_check": kg_check,
        "check_ok": check_ok,
        "lotes_empaque_mes": bc_balance["lotes_empaque_mes"],
        "kg_empaque_mes": bc_balance.get("kg_empaque_mes", 0.0),
        "detalle_diario": detalle_bc,
        "lot_detalle": lot_detalle,
        "detalle_por_tipo": detalle_por_tipo,
        "detalle_por_tipo_cajas": detalle_por_tipo_cajas,
        "detalle_diario_cajas": detalle_diario_cajas,
        "stock_inicial_por_producto": stock_inicial_producto,
        "stock_final_por_producto": stock_final_producto,
        "stock_producto_totals": stock_producto_totals,
        "conversion_productos_count": len((conversion_productos or {}).get("by_bascula") or {}),
        "cajas_stock_inicial": cajas_stock_inicial,
        "cajas_entradas": cajas_entradas,
        "cajas_ventas": cajas_ventas,
        "cajas_stock_teorico": cajas_stock_teorico,
        "cajas_stock_real": cajas_stock_real,
        "cajas_check": cajas_check,
    }

    k = report_data["kpis"]
    k["bc_bal_kg_stock_inicial"] = kg_stock_inicial
    k["bc_bal_kg_stock_apertura"] = kg_stock_apertura
    k["bc_bal_kg_produccion"] = kg_produccion
    k["bc_bal_kg_ventas"] = kg_ventas
    k["bc_bal_kg_stock_teorico"] = kg_stock_teorico
    k["bc_bal_kg_stock_real"] = kg_stock_real
    k["bc_bal_kg_stock_final"] = kg_stock_real
    k["bc_bal_kg_check"] = kg_check
    k["bc_bal_check_ok"] = check_ok
    k["bc_bal_cajas_stock_inicial"] = cajas_stock_inicial
    k["bc_bal_cajas_entradas"] = cajas_entradas
    k["bc_bal_cajas_ventas"] = cajas_ventas
    k["bc_bal_cajas_stock_teorico"] = cajas_stock_teorico
    k["bc_bal_cajas_stock_real"] = cajas_stock_real
    k["bc_bal_cajas_check"] = cajas_check

    report_data.setdefault("sql_trace", {"view_or_tables": [], "params": {}, "queries": []})
    report_data["sql_trace"]["queries"].extend(bc_balance["sql_trace"]["queries"])
    if conversion_productos and conversion_productos.get("sql_trace"):
        report_data["sql_trace"]["queries"].extend(conversion_productos["sql_trace"]["queries"])
        report_data["sql_trace"]["view_or_tables"] = list(
            dict.fromkeys(
                report_data["sql_trace"].get("view_or_tables", [])
                + conversion_productos["sql_trace"].get("view_or_tables", [])
            )
        )
    report_data["sql_trace"]["view_or_tables"] = list(
        dict.fromkeys(
            report_data["sql_trace"].get("view_or_tables", [])
            + bc_balance["sql_trace"]["view_or_tables"]
        )
    )


def parse_fecha_es(fecha: str) -> str:
    return dt.datetime.strptime(fecha, "%d/%m/%Y").date().isoformat()


def attach_bc_cruce_to_report(
    report_data: dict[str, Any],
    bc_data: dict[str, Any],
    innova_lotes: list[dict[str, Any]],
) -> None:
    bc_by_lot = {str(row["lot"]).strip(): row for row in bc_data["by_lot"]}
    bc_orders_by_lot: dict[str, list[dict[str, Any]]] = {}
    for row in bc_data.get("by_lot_order", []):
        lot_key = str(row["lot"]).strip()
        bc_orders_by_lot.setdefault(lot_key, []).append(row)
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
    bc_cruce_detalle: list[dict[str, Any]] = []

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
        lotes_detalle: list[dict[str, Any]] = []

        for lot_row in lotes_dia:
            lot_key = str(lot_row["lot"]).strip()
            bc_row = bc_by_lot.get(lot_key)
            pedidos: list[dict[str, Any]] = []
            if bc_row:
                for order_row in bc_orders_by_lot.get(lot_key, []):
                    order_no = str(order_row.get("order_no") or "").strip()
                    pedidos.append(
                        {
                            "order_no": order_no,
                            "order_label": order_no if order_no else "(sin pedido)",
                            "qty": to_float(order_row["qty"]),
                            "kg": to_float(order_row["kg"]),
                            "posting_date_min": order_row.get("posting_date_min"),
                            "posting_date_max": order_row.get("posting_date_max"),
                        }
                    )
                if not pedidos:
                    order_no = str(bc_row.get("order_no") or "").strip()
                    pedidos.append(
                        {
                            "order_no": order_no,
                            "order_label": order_no if order_no else "(sin pedido)",
                            "qty": to_float(bc_row["qty"]),
                            "kg": to_float(bc_row["kg"]),
                            "posting_date_min": None,
                            "posting_date_max": None,
                        }
                    )

            lotes_detalle.append(
                {
                    "lot": lot_key,
                    "kg_innova": to_float(lot_row["kg"]),
                    "packs": int(lot_row.get("packs") or 0),
                    "enlazado": bc_row is not None,
                    "kg_bc_total": to_float(bc_row["kg"]) if bc_row else 0.0,
                    "pedidos": pedidos,
                }
            )

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

        if not any(
            (
                detalle_row.get("kg_salida_no_tina"),
                lotes_innova,
                lotes_enlazados,
                qty_con_pedido,
                qty_sin_pedido,
            )
        ):
            continue

        bc_cruce_detalle.append(
            {
                "fecha": detalle_row["fecha"],
                "fecha_id": date_key.replace("-", ""),
                "kg_salida_no_tina": detalle_row.get("kg_salida_no_tina", 0),
                "bc_lotes_innova": lotes_innova,
                "bc_lotes_enlazados": lotes_enlazados,
                "bc_kg_innova_enlazado": kg_innova_enlazado,
                "bc_kg_bc_enlazado": kg_bc_enlazado,
                "bc_kg_diferencia_enlazado": kg_innova_enlazado - kg_bc_enlazado,
                "bc_qty_con_pedido": qty_con_pedido,
                "bc_qty_sin_pedido": qty_sin_pedido,
                "bc_kg_con_pedido": kg_con_pedido,
                "bc_kg_sin_pedido": kg_sin_pedido,
                "lotes": lotes_detalle,
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
    report_data["bc_cruce_detalle"] = bc_cruce_detalle
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
            "Fuente alternativa: dbo.vw_stolt por fdespesque (no alineada a premisas 1-6). "
            + PREMISA_SALIDA
        )
    return (
        "Premisas 1-6: fecha prday. Entradas (rtype 1,12), procesadas (matxacts xactpath 1), "
        "salidas CAJA (rtype 1), stock tinas (rtype 1), merma por balance. "
        + PREMISA_SALIDA
        + " Cruce BC premisa 6 por lote (number = Lot No.)."
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
      stocks = []
    else:
      q_entrada = f"""
      SELECT
        CAST(p.prday AS date) AS fecha,
        SUM(CAST(p.weight AS float)) AS kg_entrada_tina,
        COUNT(*) AS packs_entrada
      FROM dbo.proc_packs p
      JOIN dbo.proc_materials m ON m.material = p.material
      WHERE p.prday >= %s
        AND p.prday < DATEADD(day, 1, %s)
        AND {SQL_ENTRADA_TINA}
      GROUP BY CAST(p.prday AS date)
      ORDER BY fecha;
      """

      q_salida = f"""
      SELECT
        CAST(p.prday AS date) AS fecha,
        SUM(CAST(p.weight AS float)) AS kg_salida_no_tina,
        COUNT(*) AS packs_salida
      FROM dbo.proc_packs p
      JOIN dbo.proc_materials m ON m.material = p.material
      WHERE p.prday >= %s
        AND p.prday < DATEADD(day, 1, %s)
        AND {SQL_SALIDA_CAJA}
      GROUP BY CAST(p.prday AS date)
      ORDER BY fecha;
      """

      q_consumo = f"""
      SELECT
        CAST(pk.prday AS date) AS fecha,
        SUM(CAST(pk.weight AS float)) AS kg_consumo_tina,
        COUNT(*) AS movs_consumo
      FROM dbo.proc_matxacts pk
      JOIN dbo.proc_materials mat ON mat.material = pk.material
      WHERE pk.prday >= %s
        AND pk.prday < DATEADD(day, 1, %s)
        AND {SQL_PROCESADA_TINA}
      GROUP BY CAST(pk.prday AS date)
      ORDER BY fecha;
      """

      q_stock = f"""
      SELECT
        CAST(pk.prday AS date) AS fecha,
        SUM(CAST(pk.nregs AS float)) AS nregs_stock_tina,
        SUM(CAST(pk.weight AS float)) AS kg_stock_tina
      FROM dbo.proc_packs pk
      JOIN dbo.proc_materials mat ON mat.material = pk.material
      WHERE pk.prday >= %s
        AND pk.prday < DATEADD(day, 1, %s)
        AND {SQL_STOCK_TINA}
      GROUP BY CAST(pk.prday AS date)
      ORDER BY fecha;
      """

      q_top_entrada = f"""
      SELECT TOP 15
        m.material,
        m.name AS material_nombre,
        SUM(CAST(p.weight AS float)) AS kg
      FROM dbo.proc_packs p
      JOIN dbo.proc_materials m ON m.material = p.material
      WHERE p.prday >= %s
        AND p.prday < DATEADD(day, 1, %s)
        AND {SQL_ENTRADA_TINA}
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
      WHERE p.prday >= %s
        AND p.prday < DATEADD(day, 1, %s)
        AND {SQL_SALIDA_CAJA}
      GROUP BY m.material, m.name
      ORDER BY kg DESC;
      """

      sql_trace.extend(
        [
          {"name": "entrada_prday", "query": q_entrada.strip()},
          {"name": "salida_prday", "query": q_salida.strip()},
          {"name": "procesada_prday", "query": q_consumo.strip()},
          {"name": "stock_tinas_prday", "query": q_stock.strip()},
          {"name": "top_entrada_prday", "query": q_top_entrada.strip()},
          {"name": "top_salida_prday", "query": q_top_salida.strip()},
        ]
      )

      entradas = fetch_rows(cursor, q_entrada, params)
      salidas = fetch_rows(cursor, q_salida, params)
      consumos = fetch_rows(cursor, q_consumo, params)
      stocks = fetch_rows(cursor, q_stock, params)
      top_entradas = fetch_rows(cursor, q_top_entrada, params)
      top_salidas = fetch_rows(cursor, q_top_salida, params)

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
            "nregs_stock_tina": 0,
            "kg_stock_tina": 0.0,
            "kg_stock_balance": 0.0,
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

    if data_source != "vw_stolt_despesque":
      for row in stocks:
        key = row["fecha"].isoformat()
        by_date[key]["nregs_stock_tina"] = int(row["nregs_stock_tina"] or 0)
        by_date[key]["kg_stock_tina"] = to_float(row["kg_stock_tina"])

    detalle = []
    acumulado_dif = 0.0
    acumulado_balance = 0.0
    acumulado_stock_tinas = 0.0

    for key in sorted(by_date.keys()):
        entry = by_date[key]
        dif = entry["kg_entrada_tina"] - entry["kg_consumo_tina"]
        balance = entry["kg_entrada_tina"] - entry["kg_salida_no_tina"]
        acumulado_dif += dif
        acumulado_balance += balance
        pct_dif = (dif / entry["kg_entrada_tina"] * 100.0) if entry["kg_entrada_tina"] else None

        acumulado_stock_tinas += entry.get("kg_stock_tina", 0.0)
        stock_sin_procesar = acumulado_stock_tinas

        detalle.append(
            {
                **entry,
                "diferencia_kg": dif,
                "acumulado_diferencia_kg": acumulado_dif,
                "balance_entrada_salida_kg": balance,
                "acumulado_balance_kg": acumulado_balance,
                "porcentaje_diferencia": pct_dif,
                "kg_entrada_tina_stock": 0.0,
                "kg_cajas_stock": 0.0,
                "diferencia_stock_kg": 0.0,
                "stock_sin_procesar_kg": stock_sin_procesar,
            }
        )

    tot_entrada = sum(r["kg_entrada_tina"] for r in detalle)
    tot_salida = sum(r["kg_salida_no_tina"] for r in detalle)
    tot_consumo = sum(r["kg_consumo_tina"] for r in detalle)
    tot_stock_tinas = sum(r.get("kg_stock_tina", 0) for r in detalle)
    tot_dif = sum(r["diferencia_kg"] for r in detalle)
    tot_balance = sum(r["balance_entrada_salida_kg"] for r in detalle)
    tot_stock_fin = detalle[-1]["stock_sin_procesar_kg"] if detalle else 0.0

    kpis = {
        "kg_entrada_tina": tot_entrada,
        "kg_salida_no_tina": tot_salida,
        "kg_consumo_tina": tot_consumo,
        "kg_stock_tinas": tot_stock_tinas,
        "kg_diferencia": tot_dif,
        "kg_balance_entrada_salida": tot_balance,
        "kg_stock_sin_procesar_fin": tot_stock_fin,
        "kg_stock_balance": None,
        "kg_merma": None,
        "pct_merma": None,
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
            and r.get("kg_stock_entrada", 0) == 0
            and r.get("kg_merma", 0) == 0
        ):
            continue
        rows.append(
            "<tr>"
            f"<td>{html.escape(r['fecha'])}</td>"
            f"<td class='num'>{fmt_num(r['kg_entrada_tina'])}</td>"
            f"<td class='num'>{fmt_num(r['kg_salida_no_tina'])}</td>"
            f"<td class='num'>{fmt_num(r.get('kg_stock_entrada', 0))}</td>"
            f"<td class='num'>{fmt_num(r.get('kg_merma', 0))}</td>"
            f"<td class='num'>{fmt_num(r['balance_entrada_salida_kg'])}</td>"
            "</tr>"
        )
    return "\n".join(rows)


BC_BALANCE_ESTADO_LABELS = {
    "stock_final": "Stock final",
    "vendido": "Vendido",
    "apertura": "Apertura",
    "empaque_mes": "Empaque mes",
}


def build_bc_balance_eg_lote_detail_table(lotes: list[dict[str, Any]]) -> str:
    body_rows: list[str] = []
    for lot in lotes:
        estado = BC_BALANCE_ESTADO_LABELS.get(str(lot.get("estado")), str(lot.get("estado")))
        body_rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(lot['lot']))}</code></td>"
            f"<td>{html.escape(str(lot.get('tipo_nombre', '')))}</td>"
            f"<td>{html.escape(str(lot.get('item_no') or '—'))}</td>"
            f"<td>{_format_sql_date(lot.get('fe_empaque'))}</td>"
            f"<td class='num'>{fmt_num(lot.get('kg_innova', 0))}</td>"
            f"<td class='num'>{int(lot.get('packs_innova', 0))}</td>"
            f"<td class='num'>{fmt_num(lot.get('kg_bc', 0))}</td>"
            f"<td class='num'>{fmt_num(lot.get('kg_ventas_bc', 0))}</td>"
            f"<td class='num'>{fmt_num(lot.get('kg_stock_final', 0))}</td>"
            f"<td>{html.escape(estado)}</td>"
            "</tr>"
        )
    if not body_rows:
        return "<p class='muted'>Sin lotes para este tipo de producto.</p>"
    return (
        "<table class='bc-lot-table'>"
        "<thead><tr>"
        "<th>Lote</th><th>Producto</th><th>Item BC</th><th>Fecha empaque</th>"
        "<th class='num'>Kg Innova</th><th class='num'>Cajas</th>"
        "<th class='num'>Kg BC</th><th class='num'>Kg ventas BC</th>"
        "<th class='num'>Kg stock final</th><th>Estado</th>"
        "</tr></thead><tbody>"
        + "".join(body_rows)
        + "</tbody></table>"
    )


def build_bc_balance_eg_lote_table_rows(lot_detalle: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for lot in lot_detalle:
        estado = BC_BALANCE_ESTADO_LABELS.get(str(lot.get("estado")), str(lot.get("estado")))
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(lot.get('cod_producto') or lot.get('material') or '—'))}</td>"
            f"<td>{html.escape(str(lot.get('material_nombre') or lot.get('tipo_nombre') or '—'))}</td>"
            f"<td><code>{html.escape(str(lot['lot']))}</code></td>"
            f"<td>{html.escape(str(lot.get('material') or '—'))}</td>"
            f"<td>{html.escape(str(lot.get('pattern') or lot.get('item_no') or '—'))}</td>"
            f"<td>{_format_sql_date(lot.get('fe_empaque'))}</td>"
            f"<td>{_format_sql_date(lot.get('prday_min'))}</td>"
            f"<td class='num'>{fmt_num(lot.get('kg_innova', 0))}</td>"
            f"<td class='num'>{int(lot.get('packs_innova', 0))}</td>"
            f"<td class='num'>{fmt_num(lot.get('kg_bc', 0))}</td>"
            f"<td class='num'>{fmt_num(lot.get('kg_ventas_bc', 0))}</td>"
            f"<td class='num'>{fmt_num(lot.get('kg_stock_final', 0))}</td>"
            f"<td>{html.escape(estado)}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def build_bc_balance_eg_tipo_table_rows(detalle_por_tipo: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for tipo in detalle_por_tipo:
        tipo_id = html.escape(_tipo_slug(str(tipo.get("tipo_key"))))
        lotes = tipo.get("lotes_detalle") or []
        detail_id = f"bc-bal-tipo-{tipo_id}"
        rows.append(
            "<tr class='bc-date-row'>"
            f"<td class='bc-date-cell'>"
            f"<button type='button' class='bc-toggle' aria-expanded='false' "
            f"aria-controls='{detail_id}' title='Ver lotes del tipo'>▸</button> "
            f"{html.escape(str(tipo.get('cod_producto') or tipo.get('material') or '—'))}"
            f"<span class='bc-date-meta muted'> ({int(tipo.get('lotes', 0))} lotes)</span>"
            "</td>"
            f"<td>{html.escape(str(tipo.get('tipo_nombre') or '—'))}</td>"
            f"<td>{html.escape(str(tipo.get('material') or '—'))}</td>"
            f"<td>{html.escape(str(tipo.get('pattern') or tipo.get('item_no') or '—'))}</td>"
            f"<td class='num'>{int(tipo.get('lotes', 0))}</td>"
            f"<td class='num'>{int(tipo.get('lotes_stock_final', 0))}</td>"
            f"<td class='num'>{fmt_num(tipo.get('kg_innova', 0))}</td>"
            f"<td class='num'>{fmt_num(tipo.get('kg_ventas_bc', 0))}</td>"
            f"<td class='num'>{fmt_num(tipo.get('kg_stock_final', 0))}</td>"
            "</tr>"
            f"<tr id='{detail_id}' class='bc-detail-row' hidden>"
            f"<td colspan='9'>{build_bc_balance_eg_lote_detail_table(lotes)}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def build_bc_check_diario_table_rows(detalle: list[dict[str, Any]]) -> str:
    rows = []
    for r in detalle:
        diff_class = ""
        if abs(to_float(r.get("kg_diferencia"))) > BC_BALANCE_CHECK_TOLERANCE_KG:
            diff_class = " check-warn-cell"
        rows.append(
            "<tr>"
            f"<td>{html.escape(r['fecha'])}</td>"
            f"<td class='num'>{fmt_num(r['kg_empaque'])}</td>"
            f"<td class='num'>{int(r.get('lotes_empaque') or 0)}</td>"
            f"<td class='num'>{fmt_num(r['kg_stock_inicial'])}</td>"
            f"<td class='num'>{int(r.get('lotes_stock_inicial') or 0)}</td>"
            f"<td class='num'>{fmt_num(r['kg_ventas'])}</td>"
            f"<td class='num'>{int(r.get('lotes_ventas') or 0)}</td>"
            f"<td class='num'>{fmt_num(r.get('kg_stock_final_real', r.get('kg_stock_real_cierre', 0)))}</td>"
            f"<td class='num'>{fmt_num(r.get('kg_real_variacion', 0))}</td>"
            f"<td class='num'>{fmt_num(r.get('kg_stock_final_teorico', r.get('kg_stock_teorico', 0)))}</td>"
            f"<td class='num{diff_class}'>{fmt_num(r['kg_diferencia'])}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def build_bc_balance_eg_table_rows(detalle: list[dict[str, Any]]) -> str:
    rows = []
    for r in detalle:
        diff_class = ""
        if abs(to_float(r.get("kg_diferencia"))) > BC_BALANCE_CHECK_TOLERANCE_KG:
            diff_class = " check-warn-cell"
        rows.append(
            "<tr>"
            f"<td>{html.escape(r['fecha'])}</td>"
            f"<td class='num'>{fmt_num(r['kg_stock_inicial'])}</td>"
            f"<td class='num'>{fmt_num(r['kg_produccion'])}</td>"
            f"<td class='num'>{fmt_num(r['kg_ventas'])}</td>"
            f"<td class='num'>{fmt_num(r.get('kg_stock_final_teorico', r.get('kg_stock_teorico', 0)))}</td>"
            f"<td class='num'>{fmt_num(r.get('kg_stock_final_real', r.get('kg_stock_real_cierre', 0)))}</td>"
            f"<td class='num{diff_class}'>{fmt_num(r['kg_diferencia'])}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def build_bc_balance_eg_section_html(
    start: dt.date,
    end: dt.date,
    bc_balance: dict[str, Any] | None,
) -> str:
    if not bc_balance or not bc_balance.get("loaded"):
        return (
            "<article class='chart-card'>"
            "<h3>Balance BC almacenes E/G</h3>"
            "<p class='muted'>BC no disponible (--skip-bc o error de conexion).</p>"
            "</article>"
        )

    detalle = bc_balance["detalle_diario"]
    rows_html = build_bc_balance_eg_table_rows(detalle)
    check_rows_html = build_bc_check_diario_table_rows(detalle)
    detalle_por_tipo = bc_balance.get("detalle_por_tipo") or []
    lot_detalle = bc_balance.get("lot_detalle") or []
    tipo_rows_html = build_bc_balance_eg_tipo_table_rows(detalle_por_tipo)
    lote_rows_html = build_bc_balance_eg_lote_table_rows(lot_detalle)
    tot_tipos = len(detalle_por_tipo)
    tot_lotes_det = len(lot_detalle)
    tot_lotes_stock_final = sum(int(t.get("lotes_stock_final") or 0) for t in detalle_por_tipo)
    tot_kg_innova_lotes = sum(to_float(t.get("kg_innova")) for t in detalle_por_tipo)
    tot_kg_ventas_lotes = sum(to_float(t.get("kg_ventas_bc")) for t in detalle_por_tipo)
    tot_kg_stock_final_lotes = sum(to_float(t.get("kg_stock_final")) for t in detalle_por_tipo)
    check_ok = bool(bc_balance.get("check_ok"))
    check_class = "check-ok" if check_ok else "check-warn"
    check_label = "Cuadra" if check_ok else "Descuadre"
    reglas_items = "".join(f"<li>{html.escape(rule)}</li>" for rule in PREMISA_BC_BALANCE_EG_REGLAS)
    stock_ini_sub = (
        f"ILE: empaque anterior al dia, venta/ajuste neg. ese dia o despues · "
        f"Ventas stock antiguo en mes: {fmt_num(bc_balance.get('kg_ventas_stock_antiguo_mes', 0))} kg"
    )

    return f"""
      <section class="grid">
        <article class="card">
          <div class="kpi-title">Stock inicial BC dia 1 (kg)</div>
          <div class="kpi-value">{fmt_num(bc_balance['kg_stock_inicial'])}</div>
          <div class="kpi-sub">{html.escape(stock_ini_sub)}</div>
        </article>
        <article class="card">
          <div class="kpi-title">Salidas Innova (kg)</div>
          <div class="kpi-value">{fmt_num(bc_balance['kg_produccion'])}</div>
          <div class="kpi-sub">Salidas CAJA del periodo (premisa 3)</div>
        </article>
        <article class="card">
          <div class="kpi-title">Ventas BC E/G (kg)</div>
          <div class="kpi-value">{fmt_num(bc_balance['kg_ventas'])}</div>
          <div class="kpi-sub">{bc_balance['lotes_ventas']:,} lotes · Location E y G</div>
        </article>
        <article class="card">
          <div class="kpi-title">Stock final teorico (kg)</div>
          <div class="kpi-value">{fmt_num(bc_balance['kg_stock_teorico'])}</div>
          <div class="kpi-sub">Stock inicial + Salidas Innova − Ventas</div>
        </article>
        <article class="card">
          <div class="kpi-title">Stock final real (kg)</div>
          <div class="kpi-value">{fmt_num(bc_balance['kg_stock_real'])}</div>
          <div class="kpi-sub">Empaque del periodo sin venta · {bc_balance['lotes_stock_final']:,} lotes ({bc_balance['lotes_stock_final_bc']:,} con kg BC)</div>
        </article>
        <article class="card {check_class}">
          <div class="kpi-title">Check (kg)</div>
          <div class="kpi-value">{fmt_num(bc_balance['kg_check'])}</div>
          <div class="kpi-sub">{check_label}: Stock final teorico − Stock final real</div>
        </article>
      </section>
      <article class="chart-card" style="margin-top:14px;">
        <div class="section-head">
          <h3>Check BC diario (solo datos BC E/G)</h3>
          <button type="button" class="btn-export" data-table-id="bcCheckDiarioTable" data-file-name="check_bc_diario_eg">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <p class="muted" style="margin-top:0;">
          Tabla para validar contra Excel: solo movimientos BC en almacenes E y G.
          <strong>Empaque</strong> = lotes nuevos por [Fecha empaque] del dia.
          <strong>Stock inicial</strong> = ILE: [Fecha empaque] &lt; dia y venta o ajuste negativo (Type 1/3) en ese dia o posteriores.
          <strong>Ventas</strong> = ILE Entry Type 1 por [Posting Date].
          <strong>Stock final real</strong> = empaque acumulado del periodo sin venta hasta ese dia.
          <strong>Stock final teorico</strong> = Stock inicial + Salidas Innova − Ventas (comparar con stock final real).
          <strong>Δ real</strong> = stock final real − stock inicial del dia.
          <strong>Check</strong> = Stock final teorico − Stock final real.
        </p>
        <table id="bcCheckDiarioTable">
          <thead>
            <tr>
              <th>Fecha</th>
              <th class="num">Empaque BC (kg)</th>
              <th class="num">Lotes empaque</th>
              <th class="num">Stock inicial (kg)</th>
              <th class="num">Lotes</th>
              <th class="num">Ventas BC (kg)</th>
              <th class="num">Lotes ventas</th>
              <th class="num">Stock final real (kg)</th>
              <th class="num">Δ real (kg)</th>
              <th class="num">Stock final teorico (kg)</th>
              <th class="num">Check (kg)</th>
            </tr>
          </thead>
          <tbody>
            {check_rows_html}
            <tr>
              <td><strong>TOTAL / CIERRE</strong></td>
              <td class="num"><strong>{fmt_num(bc_balance.get('kg_empaque_mes', 0))}</strong></td>
              <td class="num"><strong>{bc_balance['lotes_empaque_mes']:,}</strong></td>
              <td class="num"><strong>{fmt_num(bc_balance['kg_stock_inicial'])}</strong></td>
              <td class="num"><strong>{bc_balance['lotes_stock_inicial']:,}</strong></td>
              <td class="num"><strong>{fmt_num(bc_balance['kg_ventas'])}</strong></td>
              <td class="num"><strong>{bc_balance['lotes_ventas']:,}</strong></td>
              <td class="num"><strong>{fmt_num(bc_balance['kg_stock_real'])}</strong></td>
              <td class="num"><strong>—</strong></td>
              <td class="num"><strong>{fmt_num(bc_balance['kg_stock_teorico'])}</strong></td>
              <td class="num"><strong class="{check_class}">{fmt_num(bc_balance['kg_check'])}</strong></td>
            </tr>
          </tbody>
        </table>
      </article>
      <article class="chart-card" style="margin-top:14px;">
        <div class="section-head">
          <h3>Balance de masa BC (almacenes E y G)</h3>
          <button type="button" class="btn-export" data-table-id="bcBalanceEgTable" data-file-name="balance_bc_almacenes_eg">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <p class="muted" style="margin-top:0;">
          <strong>Stock inicial (dia)</strong> = ILE: [Fecha empaque] &lt; dia; venta o ajuste negativo (Entry Type 1/3) en ese dia o posteriores.
          <strong>Salidas Innova</strong> = salidas CAJA del dia (premisa 3).
          <strong>Ventas</strong> = ILE Entry Type 1 en E/G por [Posting Date].
          <strong>Stock final teorico</strong> = Stock inicial + Salidas Innova − Ventas.
          <strong>Stock final real</strong> = empaque del periodo hasta ese dia sin venta hasta ese dia.
          <strong>Check</strong> = Stock final teorico − Stock final real.
          <strong>Stock apertura</strong> = empaque anterior al periodo sin venta previa (E/G): <strong>{fmt_num(bc_balance['kg_stock_apertura'])}</strong> kg.
          <strong>Fines de semana</strong> = sin Salidas Innova ni Ventas; stock inicial y finales se arrastran del dia anterior.
        </p>
        <p class="balance-formula">
          Stock final teorico fin de mes: <strong>{fmt_num(bc_balance['kg_stock_teorico'])}</strong>
          &nbsp;|&nbsp; Stock final real fin de mes: <strong>{fmt_num(bc_balance['kg_stock_real'])}</strong>
          <span class="{check_class}">(check {fmt_num(bc_balance['kg_check'])} kg)</span>
        </p>
        <table id="bcBalanceEgTable">
          <thead>
            <tr>
              <th>Fecha</th>
              <th class="num">Stock inicial (kg)</th>
              <th class="num">Salidas Innova (kg)</th>
              <th class="num">Ventas BC E/G (kg)</th>
              <th class="num">Stock final teorico (kg)</th>
              <th class="num">Stock final real (kg)</th>
              <th class="num">Check (kg)</th>
            </tr>
          </thead>
          <tbody>
            {rows_html}
            <tr>
              <td><strong>TOTAL / CIERRE</strong></td>
              <td class="num"><strong>{fmt_num(bc_balance['kg_stock_inicial'])}</strong></td>
              <td class="num"><strong>{fmt_num(bc_balance['kg_produccion'])}</strong></td>
              <td class="num"><strong>{fmt_num(bc_balance['kg_ventas'])}</strong></td>
              <td class="num"><strong>{fmt_num(bc_balance['kg_stock_teorico'])}</strong></td>
              <td class="num"><strong>{fmt_num(bc_balance['kg_stock_real'])}</strong></td>
              <td class="num"><strong class="{check_class}">{fmt_num(bc_balance['kg_check'])}</strong></td>
            </tr>
          </tbody>
        </table>
      </article>
      <article class="chart-card" style="margin-top:14px;">
        <div class="section-head">
          <h3>Desglose por tipo de producto</h3>
          <button type="button" class="btn-export" data-table-id="bcBalanceEgTipoTable" data-file-name="balance_bc_eg_por_tipo">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <p class="muted" style="margin-top:0;">
          Producto BC desde <code>bc.[Conversion productos]</code>
          (<code>Cod. bascula</code> = material Innova → <code>Cod. producto</code>).
          Pulsa ▸ para ver los lotes del tipo.
          {tot_tipos:,} productos · {tot_lotes_det:,} lotes en el periodo.
        </p>
        <table id="bcBalanceEgTipoTable">
          <thead>
            <tr>
              <th>Cod. producto BC</th>
              <th>Producto</th>
              <th>Cod. bascula</th>
              <th>Pattern / Item</th>
              <th class="num">Lotes</th>
              <th class="num">Lotes stock final</th>
              <th class="num">Kg Innova</th>
              <th class="num">Kg ventas BC</th>
              <th class="num">Kg stock final</th>
            </tr>
          </thead>
          <tbody>
            {tipo_rows_html}
            <tr>
              <td colspan="4"><strong>TOTAL</strong></td>
              <td class="num"><strong>{tot_lotes_det:,}</strong></td>
              <td class="num"><strong>{tot_lotes_stock_final:,}</strong></td>
              <td class="num"><strong>{fmt_num(tot_kg_innova_lotes)}</strong></td>
              <td class="num"><strong>{fmt_num(tot_kg_ventas_lotes)}</strong></td>
              <td class="num"><strong>{fmt_num(tot_kg_stock_final_lotes)}</strong></td>
            </tr>
          </tbody>
        </table>
      </article>
      <article class="chart-card" style="margin-top:14px;">
        <div class="section-head">
          <h3>Detalle por lote</h3>
          <button type="button" class="btn-export" data-table-id="bcBalanceEgLoteTable" data-file-name="balance_bc_eg_por_lote">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <p class="muted" style="margin-top:0;">
          Lote = <code>proc_packs.number</code> (Innova) / <code>[Lot No.]</code> (BC).
          Estados: stock final (sin venta en el mes), vendido, apertura (empaque previo al periodo).
        </p>
        <table id="bcBalanceEgLoteTable">
          <thead>
            <tr>
              <th>Cod. producto</th>
              <th>Producto</th>
              <th>Lote</th>
              <th>Cod. bascula</th>
              <th>Pattern / Item</th>
              <th>Fecha empaque</th>
              <th>Salida Innova</th>
              <th class="num">Kg Innova</th>
              <th class="num">Cajas</th>
              <th class="num">Kg BC</th>
              <th class="num">Kg ventas BC</th>
              <th class="num">Kg stock final</th>
              <th>Estado</th>
            </tr>
          </thead>
          <tbody>
            {lote_rows_html}
          </tbody>
        </table>
      </article>
      <section class="premisa-box" style="margin-top:14px;">
        <h3 class="premisa-head">Reglas balance BC E/G</h3>
        <ul class="premisa-list">{reglas_items}</ul>
        <p class="premisa-note muted">
          Lotes con empaque en el periodo (BC): {bc_balance['lotes_empaque_mes']:,}.
          Tolerancia check: ±{fmt_num(BC_BALANCE_CHECK_TOLERANCE_KG, 0)} kg.
        </p>
      </section>
    """


def build_bc_balance_tipo_cajas_table_rows(detalle: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for tipo in detalle:
        check_val = int(tipo.get("cajas_check") or 0)
        check_class = " check-warn-cell" if check_val != 0 else ""
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(tipo.get('cod_producto') or tipo.get('tipo_key') or '—'))}</code></td>"
            f"<td>{html.escape(str(tipo.get('tipo_nombre') or '—'))}</td>"
            f"<td>{html.escape(str(tipo.get('material') or '—'))}</td>"
            f"<td>{html.escape(str(tipo.get('pattern') or '—'))}</td>"
            f"<td class='num'>{int(tipo.get('cajas_stock_inicial') or 0):,}</td>"
            f"<td class='num'>{int(tipo.get('cajas_entradas') or 0):,}</td>"
            f"<td class='num'>{int(tipo.get('cajas_ventas') or 0):,}</td>"
            f"<td class='num'>{int(tipo.get('cajas_stock_teorico') or 0):,}</td>"
            f"<td class='num'>{int(tipo.get('cajas_stock_real') or 0):,}</td>"
            f"<td class='num{check_class}'>{check_val:,}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def build_bc_balance_diario_cajas_table_rows(detalle: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for r in detalle:
        check_val = int(r.get("cajas_check") or 0)
        check_class = " check-warn-cell" if check_val != 0 else ""
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(r.get('fecha') or '—'))}</td>"
            f"<td class='num'>{int(r.get('cajas_stock_inicial') or 0):,}</td>"
            f"<td class='num'>{int(r.get('cajas_entradas') or 0):,}</td>"
            f"<td class='num'>{int(r.get('cajas_ventas') or 0):,}</td>"
            f"<td class='num'>{int(r.get('cajas_stock_teorico') or 0):,}</td>"
            f"<td class='num'>{int(r.get('cajas_stock_real') or 0):,}</td>"
            f"<td class='num{check_class}'>{check_val:,}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def build_bc_balance_tipo_cajas_section_html(
    start: dt.date,
    end: dt.date,
    bc_balance: dict[str, Any] | None,
) -> str:
    if not bc_balance or not bc_balance.get("loaded"):
        return (
            "<article class='chart-card'>"
            "<h3>Balance por tipo de producto (cajas)</h3>"
            "<p class='muted'>BC no disponible (--skip-bc o error de conexion).</p>"
            "</article>"
        )

    detalle = bc_balance.get("detalle_por_tipo_cajas") or []
    detalle_diario = bc_balance.get("detalle_diario_cajas") or []
    rows_html = build_bc_balance_tipo_cajas_table_rows(detalle)
    diario_rows_html = build_bc_balance_diario_cajas_table_rows(detalle_diario)
    cajas_ini = int(bc_balance.get("cajas_stock_inicial") or 0)
    cajas_ent = int(bc_balance.get("cajas_entradas") or 0)
    cajas_ven = int(bc_balance.get("cajas_ventas") or 0)
    cajas_teo = int(bc_balance.get("cajas_stock_teorico") or 0)
    cajas_real = int(bc_balance.get("cajas_stock_real") or 0)
    cajas_check = int(bc_balance.get("cajas_check") or 0)
    check_class = "check-ok" if cajas_check == 0 else "check-warn"
    check_label = "Cuadra" if cajas_check == 0 else "Descuadre"

    return f"""
      <section class="grid">
        <article class="card">
          <div class="kpi-title">Stock inicial (cajas)</div>
          <div class="kpi-value">{cajas_ini:,}</div>
          <div class="kpi-sub">ILE dia 1 · empaque &lt; {format_date_es(start)}</div>
        </article>
        <article class="card">
          <div class="kpi-title">Entradas BC (ajustes +)</div>
          <div class="kpi-value">{cajas_ent:,}</div>
          <div class="kpi-sub">Positive Adjmt. · Entry Type 2 · E/G · ABS(Quantity)</div>
        </article>
        <article class="card">
          <div class="kpi-title">Ventas BC E/G (cajas)</div>
          <div class="kpi-value">{cajas_ven:,}</div>
          <div class="kpi-sub">Sale · Entry Type 1 · E/G · ABS(Quantity)</div>
        </article>
        <article class="card">
          <div class="kpi-title">Stock final teorico (cajas)</div>
          <div class="kpi-value">{cajas_teo:,}</div>
          <div class="kpi-sub">Inicial + Entradas − Ventas</div>
        </article>
        <article class="card">
          <div class="kpi-title">Stock final real (cajas)</div>
          <div class="kpi-value">{cajas_real:,}</div>
          <div class="kpi-sub">Cierre ILE fin de periodo (incluye stock arrastrado)</div>
        </article>
        <article class="card {check_class}">
          <div class="kpi-title">Check (cajas)</div>
          <div class="kpi-value">{cajas_check:,}</div>
          <div class="kpi-sub">{check_label}: teorico − real</div>
        </article>
      </section>
      <article class="chart-card" style="margin-top:14px;">
        <div class="section-head">
          <h3>Balance diario en cajas (encadenado)</h3>
          <button type="button" class="btn-export" data-table-id="bcBalanceDiarioCajasTable" data-file-name="balance_bc_eg_diario_cajas">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <p class="muted" style="margin-top:0;">
          Igual que el balance consolidado:
          <strong>stock final teorico del dia N = stock inicial del dia N+1</strong>.
          Dia 1 parte del stock inicial ILE; el resto se arrastra.
          <strong>Entradas</strong> = ajustes positivos BC; <strong>Ventas</strong> = ventas BC.
        </p>
        <table id="bcBalanceDiarioCajasTable">
          <thead>
            <tr>
              <th>Fecha</th>
              <th class="num">Stock inicial (cajas)</th>
              <th class="num">Entradas BC (ajustes +)</th>
              <th class="num">Ventas BC (cajas)</th>
              <th class="num">Stock final teorico</th>
              <th class="num">Stock final real</th>
              <th class="num">Check</th>
            </tr>
          </thead>
          <tbody>
            {diario_rows_html}
            <tr>
              <td><strong>TOTAL / CIERRE</strong></td>
              <td class="num"><strong>{cajas_ini:,}</strong></td>
              <td class="num"><strong>{cajas_ent:,}</strong></td>
              <td class="num"><strong>{cajas_ven:,}</strong></td>
              <td class="num"><strong>{cajas_teo:,}</strong></td>
              <td class="num"><strong>{cajas_real:,}</strong></td>
              <td class="num"><strong class="{check_class}">{cajas_check:,}</strong></td>
            </tr>
          </tbody>
        </table>
      </article>
      <article class="chart-card" style="margin-top:14px;">
        <div class="section-head">
          <h3>Balance por tipo de producto (nº de cajas)</h3>
          <button type="button" class="btn-export" data-table-id="bcBalanceTipoCajasTable" data-file-name="balance_bc_eg_por_tipo_cajas">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <p class="muted" style="margin-top:0;">
          Producto BC = <code>Cod. producto</code> / <code>[Item No.]</code>
          (conversion <code>bc.[Conversion productos]</code> cuando hay enlace Innova).
          <strong>Entradas</strong> = ajustes positivos ILE (<code>Entry Type = 2</code>).
          <strong>Ventas</strong> = ventas ILE (<code>Entry Type = 1</code>).
          Agregado por producto con <code>ABS([Quantity])</code>.
          Unidad stock = <strong>nº de cajas</strong> (1 lote = 1 caja).
          Periodo: <strong>{format_date_es(start)}</strong> a <strong>{format_date_es(end)}</strong>.
          {len(detalle):,} productos.
        </p>
        <p class="balance-formula">
          Stock teorico: <strong>{cajas_teo:,}</strong>
          &nbsp;|&nbsp; Stock real: <strong>{cajas_real:,}</strong>
          <span class="{check_class}">(check {cajas_check:,} cajas)</span>
        </p>
        <table id="bcBalanceTipoCajasTable">
          <thead>
            <tr>
              <th>Cod. producto BC</th>
              <th>Producto</th>
              <th>Cod. bascula Innova</th>
              <th>Pattern Innova</th>
              <th class="num">Stock inicial (cajas)</th>
              <th class="num">Entradas BC (ajustes +)</th>
              <th class="num">Ventas BC (cajas)</th>
              <th class="num">Stock final teorico</th>
              <th class="num">Stock final real</th>
              <th class="num">Check</th>
            </tr>
          </thead>
          <tbody>
            {rows_html}
            <tr>
              <td colspan="4"><strong>TOTAL / CIERRE</strong></td>
              <td class="num"><strong>{cajas_ini:,}</strong></td>
              <td class="num"><strong>{cajas_ent:,}</strong></td>
              <td class="num"><strong>{cajas_ven:,}</strong></td>
              <td class="num"><strong>{cajas_teo:,}</strong></td>
              <td class="num"><strong>{cajas_real:,}</strong></td>
              <td class="num"><strong class="{check_class}">{cajas_check:,}</strong></td>
            </tr>
          </tbody>
        </table>
      </article>
    """


def build_bc_stock_producto_table_rows(detalle: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for tipo in detalle:
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(tipo.get('cod_producto') or tipo.get('tipo_key') or '—'))}</code></td>"
            f"<td>{html.escape(str(tipo.get('tipo_nombre') or '—'))}</td>"
            f"<td>{html.escape(str(tipo.get('material') or '—'))}</td>"
            f"<td>{html.escape(str(tipo.get('pattern') or '—'))}</td>"
            f"<td class='num'>{int(tipo.get('cajas') or 0):,}</td>"
            f"<td class='num'>{fmt_num(tipo.get('kg', 0))}</td>"
            f"<td class='num'>{int(tipo.get('lotes') or 0):,}</td>"
            "</tr>"
        )
    return "\n".join(rows) if rows else "<tr><td colspan='7'><em>Sin lotes en este corte.</em></td></tr>"


def build_bc_stock_inicial_section_html(
    start: dt.date,
    end: dt.date,
    bc_balance: dict[str, Any] | None,
) -> str:
    if not bc_balance or not bc_balance.get("loaded"):
        return (
            "<article class='chart-card'>"
            "<h3>Stock inicial BC E/G por tipo</h3>"
            "<p class='muted'>BC no disponible (--skip-bc o error de conexion).</p>"
            "</article>"
        )
    detalle = bc_balance.get("stock_inicial_por_producto") or []
    totals = bc_balance.get("stock_producto_totals") or {}
    rows_html = build_bc_stock_producto_table_rows(detalle)
    cajas = int(totals.get("inicial_cajas") or 0)
    kg = to_float(totals.get("inicial_kg"))
    nprod = int(totals.get("inicial_productos") or 0)
    return f"""
      <section class="grid">
        <article class="card">
          <div class="kpi-title">Stock inicial (cajas)</div>
          <div class="kpi-value">{cajas:,}</div>
          <div class="kpi-sub">Al {format_date_es(start)} · almacenes E/G · 1 lote = 1 caja</div>
        </article>
        <article class="card">
          <div class="kpi-title">Stock inicial (kg)</div>
          <div class="kpi-value">{fmt_num(kg)}</div>
          <div class="kpi-sub">Suma [Kilos] BC de lotes en stock</div>
        </article>
        <article class="card">
          <div class="kpi-title">Productos</div>
          <div class="kpi-value">{nprod:,}</div>
          <div class="kpi-sub">Cod. producto / Item No.</div>
        </article>
      </section>
      <article class="chart-card" style="margin-top:14px;">
        <div class="section-head">
          <h3>Stock inicial BC E/G por tipo de producto</h3>
          <button type="button" class="btn-export" data-table-id="bcStockInicialProductoTable" data-file-name="bc_stock_inicial_eg_producto">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <p class="muted" style="margin-top:0;">
          Corte al <strong>{format_date_es(start)}</strong> (fecha inicio del informe).
          Lotes con <code>[Fecha empaque]</code> &lt; inicio y venta ese dia o despues (o sin venta).
          Almacenes <strong>E</strong> y <strong>G</strong>.
        </p>
        <table id="bcStockInicialProductoTable">
          <thead>
            <tr>
              <th>Cod. producto BC</th>
              <th>Producto</th>
              <th>Cod. bascula</th>
              <th>Pattern</th>
              <th class="num">Cajas</th>
              <th class="num">Kg BC</th>
              <th class="num">Lotes</th>
            </tr>
          </thead>
          <tbody>
            {rows_html}
            <tr>
              <td colspan="4"><strong>TOTAL</strong></td>
              <td class="num"><strong>{cajas:,}</strong></td>
              <td class="num"><strong>{fmt_num(kg)}</strong></td>
              <td class="num"><strong>{cajas:,}</strong></td>
            </tr>
          </tbody>
        </table>
      </article>
    """


def build_bc_stock_final_section_html(
    start: dt.date,
    end: dt.date,
    bc_balance: dict[str, Any] | None,
) -> str:
    if not bc_balance or not bc_balance.get("loaded"):
        return (
            "<article class='chart-card'>"
            "<h3>Stock final BC E/G por tipo</h3>"
            "<p class='muted'>BC no disponible (--skip-bc o error de conexion).</p>"
            "</article>"
        )
    detalle = bc_balance.get("stock_final_por_producto") or []
    totals = bc_balance.get("stock_producto_totals") or {}
    rows_html = build_bc_stock_producto_table_rows(detalle)
    cajas = int(totals.get("final_cajas") or 0)
    kg = to_float(totals.get("final_kg"))
    nprod = int(totals.get("final_productos") or 0)
    return f"""
      <section class="grid">
        <article class="card">
          <div class="kpi-title">Stock final pendiente (cajas)</div>
          <div class="kpi-value">{cajas:,}</div>
          <div class="kpi-sub">Al {format_date_es(end)} · empaque del periodo · E/G</div>
        </article>
        <article class="card">
          <div class="kpi-title">Stock final pendiente (kg)</div>
          <div class="kpi-value">{fmt_num(kg)}</div>
          <div class="kpi-sub">Sin venta hasta el fin de periodo</div>
        </article>
        <article class="card">
          <div class="kpi-title">Productos</div>
          <div class="kpi-value">{nprod:,}</div>
          <div class="kpi-sub">Cod. producto / Item No.</div>
        </article>
      </section>
      <article class="chart-card" style="margin-top:14px;">
        <div class="section-head">
          <h3>Stock final BC E/G — empaque del periodo pendiente de venta</h3>
          <button type="button" class="btn-export" data-table-id="bcStockFinalProductoTable" data-file-name="bc_stock_final_eg_producto">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <p class="muted" style="margin-top:0;">
          Corte al <strong>{format_date_es(end)}</strong> (fecha fin del informe).
          Solo lotes con empaque entre <strong>{format_date_es(start)}</strong> y <strong>{format_date_es(end)}</strong>
          que siguen sin vender al cierre (quedan pendientes para el mes siguiente).
          Almacenes <strong>E</strong> y <strong>G</strong>.
        </p>
        <table id="bcStockFinalProductoTable">
          <thead>
            <tr>
              <th>Cod. producto BC</th>
              <th>Producto</th>
              <th>Cod. bascula</th>
              <th>Pattern</th>
              <th class="num">Cajas</th>
              <th class="num">Kg BC</th>
              <th class="num">Lotes</th>
            </tr>
          </thead>
          <tbody>
            {rows_html}
            <tr>
              <td colspan="4"><strong>TOTAL</strong></td>
              <td class="num"><strong>{cajas:,}</strong></td>
              <td class="num"><strong>{fmt_num(kg)}</strong></td>
              <td class="num"><strong>{cajas:,}</strong></td>
            </tr>
          </tbody>
        </table>
      </article>
    """


def _format_bc_posting_range(pedido: dict[str, Any]) -> str:
    date_min = pedido.get("posting_date_min")
    date_max = pedido.get("posting_date_max")
    if not date_min and not date_max:
        return "—"
    if date_min and date_max and date_min != date_max:
        return f"{date_min} — {date_max}"
    return str(date_min or date_max)


def build_bc_lot_detail_table(lotes: list[dict[str, Any]]) -> str:
    body_rows: list[str] = []
    for lot in lotes:
        pedidos = lot.get("pedidos") or []
        if not lot.get("enlazado"):
            body_rows.append(
                "<tr class='bc-lot-sin-enlace'>"
                f"<td><code>{html.escape(str(lot['lot']))}</code></td>"
                f"<td class='num'>{fmt_num(lot.get('kg_innova', 0))}</td>"
                f"<td class='num'>{int(lot.get('packs', 0))}</td>"
                "<td colspan='5'><em>Sin enlace BC en el periodo</em></td>"
                "</tr>"
            )
            continue
        if not pedidos:
            body_rows.append(
                "<tr>"
                f"<td><code>{html.escape(str(lot['lot']))}</code></td>"
                f"<td class='num'>{fmt_num(lot.get('kg_innova', 0))}</td>"
                f"<td class='num'>{int(lot.get('packs', 0))}</td>"
                "<td><em>(sin pedido)</em></td>"
                f"<td class='num'>{fmt_num(lot.get('kg_bc_total', 0))}</td>"
                "<td class='num'>—</td>"
                "<td>—</td>"
                "<td>—</td>"
                "</tr>"
            )
            continue
        for pedido in pedidos:
            order_label = html.escape(str(pedido.get("order_label", "(sin pedido)")))
            row_class = "bc-lot-sin-pedido" if not pedido.get("order_no") else ""
            body_rows.append(
                f"<tr class='{row_class}'>"
                f"<td><code>{html.escape(str(lot['lot']))}</code></td>"
                f"<td class='num'>{fmt_num(lot.get('kg_innova', 0))}</td>"
                f"<td class='num'>{int(lot.get('packs', 0))}</td>"
                f"<td><strong>{order_label}</strong></td>"
                f"<td class='num'>{fmt_num(pedido.get('kg', 0))}</td>"
                f"<td class='num'>{fmt_num(pedido.get('qty', 0))}</td>"
                f"<td>{html.escape(_format_bc_posting_range(pedido))}</td>"
                f"<td class='num'>{fmt_num(lot.get('kg_bc_total', 0))}</td>"
                "</tr>"
            )
    if not body_rows:
        return "<p class='muted'>Sin lotes con salida en esta fecha.</p>"
    return (
        "<table class='bc-lot-table'>"
        "<thead><tr>"
        "<th>Lote (number / Lot No.)</th>"
        "<th class='num'>Kg Innova</th>"
        "<th class='num'>Nº cajas</th>"
        "<th>Pedido BC ([Order No.])</th>"
        "<th class='num'>Kg BC (pedido)</th>"
        "<th class='num'>Qty BC</th>"
        "<th>Fecha contab. BC</th>"
        "<th class='num'>Kg BC lote (total)</th>"
        "</tr></thead><tbody>"
        + "".join(body_rows)
        + "</tbody></table>"
    )


def build_bc_cruce_table_rows(bc_cruce_detalle: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for day in bc_cruce_detalle:
        fecha_id = html.escape(str(day.get("fecha_id", "")))
        lotes = day.get("lotes") or []
        lot_count = len(lotes)
        detail_id = f"bc-detail-{fecha_id}"
        rows.append(
            "<tr class='bc-date-row'>"
            f"<td class='bc-date-cell'>"
            f"<button type='button' class='bc-toggle' aria-expanded='false' "
            f"aria-controls='{detail_id}' title='Ver lotes y pedidos'>▸</button> "
            f"{html.escape(day['fecha'])}"
            f"<span class='bc-date-meta muted'> ({lot_count} lotes)</span>"
            "</td>"
            f"<td class='num'>{fmt_num(day.get('kg_salida_no_tina', 0))}</td>"
            f"<td class='num'>{int(day.get('bc_lotes_innova', 0))}</td>"
            f"<td class='num'>{int(day.get('bc_lotes_enlazados', 0))}</td>"
            f"<td class='num'>{fmt_num(day.get('bc_kg_innova_enlazado', 0))}</td>"
            f"<td class='num'>{fmt_num(day.get('bc_kg_bc_enlazado', 0))}</td>"
            f"<td class='num'>{fmt_num(day.get('bc_kg_diferencia_enlazado', 0))}</td>"
            f"<td class='num'>{fmt_num(day.get('bc_qty_con_pedido', 0))}</td>"
            f"<td class='num'>{fmt_num(day.get('bc_qty_sin_pedido', 0))}</td>"
            f"<td class='num'>{fmt_num(day.get('bc_kg_con_pedido', 0))}</td>"
            f"<td class='num'>{fmt_num(day.get('bc_kg_sin_pedido', 0))}</td>"
            "</tr>"
        )
        rows.append(
            f"<tr class='bc-detail-row' id='{detail_id}' hidden>"
            "<td colspan='11'>"
            f"{build_bc_lot_detail_table(lotes)}"
            "</td>"
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
    stock_entrada_chart = [round(r.get("kg_stock_entrada", r.get("kg_stock_tina", 0)), 2) for r in detalle]
    stock_inventario_chart = [round(r.get("kg_stock_inventario", 0), 2) for r in detalle]
    merma = [round(r.get("kg_merma", 0), 2) for r in detalle]

    detail_rows_html = build_table_rows(detalle)
    stock_merma_rows_html = build_stock_merma_table_rows(detalle)
    bc_cruce_detalle = data.get("bc_cruce_detalle", [])
    bc_cruce_rows_html = build_bc_cruce_table_rows(bc_cruce_detalle)
    bc_balance_eg = data.get("bc_balance_eg")
    bc_balance_section_html = build_bc_balance_eg_section_html(start, end, bc_balance_eg)
    bc_balance_tipo_cajas_html = build_bc_balance_tipo_cajas_section_html(start, end, bc_balance_eg)
    bc_stock_inicial_html = build_bc_stock_inicial_section_html(start, end, bc_balance_eg)
    bc_stock_final_html = build_bc_stock_final_section_html(start, end, bc_balance_eg)
    bc_loaded = bool(data.get("bc_cruce"))
    bc_note = (
        "Enlace por proc_packs.number = BC Item Ledger Entry [Lot No.]. "
        "Ventas BC solo almacenes E y G. Pedido desde Sales Shipment Line."
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
          Salidas Innova agrupadas por fecha prday (premisa 3). Pulse <strong>▸</strong> en una fecha
          para desplegar lotes (<strong>proc_packs.number</strong>) y pedidos BC
          (<strong>[Order No.]</strong>) enlazados por <strong>[Lot No.]</strong>.
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
    intro_html = build_report_intro_html(start, end, k, source_definition, bc_loaded)
    nota_alerta_vap_html = build_nota_alerta_vap_html()
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
      sub_ini = (
        "Calculado por arrastre mensual"
        if k.get("kg_stock_inicial_auto")
        else "Dato ingresado por usuario"
      )
      stock_cards_html += (
        f"<article class='card'><div class='kpi-title'>Stock inicial (kg)</div>"
        f"<div class='kpi-value'>{fmt_num(k['kg_stock_inicial'])}</div>"
        f"<div class='kpi-sub'>{html.escape(sub_ini)}</div></article>"
      )
      stock_cards_html += (
        f"<article class='card'><div class='kpi-title'>Stock final teorico sin procesar (kg)</div>"
        f"<div class='kpi-value'>{fmt_num(k['kg_stock_final_teorico'])}</div>"
        "<div class='kpi-sub'>Stock inicial + Entradas TINA - TINA procesada</div></article>"
      )
    if k.get("kg_stock_inventario") is not None:
      stock_cards_html += (
        f"<article class='card'><div class='kpi-title'>Stock inventario cierre (kg)</div>"
        f"<div class='kpi-value'>{fmt_num(k['kg_stock_inventario'])}</div>"
        "<div class='kpi-sub'>Con arrastre · stock inicial + entradas - procesada</div></article>"
      )
    if k.get("kg_merma") is not None:
      pct_txt = f" · {fmt_pct(k['pct_merma'])} sobre entradas" if k.get("pct_merma") is not None else ""
      stock_cards_html += (
        f"<article class='card'><div class='kpi-title'>Merma (kg)</div>"
        f"<div class='kpi-value'>{fmt_num(k['kg_merma'])}</div>"
        f"<div class='kpi-sub'>Entradas - Salidas - Stock de tinas{pct_txt}</div></article>"
      )
    if k.get("arrastre_activo"):
      stock_cards_html += (
        f"<article class='card'><div class='kpi-title'>Tinas arrastradas (kg)</div>"
        f"<div class='kpi-value'>{fmt_num(k.get('kg_tinas_arrastradas', 0))}</div>"
        f"<div class='kpi-sub'>{int(k.get('tinas_arrastradas_packs', 0)):,} Nº de Tinas de "
        f"{html.escape(str(k.get('tinas_arrastradas_desde', '')))} a "
        f"{html.escape(str(k.get('tinas_arrastradas_hasta', '')))}</div></article>"
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

    arrastre_trail_html = ""
    trail = k.get("arrastre_trail") or []
    if trail:
      trail_rows = []
      for row in trail:
        trail_rows.append(
          "<tr>"
          f"<td>{html.escape(str(row.get('mes', '')))}</td>"
          f"<td class='num'>{fmt_num(row.get('kg_stock_apertura', 0))}</td>"
          f"<td class='num'>{fmt_num(row.get('kg_entrada_tina', 0))}</td>"
          f"<td class='num'>{fmt_num(row.get('kg_consumo_tina', 0))}</td>"
          f"<td class='num'>{fmt_num(row.get('kg_salida_no_tina', 0))}</td>"
          f"<td class='num'>{fmt_num(row.get('kg_stock_cierre', 0))}</td>"
          "</tr>"
        )
      arrastre_trail_html = (
        "<section class='tables'>"
        "<article class='table-card'>"
        "<h3>Encadenamiento mensual (arrastre de stock TINA)</h3>"
        "<p class='table-note'>Stock cierre = apertura + Entradas TINA - TINA procesada. "
        "El stock de apertura del periodo reportado es el cierre del ultimo mes de esta tabla.</p>"
        "<div class='table-wrap'><table class='data-table' id='tblArrastre'>"
        "<thead><tr>"
        "<th>Mes</th><th>Apertura (kg)</th><th>Entradas TINA</th>"
        "<th>TINA procesada</th><th>Salidas CAJA</th><th>Cierre (kg)</th>"
        "</tr></thead><tbody>"
        + "".join(trail_rows)
        + "</tbody></table></div></article></section>"
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
      --bg: #eef4f8;
      --card: #ffffff;
      --ink: #0b2740;
      --muted: #4a657a;
      --brand: #003b5c;
      --brand-mid: #005f87;
      --brand-light: #00a3c8;
      --accent: #c8102e;
      --danger: #b42318;
      --line: #c9d9e6;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at 18% 0%, #d4eaf5 0%, transparent 38%),
        radial-gradient(circle at 92% 8%, #fde8eb 0%, transparent 28%),
        var(--bg);
    }}
    .wrap {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}
    .head {{
      background: linear-gradient(135deg, #003b5c 0%, #005f87 55%, #0077a3 100%);
      color: #ffffff;
      border-radius: 18px;
      padding: 24px;
      box-shadow: 0 10px 26px rgba(0, 59, 92, 0.28);
      border-bottom: 4px solid var(--accent);
    }}
    .head-top {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
    }}
    .head h1 {{
      margin: 0 0 8px 0;
      font-size: 30px;
      color: #ffffff;
      font-weight: 700;
      letter-spacing: 0.01em;
      text-shadow: 0 1px 2px rgba(0, 20, 40, 0.35);
    }}
    .head p {{
      margin: 6px 0 0 0;
      color: #ffffff;
      font-size: 15px;
      line-height: 1.45;
      opacity: 1;
    }}
    .head p strong {{
      color: #ffffff;
      font-weight: 700;
    }}
    .head .muted {{
      color: #e2f3fa;
      font-size: 14px;
      opacity: 1;
    }}
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
      box-shadow: 0 2px 10px rgba(0, 20, 40, 0.25);
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
      border: 1px solid var(--brand-mid);
      background: #e6f4fa;
      color: var(--brand);
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
      background: #d2ebf5;
    }}
    .btn-export-top {{
      flex-shrink: 0;
      border-color: #ffffff;
      background: #ffffff;
      color: var(--brand);
      box-shadow: 0 2px 8px rgba(0, 20, 40, 0.2);
    }}
    .btn-export-top:hover {{
      background: #e2f3fa;
    }}
    .excel-icon {{
      width: 16px;
      height: 16px;
      border-radius: 3px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: var(--accent);
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
      background: #eef7fb;
      border: 1px solid #a9d4e8;
      border-radius: 14px;
      padding: 14px 16px;
    }}
    .premisa-head {{
      margin: 0 0 8px 0;
      font-size: 16px;
      color: var(--brand);
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
    .balance-formula {{
      margin: 0 0 12px 0;
      padding: 10px 12px;
      border-radius: 10px;
      background: #f8fafc;
      border: 1px solid var(--line);
      font-size: 14px;
      font-variant-numeric: tabular-nums;
    }}
    .check-ok .kpi-value, .balance-formula .check-ok {{
      color: #0a7a4a;
    }}
    .check-warn .kpi-value, .balance-formula .check-warn {{
      color: var(--accent);
    }}
    .card.check-ok {{
      border-color: #86efac;
      background: #ecfdf5;
    }}
    .card.check-warn {{
      border-color: #fecaca;
      background: #fef2f2;
    }}
    .check-warn-cell {{
      color: var(--accent);
      font-weight: 600;
    }}
    .nota-alerta-vap-inline {{
      margin-top: 12px;
      padding: 10px 12px;
      border-left: 4px solid #b45309;
      background: #fffbeb;
      color: #78350f;
    }}
    .nota-alerta-vap {{
      margin: 24px 0 8px 0;
      padding: 14px 16px;
      border: 1px solid #f59e0b;
      border-radius: 12px;
      background: #fffbeb;
    }}
    .nota-alerta-vap-titulo {{
      margin: 0 0 6px 0;
      font-size: 14px;
      font-weight: 700;
      color: #92400e;
    }}
    .nota-alerta-vap-texto {{
      margin: 0;
      font-size: 13px;
      line-height: 1.45;
      color: #78350f;
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
    .tabs-bar {{
      margin-top: 18px;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 8px;
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      position: sticky;
      top: 8px;
      z-index: 100;
      box-shadow: 0 4px 12px rgba(2, 6, 23, 0.06);
    }}
    .tab-btn {{
      border: 1px solid transparent;
      background: transparent;
      color: var(--muted);
      border-radius: 10px;
      padding: 10px 14px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.15s, color 0.15s, border-color 0.15s;
    }}
    .tab-btn:hover {{
      background: #e8f3f9;
      color: var(--ink);
    }}
    .tab-btn.active {{
      background: #d9eef8;
      color: var(--brand);
      border-color: #8fc6de;
    }}
    .tab-btn-debug {{
      margin-left: auto;
      color: #64748b;
      font-size: 12px;
      font-weight: 600;
    }}
    .tab-btn-debug:hover {{
      background: #f1f5f9;
      color: #475569;
    }}
    .tab-btn-debug.active {{
      background: #f1f5f9;
      color: #334155;
      border-color: #cbd5e1;
    }}
    .debug-panel-intro {{
      margin: 0 0 14px 0;
      padding: 10px 12px;
      border: 1px dashed #cbd5e1;
      border-radius: 10px;
      background: #f8fafc;
      font-size: 13px;
      color: #64748b;
    }}
    .intro-debug-note {{
      margin: 10px 0 0 0;
      font-size: 12px;
    }}
    .bc-date-cell {{
      white-space: nowrap;
    }}
    .bc-date-meta {{
      font-size: 12px;
    }}
    .bc-toggle {{
      border: 1px solid var(--line);
      background: #ffffff;
      color: var(--brand);
      border-radius: 6px;
      width: 28px;
      height: 28px;
      margin-right: 6px;
      cursor: pointer;
      font-size: 14px;
      line-height: 1;
      vertical-align: middle;
    }}
    .bc-toggle:hover {{
      background: #e8f3f9;
    }}
    .bc-toggle[aria-expanded='true'] {{
      background: #d9eef8;
      border-color: #8fc6de;
    }}
    .bc-date-row {{
      background: #ffffff;
    }}
    .bc-detail-row td {{
      background: #f8fafc;
      padding: 8px 10px 14px 42px;
    }}
    .bc-lot-table {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 10px;
      overflow: hidden;
      background: #ffffff;
    }}
    .bc-lot-table th,
    .bc-lot-table td {{
      font-size: 12px;
      padding: 8px 10px;
    }}
    .bc-lot-table th {{
      background: #eef6ff;
    }}
    .bc-lot-sin-enlace td {{
      color: #64748b;
      background: #fff7ed;
    }}
    .bc-lot-sin-pedido td {{
      background: #fffbeb;
    }}
    .tab-panels {{
      margin-top: 14px;
    }}
    .tab-panel {{
      display: none;
      animation: tabFade 0.2s ease;
    }}
    .tab-panel.active {{
      display: block;
    }}
    @keyframes tabFade {{
      from {{ opacity: 0; transform: translateY(4px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    .intro-card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 20px 22px;
      box-shadow: 0 4px 12px rgba(2, 6, 23, 0.04);
    }}
    .intro-card h2 {{
      margin: 0 0 10px 0;
      font-size: 22px;
      color: var(--brand);
    }}
    .intro-lead {{
      margin: 0 0 16px 0;
      font-size: 15px;
      line-height: 1.55;
      color: #334155;
    }}
    .intro-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(280px, 1fr));
      gap: 18px;
      margin-bottom: 18px;
    }}
    .intro-grid h3,
    .intro-snapshot h3 {{
      margin: 0 0 8px 0;
      font-size: 15px;
      color: var(--brand-mid);
    }}
    .intro-list {{
      margin: 0;
      padding-left: 20px;
      font-size: 13px;
      line-height: 1.55;
      color: #334155;
    }}
    .intro-list li {{ margin: 5px 0; }}
    .intro-snapshot {{
      border-top: 1px solid var(--line);
      padding-top: 16px;
      margin-bottom: 12px;
    }}
    .intro-kpis {{
      display: grid;
      grid-template-columns: repeat(4, minmax(140px, 1fr));
      gap: 10px;
      margin-top: 12px;
    }}
    .intro-kpis div {{
      background: #f8fafc;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px 12px;
    }}
    .intro-kpi-label {{
      display: block;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--muted);
      margin-bottom: 4px;
    }}
    .intro-hint {{
      margin: 0;
      font-size: 13px;
    }}
    .tab-panel .tables {{
      grid-template-columns: 1fr;
      margin-top: 0;
    }}
    .tab-panel .grid,
    .tab-panel .charts {{
      margin-top: 0;
    }}
    .tab-panel .chart-card table {{
      display: block;
      overflow-x: auto;
    }}
    @media (max-width: 1080px) {{
      .intro-grid, .intro-kpis {{ grid-template-columns: 1fr; }}
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
          Exportar todo en Excel
        </button>
      </div>
      <p>Periodo: <strong>{format_date_es(start)}</strong> a <strong>{format_date_es(end)}</strong></p>
    </section>

    <nav class="tabs-bar" role="tablist" aria-label="Capítulos del informe">
      <button type="button" class="tab-btn active" role="tab" aria-selected="true" aria-controls="tab-intro" data-tab="tab-intro" id="tab-btn-intro">Introducción</button>
      <button type="button" class="tab-btn" role="tab" aria-selected="false" aria-controls="tab-resumen" data-tab="tab-resumen" id="tab-btn-resumen">Resumen</button>
      <button type="button" class="tab-btn" role="tab" aria-selected="false" aria-controls="tab-graficas" data-tab="tab-graficas" id="tab-btn-graficas">Gráficas</button>
      <button type="button" class="tab-btn" role="tab" aria-selected="false" aria-controls="tab-detalle" data-tab="tab-detalle" id="tab-btn-detalle">Detalle diario</button>
      <button type="button" class="tab-btn" role="tab" aria-selected="false" aria-controls="tab-balance" data-tab="tab-balance" id="tab-btn-balance">Balance</button>
      <button type="button" class="tab-btn" role="tab" aria-selected="false" aria-controls="tab-bc" data-tab="tab-bc" id="tab-btn-bc">Cruce BC</button>
      <button type="button" class="tab-btn" role="tab" aria-selected="false" aria-controls="tab-bc-balance" data-tab="tab-bc-balance" id="tab-btn-bc-balance">Balance BC E/G</button>
      <button type="button" class="tab-btn" role="tab" aria-selected="false" aria-controls="tab-bc-tipo-cajas" data-tab="tab-bc-tipo-cajas" id="tab-btn-bc-tipo-cajas">Balance por tipo (cajas)</button>
      <button type="button" class="tab-btn" role="tab" aria-selected="false" aria-controls="tab-bc-stock-ini" data-tab="tab-bc-stock-ini" id="tab-btn-bc-stock-ini">Stock inicial BC E/G</button>
      <button type="button" class="tab-btn" role="tab" aria-selected="false" aria-controls="tab-bc-stock-fin" data-tab="tab-bc-stock-fin" id="tab-btn-bc-stock-fin">Stock final BC E/G</button>
      <button type="button" class="tab-btn" role="tab" aria-selected="false" aria-controls="tab-materiales" data-tab="tab-materiales" id="tab-btn-materiales">Materiales</button>
      <button type="button" class="tab-btn tab-btn-debug" role="tab" aria-selected="false" aria-controls="tab-debug" data-tab="tab-debug" id="tab-btn-debug">Debug</button>
    </nav>

    <div class="tab-panels">
      <section class="tab-panel active" id="tab-intro" role="tabpanel" aria-labelledby="tab-btn-intro">
        {intro_html}
      </section>

      <section class="tab-panel" id="tab-resumen" role="tabpanel" aria-labelledby="tab-btn-resumen">
        <section class="grid">
          <article class="card"><div class="kpi-title">Entradas TINA (kg)</div><div class="kpi-value">{fmt_num(k['kg_entrada_tina'])}</div><div class="kpi-sub">{k['packs_entrada']} Nº de Tinas</div></article>
          <article class="card"><div class="kpi-title">TINA procesada (kg)</div><div class="kpi-value">{fmt_num(k['kg_consumo_tina'])}</div><div class="kpi-sub">{k['movs_consumo']} movimientos · entrada, no CAJA</div></article>
          <article class="card"><div class="kpi-title">Salidas CAJA (kg)</div><div class="kpi-value">{fmt_num(k['kg_salida_no_tina'])}</div><div class="kpi-sub">{k['packs_salida']} Nº de Cajas</div></article>
          <article class="card"><div class="kpi-title">Stock de tinas (kg)</div><div class="kpi-value">{fmt_num(k.get('kg_stock_entrada', 0))}</div><div class="kpi-sub">Premisa 4 · rtype 1 · SUM(weight)</div></article>
          <article class="card"><div class="kpi-title">Merma (kg)</div><div class="kpi-value">{fmt_num(k.get('kg_merma', 0))}</div><div class="kpi-sub">Entradas − Salidas − Stock tinas · {fmt_pct(k.get('pct_merma'))}</div></article>
          <article class="card"><div class="kpi-title">Stock inventario (kg)</div><div class="kpi-value">{fmt_num(k.get('kg_stock_inventario', 0))}</div><div class="kpi-sub">Premisa 7 pendiente (arrastre provisional)</div></article>
          <article class="card"><div class="kpi-title">Balance TINA − CAJA (kg)</div><div class="kpi-value">{fmt_num(k['kg_balance_entrada_salida'])}</div><div class="kpi-sub">Entradas TINA − Salidas CAJA</div></article>
          <article class="card"><div class="kpi-title">Acum. stock tinas periodo (kg)</div><div class="kpi-value">{fmt_num(k['kg_stock_sin_procesar_fin'])}</div><div class="kpi-sub">Suma diaria stock premisa 4 (no inventario)</div></article>
          <article class="card"><div class="kpi-title">BC lotes enlazados</div><div class="kpi-value">{int(k.get('bc_lotes_enlazados', 0)):,} / {int(k.get('bc_lotes_innova', 0)):,}</div><div class="kpi-sub">{fmt_pct(k.get('bc_pct_lotes_enlazados'))} · number = Lot No.</div></article>
          <article class="card"><div class="kpi-title">Kg Innova enlazado</div><div class="kpi-value">{fmt_num(k.get('bc_kg_innova_enlazado', 0))}</div><div class="kpi-sub">Salidas Innova con lote en BC</div></article>
          <article class="card"><div class="kpi-title">BC Kilos enlazados (ILE)</div><div class="kpi-value">{fmt_num(k.get('bc_kg_bc_enlazado', 0))}</div><div class="kpi-sub">Campo [Kilos] · dif.: {fmt_num(k.get('bc_kg_diferencia_enlazado', 0))} kg</div></article>
          <article class="card"><div class="kpi-title">BC con pedido</div><div class="kpi-value">{fmt_num(k.get('bc_qty_con_pedido', 0))} ud.</div><div class="kpi-sub">{fmt_num(k.get('bc_kg_con_pedido', 0))} kg · sin pedido: {fmt_num(k.get('bc_qty_sin_pedido', 0))} ud.</div></article>
          {stock_cards_html}
        </section>
      </section>

      <section class="tab-panel" id="tab-graficas" role="tabpanel" aria-labelledby="tab-btn-graficas">
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
            <h3>Stock y merma diarios (balance de masa)</h3>
            <button type="button" class="btn-max" data-chart="lineStockMerma">Maximizar</button>
            <canvas id="lineStockMerma"></canvas>
          </article>
          <article class="chart-card chart-panel">
            <h3>Diferencia entre entradas y salidas (kg)</h3>
            <button type="button" class="btn-max" data-chart="barBalance">Maximizar</button>
            <canvas id="barBalance"></canvas>
          </article>
        </section>
      </section>

      <section class="tab-panel" id="tab-detalle" role="tabpanel" aria-labelledby="tab-btn-detalle">
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
                  <th class="num">Entradas TINA (kg)</th>
                  <th class="num">TINA procesada (kg)</th>
                  <th class="num">Diferencia</th>
                  <th class="num">% Diferencia</th>
                  <th class="num">Salidas CAJA (kg)</th>
                  <th class="num">Balance TINA−CAJA</th>
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
        </section>
      </section>

      <section class="tab-panel" id="tab-balance" role="tabpanel" aria-labelledby="tab-btn-balance">
        {arrastre_trail_html}
        <section class="tables">
          <article class="chart-card">
            <div class="section-head">
              <h3>Entradas, salidas, stock y merma</h3>
              <button type="button" class="btn-export" data-table-id="stockMermaTable" data-file-name="entradas_salidas_stock_merma">
                <span class="excel-icon">X</span>
                Exportar Excel
              </button>
            </div>
            <p class="muted" style="margin-top:0;">
              Balance de masa: Entrada TINA = Salidas CAJA + Stock de tinas + Merma.
              Stock de tinas = premisa 4 (consulta directa, rtype 1).
            </p>
            <table id="stockMermaTable">
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th class="num">Entradas (kg)</th>
                  <th class="num">Salidas (kg)</th>
                  <th class="num">Stock tinas (kg)</th>
                  <th class="num">Merma (kg)</th>
                  <th class="num">Balance E-S (kg)</th>
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
        </section>
      </section>

      <section class="tab-panel" id="tab-bc" role="tabpanel" aria-labelledby="tab-btn-bc">
        <section class="tables">
          {bc_cruce_section_html}
        </section>
      </section>

      <section class="tab-panel" id="tab-bc-balance" role="tabpanel" aria-labelledby="tab-btn-bc-balance">
        {bc_balance_section_html}
      </section>

      <section class="tab-panel" id="tab-bc-tipo-cajas" role="tabpanel" aria-labelledby="tab-btn-bc-tipo-cajas">
        {bc_balance_tipo_cajas_html}
      </section>

      <section class="tab-panel" id="tab-bc-stock-ini" role="tabpanel" aria-labelledby="tab-btn-bc-stock-ini">
        {bc_stock_inicial_html}
      </section>

      <section class="tab-panel" id="tab-bc-stock-fin" role="tabpanel" aria-labelledby="tab-btn-bc-stock-fin">
        {bc_stock_final_html}
      </section>

      <section class="tab-panel" id="tab-materiales" role="tabpanel" aria-labelledby="tab-btn-materiales">
        <section class="tables">
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
      </section>

      <section class="tab-panel" id="tab-debug" role="tabpanel" aria-labelledby="tab-btn-debug">
        <p class="debug-panel-intro">
          Auditoría técnica: premisas aplicadas en esta ejecución y consultas SQL reproducibles.
          Documento canónico de reglas: <strong>PREMISAS.md</strong>.
        </p>
        {premisa_entrada_html}
        <section class="trace-box">
          <h3 class="trace-head">Trazabilidad SQL</h3>
          <p class="trace-meta">Origen: <strong>{html.escape(str(sql_trace.get('data_source', data_source)))}</strong></p>
          <p class="trace-meta">Tablas/Vistas: <strong>{html.escape(trace_tables)}</strong></p>
          <p class="trace-meta">Parametros: <strong>start={html.escape(str(trace_params.get('start', '-')))}</strong>, <strong>end={html.escape(str(trace_params.get('end', '-')))}</strong></p>
          {trace_queries_html}
        </section>
      </section>
    </div>

    {nota_alerta_vap_html}

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
const stockEntrada = {json.dumps(stock_entrada_chart)};
const merma = {json.dumps(merma)};
const balance = entrada.map((v, i) => Number((v - salida[i]).toFixed(2)));

const chartConfigs = {{
  lineKg: {{
    type: 'line',
    data: {{
      labels,
      datasets: [
        {{ label: 'Entradas TINA (kg)', data: entrada, borderColor: '#003b5c', tension: 0.25, fill: false }},
        {{ label: 'TINA procesada (kg)', data: consumo, borderColor: '#c8102e', tension: 0.25, fill: false }},
        {{ label: 'Salidas CAJA (kg)', data: salida, borderColor: '#00a3c8', tension: 0.25, fill: false }}
      ]
    }},
    options: {{ responsive: true, maintainAspectRatio: false }}
  }},
  comboDiff: {{
    data: {{
      labels,
      datasets: [
        {{ type: 'bar', label: 'Diferencia diaria', data: diferencia, backgroundColor: diferencia.map(v => v >= 0 ? 'rgba(0,95,135,.7)' : 'rgba(200,16,46,.7)') }},
        {{ type: 'line', label: 'Acumulado diferencia', data: acumulado, borderColor: '#00a3c8', tension: 0.2, yAxisID: 'y1' }}
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
      labels: ['Stock entrada', 'Merma', 'Salidas CAJA'],
      datasets: [{{
        data: [
          {k.get('kg_stock_entrada', 0):.2f},
          {max(k.get('kg_merma', 0), 0):.2f},
          {k['kg_salida_no_tina']:.2f}
        ],
        backgroundColor: ['#003b5c', '#c8102e', '#00a3c8']
      }}]
    }},
    options: {{ responsive: true, maintainAspectRatio: false }}
  }},
  lineStockMerma: {{
    type: 'line',
    data: {{
      labels,
      datasets: [
        {{ label: 'Stock de tinas', data: stockEntrada, borderColor: '#003b5c', tension: 0.25, fill: false }},
        {{ label: 'Merma (balance)', data: merma, borderColor: '#c8102e', tension: 0.25, fill: false }}
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
        backgroundColor: balance.map(v => v >= 0 ? 'rgba(0,95,135,.75)' : 'rgba(200,16,46,.75)')
      }}]
    }},
    options: {{ responsive: true, maintainAspectRatio: false }}
  }}
}};

const chartInstances = {{}};
for (const [chartId, chartConfig] of Object.entries(chartConfigs)) {{
  chartInstances[chartId] = new Chart(document.getElementById(chartId), chartConfig);
}}

function activateTab(tabId) {{
  if (!tabId) return;
  document.querySelectorAll('.tab-panel').forEach((panel) => {{
    panel.classList.toggle('active', panel.id === tabId);
  }});
  document.querySelectorAll('.tab-btn').forEach((btn) => {{
    const isActive = btn.getAttribute('data-tab') === tabId;
    btn.classList.toggle('active', isActive);
    btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
  }});
  if (tabId === 'tab-graficas') {{
    Object.values(chartInstances).forEach((chart) => chart.resize());
  }}
  if (location.hash !== `#${{tabId}}`) {{
    history.replaceState(null, '', `#${{tabId}}`);
  }}
}}

document.querySelectorAll('.tab-btn').forEach((btn) => {{
  btn.addEventListener('click', () => {{
    activateTab(btn.getAttribute('data-tab'));
  }});
}});

const initialTab = location.hash ? location.hash.slice(1) : 'tab-intro';
if (document.getElementById(initialTab)) {{
  activateTab(initialTab);
}} else {{
  activateTab('tab-intro');
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
    {{ sheetName: 'Check BC diario', tableId: 'bcCheckDiarioTable' }},
    {{ sheetName: 'Balance BC E/G', tableId: 'bcBalanceEgTable' }},
    {{ sheetName: 'BC E/G por tipo', tableId: 'bcBalanceEgTipoTable' }},
    {{ sheetName: 'BC diario cajas', tableId: 'bcBalanceDiarioCajasTable' }},
    {{ sheetName: 'BC tipo cajas', tableId: 'bcBalanceTipoCajasTable' }},
    {{ sheetName: 'Stock ini BC', tableId: 'bcStockInicialProductoTable' }},
    {{ sheetName: 'Stock fin BC', tableId: 'bcStockFinalProductoTable' }},
    {{ sheetName: 'BC E/G por lote', tableId: 'bcBalanceEgLoteTable' }},
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

document.querySelectorAll('.bc-toggle').forEach((btn) => {{
  btn.addEventListener('click', () => {{
    const detailId = btn.getAttribute('aria-controls');
    const detailRow = detailId ? document.getElementById(detailId) : null;
    if (!detailRow) return;
    const expanded = btn.getAttribute('aria-expanded') === 'true';
    btn.setAttribute('aria-expanded', expanded ? 'false' : 'true');
    btn.textContent = expanded ? '▸' : '▾';
    detailRow.hidden = expanded;
  }});
}});
</script>
</body>
</html>
"""


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    loaded_env = load_app_credentials(base_dir)
    logo_data_uri = load_logo_data_uri(base_dir)
    args = parse_args()
    if loaded_env:
        print("Credenciales cargadas desde: " + ", ".join(str(p) for p in loaded_env))
    stock_inicial_manual, stock_inicial_auto = parse_stock_inicial_arg(args.stock_inicial)
    use_arrastre = args.arrastre_mensual or stock_inicial_auto
    if args.stock_final_fisico is not None and not use_arrastre and stock_inicial_manual is None:
      raise ValueError(
          "Si informa --stock-final-fisico tambien debe informar --stock-inicial "
          "o activar --arrastre-mensual / --stock-inicial auto"
      )
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
        stock_inicial = stock_inicial_manual
        arrastre_trail: list[dict[str, Any]] = []
        if use_arrastre:
            ancla, ancla_kg = resolve_stock_ancla(args, start)
            print(
                f"Calculando arrastre mensual (ancla {format_date_es(ancla)} = {fmt_num(ancla_kg)} kg)..."
            )
            stock_inicial, arrastre_trail = compute_stock_apertura_arrastre(
                conn, start, args.data_source, ancla, ancla_kg
            )
            tinas_arrastradas = fetch_tinas_arrastradas(conn, start, end)
            enrich_arrastre_mensual(report_data, stock_inicial, arrastre_trail, tinas_arrastradas)
        enrich_stock_validation(report_data, stock_inicial, args.stock_final_fisico)
        finalize_balance_kpis(report_data)
        innova_lotes = fetch_innova_salidas_lotes(conn, start, end)
        innova_lotes_material = fetch_innova_lotes_por_material(conn, start, end)
    finally:
        conn.close()

    if not args.skip_bc:
        bc_server, bc_database, bc_user, bc_password = resolve_bc_credentials(args)
        bc_balance_timeout = max(args.bc_timeout * 2, 1800)
        try:
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
                print("Cruce BC cargado correctamente.")
            finally:
                bc_conn.close()
        except Exception as bc_exc:
            print(f"Aviso: no se pudo cargar cruce BC ({bc_exc}).")

        try:
            print(
                f"Conectando a Business Central (balance E/G, timeout {bc_balance_timeout}s)..."
            )
            bc_balance_conn = pymssql.connect(
                server=bc_server,
                user=bc_user,
                password=bc_password,
                database=bc_database,
                login_timeout=args.bc_login_timeout,
                timeout=bc_balance_timeout,
            )
            try:
                print("Consultando balance BC almacenes E/G...")
                bc_balance = fetch_bc_balance_eg(bc_balance_conn, start, end)
                print("Consultando Conversion productos (Cod. bascula -> Cod. producto)...")
                conversion_productos = fetch_bc_conversion_productos(bc_balance_conn)
                attach_bc_balance_eg_to_report(
                    report_data,
                    bc_balance,
                    innova_lotes,
                    innova_lotes_material,
                    conversion_productos,
                )
                print("Balance BC E/G cargado correctamente.")
            finally:
                bc_balance_conn.close()
        except Exception as bc_balance_exc:
            print(f"Aviso: no se pudo cargar balance BC E/G ({bc_balance_exc}).")
            print("El reporte conserva el cruce BC si estaba disponible.")

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
