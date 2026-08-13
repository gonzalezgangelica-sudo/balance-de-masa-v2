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
    parser.add_argument(
      "--bc-source",
      choices=["api", "sql"],
      default=None,
      help=(
        "Fuente BC: api (OAuth + API AL/OData + enrich Innova) o sql (Azure SQL). "
        "Default: variable BC_SOURCE o sql."
      ),
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


def resolve_bc_source(args: argparse.Namespace) -> str:
    raw = (getattr(args, "bc_source", None) or os.getenv("BC_SOURCE") or "sql").strip().lower()
    if raw not in ("api", "sql"):
        raise ValueError(f"BC_SOURCE invalido '{raw}' (use api|sql).")
    return raw


def empty_bc_conversion_productos() -> dict[str, Any]:
    return {
        "by_bascula": {},
        "productos": [],
        "rows": [],
        "sql_trace": {
            "view_or_tables": [],
            "queries": [
                {
                    "name": "bc_conversion_productos",
                    "query": "omitido (BC_SOURCE=api sin SQL Conversion productos)",
                }
            ],
        },
    }


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
          f"Balance BC E/G/Z — Stock inicial: {fmt_num(k['bc_bal_kg_stock_inicial'])} | "
          f"{LABEL_BC_PRODUCCION}: {fmt_num(k['bc_bal_kg_produccion'])} | "
          f"Altas BC: {fmt_num(k.get('bc_bal_kg_produccion_bc', 0))} | "
          f"Primera salida: {fmt_num(k['bc_bal_kg_ventas'])} | "
          f"Stock teorico: {fmt_num(k['bc_bal_kg_stock_teorico'])} | "
          f"Stock real: {fmt_num(k['bc_bal_kg_stock_real'])} | "
          f"Desvio: {fmt_num(k.get('bc_bal_desvio_kg', k['bc_bal_kg_check']))} kg "
          f"({fmt_pct(k.get('bc_bal_desvio_pct')) if k.get('bc_bal_desvio_pct') is not None else 'N/A'}) "
          f"[{k.get('bc_bal_semaforo') or '—'}] | "
          f"{LABEL_BC_MERMA_PESO}: {fmt_num(k.get('bc_bal_kg_merma_peso', 0))} "
          f"({fmt_pct(k.get('bc_bal_pct_merma_peso'))})"
        )
    if k.get("bc_bal_cajas_stock_inicial") is not None:
      adj_neg = int(k.get("bc_bal_cajas_ajustes_neg") or 0)
      est = k.get("bc_bal_cajas_estado") or "—"
      sem = k.get("bc_bal_cajas_semaforo") or "—"
      n_desv = int(k.get("bc_bal_cajas_productos_desvio") or 0)
      print(
          f"Balance por tipo (cajas) — Stock inicial: {int(k['bc_bal_cajas_stock_inicial']):,} | "
          f"{LABEL_BC_PRODUCCION}: {int(k['bc_bal_cajas_entradas']):,} | "
          f"Ventas: {int(k['bc_bal_cajas_ventas']):,} | "
          f"Ajustes neg.: {adj_neg:,} | "
          f"Teorico: {int(k['bc_bal_cajas_stock_teorico']):,} | "
          f"Real: {int(k['bc_bal_cajas_stock_real']):,} | "
          f"Check: {int(k['bc_bal_cajas_check']):,} cajas "
          f"[estado {est} / {sem}; productos con desvio: {n_desv}]"
      )
    if k.get("bc_adj_neg_cajas") is not None:
      print(
          f"Analisis ajustes neg. — Cajas: {int(k.get('bc_adj_neg_cajas') or 0):,} | "
          f"Usuarios: {int(k.get('bc_adj_neg_usuarios') or 0):,} | "
          f"Top usuario: {k.get('bc_adj_neg_top_usuario') or '—'}"
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
  "Almacenes BC: Location Code E, G y Z.",
  "Cruce kg: peso salida Innova (proc_packs.weight) vs [Kilos] BC en lotes enlazados.",
  "Ver PREMISAS.md — Premisa 6 (premisa legacy BC).",
)
SQL_BC_SALIDA_CON_PEDIDO = "NULLIF(LTRIM(RTRIM(sl.[Order No.])), '') IS NOT NULL"
SQL_BC_SALIDA_SIN_PEDIDO = "NULLIF(LTRIM(RTRIM(sl.[Order No.])), '') IS NULL"
SQL_BC_ILE_SALE = "ile.[Entry Type] = 1"
SQL_BC_ILE_POS_ADJ = "ile.[Entry Type] = 2"
SQL_BC_ILE_NEG_ADJ = "ile.[Entry Type] = 3"
SQL_BC_ILE_SALE_OR_NEG_ADJ = "ile.[Entry Type] IN (1, 3)"
# Almacenes de stock del informe: E, G y Z.
BC_STOCK_LOCATIONS: tuple[str, ...] = ("E", "G", "Z")
SQL_BC_LOCATION_EG = (
    "ile.[Location Code] IN ("
    + ", ".join(f"'{loc}'" for loc in BC_STOCK_LOCATIONS)
    + ")"
)
SQL_BC_ILE_OUTPUT = "ile.[Entry Type] = 4"
SQL_INNOVA_LOT = "CAST(p.number AS varchar(50))"
BC_BALANCE_CHECK_TOLERANCE_KG = 5000.0  # legado (auditorías); el semáforo usa %
# Semáforo desvío stock: |desvío %| respecto al teórico.
BC_DESVIO_PCT_VERDE = 0.5
BC_DESVIO_PCT_AMARILLO = 1.0
# Etiqueta de produccion en balance E/G/Z (= salidas CAJA Innova, premisa 3).
LABEL_BC_PRODUCCION = "Produccion (Salidas CAJA)"
LABEL_BC_MERMA_PESO = "Merma peso (Innova - BC)"
# Definicion canonica: produccion del teorico = Innova CAJA; comparativa vs alta BC.
DEF_BC_PRODUCCION = (
    "Produccion del teorico = Salidas CAJA Innova (proc_packs). "
    "Comparativa aparte: Innova CAJA vs altas/empaque BC E/G/Z. "
    "1 lote = 1 caja; kg = peso del lote."
)
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
  "Almacenes BC: Location Code E, G y Z.",
  "Fecha empaque: campo [Fecha empaque] en Item Ledger Entry.",
  "Stock inicial (dia): ILE — [Fecha empaque] anterior al dia y sin venta/ajuste neg. (Entry Type 1/3) antes de ese dia (salida ese dia o despues, o sin salida). Misma regla en dia/semana/mes respecto a la fecha inicio.",
  "Stock final teorico (dia): Stock inicial BC + Produccion Innova CAJA − Primera salida BC (Type 1/3 una vez).",
  "Stock final real (dia): snapshot BC E/G/Z — empaque <= dia y sin Type 1/3 hasta ese dia (incluye arrastre). Misma regla al cierre de dia/semana/mes.",
  "Encadenamiento teorico: stock final teorico del dia N = stock inicial teorico del dia N+1.",
  "Produccion (Salidas CAJA) teorico: todas las salidas CAJA Innova del dia. Comparativa: Innova CAJA vs altas/empaque BC E/G/Z.",
  "Primera salida: primera venta (Type 1) o ajuste negativo (Type 3) del lote — una sola vez.",
  "Stock apertura: produccion anterior al periodo sin venta/ajuste neg. antes del periodo (E/G/Z).",
  "Desvio kg = Stock real BC − Stock teorico. Desvio % = desvio kg / teorico × 100.",
  "Semaforo desvio: verde |%|≤0,5; amarillo 0,5<|%|≤1; rojo |%|>1 (teorico=0 y real≠0 → rojo).",
  "Alcance check mensual: desglose por producto y etapa (Inicial / Produccion / Salidas / Ajustes / Stock final).",
  "Historico ILE acotado desde 2026-01-01 para evitar timeout en BC.",
  "Fines de semana: sin Produccion (Salidas CAJA) ni Primera salida (0); stocks se arrastran del dia anterior.",
  "Merma peso (Innova - BC): peso Innova enlazado − kilos BC del mismo lote (no sustituye el desvio de stock).",
  "Desglose por tipo de producto BC: prioridad Item No. del lote ILE; Conversion bascula solo si no hay Item No.",
  "Balance kg y cajas: teorico matematico vs real BC. Columnas ILE Type 2/1/3 (ABS) son auditoria informativa.",
  "Encadenamiento en cajas: stock final teorico del dia N = stock inicial teorico del dia N+1.",
  "Stock inicial BC E/G/Z por producto: corte a fecha inicio; empaque anterior; sin Type 1/3 antes del inicio; cajas y kg.",
  "Stock final BC E/G/Z por producto: almacen completo al fin (empaque <= fin, sin Type 1/3 hasta fin); incluye arrastre; cajas y kg.",
  "CHECK cajas = real − teorico. Estado A correcto; B total 0 con productos CHECK≠0 (compensado); C total ≠ 0.",
  "Producto Innova: Item No. BC si el lote esta en ILE; conversion solo para lotes solo-Innova.",
  "Ajustes negativos (Entry Type 3): marcan la primera salida del lote (junto con Type 1). En el teorico no se resta Quantity aparte.",
)

# Limitacion conocida — debe mostrarse en todos los resultados (ver PREMISAS.md).
NOTA_ALERTA_VAP = (
  "Nota: El producto VAP entra por tinas pero no se procesa; se acumula en stock de tinas "
  "de forma ficticia y distorsiona entradas, stock y merma. "
  "Limitacion conocida — sin correccion disponible de momento."
)
NOTA_ALERTA_VAP_TITULO = "Alerta — limitacion VAP en stock de tinas"

NOTA_BC_AJUSTES_NEGATIVOS = (
  "Ajustes negativos BC (Entry Type = 3): marcan salida del lote para stock inicial/real "
  "(igual que la venta Type 1). "
  "Kg y cajas usan la misma medida de lote: el teórico cuenta empaque y primera salida "
  "(1 lote = 1 caja / kg del lote); Type 3 no añade una resta extra por Quantity."
)
NOTA_BC_AJUSTES_NEGATIVOS_TITULO = "Ajustes negativos BC (Entry Type 3)"


def build_nota_alerta_vap_html() -> str:
    return (
        "<footer class='nota-alerta-vap' role='note'>"
        f"<p class='nota-alerta-vap-titulo'>{html.escape(NOTA_ALERTA_VAP_TITULO)}</p>"
        f"<p class='nota-alerta-vap-texto'>{html.escape(NOTA_ALERTA_VAP)}</p>"
        "</footer>"
    )


def build_nota_bc_ajustes_negativos_html() -> str:
    return (
        "<footer class='nota-bc-ajustes' role='note'>"
        f"<p class='nota-bc-ajustes-titulo'>{html.escape(NOTA_BC_AJUSTES_NEGATIVOS_TITULO)}</p>"
        f"<p class='nota-bc-ajustes-texto'>{html.escape(NOTA_BC_AJUSTES_NEGATIVOS)}</p>"
        "</footer>"
    )


def build_report_footnotes_html() -> str:
    """Notas globales del informe (una sola vez al pie)."""
    return (
        "<section class='report-footnotes' aria-label='Notas del informe'>"
        f"{build_nota_alerta_vap_html()}"
        f"{build_nota_bc_ajustes_negativos_html()}"
        "</section>"
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
        "<li><strong>Balance BC E/G/Z</strong> — Inicial + Produccion (Salidas CAJA) − Primera salida; "
        "Produccion = alta stock por coincidencia de lote Innova∩BC; merma peso aparte.</li>"
        "<li><strong>Lotes del dia</strong> — solo si el informe es de <em>un dia</em>: "
        "detalle de lotes que coinciden / solo Innova / solo BC (no se genera en semana o mes).</li>"
        "<li><strong>Balance por tipo (cajas)</strong> — misma logica (1 lote = 1 caja).</li>"
        "<li><strong>Movimientos ILE</strong> — auditoría Type 2/1/3 en cajas y kg (ABS Quantity/Kilos).</li>"
        "<li><strong>Análisis ILE</strong> — validación Type 1/2/3, checks kg/cajas e indicadores.</li>"
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

    Prioridad (stock/balance E/G/Z = producto del almacén BC):
      1) Item No. BC del lote (ILE) — misma fuente que stock inicial
      2) bc.Conversion productos: Cod. bascula = material Innova → Cod. producto
      3) Innova pattern si existe como Cod. producto en Conversion
      4) pattern Innova
      5) material Innova

    Conversion sigue como respaldo cuando el lote no trae Item No.; no debe
    sobrescribir el SKU real del movimiento ILE (evita descuadres stock final).
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

    if item_no:
        return item_no, "item_no_bc", ""

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
    # Stock vivo: empaque desde historico hasta fin; salida = cualquier 1/3 hasta fin.
    params_stock_vivo = (
        BC_ILE_HISTORY_FROM.isoformat(),
        end.isoformat(),
        BC_ILE_HISTORY_FROM.isoformat(),
        end.isoformat(),
    )
    params_unsold = params_stock_vivo
    params_lot_snapshot = (
        ile_start.isoformat(),
        end.isoformat(),
        BC_ILE_HISTORY_FROM.isoformat(),
        end.isoformat(),
    )

    def run_step(name: str, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        if verbose:
            print(f"  BC balance E/G/Z: {name}...")
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

    q_ajustes_neg_adj_diario = f"""
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
      AND {SQL_BC_ILE_NEG_ADJ}
      AND {SQL_BC_LOCATION_EG}
      AND NULLIF(LTRIM(RTRIM(CAST(ile.[Item No.] AS varchar(50)))), '') IS NOT NULL
    GROUP BY CAST(ile.[Posting Date] AS date), LTRIM(RTRIM(CAST(ile.[Item No.] AS varchar(50))))
    ORDER BY fecha, item_no;
    """
    ajustes_neg_adj_diario = run_step(
        "ajustes negativos diario por producto",
        q_ajustes_neg_adj_diario,
        params_period,
    )

    q_ajustes_neg_analisis = f"""
    SELECT
      CAST(ile.[Entry Type] AS int) AS entry_type,
      CAST(ile.[Posting Date] AS date) AS fecha,
      COALESCE(
        NULLIF(LTRIM(RTRIM(CAST(ile.[Id. usuario] AS varchar(100)))), ''),
        '(sin usuario)'
      ) AS usuario,
      LTRIM(RTRIM(CAST(ile.[Item No.] AS varchar(50)))) AS item_no,
      MAX(NULLIF(LTRIM(RTRIM(CAST(ile.[Description] AS varchar(250)))), '')) AS item_description,
      SUM(ABS(CAST(ile.[Quantity] AS float))) AS qty,
      SUM(ABS(CAST(ile.[Kilos] AS float))) AS kg,
      COUNT(*) AS movimientos,
      COUNT(DISTINCT ile.[Lot No.]) AS lotes,
      SUM(CASE WHEN ABS(CAST(ile.[Quantity] AS float)) <> 1 THEN 1 ELSE 0 END) AS mov_qty_ne_1,
      SUM(CASE WHEN ABS(CAST(ile.[Kilos] AS float)) = 0 THEN 1 ELSE 0 END) AS mov_kg_0
    FROM bc.[Item Ledger Entry] ile
    WHERE ile.[Posting Date] >= %s
      AND ile.[Posting Date] < DATEADD(day, 1, %s)
      AND {sql_bc_ile_posting_from()}
      AND ile.[Entry Type] IN (1, 2, 3)
      AND {SQL_BC_LOCATION_EG}
      AND NULLIF(LTRIM(RTRIM(CAST(ile.[Item No.] AS varchar(50)))), '') IS NOT NULL
    GROUP BY
      CAST(ile.[Entry Type] AS int),
      CAST(ile.[Posting Date] AS date),
      COALESCE(
        NULLIF(LTRIM(RTRIM(CAST(ile.[Id. usuario] AS varchar(100)))), ''),
        '(sin usuario)'
      ),
      LTRIM(RTRIM(CAST(ile.[Item No.] AS varchar(50))))
    ORDER BY entry_type, fecha, usuario, item_no;
    """
    ajustes_neg_analisis_raw = run_step(
        "movimientos ILE analisis Type 1/2/3 usuario/dia/producto",
        q_ajustes_neg_analisis,
        params_period,
    )

    q_movimientos_integridad = f"""
    WITH venta AS (
      SELECT DISTINCT CAST(ile.[Lot No.] AS varchar(50)) AS lot
      FROM bc.[Item Ledger Entry] ile
      WHERE ile.[Posting Date] >= %s
        AND ile.[Posting Date] < DATEADD(day, 1, %s)
        AND {sql_bc_ile_posting_from()}
        AND {SQL_BC_ILE_SALE}
        AND {SQL_BC_LOCATION_EG}
        AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
    ),
    neg_adj AS (
      SELECT DISTINCT CAST(ile.[Lot No.] AS varchar(50)) AS lot
      FROM bc.[Item Ledger Entry] ile
      WHERE ile.[Posting Date] >= %s
        AND ile.[Posting Date] < DATEADD(day, 1, %s)
        AND {sql_bc_ile_posting_from()}
        AND {SQL_BC_ILE_NEG_ADJ}
        AND {SQL_BC_LOCATION_EG}
        AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
    ),
    pos_adj AS (
      SELECT DISTINCT CAST(ile.[Lot No.] AS varchar(50)) AS lot
      FROM bc.[Item Ledger Entry] ile
      WHERE ile.[Posting Date] >= %s
        AND ile.[Posting Date] < DATEADD(day, 1, %s)
        AND {sql_bc_ile_posting_from()}
        AND {SQL_BC_ILE_POS_ADJ}
        AND {SQL_BC_LOCATION_EG}
        AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
    )
    SELECT
      (SELECT COUNT(*) FROM venta) AS lotes_venta,
      (SELECT COUNT(*) FROM neg_adj) AS lotes_neg,
      (SELECT COUNT(*) FROM pos_adj) AS lotes_pos,
      (SELECT COUNT(*) FROM venta INNER JOIN neg_adj ON venta.lot = neg_adj.lot) AS lotes_venta_y_neg,
      (SELECT COUNT(*) FROM pos_adj INNER JOIN neg_adj ON pos_adj.lot = neg_adj.lot) AS lotes_pos_y_neg,
      (SELECT COUNT(*) FROM pos_adj INNER JOIN venta ON pos_adj.lot = venta.lot) AS lotes_pos_y_venta;
    """
    integridad_params = (
        start.isoformat(),
        end.isoformat(),
        start.isoformat(),
        end.isoformat(),
        start.isoformat(),
        end.isoformat(),
    )
    movimientos_integridad = run_step(
        "integridad lotes Type 1/2/3 solapes",
        q_movimientos_integridad,
        integridad_params,
    )[0]

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
    WITH mar_salida AS (
      SELECT DISTINCT CAST(ile.[Lot No.] AS varchar(50)) AS lot
      FROM bc.[Item Ledger Entry] ile
      WHERE ile.[Posting Date] >= %s
        AND ile.[Posting Date] < DATEADD(day, 1, %s)
        AND {sql_bc_ile_posting_from()}
        AND {SQL_BC_ILE_SALE_OR_NEG_ADJ}
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
      AND CAST(ile.[Lot No.] AS varchar(50)) NOT IN (SELECT lot FROM mar_salida)
    ORDER BY lot;
    """
    unsold_rows = run_step("lotes sin venta", q_unsold_lots, params_unsold)
    unsold_lots = [str(row["lot"]).strip() for row in unsold_rows]

    q_stock_final_total = f"""
    WITH mar_salida AS (
      SELECT DISTINCT CAST(ile.[Lot No.] AS varchar(50)) AS lot
      FROM bc.[Item Ledger Entry] ile
      WHERE ile.[Posting Date] >= %s
        AND ile.[Posting Date] < DATEADD(day, 1, %s)
        AND {sql_bc_ile_posting_from()}
        AND {SQL_BC_ILE_SALE_OR_NEG_ADJ}
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
        AND CAST(ile.[Lot No.] AS varchar(50)) NOT IN (SELECT lot FROM mar_salida)
      GROUP BY CAST(ile.[Lot No.] AS varchar(50))
    )
    SELECT
      COALESCE(SUM(kg), 0) AS kg,
      COUNT(*) AS lotes
    FROM stock_final_lot;
    """
    stock_final_total = run_step("stock final total", q_stock_final_total, params_unsold)[0]

    q_lots_stock_vivo = f"""
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
    lot_first_out AS (
      SELECT
        CAST(ile.[Lot No.] AS varchar(50)) AS lot,
        MIN(CAST(ile.[Posting Date] AS date)) AS first_out
      FROM bc.[Item Ledger Entry] ile
      WHERE ile.[Posting Date] >= %s
        AND ile.[Posting Date] < DATEADD(day, 1, %s)
        AND {sql_bc_ile_posting_from()}
        AND {SQL_BC_ILE_SALE_OR_NEG_ADJ}
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
      s.first_out
    FROM lot_empaque e
    LEFT JOIN lot_first_out s ON s.lot = e.lot
    ORDER BY e.lot;
    """
    lots_stock_vivo_raw = run_step("lotes stock vivo almacén", q_lots_stock_vivo, params_stock_vivo)
    lots_stock_vivo = [
        {
            "lot": str(row["lot"]).strip(),
            "fe_empaque": row.get("fe_empaque"),
            "kg": to_float(row.get("kg")),
            "first_out": row.get("first_out"),
            "first_sale": row.get("first_out"),
            "item_no": str(row.get("item_no") or "").strip(),
            "item_description": str(row.get("item_description") or "").strip(),
        }
        for row in lots_stock_vivo_raw
    ]

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
    lot_first_out AS (
      SELECT
        CAST(ile.[Lot No.] AS varchar(50)) AS lot,
        MIN(CAST(ile.[Posting Date] AS date)) AS first_sale
      FROM bc.[Item Ledger Entry] ile
      WHERE ile.[Posting Date] >= %s
        AND ile.[Posting Date] < DATEADD(day, 1, %s)
        AND {sql_bc_ile_posting_from()}
        AND {SQL_BC_ILE_SALE_OR_NEG_ADJ}
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
    LEFT JOIN lot_first_out s ON s.lot = e.lot
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
        merge_lots_for_stock_inicial_ile(
            [
                {
                    "lot": s["lot"],
                    "fe_empaque": s.get("fe_empaque"),
                    "kg": s.get("kg"),
                    "first_sale": s.get("first_out") or s.get("first_sale"),
                    "item_no": s.get("item_no") or "",
                    "item_description": s.get("item_description") or "",
                }
                for s in lots_stock_vivo
            ],
            lots_stock_antiguo_mes,
        ),
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
        AND {SQL_BC_ILE_SALE_OR_NEG_ADJ}
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
        AND {SQL_BC_ILE_SALE_OR_NEG_ADJ}
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
        print("  BC balance E/G/Z: stock apertura omitido (consulta historica pesada en BC).")

    trace_queries = [
        {"name": "bc_balance_ventas_diario_eg", "query": q_ventas_diario.strip()},
        {"name": "bc_balance_lots_stock_antiguo_mes_eg", "query": q_lots_stock_antiguo_mes.strip()},
        {"name": "bc_balance_ventas_stock_antiguo_diario_eg", "query": q_ventas_stock_antiguo_diario.strip()},
        {"name": "bc_balance_empaque_diario_eg", "query": q_empaque_diario.strip()},
        {"name": "bc_balance_ventas_por_lote_eg", "query": q_ventas_por_lote.strip()},
        {"name": "bc_balance_entradas_pos_adj_por_lote_eg", "query": q_entradas_pos_adj_por_lote.strip()},
        {"name": "bc_balance_entradas_pos_adj_diario_producto_eg", "query": q_entradas_pos_adj_diario.strip()},
        {"name": "bc_balance_ventas_diario_producto_eg", "query": q_ventas_diario_producto.strip()},
        {"name": "bc_balance_ajustes_neg_adj_diario_producto_eg", "query": q_ajustes_neg_adj_diario.strip()},
        {"name": "bc_balance_ajustes_neg_analisis_eg", "query": q_ajustes_neg_analisis.strip()},
        {"name": "bc_balance_movimientos_integridad_eg", "query": q_movimientos_integridad.strip()},
        {"name": "bc_balance_lot_snapshot_eg", "query": q_lot_snapshot.strip()},
        {"name": "bc_balance_unsold_lots_eg", "query": q_unsold_lots.strip()},
        {"name": "bc_balance_stock_final_eg", "query": q_stock_final_total.strip()},
        {"name": "bc_balance_lots_stock_vivo_eg", "query": q_lots_stock_vivo.strip()},
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
        "lots_stock_vivo": lots_stock_vivo,
        "lotes_apertura": lotes_apertura,
        "ventas_por_lote": ventas_por_lote,
        "entradas_pos_adj_por_lote": entradas_pos_adj_por_lote,
        "entradas_pos_adj_diario": entradas_pos_adj_diario,
        "ventas_diario_producto": ventas_diario_producto,
        "ajustes_neg_adj_diario": ajustes_neg_adj_diario,
        "ajustes_neg_analisis_raw": ajustes_neg_analisis_raw,
        "movimientos_integridad": movimientos_integridad,
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
    """Suma kg de lotes en stock al cierre del dia (empaque <= dia, sin primera salida <= dia)."""
    total = 0.0
    for row in lot_snapshot:
        fe = row.get("fe_empaque")
        out = row.get("first_sale")
        if out is None:
            out = row.get("first_out")
        kg = row.get("kg_bc")
        if kg is None:
            kg = row.get("kg")
        if _lot_in_stock_final(fe, out, day):
            total += to_float(kg)
    return total


def compute_desvio_stock(
    stock_teorico: float,
    stock_real_bc: float,
) -> dict[str, Any]:
    """Desvío = real BC − teórico; % sobre teórico; semáforo por bandas %."""
    teo = to_float(stock_teorico)
    real = to_float(stock_real_bc)
    desvio_kg = real - teo
    if abs(teo) < 1e-9:
        desvio_pct: float | None = None
        if abs(desvio_kg) < 1e-9:
            semaforo = "verde"
        else:
            semaforo = "rojo"
    else:
        desvio_pct = (desvio_kg / teo) * 100.0
        abs_pct = abs(desvio_pct)
        if abs_pct <= BC_DESVIO_PCT_VERDE:
            semaforo = "verde"
        elif abs_pct <= BC_DESVIO_PCT_AMARILLO:
            semaforo = "amarillo"
        else:
            semaforo = "rojo"
    return {
        "kg_stock_teorico": teo,
        "kg_stock_real": real,
        "desvio_kg": desvio_kg,
        "desvio_pct": desvio_pct,
        "semaforo": semaforo,
        "check_ok": semaforo == "verde",
    }


def classify_desvio_pct(desvio_pct: float | None, *, desvio_kg: float = 0.0) -> str:
    """Clasifica semáforo a partir del % (o kg si teórico=0 / pct None)."""
    if desvio_pct is None:
        return "verde" if abs(to_float(desvio_kg)) < 1e-9 else "rojo"
    abs_pct = abs(to_float(desvio_pct))
    if abs_pct <= BC_DESVIO_PCT_VERDE:
        return "verde"
    if abs_pct <= BC_DESVIO_PCT_AMARILLO:
        return "amarillo"
    return "rojo"


def build_innova_caja_by_day(
    innova_lotes: list[dict[str, Any]] | None,
    detalle_diario_report: list[dict[str, Any]] | None = None,
) -> dict[dt.date, dict[str, float]]:
    """Salidas CAJA Innova por día: {day: {kg, packs}}."""
    by_day: dict[dt.date, dict[str, float]] = {}
    for row in innova_lotes or []:
        day = sql_row_to_date(row.get("fecha"))
        if day is None:
            continue
        slot = by_day.setdefault(day, {"kg": 0.0, "packs": 0.0})
        slot["kg"] += to_float(row.get("kg"))
        slot["packs"] += to_float(row.get("packs"))
    if by_day:
        return by_day
    # Fallback: totales diarios del informe Innova (sin desglose por lote).
    for det in detalle_diario_report or []:
        day = parse_fecha_es_date(det["fecha"])
        by_day[day] = {
            "kg": to_float(det.get("kg_salida_no_tina")),
            "packs": to_float(det.get("packs_salida")),
        }
    return by_day


def build_lot_item_no_bc_map(lot_detalle: list[dict[str, Any]]) -> dict[str, str]:
    """Mapa lote → Item No. BC cuando el lote aparece en ILE E/G/Z."""
    result: dict[str, str] = {}
    for row in lot_detalle or []:
        lot = str(row.get("lot") or "").strip()
        item_no = str(row.get("item_no") or "").strip()
        if lot and item_no:
            result[lot] = item_no
    return result


def build_innova_caja_por_tipo(
    innova_lotes: list[dict[str, Any]] | None,
    innova_lotes_material: list[dict[str, Any]] | None,
    conversion_by_bascula: dict[str, dict[str, str]] | None = None,
    lot_item_no_bc: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Agrega Innova CAJA por producto.

    Prioridad de código:
      1) Item No. BC del lote si existe en ILE
      2) Conversion / pattern / material solo si el lote no está en BC (solo Innova)
    """
    item_map = lot_item_no_bc or {}
    material_by_lot: dict[str, dict[str, Any]] = {}
    for row in innova_lotes_material or []:
        lot = str(row.get("lot") or "").strip()
        if lot:
            material_by_lot[lot] = row

    by_tipo: dict[str, dict[str, Any]] = {}

    def _add(lot: str, kg: float, packs: int) -> None:
        meta = material_by_lot.get(lot, {})
        material = str(meta.get("material") or "").strip()
        pattern = str(meta.get("pattern") or "").strip()
        material_nombre = str(meta.get("material_nombre") or "").strip()
        item_no = str(item_map.get(lot) or "").strip()
        cod, origen, conv_desc = resolve_cod_producto_bc(
            material, pattern, item_no, conversion_by_bascula
        )
        key = (cod or material or pattern or "(sin tipo)").strip() or "(sin tipo)"
        bucket = by_tipo.setdefault(
            key,
            {
                "tipo_key": key,
                "tipo_nombre": (
                    (str(meta.get("item_description") or "").strip() if item_no else "")
                    or material_nombre
                    or conv_desc
                    or key
                ),
                "cod_producto": key,
                "material": material,
                "pattern": pattern,
                "item_no": item_no,
                "enlace_origen": origen,
                "kg": 0.0,
                "packs": 0,
            },
        )
        bucket["kg"] += to_float(kg)
        bucket["packs"] += int(packs)
        if item_no and not bucket.get("item_no"):
            bucket["item_no"] = item_no
            bucket["enlace_origen"] = origen
        if not bucket.get("tipo_nombre") and material_nombre:
            bucket["tipo_nombre"] = material_nombre

    for row in innova_lotes or []:
        lot = str(row.get("lot") or "").strip()
        if not lot:
            continue
        _add(lot, to_float(row.get("kg")), int(round(to_float(row.get("packs")))))

    if not by_tipo and innova_lotes_material:
        for meta in innova_lotes_material:
            lot = str(meta.get("lot") or "").strip()
            if not lot:
                continue
            _add(lot, to_float(meta.get("kg_innova")), int(meta.get("packs") or 0))
    return by_tipo


def classify_cajas_balance_estado(
    cajas_check: int,
    detalle_por_tipo_cajas: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Clasifica el balance de cajas: A correcto, B compensado, C desvío real.

    CHECK por producto y global = real − teórico.
    """
    detalle = detalle_por_tipo_cajas or []
    con_desvio = [
        row for row in detalle if int(row.get("cajas_check") or 0) != 0
    ]
    n_desvio = len(con_desvio)
    total = int(cajas_check)
    if total != 0:
        estado = "C"
        semaforo = "rojo"
        label = "Desvio real (total <> 0)"
    elif n_desvio == 0:
        estado = "A"
        semaforo = "verde"
        label = "Balance correcto"
    else:
        estado = "B"
        semaforo = "amarillo"
        label = "Desvio por producto compensado"
    return {
        "estado": estado,
        "semaforo": semaforo,
        "label": label,
        "cajas_check": total,
        "productos_con_desvio": n_desvio,
        "productos_ok": len(detalle) - n_desvio,
        "check_ok": estado == "A",
    }


def find_compensated_cajas_pairs(
    detalle_por_tipo_cajas: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Detecta pares de productos con CHECK opuesto de la misma magnitud (±X)."""
    by_mag: dict[int, dict[str, list[dict[str, Any]]]] = {}
    for row in detalle_por_tipo_cajas or []:
        check = int(row.get("cajas_check") or 0)
        if check == 0:
            continue
        mag = abs(check)
        slot = by_mag.setdefault(mag, {"neg": [], "pos": []})
        if check < 0:
            slot["neg"].append(row)
        else:
            slot["pos"].append(row)

    pairs: list[dict[str, Any]] = []
    for mag in sorted(by_mag, reverse=True):
        negs = by_mag[mag]["neg"]
        poss = by_mag[mag]["pos"]
        for neg, pos in zip(negs, poss):
            pairs.append(
                {
                    "magnitud": mag,
                    "producto_neg": str(neg.get("cod_producto") or neg.get("tipo_key") or ""),
                    "nombre_neg": str(neg.get("tipo_nombre") or ""),
                    "check_neg": int(neg.get("cajas_check") or 0),
                    "producto_pos": str(pos.get("cod_producto") or pos.get("tipo_key") or ""),
                    "nombre_pos": str(pos.get("tipo_nombre") or ""),
                    "check_pos": int(pos.get("cajas_check") or 0),
                }
            )
    return pairs


def build_bc_kg_detalle_diario_from_lots(
    lot_detalle: list[dict[str, Any]],
    start: dt.date,
    end: dt.date,
    detalle_diario_report: list[dict[str, Any]] | None = None,
    innova_caja_by_day: dict[dt.date, dict[str, float]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Balance diario kg: teorico = inicial BC + Innova CAJA − primera salida BC.

    Stock real = snapshot BC (empaque <= dia, sin Type 1/3).
    Desvio kg = real − teorico (puede ser ≠ 0).
    kg_empaque / lotes_empaque = altas BC del dia (comparativa vs Innova).
    """
    lots: list[dict[str, Any]] = []
    ventas_lots: set[str] = set()
    for row in lot_detalle:
        fe = sql_row_to_date(row.get("fe_empaque"))
        if fe is None:
            continue
        lot = str(row.get("lot") or "").strip()
        out = sql_row_to_date(row.get("first_sale"))
        kg = to_float(row.get("kg_bc"))
        # Type 1 si hay kg_ventas_bc o estado vendido con venta; resto de salidas = 1/3.
        is_venta_t1 = to_float(row.get("kg_ventas_bc")) > 0 or (
            out is not None and str(row.get("estado") or "") == "vendido"
        )
        lots.append(
            {
                "lot": lot,
                "fe": fe,
                "out": out,
                "kg": kg,
                "is_venta_t1": is_venta_t1,
            }
        )
        if is_venta_t1 and lot:
            ventas_lots.add(lot)

    days: list[dt.date] = []
    if detalle_diario_report:
        for det in detalle_diario_report:
            days.append(parse_fecha_es_date(det["fecha"]))
    else:
        current = start
        while current <= end:
            days.append(current)
            current += dt.timedelta(days=1)

    innova_by_day = innova_caja_by_day or {}
    if not innova_by_day and detalle_diario_report:
        innova_by_day = build_innova_caja_by_day(None, detalle_diario_report)

    empaque_by_day: dict[dt.date, tuple[float, int]] = {}
    salida_by_day: dict[dt.date, tuple[float, int]] = {}
    ventas_t1_by_day: dict[dt.date, tuple[float, int]] = {}
    ajustes_t3_by_day: dict[dt.date, tuple[float, int]] = {}
    for lot in lots:
        fe = lot["fe"]
        out = lot["out"]
        kg = lot["kg"]
        if start <= fe <= end:
            prev_kg, prev_n = empaque_by_day.get(fe, (0.0, 0))
            empaque_by_day[fe] = (prev_kg + kg, prev_n + 1)
        if out is not None and start <= out <= end:
            prev_kg, prev_n = salida_by_day.get(out, (0.0, 0))
            salida_by_day[out] = (prev_kg + kg, prev_n + 1)
            if lot["is_venta_t1"] or lot["lot"] in ventas_lots:
                prev_kg, prev_n = ventas_t1_by_day.get(out, (0.0, 0))
                ventas_t1_by_day[out] = (prev_kg + kg, prev_n + 1)
            else:
                prev_kg, prev_n = ajustes_t3_by_day.get(out, (0.0, 0))
                ajustes_t3_by_day[out] = (prev_kg + kg, prev_n + 1)

    stock_ini_start = sum(
        lot["kg"] for lot in lots if _lot_in_stock_inicial(lot["fe"], lot["out"], start)
    )
    lotes_ini_start = sum(
        1 for lot in lots if _lot_in_stock_inicial(lot["fe"], lot["out"], start)
    )
    # Inicial teorico dia 1 = snapshot BC; luego encadena el teorico.
    stock_ini_carry = stock_ini_start
    lotes_ini_carry = lotes_ini_start

    detalle: list[dict[str, Any]] = []
    kg_empaque_mes = 0.0
    lotes_empaque_mes = 0
    kg_produccion_innova_mes = 0.0
    packs_produccion_innova_mes = 0
    kg_salidas_mes = 0.0
    lotes_salidas_mes = 0
    kg_ventas_t1_mes = 0.0
    kg_ajustes_t3_mes = 0.0
    kg_stock_real_prev = stock_ini_start

    for day in days:
        kg_ini = stock_ini_carry
        lotes_ini = lotes_ini_carry
        kg_emp_bc, lotes_emp_bc = empaque_by_day.get(day, (0.0, 0))
        kg_sal, lotes_sal = salida_by_day.get(day, (0.0, 0))
        kg_t1, _n_t1 = ventas_t1_by_day.get(day, (0.0, 0))
        kg_t3, _n_t3 = ajustes_t3_by_day.get(day, (0.0, 0))
        innova_day = innova_by_day.get(day) or {}
        kg_prod_innova = to_float(innova_day.get("kg"))
        packs_innova = int(round(to_float(innova_day.get("packs"))))
        # Teorico: inicial + Innova CAJA − primera salida BC (T1+T3 una vez).
        kg_teo = kg_ini + kg_prod_innova - kg_sal
        lotes_teo = lotes_ini + packs_innova - lotes_sal
        kg_real = sum(lot["kg"] for lot in lots if _lot_in_stock_final(lot["fe"], lot["out"], day))
        lotes_real = sum(1 for lot in lots if _lot_in_stock_final(lot["fe"], lot["out"], day))
        desvio = compute_desvio_stock(kg_teo, kg_real)

        kg_empaque_mes += kg_emp_bc
        lotes_empaque_mes += lotes_emp_bc
        kg_produccion_innova_mes += kg_prod_innova
        packs_produccion_innova_mes += packs_innova
        kg_salidas_mes += kg_sal
        lotes_salidas_mes += lotes_sal
        kg_ventas_t1_mes += kg_t1
        kg_ajustes_t3_mes += kg_t3

        detalle.append(
            {
                "fecha": day.strftime("%d/%m/%Y"),
                "kg_stock_inicial": kg_ini,
                "lotes_stock_inicial": lotes_ini,
                "kg_stock_real_inicial": kg_stock_real_prev,
                "kg_ventas_stock_antiguo": 0.0,
                "lotes_ventas_stock_antiguo": 0,
                "kg_empaque": kg_emp_bc,
                "lotes_empaque": lotes_emp_bc,
                "kg_produccion_bc": kg_emp_bc,
                "kg_produccion": kg_prod_innova,
                "packs_produccion": packs_innova,
                "kg_comparativa_innova_bc": kg_prod_innova - kg_emp_bc,
                "kg_ventas": kg_sal,
                "kg_ventas_t1": kg_t1,
                "kg_ajustes_t3": kg_t3,
                "lotes_ventas": lotes_sal,
                "kg_stock_final_teorico": kg_teo,
                "kg_stock_final_real": kg_real,
                "kg_stock_teorico": kg_teo,
                "kg_stock_real_cierre": kg_real,
                "kg_real_variacion": kg_real - kg_stock_real_prev,
                "kg_diferencia": desvio["desvio_kg"],  # real − teorico
                "desvio_kg": desvio["desvio_kg"],
                "desvio_pct": desvio["desvio_pct"],
                "semaforo": desvio["semaforo"],
                "lotes_stock_teorico": lotes_teo,
                "lotes_stock_real": lotes_real,
            }
        )
        stock_ini_carry = kg_teo
        lotes_ini_carry = lotes_teo
        kg_stock_real_prev = kg_real

    last = detalle[-1] if detalle else {}
    totals = {
        "kg_stock_inicial": stock_ini_start,
        "lotes_stock_inicial": lotes_ini_start,
        "kg_empaque_mes": kg_empaque_mes,
        "lotes_empaque_mes": lotes_empaque_mes,
        "kg_produccion_innova": kg_produccion_innova_mes,
        "packs_produccion_innova": packs_produccion_innova_mes,
        "kg_comparativa_innova_bc": kg_produccion_innova_mes - kg_empaque_mes,
        "kg_salidas_mes": kg_salidas_mes,
        "lotes_salidas_mes": lotes_salidas_mes,
        "kg_ventas_t1": kg_ventas_t1_mes,
        "kg_ajustes_t3": kg_ajustes_t3_mes,
        "kg_stock_teorico": to_float(last.get("kg_stock_final_teorico")),
        "kg_stock_real": to_float(last.get("kg_stock_final_real")),
        "lotes_stock_final": int(last.get("lotes_stock_real") or 0),
        "desvio_kg": to_float(last.get("desvio_kg")),
        "desvio_pct": last.get("desvio_pct"),
        "semaforo": str(last.get("semaforo") or "verde"),
    }
    return detalle, totals


def _format_sql_date(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, dt.datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, dt.date):
        return value.strftime("%d/%m/%Y")
    return str(value)


def _lot_in_stock_inicial(fe_empaque: Any, first_out: Any, day: dt.date) -> bool:
    """En stock al inicio del dia: empaque < dia y sin Type 1/3 antes del dia."""
    fe = sql_row_to_date(fe_empaque)
    if fe is None or fe >= day:
        return False
    out = sql_row_to_date(first_out)
    if out is not None and out < day:
        return False
    return True


def _lot_in_stock_final(fe_empaque: Any, first_out: Any, day: dt.date) -> bool:
    """En stock al cierre: empaque <= dia y sin Type 1/3 ese dia o antes."""
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
    vivo_by_lot = {
        str(row["lot"]).strip(): row for row in (bc_balance.get("lots_stock_vivo") or [])
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
    all_lots.update(vivo_by_lot)
    all_lots.update(entradas_by_lot)

    # Empaque ficticio (dia anterior al periodo) para ventas sin Fecha empaque ni empaque del mes.
    fe_apertura_inferida = (period_start - dt.timedelta(days=1)) if period_start else None

    detalle: list[dict[str, Any]] = []
    for lot in sorted(all_lots):
        snap = snapshot_by_lot.get(lot, {})
        vivo = vivo_by_lot.get(lot, {})
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
            or str(vivo.get("item_no") or "").strip()
            or str(entrada.get("item_no") or "").strip()
            or str(antiguo.get("item_no") or "").strip()
            or str(apertura.get("item_no") if apertura else "").strip()
            or str(venta.get("item_no") or "").strip()
        )
        item_description = (
            str(snap.get("item_description") or "").strip()
            or str(vivo.get("item_description") or "").strip()
            or str(entrada.get("item_description") or "").strip()
            or str(antiguo.get("item_description") or "").strip()
            or str(apertura.get("item_description") if apertura else "").strip()
            or str(venta.get("item_description") or "").strip()
        )
        cod_producto, enlace_origen, conv_desc = resolve_cod_producto_bc(
            material, pattern, item_no, conversion_by_bascula
        )
        tipo_key = cod_producto
        # Si el SKU viene del ILE, la descripcion BC debe mandar (como stock inicial).
        if enlace_origen == "item_no_bc":
            tipo_nombre = (
                item_description
                or material_nombre
                or conv_desc
                or cod_producto
                or "(sin tipo)"
            )
        else:
            tipo_nombre = (
                material_nombre
                or conv_desc
                or item_description
                or cod_producto
                or "(sin tipo)"
            )

        fe_empaque = (
            snap.get("fe_empaque")
            or vivo.get("fe_empaque")
            or entrada.get("fe_empaque")
            or antiguo.get("fe_empaque")
            or venta.get("fe_empaque")
            or (apertura.get("fe_empaque") if apertura else None)
        )
        kg_bc = (
            to_float(snap.get("kg"))
            if snap
            else to_float(
                vivo.get("kg")
                if vivo
                else (
                    entrada.get("kg")
                    if entrada
                    else (antiguo.get("kg") if antiguo else (apertura.get("kg") if apertura else 0))
                )
            )
        )
        kg_innova = to_float(innova.get("kg_innova"))
        kg_ventas_bc = to_float(venta.get("kg"))
        qty_ventas_bc = int(round(to_float(venta.get("qty"))))
        qty_entradas_bc = int(round(to_float(entrada.get("qty"))))
        packs_innova = int(innova.get("packs") or 0)
        first_sale = (
            snap.get("first_sale")
            or vivo.get("first_out")
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

    result = list(grouped.values())
    result.sort(key=lambda item: (-item["kg_innova"], item["tipo_nombre"]))
    return result


def build_bc_balance_por_tipo_cajas(
    lot_detalle: list[dict[str, Any]],
    start: dt.date,
    end: dt.date,
    bc_balance: dict[str, Any] | None = None,
    detalle_diario_report: list[dict[str, Any]] | None = None,
    innova_caja_by_day: dict[dt.date, dict[str, float]] | None = None,
    innova_por_tipo: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Balance por tipo en cajas + diario consolidado.

    Teorico = Inicial BC + packs Innova CAJA − Primera salida BC.
    Real = snapshot BC (1 lote = 1 caja).
    Desvio = real − teorico.
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

    def _cajas_from_mov_row(row: dict[str, Any]) -> int:
        lotes = int(row.get("lotes") or 0)
        if lotes > 0:
            return lotes
        return max(0, int(round(to_float(row.get("qty")))))

    # Movimientos ILE (informativos en UI)
    ile_entradas_dia_tipo: dict[dt.date, dict[str, int]] = {}
    for row in (bc_balance or {}).get("entradas_pos_adj_diario") or []:
        day = sql_row_to_date(row.get("fecha"))
        item_no = str(row.get("item_no") or "").strip()
        if day is None or not item_no:
            continue
        key = _tipo_from_item(item_no)
        cajas = _cajas_from_mov_row(row)
        if cajas <= 0:
            continue
        ile_entradas_dia_tipo.setdefault(day, {})
        ile_entradas_dia_tipo[day][key] = ile_entradas_dia_tipo[day].get(key, 0) + cajas

    ile_ventas_dia_tipo: dict[dt.date, dict[str, int]] = {}
    for row in (bc_balance or {}).get("ventas_diario_producto") or []:
        day = sql_row_to_date(row.get("fecha"))
        item_no = str(row.get("item_no") or "").strip()
        if day is None or not item_no:
            continue
        key = _tipo_from_item(item_no)
        cajas = _cajas_from_mov_row(row)
        if cajas <= 0:
            continue
        ile_ventas_dia_tipo.setdefault(day, {})
        ile_ventas_dia_tipo[day][key] = ile_ventas_dia_tipo[day].get(key, 0) + cajas

    ile_ajustes_neg_dia_tipo: dict[dt.date, dict[str, int]] = {}
    for row in (bc_balance or {}).get("ajustes_neg_adj_diario") or []:
        day = sql_row_to_date(row.get("fecha"))
        item_no = str(row.get("item_no") or "").strip()
        if day is None or not item_no:
            continue
        key = _tipo_from_item(item_no)
        cajas = _cajas_from_mov_row(row)
        if cajas <= 0:
            continue
        ile_ajustes_neg_dia_tipo.setdefault(day, {})
        ile_ajustes_neg_dia_tipo[day][key] = ile_ajustes_neg_dia_tipo[day].get(key, 0) + cajas

    # Flujos BC empaque / primera salida (altas y salidas reales)
    empaque_dia_tipo: dict[dt.date, dict[str, int]] = {}
    salida_dia_tipo: dict[dt.date, dict[str, int]] = {}
    for key, rows in lots_by_tipo.items():
        for row in rows:
            fe = sql_row_to_date(row.get('fe_empaque'))
            out = sql_row_to_date(row.get('first_sale'))
            if fe is not None and start <= fe <= end:
                empaque_dia_tipo.setdefault(fe, {})
                empaque_dia_tipo[fe][key] = empaque_dia_tipo[fe].get(key, 0) + 1
            if out is not None and start <= out <= end:
                salida_dia_tipo.setdefault(out, {})
                salida_dia_tipo[out][key] = salida_dia_tipo[out].get(key, 0) + 1

    innova_dia = innova_caja_by_day or {}
    innova_tipo = innova_por_tipo or {}
    for key, inv in innova_tipo.items():
        _ensure_tipo(
            key,
            tipo_nombre=str(inv.get('tipo_nombre') or key),
            material=str(inv.get('material') or ''),
            cod_producto=str(inv.get('cod_producto') or key),
            pattern=str(inv.get('pattern') or ''),
        )

    stock_ini_tipo: dict[str, int] = {}
    for key, rows in lots_by_tipo.items():
        stock_ini_tipo[key] = sum(
            1
            for row in rows
            if _lot_in_stock_inicial(row.get('fe_empaque'), row.get('first_sale'), start)
        )

    days: list[dt.date] = []
    if detalle_diario_report:
        for det in detalle_diario_report:
            days.append(parse_fecha_es_date(det['fecha']))
    else:
        current = start
        while current <= end:
            days.append(current)
            current += dt.timedelta(days=1)

    innova_packs_dia_tipo: dict[dt.date, dict[str, int]] = {d: {} for d in days}
    if len(days) == 1 and innova_tipo:
        d0 = days[0]
        for key, inv in innova_tipo.items():
            innova_packs_dia_tipo[d0][key] = int(inv.get('packs') or 0)
    elif innova_dia:
        total_packs_periodo = sum(int(inv.get('packs') or 0) for inv in innova_tipo.values()) or 1
        for day in days:
            packs_day = int(round(to_float((innova_dia.get(day) or {}).get('packs'))))
            if packs_day <= 0:
                continue
            if innova_tipo:
                assigned = 0
                items = list(innova_tipo.items())
                for i, (key, inv) in enumerate(items):
                    if i == len(items) - 1:
                        share = packs_day - assigned
                    else:
                        share = int(round(packs_day * int(inv.get('packs') or 0) / total_packs_periodo))
                        assigned += share
                    innova_packs_dia_tipo[day][key] = max(0, share)
            else:
                innova_packs_dia_tipo[day]['(sin tipo)'] = packs_day
                _ensure_tipo('(sin tipo)')

    stock_real_dia_tipo: dict[dt.date, dict[str, int]] = {day: {} for day in days}
    for key, rows in lots_by_tipo.items():
        for row in rows:
            fe = sql_row_to_date(row.get('fe_empaque'))
            out = sql_row_to_date(row.get('first_sale'))
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
    acc_ajustes_neg: dict[str, int] = {key: 0 for key in tipo_meta}
    acc_empaque: dict[str, int] = {key: 0 for key in tipo_meta}
    acc_innova: dict[str, int] = {key: 0 for key in tipo_meta}
    acc_salida: dict[str, int] = {key: 0 for key in tipo_meta}
    stock_ini_carry: dict[str, int] = dict(stock_ini_tipo)

    for day in days:
        empaque_map = empaque_dia_tipo.get(day, {})
        innova_map = innova_packs_dia_tipo.get(day, {})
        salida_map = salida_dia_tipo.get(day, {})
        ile_ent_map = ile_entradas_dia_tipo.get(day, {})
        ile_ven_map = ile_ventas_dia_tipo.get(day, {})
        ile_adj_map = ile_ajustes_neg_dia_tipo.get(day, {})
        real_map = stock_real_dia_tipo.get(day, {})
        cajas_entradas_dia = 0
        cajas_innova_dia = 0
        cajas_ventas_dia = 0
        cajas_ajustes_neg_dia = 0
        cajas_ini_dia = 0
        cajas_teo_dia = 0
        cajas_real_dia = 0

        keys_day = set(tipo_meta) | set(innova_map) | set(empaque_map) | set(salida_map) | set(real_map)
        for key in keys_day:
            _ensure_tipo(key)
            ini = int(stock_ini_carry.get(key, 0))
            emp = int(empaque_map.get(key, 0))
            packs_inv = int(innova_map.get(key, 0))
            sal = int(salida_map.get(key, 0))
            teorico = ini + packs_inv - sal
            real = int(real_map.get(key, 0))
            ile_ent = int(ile_ent_map.get(key, 0))
            ile_ven = int(ile_ven_map.get(key, 0))
            ile_adj = int(ile_adj_map.get(key, 0))
            acc_empaque[key] = acc_empaque.get(key, 0) + emp
            acc_innova[key] = acc_innova.get(key, 0) + packs_inv
            acc_salida[key] = acc_salida.get(key, 0) + sal
            acc_entradas[key] = acc_entradas.get(key, 0) + ile_ent
            acc_ventas[key] = acc_ventas.get(key, 0) + ile_ven
            acc_ajustes_neg[key] = acc_ajustes_neg.get(key, 0) + ile_adj
            stock_ini_carry[key] = teorico
            cajas_ini_dia += ini
            cajas_entradas_dia += emp
            cajas_innova_dia += packs_inv
            cajas_ventas_dia += sal
            cajas_ajustes_neg_dia += ile_adj
            cajas_teo_dia += teorico
            cajas_real_dia += real

        desvio_cajas = cajas_real_dia - cajas_teo_dia
        detalle_diario_cajas.append(
            {
                'fecha': day.strftime('%d/%m/%Y'),
                'cajas_stock_inicial': cajas_ini_dia,
                'cajas_entradas': cajas_innova_dia,
                'cajas_produccion_bc': cajas_entradas_dia,
                'cajas_ventas': cajas_ventas_dia,
                'cajas_ajustes_neg': cajas_ajustes_neg_dia,
                'cajas_stock_teorico': cajas_teo_dia,
                'cajas_stock_real': cajas_real_dia,
                'cajas_check': desvio_cajas,
                'desvio_cajas': desvio_cajas,
            }
        )

    result: list[dict[str, Any]] = []
    for key, meta in tipo_meta.items():
        ini = int(stock_ini_tipo.get(key, 0))
        emp = int(acc_empaque.get(key, 0))
        packs_inv = int(acc_innova.get(key, 0))
        if packs_inv <= 0 and key in innova_tipo:
            packs_inv = int(innova_tipo[key].get('packs') or 0)
        sal = int(acc_salida.get(key, 0))
        teorico = ini + packs_inv - sal
        real = int(stock_real_dia_tipo.get(end, {}).get(key, 0))
        desvio = real - teorico
        result.append(
            {
                **meta,
                'cajas_stock_inicial': ini,
                'cajas_entradas': packs_inv,
                'cajas_produccion_bc': emp,
                'cajas_ventas': sal,
                'cajas_ajustes_neg': int(acc_ajustes_neg.get(key, 0)),
                'cajas_ile_type2': int(acc_entradas.get(key, 0)),
                'cajas_ile_type1': int(acc_ventas.get(key, 0)),
                'cajas_stock_teorico': teorico,
                'cajas_stock_real': real,
                'cajas_check': desvio,
                'desvio_cajas': desvio,
                'lotes': len(lots_by_tipo.get(key, [])),
            }
        )

    result.sort(
        key=lambda item: (
            -(
                int(item['cajas_entradas'])
                + int(item['cajas_ventas'])
                + int(item['cajas_ajustes_neg'])
            ),
            str(item['tipo_key']),
        )
    )
    return result, detalle_diario_cajas



def build_bc_desvio_cadena_por_producto(
    lot_detalle: list[dict[str, Any]],
    start: dt.date,
    end: dt.date,
    innova_por_tipo: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Desvío por producto en cada etapa: Inicial / Producción / Salidas / Ajustes / Stock final.

    Producción: teorico = Innova CAJA; real = empaque BC.
    Stock final: teorico = ini + innova − primera salida; real = snapshot BC.
    Desvio = real − teorico.
    """
    innova_por_tipo = innova_por_tipo or {}
    by_tipo: dict[str, dict[str, Any]] = {}

    def _bucket(row: dict[str, Any]) -> dict[str, Any]:
        key = str(row.get("tipo_key") or row.get("cod_producto") or "(sin tipo)").strip() or "(sin tipo)"
        return by_tipo.setdefault(
            key,
            {
                "tipo_key": key,
                "tipo_nombre": str(row.get("tipo_nombre") or key),
                "cod_producto": str(row.get("cod_producto") or key),
                "material": str(row.get("material") or ""),
                "pattern": str(row.get("pattern") or ""),
                "item_no": str(row.get("item_no") or ""),
                "kg_inicial": 0.0,
                "cajas_inicial": 0,
                "kg_prod_innova": 0.0,
                "packs_prod_innova": 0,
                "kg_prod_bc": 0.0,
                "cajas_prod_bc": 0,
                "kg_ventas_t1": 0.0,
                "cajas_ventas_t1": 0,
                "kg_ajustes_t3": 0.0,
                "cajas_ajustes_t3": 0,
                "kg_salida": 0.0,
                "cajas_salida": 0,
                "kg_final_real": 0.0,
                "cajas_final_real": 0,
            },
        )

    for key, inv in innova_por_tipo.items():
        b = by_tipo.setdefault(
            key,
            {
                "tipo_key": key,
                "tipo_nombre": str(inv.get("tipo_nombre") or key),
                "cod_producto": str(inv.get("cod_producto") or key),
                "material": str(inv.get("material") or ""),
                "pattern": str(inv.get("pattern") or ""),
                "item_no": "",
                "kg_inicial": 0.0,
                "cajas_inicial": 0,
                "kg_prod_innova": 0.0,
                "packs_prod_innova": 0,
                "kg_prod_bc": 0.0,
                "cajas_prod_bc": 0,
                "kg_ventas_t1": 0.0,
                "cajas_ventas_t1": 0,
                "kg_ajustes_t3": 0.0,
                "cajas_ajustes_t3": 0,
                "kg_salida": 0.0,
                "cajas_salida": 0,
                "kg_final_real": 0.0,
                "cajas_final_real": 0,
            },
        )
        b["kg_prod_innova"] += to_float(inv.get("kg"))
        b["packs_prod_innova"] += int(inv.get("packs") or 0)

    for row in lot_detalle:
        b = _bucket(row)
        fe = row.get("fe_empaque")
        out = row.get("first_sale")
        kg = to_float(row.get("kg_bc"))
        is_t1 = to_float(row.get("kg_ventas_bc")) > 0 or str(row.get("estado") or "") == "vendido"
        if _lot_in_stock_inicial(fe, out, start):
            b["kg_inicial"] += kg
            b["cajas_inicial"] += 1
        fe_d = sql_row_to_date(fe)
        if fe_d is not None and start <= fe_d <= end:
            b["kg_prod_bc"] += kg
            b["cajas_prod_bc"] += 1
        out_d = sql_row_to_date(out)
        if out_d is not None and start <= out_d <= end:
            b["kg_salida"] += kg
            b["cajas_salida"] += 1
            if is_t1:
                b["kg_ventas_t1"] += kg
                b["cajas_ventas_t1"] += 1
            else:
                b["kg_ajustes_t3"] += kg
                b["cajas_ajustes_t3"] += 1
        if _lot_in_stock_final(fe, out, end):
            b["kg_final_real"] += kg
            b["cajas_final_real"] += 1

    result: list[dict[str, Any]] = []
    for key, b in by_tipo.items():
        kg_ini = to_float(b["kg_inicial"])
        kg_innova = to_float(b["kg_prod_innova"])
        kg_bc_emp = to_float(b["kg_prod_bc"])
        kg_sal = to_float(b["kg_salida"])
        kg_final_teo = kg_ini + kg_innova - kg_sal
        kg_final_real = to_float(b["kg_final_real"])

        etapas = {
            "inicial": compute_desvio_stock(kg_ini, kg_ini),
            "produccion": compute_desvio_stock(kg_innova, kg_bc_emp),
            "salidas": compute_desvio_stock(
                to_float(b["kg_ventas_t1"]), to_float(b["kg_ventas_t1"])
            ),
            "ajustes": compute_desvio_stock(
                to_float(b["kg_ajustes_t3"]), to_float(b["kg_ajustes_t3"])
            ),
            "stock_final": compute_desvio_stock(kg_final_teo, kg_final_real),
        }
        # Etapa critica = mayor |desvio_pct| (o |desvio_kg| si pct None)
        critica = "stock_final"
        critica_score = -1.0
        for nombre, d in etapas.items():
            if nombre == "inicial":
                continue
            pct = d.get("desvio_pct")
            score = abs(to_float(pct)) if pct is not None else abs(to_float(d.get("desvio_kg"))) * 1e6
            if score > critica_score:
                critica_score = score
                critica = nombre

        final = etapas["stock_final"]
        prod = etapas["produccion"]
        result.append(
            {
                **{k: b[k] for k in (
                    "tipo_key", "tipo_nombre", "cod_producto", "material", "pattern", "item_no",
                    "kg_inicial", "cajas_inicial",
                    "kg_prod_innova", "packs_prod_innova",
                    "kg_prod_bc", "cajas_prod_bc",
                    "kg_ventas_t1", "cajas_ventas_t1",
                    "kg_ajustes_t3", "cajas_ajustes_t3",
                    "kg_salida", "cajas_salida",
                    "kg_final_real", "cajas_final_real",
                )},
                "kg_final_teorico": kg_final_teo,
                "desvio_produccion_kg": prod["desvio_kg"],
                "desvio_produccion_pct": prod["desvio_pct"],
                "semaforo_produccion": prod["semaforo"],
                "desvio_kg": final["desvio_kg"],
                "desvio_pct": final["desvio_pct"],
                "semaforo": final["semaforo"],
                "etapa_critica": critica,
                "etapas": etapas,
            }
        )

    result.sort(key=lambda r: (-abs(to_float(r.get("desvio_kg"))), str(r.get("tipo_key"))))
    return result


NOTA_BC_BALANCE_MOVIMIENTOS_ILE = (
    "Este cálculo es una auditoría de movimientos ILE (Entry Type 2 / 1 / 3) con "
    "ABS(Quantity) en cajas y ABS(Kilos) en kg. "
    "Fórmula: Inicial + Entradas (Type 2) − Ventas (Type 1) − Ajustes neg. (Type 3). "
    "El stock real sigue contando 1 lote = 1 caja (o kg del lote). "
    "Por eso el check puede no ser cero: Quantity≠1, doble salida Type 1+3, o Type 3 con Kilos=0. "
    "El balance de almacén (pestañas Balance BC E/G/Z y Balance por tipo cajas) usa otra lógica "
    "más coherente con el stock: Inicial + Producción (Salidas CAJA) − Primera salida "
    "(misma base que el check de kg)."
)
NOTA_BC_BALANCE_MOVIMIENTOS_ILE_TITULO = (
    "Nota — balance por movimientos ILE (no es el stock de almacén)"
)


def build_bc_balance_movimientos_ile(
    lot_detalle: list[dict[str, Any]],
    start: dt.date,
    end: dt.date,
    bc_balance: dict[str, Any] | None,
) -> dict[str, Any]:
    """Balance por Type 2/1/3 con ABS(Quantity)/ABS(Kilos) — auditoría de movimientos."""
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

    def _accum_mov(
        source_key: str,
        qty_map: dict[str, float],
        kg_map: dict[str, float],
    ) -> None:
        for row in (bc_balance or {}).get(source_key) or []:
            day = sql_row_to_date(row.get("fecha"))
            item_no = str(row.get("item_no") or "").strip()
            if day is None or not item_no or day < start or day > end:
                continue
            key = _tipo_from_item(item_no)
            qty = abs(to_float(row.get("qty")))
            kg = abs(to_float(row.get("kg")))
            if qty <= 0 and kg <= 0:
                continue
            qty_map[key] = qty_map.get(key, 0.0) + qty
            kg_map[key] = kg_map.get(key, 0.0) + kg

    entradas_qty: dict[str, float] = {}
    entradas_kg: dict[str, float] = {}
    ventas_qty: dict[str, float] = {}
    ventas_kg: dict[str, float] = {}
    ajustes_qty: dict[str, float] = {}
    ajustes_kg: dict[str, float] = {}
    _accum_mov("entradas_pos_adj_diario", entradas_qty, entradas_kg)
    _accum_mov("ventas_diario_producto", ventas_qty, ventas_kg)
    _accum_mov("ajustes_neg_adj_diario", ajustes_qty, ajustes_kg)

    stock_ini_cajas: dict[str, int] = {}
    stock_ini_kg: dict[str, float] = {}
    stock_real_cajas: dict[str, int] = {}
    stock_real_kg: dict[str, float] = {}
    for key, rows in lots_by_tipo.items():
        stock_ini_cajas[key] = 0
        stock_ini_kg[key] = 0.0
        stock_real_cajas[key] = 0
        stock_real_kg[key] = 0.0
        for row in rows:
            kg = to_float(row.get("kg_bc") or row.get("kg") or 0)
            if _lot_in_stock_inicial(row.get("fe_empaque"), row.get("first_sale"), start):
                stock_ini_cajas[key] += 1
                stock_ini_kg[key] += kg
            fe = sql_row_to_date(row.get("fe_empaque"))
            out = sql_row_to_date(row.get("first_sale"))
            if fe is None or fe > end:
                continue
            if out is not None and out <= end:
                continue
            stock_real_cajas[key] += 1
            stock_real_kg[key] += kg

    # Asegurar tipos solo presentes en movimientos ILE
    for key in list(entradas_qty) + list(ventas_qty) + list(ajustes_qty):
        tipo_meta.setdefault(
            key,
            {
                "tipo_key": key,
                "tipo_nombre": key,
                "material": "",
                "cod_producto": key,
                "pattern": "",
                "item_no": key,
            },
        )

    por_tipo_cajas: list[dict[str, Any]] = []
    por_tipo_kg: list[dict[str, Any]] = []
    for key, meta in tipo_meta.items():
        ini_c = int(stock_ini_cajas.get(key, 0))
        ent_c = int(round(entradas_qty.get(key, 0.0)))
        ven_c = int(round(ventas_qty.get(key, 0.0)))
        adj_c = int(round(ajustes_qty.get(key, 0.0)))
        teo_c = ini_c + ent_c - ven_c - adj_c
        real_c = int(stock_real_cajas.get(key, 0))
        por_tipo_cajas.append(
            {
                **meta,
                "stock_inicial": ini_c,
                "entradas": ent_c,
                "ventas": ven_c,
                "ajustes_neg": adj_c,
                "stock_teorico": teo_c,
                "stock_real": real_c,
                "check": teo_c - real_c,
            }
        )

        ini_k = to_float(stock_ini_kg.get(key, 0.0))
        ent_k = to_float(entradas_kg.get(key, 0.0))
        ven_k = to_float(ventas_kg.get(key, 0.0))
        adj_k = to_float(ajustes_kg.get(key, 0.0))
        teo_k = ini_k + ent_k - ven_k - adj_k
        real_k = to_float(stock_real_kg.get(key, 0.0))
        por_tipo_kg.append(
            {
                **meta,
                "stock_inicial": ini_k,
                "entradas": ent_k,
                "ventas": ven_k,
                "ajustes_neg": adj_k,
                "stock_teorico": teo_k,
                "stock_real": real_k,
                "check": teo_k - real_k,
            }
        )

    def _sort_key(item: dict[str, Any]) -> tuple:
        return (
            -(
                abs(to_float(item["entradas"]))
                + abs(to_float(item["ventas"]))
                + abs(to_float(item["ajustes_neg"]))
            ),
            str(item["tipo_key"]),
        )

    por_tipo_cajas.sort(key=_sort_key)
    por_tipo_kg.sort(key=_sort_key)

    def _totales(rows: list[dict[str, Any]], as_int: bool) -> dict[str, Any]:
        keys = (
            "stock_inicial",
            "entradas",
            "ventas",
            "ajustes_neg",
            "stock_teorico",
            "stock_real",
            "check",
        )
        out: dict[str, Any] = {}
        for k in keys:
            s = sum(to_float(r.get(k)) for r in rows)
            out[k] = int(round(s)) if as_int else to_float(s)
        out["productos"] = len(rows)
        return out

    return {
        "por_tipo_cajas": por_tipo_cajas,
        "por_tipo_kg": por_tipo_kg,
        "totales_cajas": _totales(por_tipo_cajas, as_int=True),
        "totales_kg": _totales(por_tipo_kg, as_int=False),
        "nota_titulo": NOTA_BC_BALANCE_MOVIMIENTOS_ILE_TITULO,
        "nota": NOTA_BC_BALANCE_MOVIMIENTOS_ILE,
    }


AJUSTES_NEG_CHART_COLORS = (
    "#003b5c",
    "#00a3c8",
    "#c8102e",
    "#005f87",
    "#7a9eb1",
    "#e87722",
    "#2d6a4f",
    "#6c5ce7",
    "#b08968",
    "#495057",
)

BC_ENTRY_TYPE_LABELS = {
    1: "Venta (Type 1)",
    2: "Ajuste + (Type 2)",
    3: "Ajuste − (Type 3)",
}


def build_bc_ajustes_neg_analisis(
    raw_rows: list[dict[str, Any]] | None,
    integridad_row: dict[str, Any] | None = None,
    *,
    cajas_check: int | None = None,
    kg_check: float | None = None,
    cajas_stock_teorico: int | None = None,
    cajas_stock_real: int | None = None,
    kg_stock_teorico: float | None = None,
    kg_stock_real: float | None = None,
) -> dict[str, Any]:
    """KPI / analisis de movimientos ILE Type 1/2/3 + validacion de ecuacion."""
    detalle_all: list[dict[str, Any]] = []
    for row in raw_rows or []:
        day = sql_row_to_date(row.get("fecha"))
        if day is None:
            continue
        entry_type = int(row.get("entry_type") or 0)
        if entry_type not in (1, 2, 3):
            continue
        item_no = str(row.get("item_no") or "").strip() or "(sin producto)"
        usuario = str(row.get("usuario") or "").strip() or "(sin usuario)"
        desc = str(row.get("item_description") or "").strip()
        qty = int(round(to_float(row.get("qty"))))
        kg = to_float(row.get("kg"))
        movimientos = int(row.get("movimientos") or 0)
        lotes = int(row.get("lotes") or 0)
        mov_qty_ne_1 = int(row.get("mov_qty_ne_1") or 0)
        mov_kg_0 = int(row.get("mov_kg_0") or 0)
        if qty <= 0 and kg <= 0:
            continue
        detalle_all.append(
            {
                "entry_type": entry_type,
                "tipo_label": BC_ENTRY_TYPE_LABELS.get(entry_type, f"Type {entry_type}"),
                "fecha": day.strftime("%d/%m/%Y"),
                "fecha_iso": day.isoformat(),
                "usuario": usuario,
                "item_no": item_no,
                "item_description": desc or item_no,
                "cajas": qty,
                "kg": kg,
                "movimientos": movimientos,
                "lotes": lotes,
                "mov_qty_ne_1": mov_qty_ne_1,
                "mov_kg_0": mov_kg_0,
            }
        )

    # Resumen por tipo de movimiento
    por_tipo: dict[int, dict[str, Any]] = {}
    for etype in (1, 2, 3):
        por_tipo[etype] = {
            "entry_type": etype,
            "tipo_label": BC_ENTRY_TYPE_LABELS[etype],
            "cajas": 0,
            "kg": 0.0,
            "movimientos": 0,
            "lotes": 0,
            "mov_qty_ne_1": 0,
            "mov_kg_0": 0,
            "usuarios": set(),
            "productos": set(),
        }
    for row in detalle_all:
        etype = int(row["entry_type"])
        bucket = por_tipo[etype]
        bucket["cajas"] += int(row["cajas"])
        bucket["kg"] += to_float(row["kg"])
        bucket["movimientos"] += int(row["movimientos"])
        bucket["lotes"] += int(row["lotes"])
        bucket["mov_qty_ne_1"] += int(row["mov_qty_ne_1"])
        bucket["mov_kg_0"] += int(row["mov_kg_0"])
        bucket["usuarios"].add(row["usuario"])
        bucket["productos"].add(row["item_no"])

    por_tipo_list = []
    for etype in (1, 2, 3):
        meta = por_tipo[etype]
        cajas = int(meta["cajas"])
        lotes = int(meta["lotes"])
        por_tipo_list.append(
            {
                **meta,
                "usuarios": len(meta["usuarios"]),
                "productos": len(meta["productos"]),
                "delta_qty_lotes": cajas - lotes,
                "ok_qty_vs_lote": abs(cajas - lotes) <= max(5, int(0.001 * max(cajas, 1))),
                "pct_kg_0": (100.0 * int(meta["mov_kg_0"]) / int(meta["movimientos"]))
                if int(meta["movimientos"])
                else 0.0,
            }
        )

    # Analisis detallado Type 3 (usuario / dia / producto) — UI KPI existente
    detalle = [r for r in detalle_all if int(r["entry_type"]) == 3]
    por_usuario: dict[str, dict[str, Any]] = {}
    por_dia: dict[str, dict[str, Any]] = {}
    por_producto: dict[str, dict[str, Any]] = {}
    dia_usuario: dict[str, dict[str, int]] = {}

    for row in detalle:
        usuario = row["usuario"]
        fecha = row["fecha"]
        item = row["item_no"]
        cajas = int(row["cajas"])
        kg = to_float(row["kg"])
        movs = int(row["movimientos"])
        lotes = int(row["lotes"])

        u = por_usuario.setdefault(
            usuario,
            {
                "usuario": usuario,
                "cajas": 0,
                "kg": 0.0,
                "movimientos": 0,
                "lotes": 0,
                "dias": set(),
                "productos": set(),
            },
        )
        u["cajas"] += cajas
        u["kg"] += kg
        u["movimientos"] += movs
        u["lotes"] += lotes
        u["dias"].add(fecha)
        u["productos"].add(item)

        d = por_dia.setdefault(
            fecha,
            {
                "fecha": fecha,
                "fecha_iso": row["fecha_iso"],
                "cajas": 0,
                "kg": 0.0,
                "movimientos": 0,
                "lotes": 0,
                "usuarios": set(),
            },
        )
        d["cajas"] += cajas
        d["kg"] += kg
        d["movimientos"] += movs
        d["lotes"] += lotes
        d["usuarios"].add(usuario)

        p = por_producto.setdefault(
            item,
            {
                "item_no": item,
                "item_description": row["item_description"],
                "cajas": 0,
                "kg": 0.0,
                "movimientos": 0,
                "lotes": 0,
                "usuarios": set(),
            },
        )
        p["cajas"] += cajas
        p["kg"] += kg
        p["movimientos"] += movs
        p["lotes"] += lotes
        p["usuarios"].add(usuario)
        if not p.get("item_description") and row["item_description"]:
            p["item_description"] = row["item_description"]

        dia_usuario.setdefault(fecha, {})
        dia_usuario[fecha][usuario] = dia_usuario[fecha].get(usuario, 0) + cajas

    usuarios_sorted = sorted(
        (
            {
                **meta,
                "dias": len(meta["dias"]),
                "productos": len(meta["productos"]),
            }
            for meta in por_usuario.values()
        ),
        key=lambda item: (-int(item["cajas"]), str(item["usuario"])),
    )
    dias_sorted = sorted(
        (
            {
                **meta,
                "usuarios": len(meta["usuarios"]),
            }
            for meta in por_dia.values()
        ),
        key=lambda item: item["fecha_iso"],
    )
    productos_sorted = sorted(
        (
            {
                **meta,
                "usuarios": len(meta["usuarios"]),
            }
            for meta in por_producto.values()
        ),
        key=lambda item: (-int(item["cajas"]), str(item["item_no"])),
    )
    detalle.sort(
        key=lambda item: (
            item["fecha_iso"],
            -int(item["cajas"]),
            str(item["usuario"]),
            str(item["item_no"]),
        )
    )

    total_cajas = sum(int(u["cajas"]) for u in usuarios_sorted)
    total_kg = sum(to_float(u["kg"]) for u in usuarios_sorted)
    total_movs = sum(int(u["movimientos"]) for u in usuarios_sorted)
    total_lotes = sum(int(u["lotes"]) for u in usuarios_sorted)
    top_user = usuarios_sorted[0]["usuario"] if usuarios_sorted else "—"
    top_product = productos_sorted[0]["item_no"] if productos_sorted else "—"

    user_labels = [str(u["usuario"]) for u in usuarios_sorted]
    user_colors = [
        AJUSTES_NEG_CHART_COLORS[i % len(AJUSTES_NEG_CHART_COLORS)]
        for i in range(len(user_labels))
    ]
    day_labels = [d["fecha"] for d in dias_sorted]
    stacked_datasets = []
    for idx, usuario in enumerate(user_labels):
        stacked_datasets.append(
            {
                "label": usuario,
                "data": [
                    int(dia_usuario.get(day["fecha"], {}).get(usuario, 0))
                    for day in dias_sorted
                ],
                "backgroundColor": AJUSTES_NEG_CHART_COLORS[
                    idx % len(AJUSTES_NEG_CHART_COLORS)
                ],
                "stack": "cajas",
            }
        )

    top_productos = productos_sorted[:15]
    integ = integridad_row or {}
    lotes_venta_y_neg = int(integ.get("lotes_venta_y_neg") or 0)
    type3 = next((t for t in por_tipo_list if t["entry_type"] == 3), {})
    type2 = next((t for t in por_tipo_list if t["entry_type"] == 2), {})
    type1 = next((t for t in por_tipo_list if t["entry_type"] == 1), {})

    alertas: list[dict[str, Any]] = []
    if lotes_venta_y_neg > 0:
        alertas.append(
            {
                "nivel": "info",
                "codigo": "DOBLE_SALIDA",
                "titulo": "Lotes con venta y ajuste negativo",
                "detalle": (
                    f"{lotes_venta_y_neg:,} lotes tienen Type 1 y Type 3 en el periodo. "
                    "El stock real solo saca el lote una vez; Type 3 ya no resta en el "
                    "teórico de cajas (misma lógica que kg)."
                ),
                "valor": lotes_venta_y_neg,
            }
        )
    delta_t3 = int(type3.get("delta_qty_lotes") or 0)
    if delta_t3 > 50:
        alertas.append(
            {
                "nivel": "info",
                "codigo": "QTY_NE_1_TYPE3",
                "titulo": "Type 3 con Quantity ≠ 1",
                "detalle": (
                    f"ABS(Quantity)−lotes = {delta_t3:,}. El balance de cajas cuenta "
                    "1 lote = 1 caja (no ABS(Quantity)); este delta solo afecta al "
                    "análisis de movimientos ILE."
                ),
                "valor": delta_t3,
            }
        )
    pct_kg0 = to_float(type3.get("pct_kg_0"))
    if pct_kg0 >= 50:
        alertas.append(
            {
                "nivel": "info",
                "codigo": "TYPE3_KG_CERO",
                "titulo": "Type 3 casi sin kilos BC",
                "detalle": (
                    f"{pct_kg0:.0f}% de movimientos Type 3 tienen [Kilos]=0 "
                    f"(solo {fmt_num(type3.get('kg', 0))} kg totales). "
                    "Coherente con no restarlos como flujo en kg ni en cajas."
                ),
                "valor": pct_kg0,
            }
        )
    delta_t2 = int(type2.get("delta_qty_lotes") or 0)
    if delta_t2 > 100:
        alertas.append(
            {
                "nivel": "info",
                "codigo": "QTY_VS_LOTES_TYPE2",
                "titulo": "Type 2: más Quantity que lotes distintos",
                "detalle": (
                    f"ABS(Quantity)−lotes = {delta_t2:,} (reentradas / varios movimientos "
                    "sobre el mismo lote). Entradas en cajas usan Quantity; stock usa lotes."
                ),
                "valor": delta_t2,
            }
        )

    # Coherencia de ecuaciones documentadas
    ecuacion_cajas_ok = cajas_check is not None and abs(int(cajas_check)) == 0
    ecuacion_kg_ok = kg_check is not None and abs(to_float(kg_check)) <= BC_BALANCE_CHECK_TOLERANCE_KG
    alertas.append(
        {
            "nivel": "ok" if ecuacion_kg_ok else "warn",
            "codigo": "CHECK_KG",
            "titulo": "Ecuación kg (Innova − Ventas Type 1)",
            "detalle": (
                f"Teórico {fmt_num(kg_stock_teorico)} − Real {fmt_num(kg_stock_real)} "
                f"= check {fmt_num(kg_check)} kg. "
                + (
                    "Dentro de tolerancia."
                    if ecuacion_kg_ok
                    else "Fuera de tolerancia o descuadre a revisar."
                )
            ),
            "valor": to_float(kg_check),
        }
    )
    alertas.append(
        {
            "nivel": "ok" if ecuacion_cajas_ok else "warn",
            "codigo": "CHECK_CAJAS",
            "titulo": "Ecuación cajas (Ini + Type2 − Type1 − Type3)",
            "detalle": (
                f"Teórico {int(cajas_stock_teorico or 0):,} − Real {int(cajas_stock_real or 0):,} "
                f"= check {int(cajas_check or 0):,} cajas. "
                "No es comparable 1:1 con el check de kg (unidades y fórmulas distintas)."
            ),
            "valor": int(cajas_check or 0),
        }
    )

    return {
        "loaded": True,
        "detalle": detalle,
        "detalle_all": detalle_all,
        "por_tipo": por_tipo_list,
        "por_usuario": usuarios_sorted,
        "por_dia": dias_sorted,
        "por_producto": productos_sorted,
        "alertas": alertas,
        "integridad": {
            "lotes_venta": int(integ.get("lotes_venta") or 0),
            "lotes_neg": int(integ.get("lotes_neg") or 0),
            "lotes_pos": int(integ.get("lotes_pos") or 0),
            "lotes_venta_y_neg": lotes_venta_y_neg,
            "lotes_pos_y_neg": int(integ.get("lotes_pos_y_neg") or 0),
            "lotes_pos_y_venta": int(integ.get("lotes_pos_y_venta") or 0),
        },
        "checks": {
            "cajas_check": int(cajas_check or 0),
            "kg_check": to_float(kg_check),
            "cajas_stock_teorico": int(cajas_stock_teorico or 0),
            "cajas_stock_real": int(cajas_stock_real or 0),
            "kg_stock_teorico": to_float(kg_stock_teorico),
            "kg_stock_real": to_float(kg_stock_real),
            "ecuacion_cajas_ok": ecuacion_cajas_ok,
            "ecuacion_kg_ok": bool(ecuacion_kg_ok),
        },
        "totales": {
            "cajas": total_cajas,
            "kg": total_kg,
            "movimientos": total_movs,
            "lotes": total_lotes,
            "usuarios": len(usuarios_sorted),
            "dias": len(dias_sorted),
            "productos": len(productos_sorted),
            "top_usuario": top_user,
            "top_producto": top_product,
            "type1_cajas": int(type1.get("cajas") or 0),
            "type2_cajas": int(type2.get("cajas") or 0),
            "type3_cajas": int(type3.get("cajas") or 0),
        },
        "charts": {
            "user_labels": user_labels,
            "user_cajas": [int(u["cajas"]) for u in usuarios_sorted],
            "user_colors": user_colors,
            "day_labels": day_labels,
            "day_cajas": [int(d["cajas"]) for d in dias_sorted],
            "stacked_datasets": stacked_datasets,
            "product_labels": [p["item_no"] for p in top_productos],
            "product_cajas": [int(p["cajas"]) for p in top_productos],
            "type_labels": [t["tipo_label"] for t in por_tipo_list],
            "type_cajas": [int(t["cajas"]) for t in por_tipo_list],
            "type_colors": ["#00a3c8", "#003b5c", "#c8102e"],
        },
    }


def build_bc_stock_snapshot_por_producto(
    lot_detalle: list[dict[str, Any]],
    start: dt.date,
    end: dt.date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Stock inicial y stock final del almacén E/G/Z por producto (universo completo).

    Misma regla en diario / semanal / mensual (fechas = inicio y fin del informe):
    - Stock inicial: empaque < start y sin venta/ajuste neg. antes de start
      (primera salida >= start o sin salida).
    - Stock final: empaque <= end y sin venta/ajuste neg. hasta end
      (incluye arrastre anterior al periodo + producción del periodo aún sin vender).
    Unidades: 1 lote = 1 caja; kg = kg BC del lote.
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

        if _lot_in_stock_final(fe, out, end):
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


def enrich_bc_detalle_merma_peso(
    detalle_bc: list[dict[str, Any]],
    report_data: dict[str, Any],
) -> dict[str, Any]:
    """Añade merma diaria Innova−BC al detalle del balance E/G/Z (no altera el check de stock)."""
    by_fecha: dict[str, dict[str, Any]] = {}
    for det in report_data.get("detalle_diario") or []:
        fecha = str(det.get("fecha") or "")
        if fecha:
            by_fecha[fecha] = det
    for day in report_data.get("bc_cruce_detalle") or []:
        fecha = str(day.get("fecha") or "")
        if fecha and fecha not in by_fecha:
            by_fecha[fecha] = day

    tot_innova = 0.0
    tot_bc = 0.0
    for row in detalle_bc:
        src = by_fecha.get(str(row.get("fecha") or ""), {})
        kg_innova = to_float(src.get("bc_kg_innova_enlazado"))
        kg_bc = to_float(src.get("bc_kg_bc_enlazado"))
        if "bc_kg_diferencia_enlazado" in src:
            kg_merma = to_float(src.get("bc_kg_diferencia_enlazado"))
        else:
            kg_merma = kg_innova - kg_bc
        pct = (kg_merma / kg_innova * 100.0) if kg_innova else None
        row["kg_innova_enlazado"] = kg_innova
        row["kg_bc_enlazado"] = kg_bc
        row["kg_merma_peso"] = kg_merma
        row["pct_merma_peso"] = pct
        tot_innova += kg_innova
        tot_bc += kg_bc

    kpis = report_data.get("kpis") or {}
    if kpis.get("bc_kg_innova_enlazado") is not None:
        tot_innova = to_float(kpis.get("bc_kg_innova_enlazado"))
        tot_bc = to_float(kpis.get("bc_kg_bc_enlazado"))
    tot_merma = tot_innova - tot_bc
    if kpis.get("bc_kg_diferencia_enlazado") is not None:
        tot_merma = to_float(kpis.get("bc_kg_diferencia_enlazado"))
    pct_tot = (tot_merma / tot_innova * 100.0) if tot_innova else None
    return {
        "kg_innova_enlazado": tot_innova,
        "kg_bc_enlazado": tot_bc,
        "kg_merma_peso": tot_merma,
        "pct_merma_peso": pct_tot,
    }


def build_bc_lot_movimientos_dia(
    lot_detalle: list[dict[str, Any]],
    day: dt.date,
) -> dict[str, Any]:
    """Detalle de lotes del dia: coinciden Innova∩BC, solo Innova o solo BC.

    Solo para informes de un dia (start==end). En semana/mes no se usa.
    """
    coincide: list[dict[str, Any]] = []
    solo_innova: list[dict[str, Any]] = []
    solo_bc: list[dict[str, Any]] = []

    for row in lot_detalle:
        fe = sql_row_to_date(row.get("fe_empaque"))
        pr = sql_row_to_date(row.get("prday_min"))
        out = sql_row_to_date(row.get("first_sale"))
        if not (fe == day or pr == day or out == day):
            continue

        has_innova = (
            to_float(row.get("kg_innova")) > 0
            or int(row.get("packs_innova") or 0) > 0
            or pr is not None
        )
        has_bc = bool(
            to_float(row.get("kg_bc")) > 0 or str(row.get("item_no") or "").strip()
        )

        if bool(row.get("enlazado")) and has_innova and has_bc:
            match = "coincide"
            bucket = coincide
        elif has_innova and not has_bc:
            match = "solo_innova"
            bucket = solo_innova
        elif has_bc and not has_innova:
            match = "solo_bc"
            bucket = solo_bc
        elif has_innova:
            # Innova presente pero sin enlace fiable a BC
            match = "solo_innova"
            bucket = solo_innova
        else:
            match = "solo_bc"
            bucket = solo_bc

        movs: list[str] = []
        if fe == day or pr == day:
            movs.append("Produccion")
        if out == day:
            movs.append("Primera salida")
        if not movs:
            movs.append("Movimiento")

        kg_i = to_float(row.get("kg_innova"))
        kg_b = to_float(row.get("kg_bc"))
        bucket.append(
            {
                "lot": str(row.get("lot") or ""),
                "match": match,
                "movimientos": " + ".join(movs),
                "fe_empaque": _format_sql_date(fe or pr),
                "primera_salida": _format_sql_date(out),
                "cod_producto": str(row.get("cod_producto") or row.get("item_no") or "—"),
                "tipo_nombre": str(row.get("tipo_nombre") or "—"),
                "item_no": str(row.get("item_no") or "—"),
                "kg_innova": kg_i,
                "kg_bc": kg_b,
                "kg_diff": kg_i - kg_b if (kg_i > 0 and kg_b > 0) else None,
                "estado": str(row.get("estado") or "—"),
            }
        )

    def _sort_key(r: dict[str, Any]) -> tuple:
        return (str(r.get("movimientos") or ""), str(r.get("lot") or ""))

    coincide.sort(key=_sort_key)
    solo_innova.sort(key=_sort_key)
    solo_bc.sort(key=_sort_key)

    return {
        "enabled": True,
        "fecha": day,
        "coincide": coincide,
        "solo_innova": solo_innova,
        "solo_bc": solo_bc,
        "totales": {
            "coincide": len(coincide),
            "solo_innova": len(solo_innova),
            "solo_bc": len(solo_bc),
            "total": len(coincide) + len(solo_innova) + len(solo_bc),
            "kg_innova_coincide": sum(to_float(r["kg_innova"]) for r in coincide),
            "kg_bc_coincide": sum(to_float(r["kg_bc"]) for r in coincide),
            "kg_innova_solo": sum(to_float(r["kg_innova"]) for r in solo_innova),
            "kg_bc_solo": sum(to_float(r["kg_bc"]) for r in solo_bc),
        },
    }


def build_bc_lot_movimientos_dia_table_rows(rows: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for r in rows:
        diff = r.get("kg_diff")
        diff_txt = fmt_num(diff) if diff is not None else "—"
        out.append(
            "<tr>"
            f"<td><code>{html.escape(str(r.get('lot') or ''))}</code></td>"
            f"<td>{html.escape(str(r.get('movimientos') or '—'))}</td>"
            f"<td>{html.escape(str(r.get('fe_empaque') or '—'))}</td>"
            f"<td>{html.escape(str(r.get('primera_salida') or '—'))}</td>"
            f"<td><code>{html.escape(str(r.get('cod_producto') or '—'))}</code></td>"
            f"<td>{html.escape(str(r.get('tipo_nombre') or '—'))}</td>"
            f"<td class='num'>{fmt_num(r.get('kg_innova', 0))}</td>"
            f"<td class='num'>{fmt_num(r.get('kg_bc', 0))}</td>"
            f"<td class='num'>{diff_txt}</td>"
            f"<td>{html.escape(str(r.get('estado') or '—'))}</td>"
            "</tr>"
        )
    return "\n".join(out)


def build_bc_lot_movimientos_dia_section_html(
    start: dt.date,
    end: dt.date,
    bc_balance: dict[str, Any] | None,
) -> tuple[str, str]:
    """Devuelve (boton_tab_html, panel_html). Vacío si el periodo no es un solo dia."""
    if start != end:
        return "", ""
    data = (bc_balance or {}).get("lot_movimientos_dia") or {}
    if not data.get("enabled"):
        return "", ""

    tot = data.get("totales") or {}
    fecha = format_date_es(start)
    coincide = data.get("coincide") or []
    solo_innova = data.get("solo_innova") or []
    solo_bc = data.get("solo_bc") or []
    rows_ok = build_bc_lot_movimientos_dia_table_rows(coincide)
    rows_inn = build_bc_lot_movimientos_dia_table_rows(solo_innova)
    rows_bc = build_bc_lot_movimientos_dia_table_rows(solo_bc)

    def _table(table_id: str, file_name: str, title: str, rows_html: str, empty_msg: str) -> str:
        body = rows_html if rows_html.strip() else f"<tr><td colspan='10' class='muted'>{html.escape(empty_msg)}</td></tr>"
        return f"""
      <article class="chart-card" style="margin-top:14px;">
        <div class="section-head">
          <h3>{html.escape(title)}</h3>
          <button type="button" class="btn-export" data-table-id="{table_id}" data-file-name="{file_name}">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <table id="{table_id}">
          <thead>
            <tr>
              <th>Lote</th>
              <th>Movimiento</th>
              <th>Fecha empaque / prday</th>
              <th>Primera salida</th>
              <th>Cod. producto</th>
              <th>Producto</th>
              <th class="num">Kg Innova</th>
              <th class="num">Kg BC</th>
              <th class="num">Diff (I-BC)</th>
              <th>Estado</th>
            </tr>
          </thead>
          <tbody>
            {body}
          </tbody>
        </table>
      </article>"""

    tab_btn = (
        '<button type="button" class="tab-btn" role="tab" aria-selected="false" '
        'aria-controls="tab-bc-lotes-dia" data-tab="tab-bc-lotes-dia" id="tab-btn-bc-lotes-dia">'
        "Lotes del dia</button>"
    )
    panel = f"""
      <section class="tab-panel" id="tab-bc-lotes-dia" role="tabpanel" aria-labelledby="tab-btn-bc-lotes-dia">
        <section class="grid">
          <article class="card check-ok">
            <div class="kpi-title">Coinciden (Innova ∩ BC)</div>
            <div class="kpi-value">{int(tot.get('coincide') or 0):,}</div>
            <div class="kpi-sub">{fecha} · {fmt_num(tot.get('kg_innova_coincide', 0))} kg Innova / {fmt_num(tot.get('kg_bc_coincide', 0))} kg BC</div>
          </article>
          <article class="card check-warn">
            <div class="kpi-title">Solo Innova (sin BC E/G/Z)</div>
            <div class="kpi-value">{int(tot.get('solo_innova') or 0):,}</div>
            <div class="kpi-sub">{fmt_num(tot.get('kg_innova_solo', 0))} kg</div>
          </article>
          <article class="card check-warn">
            <div class="kpi-title">Solo BC (sin CAJA Innova)</div>
            <div class="kpi-value">{int(tot.get('solo_bc') or 0):,}</div>
            <div class="kpi-sub">{fmt_num(tot.get('kg_bc_solo', 0))} kg</div>
          </article>
          <article class="card">
            <div class="kpi-title">Total lotes del dia</div>
            <div class="kpi-value">{int(tot.get('total') or 0):,}</div>
            <div class="kpi-sub">Produccion y/o primera salida el {fecha}</div>
          </article>
        </section>
        <p class="muted" style="margin-top:12px;">
          Disponible <strong>solo en informes de un dia</strong> (inicio = fin).
          En semana o mes esta pestana no se genera (el detalle por lote seria demasiado pesado).
          <strong>Coinciden</strong> = mismo lote en Innova CAJA y en BC E/G/Z.
          <strong>Solo Innova</strong> = salida CAJA sin lote en ILE E/G/Z.
          <strong>Solo BC</strong> = movimiento BC sin salida CAJA Innova ese dia.
        </p>
        {_table("bcLotesCoincideTable", "lotes_dia_coinciden", "Lotes que coinciden (Innova ∩ BC)", rows_ok, "Ningun lote coincidente.")}
        {_table("bcLotesSoloInnovaTable", "lotes_dia_solo_innova", "Solo Innova (no coinciden en BC)", rows_inn, "Ningun lote solo Innova.")}
        {_table("bcLotesSoloBcTable", "lotes_dia_solo_bc", "Solo BC (no coinciden en Innova)", rows_bc, "Ningun lote solo BC.")}
      </section>
    """
    return tab_btn, panel


def attach_bc_balance_eg_to_report(
    report_data: dict[str, Any],
    bc_balance: dict[str, Any],
    innova_lotes: list[dict[str, Any]] | None = None,
    innova_lotes_material: list[dict[str, Any]] | None = None,
    conversion_productos: dict[str, Any] | None = None,
) -> None:
    kg_stock_apertura = bc_balance["kg_stock_apertura"]

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
    conv_bascula = (conversion_productos or {}).get("by_bascula")
    lot_item_no_bc = build_lot_item_no_bc_map(lot_detalle)
    innova_caja_by_day = build_innova_caja_by_day(
        innova_lotes, report_data.get("detalle_diario")
    )
    innova_por_tipo = build_innova_caja_por_tipo(
        innova_lotes,
        innova_lotes_material,
        conv_bascula,
        lot_item_no_bc=lot_item_no_bc,
    )
    # Teorico = inicial BC + Innova CAJA − primera salida BC; real = snapshot BC.
    detalle_bc, kg_totals = build_bc_kg_detalle_diario_from_lots(
        lot_detalle,
        period_start,
        period_end,
        detalle_diario_report=report_data.get("detalle_diario"),
        innova_caja_by_day=innova_caja_by_day,
    )
    merma_peso = enrich_bc_detalle_merma_peso(detalle_bc, report_data)
    kg_stock_inicial = kg_totals["kg_stock_inicial"]
    kg_produccion = to_float(kg_totals.get("kg_produccion_innova"))  # Innova CAJA
    kg_produccion_bc = to_float(kg_totals.get("kg_empaque_mes"))  # altas BC
    kg_ventas = kg_totals["kg_salidas_mes"]  # Primera salida
    kg_stock_teorico = kg_totals["kg_stock_teorico"]
    kg_stock_real = kg_totals["kg_stock_real"]
    desvio_info = compute_desvio_stock(kg_stock_teorico, kg_stock_real)
    kg_check = desvio_info["desvio_kg"]  # real − teorico
    check_ok = bool(desvio_info["check_ok"])
    desvio_pct = desvio_info["desvio_pct"]
    semaforo = desvio_info["semaforo"]
    lotes_stock_inicial = kg_totals["lotes_stock_inicial"]
    lotes_empaque_mes = kg_totals["lotes_empaque_mes"]
    lotes_ventas = kg_totals["lotes_salidas_mes"]
    lotes_stock_final = kg_totals["lotes_stock_final"]
    lotes_stock_final_bc = sum(
        1
        for row in lot_detalle
        if _lot_in_stock_final(row.get("fe_empaque"), row.get("first_sale"), period_end)
        and to_float(row.get("kg_bc")) > 0
    )

    detalle_por_tipo = build_bc_balance_por_tipo(lot_detalle)
    detalle_por_tipo_cajas, detalle_diario_cajas = build_bc_balance_por_tipo_cajas(
        lot_detalle,
        period_start,
        period_end,
        bc_balance=bc_balance,
        detalle_diario_report=report_data.get("detalle_diario"),
        innova_caja_by_day=innova_caja_by_day,
        innova_por_tipo=innova_por_tipo,
    )
    desvio_cadena = build_bc_desvio_cadena_por_producto(
        lot_detalle,
        period_start,
        period_end,
        innova_por_tipo=innova_por_tipo,
    )
    balance_movimientos_ile = build_bc_balance_movimientos_ile(
        lot_detalle, period_start, period_end, bc_balance
    )
    stock_inicial_producto, stock_final_producto, stock_producto_totals = (
        build_bc_stock_snapshot_por_producto(lot_detalle, period_start, period_end)
    )

    cajas_stock_inicial = sum(int(t["cajas_stock_inicial"]) for t in detalle_por_tipo_cajas)
    cajas_entradas = sum(int(t["cajas_entradas"]) for t in detalle_por_tipo_cajas)
    cajas_ventas = sum(int(t["cajas_ventas"]) for t in detalle_por_tipo_cajas)
    cajas_ajustes_neg = sum(int(t.get("cajas_ajustes_neg") or 0) for t in detalle_por_tipo_cajas)
    cajas_stock_teorico = sum(int(t["cajas_stock_teorico"]) for t in detalle_por_tipo_cajas)
    cajas_stock_real = sum(int(t["cajas_stock_real"]) for t in detalle_por_tipo_cajas)
    cajas_check = cajas_stock_real - cajas_stock_teorico  # real − teórico

    for row in detalle_por_tipo_cajas:
        check_p = int(row.get("cajas_check") or 0)
        if check_p == 0:
            row["estado_check"] = "cuadrado"
        elif check_p < 0:
            row["estado_check"] = "falta_real"
        else:
            row["estado_check"] = "exceso_real"
    detalle_por_tipo_cajas.sort(
        key=lambda r: (-abs(int(r.get("cajas_check") or 0)), str(r.get("tipo_key") or ""))
    )

    cajas_estado = classify_cajas_balance_estado(cajas_check, detalle_por_tipo_cajas)
    cajas_pares = find_compensated_cajas_pairs(detalle_por_tipo_cajas)

    ajustes_neg_analisis = build_bc_ajustes_neg_analisis(
        bc_balance.get("ajustes_neg_analisis_raw"),
        bc_balance.get("movimientos_integridad"),
        cajas_check=cajas_check,
        kg_check=kg_check,
        cajas_stock_teorico=cajas_stock_teorico,
        cajas_stock_real=cajas_stock_real,
        kg_stock_teorico=kg_stock_teorico,
        kg_stock_real=kg_stock_real,
    )

    report_data["bc_balance_eg"] = {
        "loaded": True,
        "kg_stock_inicial": kg_stock_inicial,
        "lotes_stock_inicial": lotes_stock_inicial,
        "kg_stock_apertura": kg_stock_apertura,
        "lotes_stock_apertura": bc_balance["lotes_stock_apertura"],
        "kg_produccion": kg_produccion,
        "kg_produccion_bc": kg_produccion_bc,
        "kg_comparativa_innova_bc": to_float(kg_totals.get("kg_comparativa_innova_bc")),
        "packs_produccion_innova": int(kg_totals.get("packs_produccion_innova") or 0),
        "kg_ventas": kg_ventas,
        "lotes_ventas": lotes_ventas,
        "kg_ventas_t1": to_float(kg_totals.get("kg_ventas_t1")),
        "kg_ajustes_t3": to_float(kg_totals.get("kg_ajustes_t3")),
        "kg_stock_teorico": kg_stock_teorico,
        "kg_stock_real": kg_stock_real,
        "kg_stock_final": kg_stock_real,
        "lotes_stock_final": lotes_stock_final,
        "lotes_stock_final_bc": lotes_stock_final_bc,
        "kg_check": kg_check,
        "desvio_kg": kg_check,
        "desvio_pct": desvio_pct,
        "semaforo": semaforo,
        "check_ok": check_ok,
        "kg_innova_enlazado": merma_peso["kg_innova_enlazado"],
        "kg_bc_enlazado": merma_peso["kg_bc_enlazado"],
        "kg_merma_peso": merma_peso["kg_merma_peso"],
        "pct_merma_peso": merma_peso["pct_merma_peso"],
        "lotes_empaque_mes": lotes_empaque_mes,
        "kg_empaque_mes": kg_totals["kg_empaque_mes"],
        "kg_ventas_stock_antiguo_mes": bc_balance.get("kg_ventas_stock_antiguo_mes", 0.0),
        "detalle_diario": detalle_bc,
        "lot_detalle": [],
        "detalle_por_tipo": detalle_por_tipo,
        "detalle_por_tipo_cajas": detalle_por_tipo_cajas,
        "detalle_diario_cajas": detalle_diario_cajas,
        "desvio_cadena_por_producto": desvio_cadena,
        "stock_inicial_por_producto": stock_inicial_producto,
        "stock_final_por_producto": stock_final_producto,
        "stock_producto_totals": stock_producto_totals,
        "conversion_productos_count": len((conversion_productos or {}).get("by_bascula") or {}),
        "cajas_stock_inicial": cajas_stock_inicial,
        "cajas_entradas": cajas_entradas,
        "cajas_ventas": cajas_ventas,
        "cajas_ajustes_neg": cajas_ajustes_neg,
        "cajas_stock_teorico": cajas_stock_teorico,
        "cajas_stock_real": cajas_stock_real,
        "cajas_check": cajas_check,
        "cajas_estado": cajas_estado,
        "cajas_pares_compensados": cajas_pares,
        "ajustes_neg_analisis": ajustes_neg_analisis,
        "balance_movimientos_ile": balance_movimientos_ile,
        "lot_movimientos_dia": (
            build_bc_lot_movimientos_dia(lot_detalle, period_start)
            if period_start == period_end
            else {"enabled": False, "coincide": [], "solo_innova": [], "solo_bc": [], "totales": {}}
        ),
    }

    k = report_data["kpis"]
    k["bc_bal_kg_stock_inicial"] = kg_stock_inicial
    k["bc_bal_kg_stock_apertura"] = kg_stock_apertura
    k["bc_bal_kg_produccion"] = kg_produccion
    k["bc_bal_kg_produccion_bc"] = kg_produccion_bc
    k["bc_bal_kg_ventas"] = kg_ventas
    k["bc_bal_kg_stock_teorico"] = kg_stock_teorico
    k["bc_bal_kg_stock_real"] = kg_stock_real
    k["bc_bal_kg_stock_final"] = kg_stock_real
    k["bc_bal_kg_check"] = kg_check
    k["bc_bal_desvio_kg"] = kg_check
    k["bc_bal_desvio_pct"] = desvio_pct
    k["bc_bal_semaforo"] = semaforo
    k["bc_bal_check_ok"] = check_ok
    k["bc_bal_kg_merma_peso"] = merma_peso["kg_merma_peso"]
    k["bc_bal_pct_merma_peso"] = merma_peso["pct_merma_peso"]
    k["bc_bal_kg_innova_enlazado"] = merma_peso["kg_innova_enlazado"]
    k["bc_bal_kg_bc_enlazado"] = merma_peso["kg_bc_enlazado"]
    k["bc_bal_cajas_stock_inicial"] = cajas_stock_inicial
    k["bc_bal_cajas_entradas"] = cajas_entradas
    k["bc_bal_cajas_ventas"] = cajas_ventas
    k["bc_bal_cajas_ajustes_neg"] = cajas_ajustes_neg
    k["bc_bal_cajas_stock_teorico"] = cajas_stock_teorico
    k["bc_bal_cajas_stock_real"] = cajas_stock_real
    k["bc_bal_cajas_check"] = cajas_check
    k["bc_bal_cajas_estado"] = cajas_estado.get("estado")
    k["bc_bal_cajas_semaforo"] = cajas_estado.get("semaforo")
    k["bc_bal_cajas_productos_desvio"] = cajas_estado.get("productos_con_desvio")
    k["bc_adj_neg_cajas"] = int(ajustes_neg_analisis.get("totales", {}).get("cajas") or 0)
    k["bc_adj_neg_usuarios"] = int(ajustes_neg_analisis.get("totales", {}).get("usuarios") or 0)
    k["bc_adj_neg_top_usuario"] = str(
        ajustes_neg_analisis.get("totales", {}).get("top_usuario") or "—"
    )

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


def build_bc_balance_eg_tipo_table_rows(detalle_por_tipo: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for tipo in detalle_por_tipo:
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(tipo.get('cod_producto') or tipo.get('material') or '—'))}</code></td>"
            f"<td>{html.escape(str(tipo.get('tipo_nombre') or '—'))}</td>"
            f"<td>{html.escape(str(tipo.get('material') or '—'))}</td>"
            f"<td>{html.escape(str(tipo.get('pattern') or tipo.get('item_no') or '—'))}</td>"
            f"<td class='num'>{int(tipo.get('lotes', 0))}</td>"
            f"<td class='num'>{int(tipo.get('lotes_stock_final', 0))}</td>"
            f"<td class='num'>{fmt_num(tipo.get('kg_innova', 0))}</td>"
            f"<td class='num'>{fmt_num(tipo.get('kg_ventas_bc', 0))}</td>"
            f"<td class='num'>{fmt_num(tipo.get('kg_stock_final', 0))}</td>"
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
            f"<td class='num'>{fmt_num(r.get('kg_innova_enlazado', 0))}</td>"
            f"<td class='num'>{fmt_num(r.get('kg_bc_enlazado', 0))}</td>"
            f"<td class='num'>{fmt_num(r.get('kg_merma_peso', 0))}</td>"
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
            f"<td class='num'>{fmt_num(r.get('kg_innova_enlazado', 0))}</td>"
            f"<td class='num'>{fmt_num(r.get('kg_bc_enlazado', 0))}</td>"
            f"<td class='num'>{fmt_num(r.get('kg_merma_peso', 0))}</td>"
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
            "<h3>Balance BC almacenes E/G/Z</h3>"
            "<p class='muted'>BC no disponible (--skip-bc o error de conexion).</p>"
            "</article>"
        )

    detalle = bc_balance["detalle_diario"]
    rows_html = build_bc_balance_eg_table_rows(detalle)
    check_rows_html = build_bc_check_diario_table_rows(detalle)
    detalle_por_tipo = bc_balance.get("detalle_por_tipo") or []
    tipo_rows_html = build_bc_balance_eg_tipo_table_rows(detalle_por_tipo)
    tot_tipos = len(detalle_por_tipo)
    tot_lotes_det = sum(int(t.get("lotes") or 0) for t in detalle_por_tipo)
    tot_lotes_stock_final = sum(int(t.get("lotes_stock_final") or 0) for t in detalle_por_tipo)
    tot_kg_innova_lotes = sum(to_float(t.get("kg_innova")) for t in detalle_por_tipo)
    tot_kg_ventas_lotes = sum(to_float(t.get("kg_ventas_bc")) for t in detalle_por_tipo)
    tot_kg_stock_final_lotes = sum(to_float(t.get("kg_stock_final")) for t in detalle_por_tipo)
    check_ok = bool(bc_balance.get("check_ok"))
    semaforo = str(bc_balance.get("semaforo") or ("verde" if check_ok else "rojo"))
    semaforo_class = {
        "verde": "semaforo-verde",
        "amarillo": "semaforo-amarillo",
        "rojo": "semaforo-rojo",
    }.get(semaforo, "semaforo-rojo")
    check_class = semaforo_class
    desvio_pct = bc_balance.get("desvio_pct")
    desvio_pct_txt = fmt_pct(desvio_pct) if desvio_pct is not None else "N/A"
    check_label = {
        "verde": "Dentro de tolerancia (±0,5%)",
        "amarillo": "Atencion (0,5–1%)",
        "rojo": "Descuadre (>1% o teorico=0)",
    }.get(semaforo, "Descuadre")
    kg_merma_peso = to_float(bc_balance.get("kg_merma_peso"))
    pct_merma_peso = bc_balance.get("pct_merma_peso")
    merma_class = "check-warn" if abs(kg_merma_peso) > 0.01 else "check-ok"
    reglas_items = "".join(f"<li>{html.escape(rule)}</li>" for rule in PREMISA_BC_BALANCE_EG_REGLAS)
    stock_ini_sub = (
        f"ILE: empaque anterior al dia, sin Type 1/3 antes · "
        f"Ventas stock antiguo en mes: {fmt_num(bc_balance.get('kg_ventas_stock_antiguo_mes', 0))} kg"
    )
    desvio_cadena = bc_balance.get("desvio_cadena_por_producto") or []
    desvio_cadena_html = build_bc_desvio_cadena_table_rows(desvio_cadena)

    return f"""
      <section class="grid">
        <article class="card">
          <div class="kpi-title">Stock inicial BC dia 1 (kg)</div>
          <div class="kpi-value">{fmt_num(bc_balance['kg_stock_inicial'])}</div>
          <div class="kpi-sub">{html.escape(stock_ini_sub)}</div>
        </article>
        <article class="card">
          <div class="kpi-title">{LABEL_BC_PRODUCCION} Innova (kg)</div>
          <div class="kpi-value">{fmt_num(bc_balance['kg_produccion'])}</div>
          <div class="kpi-sub">{html.escape(DEF_BC_PRODUCCION)}</div>
        </article>
        <article class="card">
          <div class="kpi-title">Altas BC / empaque (kg)</div>
          <div class="kpi-value">{fmt_num(bc_balance.get('kg_produccion_bc', bc_balance.get('kg_empaque_mes', 0)))}</div>
          <div class="kpi-sub">Comparativa vs Innova: {fmt_num(bc_balance.get('kg_comparativa_innova_bc', 0))} kg (Innova − BC)</div>
        </article>
        <article class="card {merma_class}">
          <div class="kpi-title">{LABEL_BC_MERMA_PESO}</div>
          <div class="kpi-value">{fmt_num(kg_merma_peso)}</div>
          <div class="kpi-sub">Innova {fmt_num(bc_balance.get('kg_innova_enlazado', 0))} − BC {fmt_num(bc_balance.get('kg_bc_enlazado', 0))} · {fmt_pct(pct_merma_peso)} sobre Innova</div>
        </article>
        <article class="card">
          <div class="kpi-title">Primera salida BC (kg)</div>
          <div class="kpi-value">{fmt_num(bc_balance['kg_ventas'])}</div>
          <div class="kpi-sub">{bc_balance['lotes_ventas']:,} lotes · Type 1 o 3 (una vez)</div>
        </article>
        <article class="card">
          <div class="kpi-title">Stock final teorico (kg)</div>
          <div class="kpi-value">{fmt_num(bc_balance['kg_stock_teorico'])}</div>
          <div class="kpi-sub">Inicial BC + Innova CAJA − Primera salida</div>
        </article>
        <article class="card">
          <div class="kpi-title">Stock final real BC (kg)</div>
          <div class="kpi-value">{fmt_num(bc_balance['kg_stock_real'])}</div>
          <div class="kpi-sub">Almacen E/G/Z al cierre · {bc_balance['lotes_stock_final']:,} lotes</div>
        </article>
        <article class="card {check_class}">
          <div class="kpi-title">Desvio (real − teorico)</div>
          <div class="kpi-value">{fmt_num(bc_balance.get('desvio_kg', bc_balance['kg_check']))}</div>
          <div class="kpi-sub">{check_label} · {desvio_pct_txt}</div>
        </article>
      </section>
      <article class="chart-card" style="margin-top:14px;">
        <div class="section-head">
          <h3>Check BC diario (solo datos BC E/G/Z)</h3>
          <button type="button" class="btn-export" data-table-id="bcCheckDiarioTable" data-file-name="check_bc_diario_eg">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <p class="muted" style="margin-top:0;">
          Tabla para validar contra Excel: misma linea de vida del lote en kg y en cajas.
          <strong>{LABEL_BC_PRODUCCION}</strong> = {html.escape(DEF_BC_PRODUCCION)}
          <strong>Stock inicial</strong> = produccion anterior al dia; primera salida ese dia o despues.
          <strong>Primera salida</strong> = Type 1 o Type 3 del lote (una sola vez).
          <strong>Stock final real</strong> = lotes en stock al cierre del dia.
          <strong>Stock final teorico</strong> = Stock inicial + {LABEL_BC_PRODUCCION} − Primera salida (igual que cajas).
          <strong>Δ real</strong> = stock final real − stock inicial del dia.
          <strong>Check / Desvio</strong> = Stock real BC − Stock teorico (semáforo ±0,5% / ±1%).
          <strong>{LABEL_BC_MERMA_PESO}</strong> = kg Innova enlazado − kg BC del mismo lote (desvio de bascula; no sustituye el desvio de stock).
        </p>
        <table id="bcCheckDiarioTable">
          <thead>
            <tr>
              <th>Fecha</th>
              <th class="num">{LABEL_BC_PRODUCCION} (kg)</th>
              <th class="num">Lotes produccion</th>
              <th class="num">Kg Innova</th>
              <th class="num">Kg BC</th>
              <th class="num">{LABEL_BC_MERMA_PESO}</th>
              <th class="num">Stock inicial (kg)</th>
              <th class="num">Lotes</th>
              <th class="num">Primera salida (kg)</th>
              <th class="num">Lotes salida</th>
              <th class="num">Stock final real (kg)</th>
              <th class="num">Δ real (kg)</th>
              <th class="num">Stock final teorico (kg)</th>
              <th class="num">Desvio (kg)</th>
            </tr>
          </thead>
          <tbody>
            {check_rows_html}
            <tr>
              <td><strong>TOTAL / CIERRE</strong></td>
              <td class="num"><strong>{fmt_num(bc_balance.get('kg_empaque_mes', 0))}</strong></td>
              <td class="num"><strong>{bc_balance['lotes_empaque_mes']:,}</strong></td>
              <td class="num"><strong>{fmt_num(bc_balance.get('kg_innova_enlazado', 0))}</strong></td>
              <td class="num"><strong>{fmt_num(bc_balance.get('kg_bc_enlazado', 0))}</strong></td>
              <td class="num"><strong class="{merma_class}">{fmt_num(kg_merma_peso)}</strong></td>
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
          <h3>Balance de masa BC (almacenes E, G y Z)</h3>
          <button type="button" class="btn-export" data-table-id="bcBalanceEgTable" data-file-name="balance_bc_almacenes_eg">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <p class="muted" style="margin-top:0;">
          <strong>Stock inicial (dia)</strong> = produccion anterior al dia; primera salida ese dia o posteriores.
          <strong>{LABEL_BC_PRODUCCION}</strong> = {html.escape(DEF_BC_PRODUCCION)}
          <strong>Primera salida</strong> = Type 1 o Type 3 del lote (una sola vez).
          <strong>Stock final teorico</strong> = Stock inicial + {LABEL_BC_PRODUCCION} − Primera salida (igual que cajas).
          <strong>Stock final real</strong> = lotes en stock al cierre del dia.
          <strong>Check</strong> = Stock real BC − Stock teorico (desvio; semáforo ±0,5% / ±1%).
          <strong>{LABEL_BC_MERMA_PESO}</strong> = kg Innova − kg BC del lote enlazado (no altera el stock teorico).
          <strong>Stock apertura</strong> = produccion anterior al periodo sin venta previa (E/G/Z): <strong>{fmt_num(bc_balance['kg_stock_apertura'])}</strong> kg.
          <strong>Fines de semana</strong> = sin {LABEL_BC_PRODUCCION} ni Primera salida; stock inicial y finales se arrastran del dia anterior.
        </p>
        <p class="balance-formula">
          Stock final teorico fin de mes: <strong>{fmt_num(bc_balance['kg_stock_teorico'])}</strong>
          &nbsp;|&nbsp; Stock final real fin de mes: <strong>{fmt_num(bc_balance['kg_stock_real'])}</strong>
          <span class="{check_class}">(check {fmt_num(bc_balance['kg_check'])} kg)</span>
          &nbsp;|&nbsp; {LABEL_BC_MERMA_PESO}: <strong class="{merma_class}">{fmt_num(kg_merma_peso)}</strong>
          ({fmt_pct(pct_merma_peso)})
        </p>
        <table id="bcBalanceEgTable">
          <thead>
            <tr>
              <th>Fecha</th>
              <th class="num">Stock inicial (kg)</th>
              <th class="num">{LABEL_BC_PRODUCCION} (kg)</th>
              <th class="num">Kg Innova</th>
              <th class="num">Kg BC</th>
              <th class="num">{LABEL_BC_MERMA_PESO}</th>
              <th class="num">Primera salida (kg)</th>
              <th class="num">Stock final teorico (kg)</th>
              <th class="num">Stock final real (kg)</th>
              <th class="num">Desvio (kg)</th>
            </tr>
          </thead>
          <tbody>
            {rows_html}
            <tr>
              <td><strong>TOTAL / CIERRE</strong></td>
              <td class="num"><strong>{fmt_num(bc_balance['kg_stock_inicial'])}</strong></td>
              <td class="num"><strong>{fmt_num(bc_balance['kg_produccion'])}</strong></td>
              <td class="num"><strong>{fmt_num(bc_balance.get('kg_innova_enlazado', 0))}</strong></td>
              <td class="num"><strong>{fmt_num(bc_balance.get('kg_bc_enlazado', 0))}</strong></td>
              <td class="num"><strong class="{merma_class}">{fmt_num(kg_merma_peso)}</strong></td>
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
          Resumen agregado por tipo (sin detalle por lote).
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
          <h3>Desvio por producto y etapa de la cadena</h3>
          <button type="button" class="btn-export" data-table-id="bcDesvioCadenaTable" data-file-name="desvio_bc_cadena_producto">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <p class="muted" style="margin-top:0;">
          <strong>Produccion</strong>: teorico = Innova CAJA; real = altas/empaque BC (comparativa).
          <strong>Stock final</strong>: teorico = inicial + Innova − primera salida; real = snapshot BC al cierre.
          <strong>Desvio</strong> = real − teorico. Semaforo: verde ≤0,5%; amarillo ≤1%; rojo &gt;1%.
          <strong>Etapa critica</strong> = etapa con mayor |desvio %| (excl. inicial).
        </p>
        <table id="bcDesvioCadenaTable">
          <thead>
            <tr>
              <th>Cod. producto</th>
              <th>Producto</th>
              <th class="num">Prod. Innova (kg)</th>
              <th class="num">Alta BC (kg)</th>
              <th class="num">Δ prod. (kg)</th>
              <th class="num">Δ prod. %</th>
              <th class="num">Stock teo (kg)</th>
              <th class="num">Stock real BC (kg)</th>
              <th class="num">Desvio kg</th>
              <th class="num">Desvio %</th>
              <th>Semaforo</th>
              <th>Etapa critica</th>
            </tr>
          </thead>
          <tbody>
            {desvio_cadena_html}
          </tbody>
        </table>
      </article>
      <section class="premisa-box" style="margin-top:14px;">
        <h3 class="premisa-head">Reglas balance BC E/G/Z</h3>
        <ul class="premisa-list">{reglas_items}</ul>
        <p class="premisa-note muted">
          Lotes empaque BC en el periodo: {bc_balance['lotes_empaque_mes']:,}.
          Packs Innova CAJA: {int(bc_balance.get('packs_produccion_innova') or 0):,}.
          Semaforo desvio: verde ≤{BC_DESVIO_PCT_VERDE:g}% · amarillo ≤{BC_DESVIO_PCT_AMARILLO:g}% · rojo &gt;{BC_DESVIO_PCT_AMARILLO:g}%.
        </p>
      </section>
    """


def build_bc_desvio_cadena_table_rows(detalle: list[dict[str, Any]]) -> str:
    etapa_labels = {
        "produccion": "Produccion (Innova vs alta BC)",
        "salidas": "Salidas / ventas",
        "ajustes": "Ajustes neg.",
        "stock_final": "Stock final",
        "inicial": "Stock inicial",
    }
    rows: list[str] = []
    for r in detalle:
        sem = str(r.get("semaforo") or "verde")
        sem_cls = f"semaforo-{sem}"
        pct = r.get("desvio_pct")
        pct_txt = fmt_pct(to_float(pct)) if pct is not None else "N/A"
        prod_pct = r.get("desvio_produccion_pct")
        prod_pct_txt = (
            fmt_pct(to_float(prod_pct)) if prod_pct is not None else "N/A"
        )
        critica = etapa_labels.get(
            str(r.get("etapa_critica") or ""), str(r.get("etapa_critica") or "—")
        )
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(r.get('cod_producto') or r.get('tipo_key') or '—'))}</code></td>"
            f"<td>{html.escape(str(r.get('tipo_nombre') or '—'))}</td>"
            f"<td class='num'>{fmt_num(r.get('kg_prod_innova'))}</td>"
            f"<td class='num'>{fmt_num(r.get('kg_prod_bc'))}</td>"
            f"<td class='num'>{fmt_num(r.get('desvio_produccion_kg'))}</td>"
            f"<td class='num'>{prod_pct_txt}</td>"
            f"<td class='num'>{fmt_num(r.get('kg_final_teorico'))}</td>"
            f"<td class='num'>{fmt_num(r.get('kg_final_real'))}</td>"
            f"<td class='num {sem_cls}'>{fmt_num(r.get('desvio_kg'))}</td>"
            f"<td class='num {sem_cls}'>{pct_txt}</td>"
            f"<td class='{sem_cls}'>{html.escape(sem)}</td>"
            f"<td>{html.escape(critica)}</td>"
            "</tr>"
        )
    if not rows:
        return "<tr><td colspan='12' class='muted'>Sin desvio por producto.</td></tr>"
    return "\n".join(rows)


def build_bc_balance_tipo_cajas_table_rows(detalle: list[dict[str, Any]]) -> str:
    estado_labels = {
        "cuadrado": "Cuadrado",
        "falta_real": "Falta real (CHECK < 0)",
        "exceso_real": "Exceso real (CHECK > 0)",
    }
    rows: list[str] = []
    for tipo in detalle:
        check_val = int(tipo.get("cajas_check") or 0)
        check_class = " check-warn-cell" if check_val != 0 else ""
        estado = str(tipo.get("estado_check") or ("cuadrado" if check_val == 0 else ""))
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(tipo.get('cod_producto') or tipo.get('tipo_key') or '—'))}</code></td>"
            f"<td>{html.escape(str(tipo.get('tipo_nombre') or '—'))}</td>"
            f"<td>{html.escape(str(tipo.get('material') or '—'))}</td>"
            f"<td>{html.escape(str(tipo.get('pattern') or '—'))}</td>"
            f"<td class='num'>{int(tipo.get('cajas_stock_inicial') or 0):,}</td>"
            f"<td class='num'>{int(tipo.get('cajas_entradas') or 0):,}</td>"
            f"<td class='num'>{int(tipo.get('cajas_ventas') or 0):,}</td>"
            f"<td class='num'>{int(tipo.get('cajas_ajustes_neg') or 0):,}</td>"
            f"<td class='num'>{int(tipo.get('cajas_stock_teorico') or 0):,}</td>"
            f"<td class='num'>{int(tipo.get('cajas_stock_real') or 0):,}</td>"
            f"<td class='num{check_class}'>{check_val:,}</td>"
            f"<td class='{check_class}'>{html.escape(estado_labels.get(estado, estado or '—'))}</td>"
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
            f"<td class='num'>{int(r.get('cajas_ajustes_neg') or 0):,}</td>"
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
    cajas_adj = int(bc_balance.get("cajas_ajustes_neg") or 0)
    cajas_teo = int(bc_balance.get("cajas_stock_teorico") or 0)
    cajas_real = int(bc_balance.get("cajas_stock_real") or 0)
    cajas_check = int(bc_balance.get("cajas_check") or 0)
    estado_info = bc_balance.get("cajas_estado") or classify_cajas_balance_estado(
        cajas_check, detalle
    )
    pares = bc_balance.get("cajas_pares_compensados") or []
    sem = str(estado_info.get("semaforo") or "verde")
    check_class = {
        "verde": "semaforo-verde",
        "amarillo": "semaforo-amarillo",
        "rojo": "semaforo-rojo",
    }.get(sem, "check-warn")
    check_label = str(estado_info.get("label") or "")
    n_desvio = int(estado_info.get("productos_con_desvio") or 0)
    estado_code = str(estado_info.get("estado") or "")
    pares_html = ""
    if pares:
        pares_rows = "".join(
            "<tr>"
            f"<td class='num'>{int(p.get('magnitud') or 0):,}</td>"
            f"<td><code>{html.escape(str(p.get('producto_neg') or ''))}</code> "
            f"{html.escape(str(p.get('nombre_neg') or ''))}</td>"
            f"<td class='num'>{int(p.get('check_neg') or 0):,}</td>"
            f"<td><code>{html.escape(str(p.get('producto_pos') or ''))}</code> "
            f"{html.escape(str(p.get('nombre_pos') or ''))}</td>"
            f"<td class='num'>{int(p.get('check_pos') or 0):,}</td>"
            "</tr>"
            for p in pares
        )
        pares_html = f"""
      <article class="chart-card" style="margin-top:14px;">
        <h3>Pares compensados (±X) — posible mapeo distinto Innova/BC</h3>
        <p class="muted" style="margin-top:0;">
          Mismo volumen con signo opuesto en dos productos: suele indicar que el lote
          teórico y el real usan códigos distintos. No se ocultan los CHECK individuales.
        </p>
        <table id="bcCajasParesCompensadosTable">
          <thead>
            <tr>
              <th class="num">|X|</th>
              <th>Producto CHECK &lt; 0</th>
              <th class="num">CHECK</th>
              <th>Producto CHECK &gt; 0</th>
              <th class="num">CHECK</th>
            </tr>
          </thead>
          <tbody>{pares_rows}</tbody>
        </table>
      </article>
        """

    return f"""
      <section class="grid">
        <article class="card">
          <div class="kpi-title">Stock inicial (cajas)</div>
          <div class="kpi-value">{cajas_ini:,}</div>
          <div class="kpi-sub">ILE dia 1 · empaque &lt; {format_date_es(start)}</div>
        </article>
        <article class="card">
          <div class="kpi-title">{LABEL_BC_PRODUCCION} (cajas)</div>
          <div class="kpi-value">{cajas_ent:,}</div>
          <div class="kpi-sub">Innova CAJA; Item No. BC si el lote está en ILE</div>
        </article>
        <article class="card">
          <div class="kpi-title">Salidas (1ª salida)</div>
          <div class="kpi-value">{cajas_ven:,}</div>
          <div class="kpi-sub">Primera salida Type 1 o 3 · 1 lote = 1 caja</div>
        </article>
        <article class="card">
          <div class="kpi-title">Ajustes negativos BC</div>
          <div class="kpi-value">{cajas_adj:,}</div>
          <div class="kpi-sub">Type 3 ILE · informativo (lotes)</div>
        </article>
        <article class="card">
          <div class="kpi-title">Stock final teorico (cajas)</div>
          <div class="kpi-value">{cajas_teo:,}</div>
          <div class="kpi-sub">Inicial + {LABEL_BC_PRODUCCION} − Salidas</div>
        </article>
        <article class="card">
          <div class="kpi-title">Stock final real (cajas)</div>
          <div class="kpi-value">{cajas_real:,}</div>
          <div class="kpi-sub">Snapshot BC al cierre</div>
        </article>
        <article class="card {check_class}">
          <div class="kpi-title">Check global (real − teorico)</div>
          <div class="kpi-value">{cajas_check:,}</div>
          <div class="kpi-sub">Estado {html.escape(estado_code)} · {html.escape(check_label)} · {n_desvio:,} productos con CHECK ≠ 0</div>
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
          Misma base que el stock real y el check de kg (unidad: <strong>1 lote = 1 caja</strong>):
          <strong>teórico = Inicial + {LABEL_BC_PRODUCCION} − Primera salida</strong>
          (encadenado: final dia N = inicial dia N+1).
          <strong>Ajustes neg.</strong> = movimientos Type 3 ILE (informativo).
        </p>
        <table id="bcBalanceDiarioCajasTable">
          <thead>
            <tr>
              <th>Fecha</th>
              <th class="num">Stock inicial (cajas)</th>
              <th class="num">{LABEL_BC_PRODUCCION} (cajas)</th>
              <th class="num">Primera salida (cajas)</th>
              <th class="num">Ajustes neg. BC</th>
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
              <td class="num"><strong>{cajas_adj:,}</strong></td>
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
          Producto = <code>Item No.</code> BC si el lote está en ILE; conversion solo para lotes solo-Innova.
          CHECK = <strong>real − teórico</strong> (0 cuadrado; &lt;0 falta real; &gt;0 exceso real).
          Estado global: <strong>A</strong> correcto · <strong>B</strong> compensado · <strong>C</strong> desvío real.
          Periodo: <strong>{format_date_es(start)}</strong> a <strong>{format_date_es(end)}</strong>.
          {len(detalle):,} productos · {n_desvio:,} con CHECK ≠ 0.
        </p>
        <p class="balance-formula">
          Stock teorico: <strong>{cajas_teo:,}</strong>
          &nbsp;|&nbsp; Stock real: <strong>{cajas_real:,}</strong>
          <span class="{check_class}">(check {cajas_check:,} · estado {html.escape(estado_code)})</span>
        </p>
        <table id="bcBalanceTipoCajasTable">
          <thead>
            <tr>
              <th>Cod. producto BC</th>
              <th>Producto</th>
              <th>Cod. bascula Innova</th>
              <th>Pattern Innova</th>
              <th class="num">Stock inicial (cajas)</th>
              <th class="num">{LABEL_BC_PRODUCCION} (cajas)</th>
              <th class="num">Primera salida (cajas)</th>
              <th class="num">Ajustes neg. BC</th>
              <th class="num">Stock final teorico</th>
              <th class="num">Stock final real</th>
              <th class="num">Check</th>
              <th>Estado</th>
            </tr>
          </thead>
          <tbody>
            {rows_html}
            <tr>
              <td colspan="4"><strong>TOTAL / CIERRE</strong></td>
              <td class="num"><strong>{cajas_ini:,}</strong></td>
              <td class="num"><strong>{cajas_ent:,}</strong></td>
              <td class="num"><strong>{cajas_ven:,}</strong></td>
              <td class="num"><strong>{cajas_adj:,}</strong></td>
              <td class="num"><strong>{cajas_teo:,}</strong></td>
              <td class="num"><strong>{cajas_real:,}</strong></td>
              <td class="num"><strong class="{check_class}">{cajas_check:,}</strong></td>
              <td><strong>{html.escape(estado_code)} · {html.escape(check_label)}</strong></td>
            </tr>
          </tbody>
        </table>
      </article>
      {pares_html}
    """


def build_bc_movimientos_ile_table_rows(
    detalle: list[dict[str, Any]], *, as_cajas: bool
) -> str:
    rows: list[str] = []
    for tipo in detalle:
        check_val = to_float(tipo.get("check"))
        check_class = (
            " check-warn-cell"
            if abs(check_val) > (0.01 if not as_cajas else 0)
            else ""
        )

        def _fmt(v: Any, _as_cajas: bool = as_cajas) -> str:
            if _as_cajas:
                return f"{int(round(to_float(v))):,}"
            return fmt_num(v)

        rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(tipo.get('cod_producto') or tipo.get('tipo_key') or '—'))}</code></td>"
            f"<td>{html.escape(str(tipo.get('tipo_nombre') or '—'))}</td>"
            f"<td>{html.escape(str(tipo.get('material') or '—'))}</td>"
            f"<td>{html.escape(str(tipo.get('pattern') or '—'))}</td>"
            f"<td class='num'>{_fmt(tipo.get('stock_inicial'))}</td>"
            f"<td class='num'>{_fmt(tipo.get('entradas'))}</td>"
            f"<td class='num'>{_fmt(tipo.get('ventas'))}</td>"
            f"<td class='num'>{_fmt(tipo.get('ajustes_neg'))}</td>"
            f"<td class='num'>{_fmt(tipo.get('stock_teorico'))}</td>"
            f"<td class='num'>{_fmt(tipo.get('stock_real'))}</td>"
            f"<td class='num{check_class}'>{_fmt(tipo.get('check'))}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def build_bc_balance_movimientos_ile_section_html(
    start: dt.date,
    end: dt.date,
    bc_balance: dict[str, Any] | None,
) -> str:
    if not bc_balance or not bc_balance.get("loaded"):
        return (
            "<article class='chart-card'>"
            "<h3>Balance por movimientos ILE (Type 2/1/3)</h3>"
            "<p class='muted'>BC no disponible (--skip-bc o error de conexion).</p>"
            "</article>"
        )

    data = bc_balance.get("balance_movimientos_ile") or {}
    if not data:
        return (
            "<article class='chart-card'>"
            "<h3>Balance por movimientos ILE (Type 2/1/3)</h3>"
            "<p class='muted'>Sin datos de movimientos ILE para este periodo.</p>"
            "</article>"
        )

    tc = data.get("totales_cajas") or {}
    tk = data.get("totales_kg") or {}
    rows_cajas = build_bc_movimientos_ile_table_rows(
        data.get("por_tipo_cajas") or [], as_cajas=True
    )
    rows_kg = build_bc_movimientos_ile_table_rows(
        data.get("por_tipo_kg") or [], as_cajas=False
    )
    check_c = int(tc.get("check") or 0)
    check_k = to_float(tk.get("check"))
    cls_c = "check-ok" if check_c == 0 else "check-warn"
    cls_k = (
        "check-ok"
        if abs(check_k) <= BC_BALANCE_CHECK_TOLERANCE_KG
        else "check-warn"
    )
    nota_titulo = html.escape(
        str(data.get("nota_titulo") or NOTA_BC_BALANCE_MOVIMIENTOS_ILE_TITULO)
    )
    nota = html.escape(str(data.get("nota") or NOTA_BC_BALANCE_MOVIMIENTOS_ILE))

    return f"""
      <header class="panel-intro">
        <h2>Balance por movimientos ILE (auditoría)</h2>
        <p>
          Periodo: <strong>{format_date_es(start)}</strong> –
          <strong>{format_date_es(end)}</strong>.
          Fórmula:
          <code>Inicial + Type 2 − Type 1 − Type 3</code>
          con <code>ABS(Quantity)</code> / <code>ABS(Kilos)</code>.
        </p>
      </header>
      <section class="premisa-box" role="note">
        <h3 class="premisa-head">{nota_titulo}</h3>
        <p class="premisa-note" style="margin:0;">{nota}</p>
      </section>
      <section class="grid" style="margin-top:14px;">
        <article class="card">
          <div class="kpi-title">Check cajas (movimientos)</div>
          <div class="kpi-value">{check_c:,}</div>
          <div class="kpi-sub">Teórico {int(tc.get('stock_teorico') or 0):,} − Real {int(tc.get('stock_real') or 0):,}</div>
        </article>
        <article class="card {cls_c}">
          <div class="kpi-title">Cajas · Type 2 / 1 / 3</div>
          <div class="kpi-value" style="font-size:1.1rem;">
            {int(tc.get('entradas') or 0):,} / {int(tc.get('ventas') or 0):,} / {int(tc.get('ajustes_neg') or 0):,}
          </div>
          <div class="kpi-sub">ABS(Quantity) · E/G/Z</div>
        </article>
        <article class="card">
          <div class="kpi-title">Check kg (movimientos)</div>
          <div class="kpi-value">{fmt_num(check_k)}</div>
          <div class="kpi-sub">Teórico {fmt_num(tk.get('stock_teorico'))} − Real {fmt_num(tk.get('stock_real'))}</div>
        </article>
        <article class="card {cls_k}">
          <div class="kpi-title">Kg · Type 2 / 1 / 3</div>
          <div class="kpi-value" style="font-size:1.1rem;">
            {fmt_num(tk.get('entradas'))} / {fmt_num(tk.get('ventas'))} / {fmt_num(tk.get('ajustes_neg'))}
          </div>
          <div class="kpi-sub">ABS(Kilos) · E/G/Z</div>
        </article>
      </section>
      <article class="chart-card" style="margin-top:14px;">
        <div class="section-head">
          <h3>Por producto — cajas (ABS Quantity)</h3>
          <button type="button" class="btn-export" data-table-id="bcMovIleCajasTable" data-file-name="balance_movimientos_ile_cajas">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <p class="muted" style="margin-top:0;">
          Inicial / real = lotes en stock (1 lote = 1 caja).
          Flujos = <code>SUM(ABS(Quantity))</code> Type 2, 1 y 3.
        </p>
        <p class="balance-formula">
          Teórico: <strong>{int(tc.get('stock_teorico') or 0):,}</strong>
          &nbsp;|&nbsp; Real: <strong>{int(tc.get('stock_real') or 0):,}</strong>
          <span class="{cls_c}">(check {check_c:,} cajas)</span>
        </p>
        <table id="bcMovIleCajasTable">
          <thead>
            <tr>
              <th>Cod. producto BC</th>
              <th>Producto</th>
              <th>Cod. bascula</th>
              <th>Pattern</th>
              <th class="num">Stock inicial</th>
              <th class="num">Entradas T2</th>
              <th class="num">Ventas T1</th>
              <th class="num">Ajustes T3</th>
              <th class="num">Teórico</th>
              <th class="num">Real</th>
              <th class="num">Check</th>
            </tr>
          </thead>
          <tbody>
            {rows_cajas}
            <tr>
              <td colspan="4"><strong>TOTAL</strong></td>
              <td class="num"><strong>{int(tc.get('stock_inicial') or 0):,}</strong></td>
              <td class="num"><strong>{int(tc.get('entradas') or 0):,}</strong></td>
              <td class="num"><strong>{int(tc.get('ventas') or 0):,}</strong></td>
              <td class="num"><strong>{int(tc.get('ajustes_neg') or 0):,}</strong></td>
              <td class="num"><strong>{int(tc.get('stock_teorico') or 0):,}</strong></td>
              <td class="num"><strong>{int(tc.get('stock_real') or 0):,}</strong></td>
              <td class="num"><strong class="{cls_c}">{check_c:,}</strong></td>
            </tr>
          </tbody>
        </table>
      </article>
      <article class="chart-card" style="margin-top:14px;">
        <div class="section-head">
          <h3>Por producto — kilos (ABS Kilos)</h3>
          <button type="button" class="btn-export" data-table-id="bcMovIleKgTable" data-file-name="balance_movimientos_ile_kg">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <p class="muted" style="margin-top:0;">
          Inicial / real = kg de lotes en stock.
          Flujos = <code>SUM(ABS(Kilos))</code> Type 2, 1 y 3
          (en vía API híbrida, kilos enriquecidos desde Innova).
        </p>
        <p class="balance-formula">
          Teórico: <strong>{fmt_num(tk.get('stock_teorico'))}</strong>
          &nbsp;|&nbsp; Real: <strong>{fmt_num(tk.get('stock_real'))}</strong>
          <span class="{cls_k}">(check {fmt_num(check_k)} kg)</span>
        </p>
        <table id="bcMovIleKgTable">
          <thead>
            <tr>
              <th>Cod. producto BC</th>
              <th>Producto</th>
              <th>Cod. bascula</th>
              <th>Pattern</th>
              <th class="num">Stock inicial (kg)</th>
              <th class="num">Entradas T2 (kg)</th>
              <th class="num">Ventas T1 (kg)</th>
              <th class="num">Ajustes T3 (kg)</th>
              <th class="num">Teórico (kg)</th>
              <th class="num">Real (kg)</th>
              <th class="num">Desvio (kg)</th>
            </tr>
          </thead>
          <tbody>
            {rows_kg}
            <tr>
              <td colspan="4"><strong>TOTAL</strong></td>
              <td class="num"><strong>{fmt_num(tk.get('stock_inicial'))}</strong></td>
              <td class="num"><strong>{fmt_num(tk.get('entradas'))}</strong></td>
              <td class="num"><strong>{fmt_num(tk.get('ventas'))}</strong></td>
              <td class="num"><strong>{fmt_num(tk.get('ajustes_neg'))}</strong></td>
              <td class="num"><strong>{fmt_num(tk.get('stock_teorico'))}</strong></td>
              <td class="num"><strong>{fmt_num(tk.get('stock_real'))}</strong></td>
              <td class="num"><strong class="{cls_k}">{fmt_num(check_k)}</strong></td>
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
            "<h3>Stock inicial BC E/G/Z por tipo</h3>"
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
          <div class="kpi-sub">Al {format_date_es(start)} · almacenes E/G/Z · 1 lote = 1 caja</div>
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
          <h3>Stock inicial BC E/G/Z por tipo de producto</h3>
          <button type="button" class="btn-export" data-table-id="bcStockInicialProductoTable" data-file-name="bc_stock_inicial_eg_producto">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <p class="muted" style="margin-top:0;">
          Corte al <strong>{format_date_es(start)}</strong> (fecha inicio del informe).
          Lotes con <code>[Fecha empaque]</code> &lt; inicio y <strong>sin</strong> venta ni ajuste negativo
          (Entry Type 1/3) antes de esa fecha (salida ese dia o despues, o sin salida).
          Misma regla en diario, semanal y mensual. Almacenes <strong>E</strong>, <strong>G</strong> y <strong>Z</strong>.
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
            "<h3>Stock final BC E/G/Z por tipo</h3>"
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
          <div class="kpi-title">Stock final almacén (cajas)</div>
          <div class="kpi-value">{cajas:,}</div>
          <div class="kpi-sub">Al {format_date_es(end)} · E/G/Z · incluye arrastre</div>
        </article>
        <article class="card">
          <div class="kpi-title">Stock final almacén (kg)</div>
          <div class="kpi-value">{fmt_num(kg)}</div>
          <div class="kpi-sub">Empaque ≤ fin · sin Type 1/3 hasta el cierre</div>
        </article>
        <article class="card">
          <div class="kpi-title">Productos</div>
          <div class="kpi-value">{nprod:,}</div>
          <div class="kpi-sub">Cod. producto / Item No.</div>
        </article>
      </section>
      <article class="chart-card" style="margin-top:14px;">
        <div class="section-head">
          <h3>Stock final BC E/G/Z — almacén completo al cierre</h3>
          <button type="button" class="btn-export" data-table-id="bcStockFinalProductoTable" data-file-name="bc_stock_final_eg_producto">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <p class="muted" style="margin-top:0;">
          Corte al <strong>{format_date_es(end)}</strong> (fecha fin del informe).
          Todos los lotes con empaque ≤ fin y sin venta/ajuste negativo (Type 1/3) hasta ese dia
          (arrastre empacado antes de <strong>{format_date_es(start)}</strong> + producción del periodo aún sin vender).
          Misma regla en diario, semanal y mensual. Almacenes <strong>E</strong>, <strong>G</strong> y <strong>Z</strong>.
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


def build_bc_ajustes_neg_analisis_section_html(
    start: dt.date,
    end: dt.date,
    bc_balance: dict[str, Any] | None,
) -> str:
    analisis = (bc_balance or {}).get("ajustes_neg_analisis") if bc_balance else None
    if not bc_balance or not bc_balance.get("loaded") or not analisis or not analisis.get("loaded"):
        return (
            "<article class='chart-card'>"
            "<h3>Analisis movimientos ILE (Type 1/2/3)</h3>"
            "<p class='muted'>BC no disponible (--skip-bc o error de conexion).</p>"
            "</article>"
        )

    tot = analisis.get("totales") or {}
    checks = analisis.get("checks") or {}
    integ = analisis.get("integridad") or {}
    alertas = analisis.get("alertas") or []
    por_tipo = analisis.get("por_tipo") or []
    por_usuario = analisis.get("por_usuario") or []
    por_dia = analisis.get("por_dia") or []
    por_producto = analisis.get("por_producto") or []

    def _rows_tipo() -> str:
        rows: list[str] = []
        for t in por_tipo:
            ok = bool(t.get("ok_qty_vs_lote"))
            flag = "ok" if ok else "check-warn-cell"
            rows.append(
                "<tr>"
                f"<td>{html.escape(str(t.get('tipo_label') or '—'))}</td>"
                f"<td class='num'>{int(t.get('cajas') or 0):,}</td>"
                f"<td class='num'>{int(t.get('lotes') or 0):,}</td>"
                f"<td class='num {flag}'>{int(t.get('delta_qty_lotes') or 0):,}</td>"
                f"<td class='num'>{fmt_num(t.get('kg', 0))}</td>"
                f"<td class='num'>{int(t.get('movimientos') or 0):,}</td>"
                f"<td class='num'>{int(t.get('mov_qty_ne_1') or 0):,}</td>"
                f"<td class='num'>{int(t.get('mov_kg_0') or 0):,}</td>"
                f"<td class='num'>{to_float(t.get('pct_kg_0')):.0f}%</td>"
                "</tr>"
            )
        return "\n".join(rows)

    def _rows_alertas() -> str:
        if not alertas:
            return "<tr><td colspan='3'><em>Sin alertas.</em></td></tr>"
        rows: list[str] = []
        for a in alertas:
            nivel = str(a.get("nivel") or "info")
            cls = "check-ok" if nivel == "ok" else ("check-warn" if nivel == "warn" else "")
            rows.append(
                "<tr>"
                f"<td class='{cls}'><strong>{html.escape(str(a.get('titulo') or ''))}</strong></td>"
                f"<td><code>{html.escape(str(a.get('codigo') or ''))}</code></td>"
                f"<td>{html.escape(str(a.get('detalle') or ''))}</td>"
                "</tr>"
            )
        return "\n".join(rows)

    def _rows_usuario() -> str:
        rows: list[str] = []
        for u in por_usuario:
            rows.append(
                "<tr>"
                f"<td><code>{html.escape(str(u.get('usuario') or '—'))}</code></td>"
                f"<td class='num'>{int(u.get('cajas') or 0):,}</td>"
                f"<td class='num'>{fmt_num(u.get('kg', 0))}</td>"
                f"<td class='num'>{int(u.get('movimientos') or 0):,}</td>"
                f"<td class='num'>{int(u.get('lotes') or 0):,}</td>"
                f"<td class='num'>{int(u.get('dias') or 0):,}</td>"
                f"<td class='num'>{int(u.get('productos') or 0):,}</td>"
                "</tr>"
            )
        return "\n".join(rows)

    def _rows_dia() -> str:
        rows: list[str] = []
        for d in por_dia:
            rows.append(
                "<tr>"
                f"<td>{html.escape(str(d.get('fecha') or '—'))}</td>"
                f"<td class='num'>{int(d.get('cajas') or 0):,}</td>"
                f"<td class='num'>{fmt_num(d.get('kg', 0))}</td>"
                f"<td class='num'>{int(d.get('movimientos') or 0):,}</td>"
                f"<td class='num'>{int(d.get('usuarios') or 0):,}</td>"
                f"<td class='num'>{int(d.get('lotes') or 0):,}</td>"
                "</tr>"
            )
        return "\n".join(rows)

    def _rows_producto() -> str:
        rows: list[str] = []
        for p in por_producto:
            rows.append(
                "<tr>"
                f"<td><code>{html.escape(str(p.get('item_no') or '—'))}</code></td>"
                f"<td>{html.escape(str(p.get('item_description') or p.get('item_no') or '—'))}</td>"
                f"<td class='num'>{int(p.get('cajas') or 0):,}</td>"
                f"<td class='num'>{fmt_num(p.get('kg', 0))}</td>"
                f"<td class='num'>{int(p.get('movimientos') or 0):,}</td>"
                f"<td class='num'>{int(p.get('usuarios') or 0):,}</td>"
                "</tr>"
            )
        return "\n".join(rows)

    cajas = int(tot.get("cajas") or 0)
    kg = to_float(tot.get("kg"))
    n_users = int(tot.get("usuarios") or 0)
    n_dias = int(tot.get("dias") or 0)
    n_prod = int(tot.get("productos") or 0)
    top_user = str(tot.get("top_usuario") or "—")
    cajas_check = int(checks.get("cajas_check") or 0)
    kg_check_v = to_float(checks.get("kg_check"))
    cajas_cls = "check-ok" if checks.get("ecuacion_cajas_ok") else "check-warn"
    kg_cls = "check-ok" if checks.get("ecuacion_kg_ok") else "check-warn"
    doble = int(integ.get("lotes_venta_y_neg") or 0)

    return f"""
      <header class="panel-intro">
        <h2>Análisis de movimientos ILE (E/G/Z)</h2>
        <p>
          Validación de la ecuación de balance y desglose de ventas (Type 1),
          ajustes positivos (Type 2) y ajustes negativos (Type 3) por usuario, día y producto.
          Periodo: <strong>{format_date_es(start)}</strong> – <strong>{format_date_es(end)}</strong>.
        </p>
      </header>
      <article class="chart-card">
        <h3>Validación de ecuación (kg vs cajas)</h3>
        <p class="muted" style="margin-top:0;">
          Kg y cajas usan la misma base de lote:
          stock por empaque / primera salida;
          en cajas la unidad es <strong>1 lote = 1 caja</strong>.
        </p>
        <section class="grid">
          <article class="card {kg_cls}">
            <div class="kpi-title">Check kg</div>
            <div class="kpi-value">{fmt_num(kg_check_v)}</div>
            <div class="kpi-sub">Teorico {fmt_num(checks.get('kg_stock_teorico'))} − Real {fmt_num(checks.get('kg_stock_real'))}</div>
          </article>
          <article class="card {cajas_cls}">
            <div class="kpi-title">Check cajas</div>
            <div class="kpi-value">{cajas_check:,}</div>
            <div class="kpi-sub">Teorico {int(checks.get('cajas_stock_teorico') or 0):,} − Real {int(checks.get('cajas_stock_real') or 0):,}</div>
          </article>
          <article class="card">
            <div class="kpi-title">Lotes venta+neg</div>
            <div class="kpi-value">{doble:,}</div>
            <div class="kpi-sub">Doble salida en formula de cajas</div>
          </article>
          <article class="card">
            <div class="kpi-title">Type 3 cajas / kg</div>
            <div class="kpi-value" style="font-size:1.2rem;">{int(tot.get('type3_cajas') or 0):,} / {fmt_num(kg)}</div>
            <div class="kpi-sub">Muchos Type 3 tienen Kilos=0</div>
          </article>
        </section>
        <table id="bcAdjIntegridadAlertasTable" style="margin-top:12px;">
          <thead>
            <tr><th>Indicador</th><th>Codigo</th><th>Detalle</th></tr>
          </thead>
          <tbody>
            {_rows_alertas()}
          </tbody>
        </table>
      </article>

      <article class="chart-card" style="margin-top:14px;">
        <div class="section-head">
          <h3>Resumen Type 1 / 2 / 3 (coherencia Quantity vs lote)</h3>
          <button type="button" class="btn-export" data-table-id="bcAdjPorTipoTable" data-file-name="bc_movimientos_por_tipo">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <p class="muted" style="margin-top:0;">
          Si <strong>Cajas − Lotes</strong> es grande, ABS(Quantity) no equivale a 1 caja/lote
          (indicador incorrecto para un stock contado por lote).
        </p>
        <table id="bcAdjPorTipoTable">
          <thead>
            <tr>
              <th>Tipo</th>
              <th class="num">Cajas (ABS Qty)</th>
              <th class="num">Lotes</th>
              <th class="num">Cajas − Lotes</th>
              <th class="num">Kg</th>
              <th class="num">Movimientos</th>
              <th class="num">Mov. Qty≠1</th>
              <th class="num">Mov. Kg=0</th>
              <th class="num">% Kg=0</th>
            </tr>
          </thead>
          <tbody>
            {_rows_tipo()}
          </tbody>
        </table>
      </article>

      <section class="grid" style="margin-top:14px;">
        <article class="card">
          <div class="kpi-title">Ventas Type 1</div>
          <div class="kpi-value">{int(tot.get('type1_cajas') or 0):,}</div>
          <div class="kpi-sub">ABS(Quantity) · E/G/Z</div>
        </article>
        <article class="card">
          <div class="kpi-title">Ajustes + Type 2</div>
          <div class="kpi-value">{int(tot.get('type2_cajas') or 0):,}</div>
          <div class="kpi-sub">Entradas BC</div>
        </article>
        <article class="card">
          <div class="kpi-title">Ajustes − Type 3</div>
          <div class="kpi-value">{cajas:,}</div>
          <div class="kpi-sub">Top usuario: {html.escape(top_user)}</div>
        </article>
        <article class="card">
          <div class="kpi-title">Usuarios Type 3</div>
          <div class="kpi-value">{n_users:,}</div>
          <div class="kpi-sub">Id. usuario · {n_dias:,} dias · {n_prod:,} productos</div>
        </article>
      </section>

      <section class="charts" style="margin-top:14px;">
        <article class="chart-card chart-panel">
          <h3>Volumen por tipo (cajas)</h3>
          <button type="button" class="btn-max" data-chart="adjByType">Maximizar</button>
          <canvas id="adjByType"></canvas>
        </article>
        <article class="chart-card chart-panel">
          <h3>Ajustes negativos por usuario (cajas)</h3>
          <button type="button" class="btn-max" data-chart="adjNegByUser">Maximizar</button>
          <canvas id="adjNegByUser"></canvas>
        </article>
        <article class="chart-card chart-panel">
          <h3>Evolucion diaria Type 3 (apilado por usuario)</h3>
          <button type="button" class="btn-max" data-chart="adjNegByDayStacked">Maximizar</button>
          <canvas id="adjNegByDayStacked"></canvas>
        </article>
        <article class="chart-card chart-panel">
          <h3>Top productos Type 3 (cajas)</h3>
          <button type="button" class="btn-max" data-chart="adjNegByProduct">Maximizar</button>
          <canvas id="adjNegByProduct"></canvas>
        </article>
      </section>

      <article class="chart-card" style="margin-top:14px;">
        <div class="section-head">
          <h3>Type 3 por usuario</h3>
          <button type="button" class="btn-export" data-table-id="bcAdjNegUsuarioTable" data-file-name="bc_ajustes_neg_por_usuario">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <table id="bcAdjNegUsuarioTable">
          <thead>
            <tr>
              <th>Usuario</th>
              <th class="num">Cajas</th>
              <th class="num">Kg</th>
              <th class="num">Movimientos</th>
              <th class="num">Lotes</th>
              <th class="num">Dias</th>
              <th class="num">Productos</th>
            </tr>
          </thead>
          <tbody>
            {_rows_usuario()}
            <tr>
              <td><strong>TOTAL</strong></td>
              <td class="num"><strong>{cajas:,}</strong></td>
              <td class="num"><strong>{fmt_num(kg)}</strong></td>
              <td class="num"><strong>{int(tot.get('movimientos') or 0):,}</strong></td>
              <td class="num"><strong>{int(tot.get('lotes') or 0):,}</strong></td>
              <td class="num"><strong>{n_dias:,}</strong></td>
              <td class="num"><strong>{n_prod:,}</strong></td>
            </tr>
          </tbody>
        </table>
      </article>

      <article class="chart-card" style="margin-top:14px;">
        <div class="section-head">
          <h3>Type 3 por dia</h3>
          <button type="button" class="btn-export" data-table-id="bcAdjNegDiaTable" data-file-name="bc_ajustes_neg_por_dia">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <table id="bcAdjNegDiaTable">
          <thead>
            <tr>
              <th>Fecha</th>
              <th class="num">Cajas</th>
              <th class="num">Kg</th>
              <th class="num">Movimientos</th>
              <th class="num">Usuarios</th>
              <th class="num">Lotes</th>
            </tr>
          </thead>
          <tbody>
            {_rows_dia()}
          </tbody>
        </table>
      </article>

      <article class="chart-card" style="margin-top:14px;">
        <div class="section-head">
          <h3>Type 3 por producto</h3>
          <button type="button" class="btn-export" data-table-id="bcAdjNegProductoTable" data-file-name="bc_ajustes_neg_por_producto">
            <span class="excel-icon">X</span>
            Exportar Excel
          </button>
        </div>
        <table id="bcAdjNegProductoTable">
          <thead>
            <tr>
              <th>Cod. producto</th>
              <th>Descripcion</th>
              <th class="num">Cajas</th>
              <th class="num">Kg</th>
              <th class="num">Movimientos</th>
              <th class="num">Usuarios</th>
            </tr>
          </thead>
          <tbody>
            {_rows_producto()}
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
    bc_balance_movimientos_ile_html = build_bc_balance_movimientos_ile_section_html(
        start, end, bc_balance_eg
    )
    bc_stock_inicial_html = build_bc_stock_inicial_section_html(start, end, bc_balance_eg)
    bc_stock_final_html = build_bc_stock_final_section_html(start, end, bc_balance_eg)
    bc_ajustes_neg_analisis_html = build_bc_ajustes_neg_analisis_section_html(
        start, end, bc_balance_eg
    )
    bc_lotes_dia_tab_btn, bc_lotes_dia_panel = build_bc_lot_movimientos_dia_section_html(
        start, end, bc_balance_eg
    )
    adj_charts = ((bc_balance_eg or {}).get("ajustes_neg_analisis") or {}).get("charts") or {}
    adj_user_labels = adj_charts.get("user_labels") or []
    adj_user_cajas = adj_charts.get("user_cajas") or []
    adj_user_colors = adj_charts.get("user_colors") or []
    adj_day_labels = adj_charts.get("day_labels") or []
    adj_stacked = adj_charts.get("stacked_datasets") or []
    adj_product_labels = adj_charts.get("product_labels") or []
    adj_product_cajas = adj_charts.get("product_cajas") or []
    adj_type_labels = adj_charts.get("type_labels") or []
    adj_type_cajas = adj_charts.get("type_cajas") or []
    adj_type_colors = adj_charts.get("type_colors") or []
    bc_loaded = bool(data.get("bc_cruce"))
    bc_note = (
        "Enlace por proc_packs.number = BC Item Ledger Entry [Lot No.]. "
        "Ventas BC solo almacenes E, G y Z. Pedido desde Sales Shipment Line."
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
    report_footnotes_html = build_report_footnotes_html()
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
    .card.semaforo-verde, .semaforo-verde {{
      color: #0a7a4a;
    }}
    .card.semaforo-verde {{
      border-color: #86efac;
      background: #ecfdf5;
    }}
    .card.semaforo-amarillo, .semaforo-amarillo {{
      color: #a16207;
    }}
    .card.semaforo-amarillo {{
      border-color: #fde68a;
      background: #fffbeb;
    }}
    .card.semaforo-rojo, .semaforo-rojo {{
      color: #b91c1c;
    }}
    .card.semaforo-rojo {{
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
      margin: 0;
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
    .report-footnotes {{
      margin: 20px 0 8px 0;
      display: grid;
      gap: 12px;
    }}
    .nota-bc-ajustes {{
      margin: 0;
      padding: 14px 16px;
      border: 1px solid #8fc6de;
      border-radius: 12px;
      background: #eef7fb;
    }}
    .nota-bc-ajustes-titulo {{
      margin: 0 0 6px 0;
      font-size: 14px;
      font-weight: 700;
      color: var(--brand);
    }}
    .nota-bc-ajustes-texto {{
      margin: 0;
      font-size: 13px;
      line-height: 1.45;
      color: #0b2740;
    }}
    .panel-intro {{
      margin: 0 0 16px 0;
      padding: 16px 18px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: linear-gradient(135deg, #f7fbfe 0%, #eef6fa 100%);
    }}
    .panel-intro h2 {{
      margin: 0 0 8px 0;
      font-size: 1.25rem;
      color: var(--brand);
    }}
    .panel-intro p {{
      margin: 0;
      font-size: 14px;
      line-height: 1.5;
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
      <button type="button" class="tab-btn" role="tab" aria-selected="false" aria-controls="tab-bc-balance" data-tab="tab-bc-balance" id="tab-btn-bc-balance">Balance BC E/G/Z</button>
      {bc_lotes_dia_tab_btn}
      <button type="button" class="tab-btn" role="tab" aria-selected="false" aria-controls="tab-bc-tipo-cajas" data-tab="tab-bc-tipo-cajas" id="tab-btn-bc-tipo-cajas">Balance por tipo (cajas)</button>
      <button type="button" class="tab-btn" role="tab" aria-selected="false" aria-controls="tab-bc-mov-ile" data-tab="tab-bc-mov-ile" id="tab-btn-bc-mov-ile">Movimientos ILE (T2/1/3)</button>
      <button type="button" class="tab-btn" role="tab" aria-selected="false" aria-controls="tab-bc-stock-ini" data-tab="tab-bc-stock-ini" id="tab-btn-bc-stock-ini">Stock inicial BC E/G/Z</button>
      <button type="button" class="tab-btn" role="tab" aria-selected="false" aria-controls="tab-bc-stock-fin" data-tab="tab-bc-stock-fin" id="tab-btn-bc-stock-fin">Stock final BC E/G/Z</button>
      <button type="button" class="tab-btn" role="tab" aria-selected="false" aria-controls="tab-bc-adj-neg" data-tab="tab-bc-adj-neg" id="tab-btn-bc-adj-neg">Análisis ILE (1/2/3)</button>
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

      {bc_lotes_dia_panel}

      <section class="tab-panel" id="tab-bc-tipo-cajas" role="tabpanel" aria-labelledby="tab-btn-bc-tipo-cajas">
        {bc_balance_tipo_cajas_html}
      </section>

      <section class="tab-panel" id="tab-bc-mov-ile" role="tabpanel" aria-labelledby="tab-btn-bc-mov-ile">
        {bc_balance_movimientos_ile_html}
      </section>

      <section class="tab-panel" id="tab-bc-stock-ini" role="tabpanel" aria-labelledby="tab-btn-bc-stock-ini">
        {bc_stock_inicial_html}
      </section>

      <section class="tab-panel" id="tab-bc-stock-fin" role="tabpanel" aria-labelledby="tab-btn-bc-stock-fin">
        {bc_stock_final_html}
      </section>

      <section class="tab-panel" id="tab-bc-adj-neg" role="tabpanel" aria-labelledby="tab-btn-bc-adj-neg">
        {bc_ajustes_neg_analisis_html}
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

    {report_footnotes_html}

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
const adjNegUserLabels = {json.dumps(adj_user_labels)};
const adjNegUserCajas = {json.dumps(adj_user_cajas)};
const adjNegUserColors = {json.dumps(adj_user_colors)};
const adjNegDayLabels = {json.dumps(adj_day_labels)};
const adjNegStacked = {json.dumps(adj_stacked)};
const adjNegProductLabels = {json.dumps(adj_product_labels)};
const adjNegProductCajas = {json.dumps(adj_product_cajas)};
const adjTypeLabels = {json.dumps(adj_type_labels)};
const adjTypeCajas = {json.dumps(adj_type_cajas)};
const adjTypeColors = {json.dumps(adj_type_colors)};

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
  }},
  adjNegByUser: {{
    type: 'bar',
    data: {{
      labels: adjNegUserLabels,
      datasets: [{{
        label: 'Cajas ajuste neg.',
        data: adjNegUserCajas,
        backgroundColor: adjNegUserColors
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{ y: {{ beginAtZero: true }} }}
    }}
  }},
  adjByType: {{
    type: 'bar',
    data: {{
      labels: adjTypeLabels,
      datasets: [{{
        label: 'Cajas (ABS Quantity)',
        data: adjTypeCajas,
        backgroundColor: adjTypeColors
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{ y: {{ beginAtZero: true }} }}
    }}
  }},
  adjNegByDayStacked: {{
    type: 'bar',
    data: {{
      labels: adjNegDayLabels,
      datasets: adjNegStacked
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      scales: {{
        x: {{ stacked: true }},
        y: {{ stacked: true, beginAtZero: true }}
      }}
    }}
  }},
  adjNegByProduct: {{
    type: 'bar',
    data: {{
      labels: adjNegProductLabels,
      datasets: [{{
        label: 'Cajas ajuste neg.',
        data: adjNegProductCajas,
        backgroundColor: 'rgba(200,16,46,.75)'
      }}]
    }},
    options: {{
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{ x: {{ beginAtZero: true }} }}
    }}
  }}
}};

const chartInstances = {{}};
for (const [chartId, chartConfig] of Object.entries(chartConfigs)) {{
  const canvas = document.getElementById(chartId);
  if (!canvas) continue;
  chartInstances[chartId] = new Chart(canvas, chartConfig);
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
  if (tabId === 'tab-graficas' || tabId === 'tab-bc-adj-neg') {{
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
    {{ sheetName: 'Balance BC E/G/Z', tableId: 'bcBalanceEgTable' }},
    {{ sheetName: 'Lotes coinciden', tableId: 'bcLotesCoincideTable' }},
    {{ sheetName: 'Lotes solo Innova', tableId: 'bcLotesSoloInnovaTable' }},
    {{ sheetName: 'Lotes solo BC', tableId: 'bcLotesSoloBcTable' }},
    {{ sheetName: 'BC E/G/Z por tipo', tableId: 'bcBalanceEgTipoTable' }},
    {{ sheetName: 'BC diario cajas', tableId: 'bcBalanceDiarioCajasTable' }},
    {{ sheetName: 'BC tipo cajas', tableId: 'bcBalanceTipoCajasTable' }},
    {{ sheetName: 'Stock ini BC', tableId: 'bcStockInicialProductoTable' }},
    {{ sheetName: 'Stock fin BC', tableId: 'bcStockFinalProductoTable' }},
    {{ sheetName: 'Adj integridad', tableId: 'bcAdjIntegridadAlertasTable' }},
    {{ sheetName: 'Adj por tipo', tableId: 'bcAdjPorTipoTable' }},
    {{ sheetName: 'Adj neg usuario', tableId: 'bcAdjNegUsuarioTable' }},
    {{ sheetName: 'Adj neg dia', tableId: 'bcAdjNegDiaTable' }},
    {{ sheetName: 'Adj neg producto', tableId: 'bcAdjNegProductoTable' }},
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

    if not args.skip_bc and resolve_bc_source(args) == "api":
        try:
            from bc_ile_hybrid import build_bundle_from_ile, download_ile_eg_api

            print(
                "Business Central via API (destino: API AL custom; "
                "puente ODataV4 + enrich Innova si la custom no esta publicada)..."
            )
            raw_ile, transport = download_ile_eg_api(start, end, verbose=True)
            print("Reconectando Innova para enriquecimiento prday/weight...")
            innova_enrich = pymssql.connect(
                server=args.server,
                user=db_user,
                password=db_password,
                database=args.database,
                login_timeout=8,
                timeout=600,
            )
            try:
                bc_data, bc_balance = build_bundle_from_ile(
                    innova_enrich,
                    raw_ile,
                    start,
                    end,
                    transport=transport,
                    verbose=True,
                )
            finally:
                innova_enrich.close()

            attach_bc_cruce_to_report(report_data, bc_data, innova_lotes)
            print("Cruce BC (API) cargado correctamente.")

            conversion_productos = empty_bc_conversion_productos()
            try:
                bc_server, bc_database, bc_user, bc_password = resolve_bc_credentials(args)
                print("Conversion productos: intentando Azure SQL (mapa bascula)...")
                conv_conn = pymssql.connect(
                    server=bc_server,
                    user=bc_user,
                    password=bc_password,
                    database=bc_database,
                    login_timeout=min(args.bc_login_timeout, 30),
                    timeout=min(args.bc_timeout, 120),
                )
                try:
                    conversion_productos = fetch_bc_conversion_productos(conv_conn)
                    print("Conversion productos cargada desde SQL BC.")
                finally:
                    conv_conn.close()
            except Exception as conv_exc:
                print(
                    f"Aviso: Conversion productos no disponible ({conv_exc}). "
                    "Se usa Item No. BC / material Innova."
                )

            attach_bc_balance_eg_to_report(
                report_data,
                bc_balance,
                innova_lotes,
                innova_lotes_material,
                conversion_productos,
            )
            print("Balance BC E/G/Z (API hibrido) cargado correctamente.")
        except Exception as bc_api_exc:
            print(f"Aviso: no se pudo cargar BC via API ({bc_api_exc}).")

    if not args.skip_bc and resolve_bc_source(args) == "sql":
        bc_server, bc_database, bc_user, bc_password = resolve_bc_credentials(args)
        bc_balance_timeout = max(args.bc_timeout * 2, 1800)
        try:
            print("Conectando a Business Central SQL (cruce por lote)...")
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
                f"Conectando a Business Central SQL "
                f"(balance E/G/Z, timeout {bc_balance_timeout}s)..."
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
                print("Consultando balance BC almacenes E/G/Z...")
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
                print("Balance BC E/G/Z cargado correctamente.")
            finally:
                bc_balance_conn.close()
        except Exception as bc_balance_exc:
            print(f"Aviso: no se pudo cargar balance BC E/G/Z ({bc_balance_exc}).")
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
