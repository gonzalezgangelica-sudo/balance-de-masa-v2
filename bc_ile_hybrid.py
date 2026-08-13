"""Fetch BC ILE via API + enriquecimiento Innova (prday/weight) y agregacion balance E/G/Z.

Reproduce la estructura de retorno de fetch_bc_balance_eg / fetch_bc_salidas_pedido
sin depender de campos custom SQL ([Kilos], [Fecha empaque], [Id. usuario]) cuando
no vienen en la API.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any

import pymssql

from bc_api_client import BcIleApiClient
from generar_reporte_biomasa import (
    BC_ILE_HISTORY_FROM,
    SQL_INNOVA_LOT,
    SQL_SALIDA_CAJA,
    bc_ile_effective_start,
    build_stock_inicial_ile_diario,
    fetch_rows,
    merge_lots_for_stock_inicial_ile,
    to_float,
)


def fetch_innova_lot_enrichment(
    conn: pymssql.Connection,
    lots: set[str] | None = None,
    *,
    prday_from: dt.date | None = None,
    prday_to: dt.date | None = None,
) -> dict[str, dict[str, Any]]:
    """Mapa lote -> {prday_min, prday_max, kg, packs, material} desde proc_packs CAJA.

    Preferir filtro por rango de prday (rapido). Si se pasan `lots`, se filtra en Python
    tras una sola agregacion por rango (evita IN gigantes lentos en SQL Server).
    """
    cursor = conn.cursor()
    where = [SQL_SALIDA_CAJA, f"NULLIF(LTRIM(RTRIM({SQL_INNOVA_LOT})), '') IS NOT NULL"]
    params: list[Any] = []
    if prday_from is not None and prday_to is not None:
        where.append("p.prday >= %s AND p.prday < DATEADD(day, 1, %s)")
        params.extend([prday_from.isoformat(), prday_to.isoformat()])
    elif lots:
        # Ventana amplia desde historico ILE: una sola pasada indexable por prday
        where.append("p.prday >= %s")
        params.append(BC_ILE_HISTORY_FROM.isoformat())

    q = f"""
    SELECT
      {SQL_INNOVA_LOT} AS lot,
      MIN(CAST(p.prday AS date)) AS prday_min,
      MAX(CAST(p.prday AS date)) AS prday_max,
      SUM(CAST(p.weight AS float)) AS kg,
      COUNT(*) AS packs,
      MAX(NULLIF(LTRIM(RTRIM(CAST(p.material AS varchar(50)))), '')) AS material
    FROM dbo.proc_packs p
    JOIN dbo.proc_materials m ON m.material = p.material
    WHERE {" AND ".join(where)}
    GROUP BY {SQL_INNOVA_LOT};
    """
    rows = fetch_rows(cursor, q, tuple(params))
    result = {
        str(row["lot"]).strip(): {
            "prday_min": row.get("prday_min"),
            "prday_max": row.get("prday_max"),
            "kg": to_float(row.get("kg")),
            "packs": int(row.get("packs") or 0),
            "material": str(row.get("material") or "").strip(),
        }
        for row in rows
    }
    if lots is not None:
        return {lot: meta for lot, meta in result.items() if lot in lots}
    return result


def _as_date(value: Any) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return None


def enrich_ile_rows(
    rows: list[dict[str, Any]],
    lot_meta: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rellena kilos / fecha_empaque desde Innova cuando faltan en la API."""
    enriched: list[dict[str, Any]] = []
    for row in rows:
        lot = str(row.get("lot") or "").strip()
        meta = lot_meta.get(lot) or {}
        lot_kg = to_float(meta.get("kg"))
        packs = int(meta.get("packs") or 0)
        unit_kg = (lot_kg / packs) if packs > 0 else lot_kg
        kilos = row.get("kilos")
        if kilos is None:
            kilos = abs(to_float(row.get("quantity"))) * unit_kg if unit_kg else 0.0
        fecha = row.get("fecha_empaque") or _as_date(meta.get("prday_min"))
        usuario = str(row.get("usuario") or "").strip() or "(sin usuario)"
        enriched.append(
            {
                **row,
                "lot": lot,
                "kilos": to_float(kilos),
                "fecha_empaque": fecha,
                "usuario": usuario,
                "lot_kg": lot_kg,
                "lot_packs": packs,
            }
        )
    return enriched


def _group_sum_daily(
    rows: list[dict[str, Any]],
    *,
    entry_types: set[int],
    start: dt.date,
    end: dt.date,
) -> list[dict[str, Any]]:
    by_day: dict[dt.date, dict[str, Any]] = defaultdict(lambda: {"kg": 0.0, "lotes": set()})
    for row in rows:
        if row["entry_type"] not in entry_types:
            continue
        d = row["posting_date"]
        if d < start or d > end:
            continue
        by_day[d]["kg"] += abs(to_float(row["kilos"]))
        if row["lot"]:
            by_day[d]["lotes"].add(row["lot"])
    return [
        {"fecha": d, "kg": to_float(v["kg"]), "lotes": len(v["lotes"])}
        for d, v in sorted(by_day.items())
    ]


def _group_daily_producto(
    rows: list[dict[str, Any]],
    *,
    entry_types: set[int],
    start: dt.date,
    end: dt.date,
) -> list[dict[str, Any]]:
    key_map: dict[tuple[dt.date, str], dict[str, Any]] = {}
    for row in rows:
        if row["entry_type"] not in entry_types:
            continue
        d = row["posting_date"]
        if d < start or d > end:
            continue
        item = str(row.get("item_no") or "").strip()
        if not item:
            continue
        key = (d, item)
        slot = key_map.setdefault(
            key, {"fecha": d, "item_no": item, "qty": 0.0, "kg": 0.0, "lotes": set()}
        )
        slot["qty"] += abs(to_float(row["quantity"]))
        slot["kg"] += abs(to_float(row["kilos"]))
        if row["lot"]:
            slot["lotes"].add(row["lot"])
    out = []
    for (_d, _i), slot in sorted(key_map.items()):
        out.append(
            {
                "fecha": slot["fecha"],
                "item_no": slot["item_no"],
                "qty": to_float(slot["qty"]),
                "kg": to_float(slot["kg"]),
                "lotes": len(slot["lotes"]),
            }
        )
    return out


def _lot_agg(
    rows: list[dict[str, Any]],
    *,
    entry_types: set[int],
    start: dt.date,
    end: dt.date,
    date_field_first: str,
    date_field_last: str,
) -> list[dict[str, Any]]:
    by_lot: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["entry_type"] not in entry_types:
            continue
        d = row["posting_date"]
        if d < start or d > end:
            continue
        lot = row["lot"]
        if not lot:
            continue
        slot = by_lot.setdefault(
            lot,
            {
                "lot": lot,
                "kg": 0.0,
                "qty": 0.0,
                "item_no": "",
                "item_description": "",
                "fe_empaque": None,
                date_field_first: None,
                date_field_last: None,
            },
        )
        slot["kg"] += abs(to_float(row["kilos"]))
        slot["qty"] += abs(to_float(row["quantity"]))
        if row.get("item_no") and not slot["item_no"]:
            slot["item_no"] = row["item_no"]
        if row.get("item_description") and not slot["item_description"]:
            slot["item_description"] = row["item_description"]
        fe = row.get("fecha_empaque")
        if fe and (slot["fe_empaque"] is None or fe < slot["fe_empaque"]):
            slot["fe_empaque"] = fe
        if slot[date_field_first] is None or d < slot[date_field_first]:
            slot[date_field_first] = d
        if slot[date_field_last] is None or d > slot[date_field_last]:
            slot[date_field_last] = d
    return [by_lot[k] for k in sorted(by_lot)]


def build_lots_stock_vivo_from_ile(
    rows: list[dict[str, Any]],
    lot_meta: dict[str, dict[str, Any]],
    end: dt.date,
) -> list[dict[str, Any]]:
    """Universo de lotes en almacén E/G/Z para stock real: empaque <= fin, con 1ª salida.

    Incluye empacados antes del periodo aún sin vender (arrastre), no solo el periodo.
    """
    by_lot: dict[str, dict[str, Any]] = {}
    for row in rows:
        lot = str(row.get("lot") or "").strip()
        if not lot:
            continue
        slot = by_lot.setdefault(
            lot,
            {
                "lot": lot,
                "fe_empaque": None,
                "kg": 0.0,
                "first_out": None,
                "first_sale": None,
                "item_no": "",
                "item_description": "",
            },
        )
        fe = row.get("fecha_empaque")
        if fe is not None and (slot["fe_empaque"] is None or fe < slot["fe_empaque"]):
            slot["fe_empaque"] = fe
        if row.get("entry_type") in (1, 3):
            pd = row.get("posting_date")
            if pd is not None and (slot["first_out"] is None or pd < slot["first_out"]):
                slot["first_out"] = pd
                slot["first_sale"] = pd
        if row.get("lot_kg"):
            slot["kg"] = to_float(row["lot_kg"])
        elif to_float(row.get("kilos")) > slot["kg"]:
            slot["kg"] = abs(to_float(row.get("kilos")))
        if row.get("item_no") and not slot["item_no"]:
            slot["item_no"] = str(row.get("item_no") or "")
        if row.get("item_description") and not slot["item_description"]:
            slot["item_description"] = str(row.get("item_description") or "")

    for lot, meta in (lot_meta or {}).items():
        lot = str(lot or "").strip()
        if not lot:
            continue
        prday = _as_date(meta.get("prday_min"))
        kg = to_float(meta.get("kg"))
        slot = by_lot.get(lot)
        if slot is None:
            if prday is None or prday > end:
                continue
            # Solo si el lote aparece en ILE (ya en by_lot). Si no, no inventar stock.
            continue
        if prday is not None and (slot["fe_empaque"] is None or prday < slot["fe_empaque"]):
            slot["fe_empaque"] = prday
        if kg > 0:
            slot["kg"] = kg

    vivo: list[dict[str, Any]] = []
    for lot in sorted(by_lot):
        slot = by_lot[lot]
        fe = slot.get("fe_empaque")
        if fe is None or fe > end:
            continue
        if fe < BC_ILE_HISTORY_FROM:
            continue
        vivo.append(slot)
    return vivo


def build_balance_from_ile(
    rows: list[dict[str, Any]],
    lot_meta: dict[str, dict[str, Any]],
    start: dt.date,
    end: dt.date,
    *,
    transport: str,
) -> dict[str, Any]:
    """Agrega balance E/G/Z equivalente a fetch_bc_balance_eg."""
    ile_start = bc_ile_effective_start(start)
    period_rows = [
        r
        for r in rows
        if r["posting_date"] >= ile_start
        and r["posting_date"] <= end
        and r["posting_date"] >= BC_ILE_HISTORY_FROM
    ]

    ventas_diario = _group_sum_daily(period_rows, entry_types={1}, start=start, end=end)

    # Lotes con salida en mes y empaque anterior al posting
    lots_stock_antiguo_mes: list[dict[str, Any]] = []
    antigo_by_lot: dict[str, dict[str, Any]] = {}
    for row in period_rows:
        if row["entry_type"] not in (1, 3):
            continue
        lot = row["lot"]
        if not lot:
            continue
        fe = row.get("fecha_empaque")
        if fe is None or fe >= row["posting_date"]:
            continue
        slot = antigo_by_lot.setdefault(
            lot,
            {
                "lot": lot,
                "fe_empaque": fe,
                "kg": to_float(row.get("lot_kg") or row.get("kilos")),
                "first_out": row["posting_date"],
                "item_no": row.get("item_no") or "",
                "item_description": row.get("item_description") or "",
            },
        )
        if fe < (slot["fe_empaque"] or fe):
            slot["fe_empaque"] = fe
        if row["posting_date"] < slot["first_out"]:
            slot["first_out"] = row["posting_date"]
        if row.get("lot_kg"):
            slot["kg"] = to_float(row["lot_kg"])
    lots_stock_antiguo_mes = [antigo_by_lot[k] for k in sorted(antigo_by_lot)]

    ventas_stock_antiguo_diario = _group_sum_daily(
        [
            r
            for r in period_rows
            if r["entry_type"] == 1
            and r.get("fecha_empaque") is not None
            and r["fecha_empaque"] < r["posting_date"]
        ],
        entry_types={1},
        start=start,
        end=end,
    )

    # Produccion (Salidas CAJA): lotes Innova con prday en periodo presentes en ILE E/G/Z
    # (alta stock E/G/Z por coincidencia de lote; no es salida de almacen BC).
    ile_lots = {r["lot"] for r in period_rows if r["lot"]}
    first_out_by_lot: dict[str, dt.date] = {}
    item_by_lot: dict[str, tuple[str, str]] = {}
    for row in period_rows:
        lot = row["lot"]
        if not lot:
            continue
        if row["entry_type"] in (1, 3):
            prev = first_out_by_lot.get(lot)
            if prev is None or row["posting_date"] < prev:
                first_out_by_lot[lot] = row["posting_date"]
        if lot not in item_by_lot and row.get("item_no"):
            item_by_lot[lot] = (row["item_no"], row.get("item_description") or "")

    empaque_by_day: dict[dt.date, dict[str, Any]] = defaultdict(
        lambda: {"kg": 0.0, "lotes": set()}
    )
    lot_snapshot: list[dict[str, Any]] = []
    for lot, meta in lot_meta.items():
        prday = _as_date(meta.get("prday_min"))
        if prday is None or prday < start or prday > end:
            continue
        if prday < BC_ILE_HISTORY_FROM:
            continue
        if lot not in ile_lots:
            continue
        kg = to_float(meta.get("kg"))
        empaque_by_day[prday]["kg"] += kg
        empaque_by_day[prday]["lotes"].add(lot)
        item_no, item_description = item_by_lot.get(lot, ("", ""))
        lot_snapshot.append(
            {
                "lot": lot,
                "fe_empaque": prday,
                "kg": kg,
                "item_no": item_no,
                "item_description": item_description,
                "first_sale": first_out_by_lot.get(lot),
            }
        )
    empaque_diario = [
        {"fecha": d, "kg": to_float(v["kg"]), "lotes": len(v["lotes"])}
        for d, v in sorted(empaque_by_day.items())
    ]
    lot_snapshot.sort(key=lambda x: x["lot"])

    ventas_stock_antiguo_total_kg = sum(
        abs(to_float(r["kilos"]))
        for r in period_rows
        if r["entry_type"] == 1
        and r.get("fecha_empaque") is not None
        and r["fecha_empaque"] < r["posting_date"]
    )
    lotes_ventas_stock_antiguo = {
        r["lot"]
        for r in period_rows
        if r["entry_type"] == 1
        and r["lot"]
        and r.get("fecha_empaque") is not None
        and r["fecha_empaque"] < r["posting_date"]
    }

    ventas_por_lote = _lot_agg(
        period_rows,
        entry_types={1},
        start=start,
        end=end,
        date_field_first="first_sale",
        date_field_last="last_sale",
    )
    entradas_pos_adj_por_lote = _lot_agg(
        period_rows,
        entry_types={2},
        start=start,
        end=end,
        date_field_first="first_in",
        date_field_last="last_in",
    )
    # Prefer lot_kg for lot-level kg when available (MAX kilos semantics)
    for lst in (ventas_por_lote, entradas_pos_adj_por_lote):
        for row in lst:
            meta = lot_meta.get(row["lot"]) or {}
            if meta.get("kg"):
                row["kg"] = to_float(meta["kg"])

    entradas_pos_adj_diario = _group_daily_producto(
        period_rows, entry_types={2}, start=start, end=end
    )
    ventas_diario_producto = _group_daily_producto(
        period_rows, entry_types={1}, start=start, end=end
    )
    ajustes_neg_adj_diario = _group_daily_producto(
        period_rows, entry_types={3}, start=start, end=end
    )

    # Analisis Type 1/2/3
    analisis_map: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in period_rows:
        if row["entry_type"] not in (1, 2, 3):
            continue
        item = str(row.get("item_no") or "").strip()
        if not item:
            continue
        key = (row["entry_type"], row["posting_date"], row["usuario"], item)
        slot = analisis_map.setdefault(
            key,
            {
                "entry_type": row["entry_type"],
                "fecha": row["posting_date"],
                "usuario": row["usuario"],
                "item_no": item,
                "item_description": row.get("item_description") or "",
                "qty": 0.0,
                "kg": 0.0,
                "movimientos": 0,
                "lotes": set(),
                "mov_qty_ne_1": 0,
                "mov_kg_0": 0,
            },
        )
        qty = abs(to_float(row["quantity"]))
        kg = abs(to_float(row["kilos"]))
        slot["qty"] += qty
        slot["kg"] += kg
        slot["movimientos"] += 1
        if row["lot"]:
            slot["lotes"].add(row["lot"])
        if qty != 1:
            slot["mov_qty_ne_1"] += 1
        if kg == 0:
            slot["mov_kg_0"] += 1
        if row.get("item_description") and not slot["item_description"]:
            slot["item_description"] = row["item_description"]
    ajustes_neg_analisis_raw = []
    for key in sorted(analisis_map):
        slot = analisis_map[key]
        ajustes_neg_analisis_raw.append(
            {
                "entry_type": slot["entry_type"],
                "fecha": slot["fecha"],
                "usuario": slot["usuario"],
                "item_no": slot["item_no"],
                "item_description": slot["item_description"],
                "qty": to_float(slot["qty"]),
                "kg": to_float(slot["kg"]),
                "movimientos": slot["movimientos"],
                "lotes": len(slot["lotes"]),
                "mov_qty_ne_1": slot["mov_qty_ne_1"],
                "mov_kg_0": slot["mov_kg_0"],
            }
        )

    lots_venta = {r["lot"] for r in period_rows if r["entry_type"] == 1 and r["lot"]}
    lots_neg = {r["lot"] for r in period_rows if r["entry_type"] == 3 and r["lot"]}
    lots_pos = {r["lot"] for r in period_rows if r["entry_type"] == 2 and r["lot"]}
    movimientos_integridad = {
        "lotes_venta": len(lots_venta),
        "lotes_neg": len(lots_neg),
        "lotes_pos": len(lots_pos),
        "lotes_venta_y_neg": len(lots_venta & lots_neg),
        "lotes_pos_y_neg": len(lots_pos & lots_neg),
        "lotes_pos_y_venta": len(lots_pos & lots_venta),
    }

    kg_ventas = sum(abs(to_float(r["kilos"])) for r in period_rows if r["entry_type"] == 1)
    # Prefer sum of unique lot kg for parity with MAX(kilos) style when multiple lines
    # Keep line sum as SQL does SUM(ABS(Kilos))

    sold_or_neg = {
        r["lot"] for r in period_rows if r["entry_type"] in (1, 3) and r["lot"]
    }
    # Stock vivo = todos los lotes con empaque <= fin (incluye arrastre anterior al periodo).
    lots_stock_vivo = build_lots_stock_vivo_from_ile(rows, lot_meta, end)
    unsold_lots = sorted(
        {
            str(slot["lot"]).strip()
            for slot in lots_stock_vivo
            if slot.get("lot")
            and (
                slot.get("first_out") is None
                or _as_date(slot.get("first_out")) is None
                or _as_date(slot.get("first_out")) > end  # type: ignore[operator]
            )
        }
    )
    unsold_set = set(unsold_lots)
    kg_stock_final = sum(
        to_float(slot["kg"]) for slot in lots_stock_vivo if str(slot["lot"]).strip() in unsold_set
    )

    # Stock inicial diario: snapshot del periodo + arrastre (vivo con empaque < dia).
    stock_inicial_ile_diario = build_stock_inicial_ile_diario(
        start,
        end,
        merge_lots_for_stock_inicial_ile(
            [
                {
                    "lot": s["lot"],
                    "fe_empaque": s.get("fe_empaque"),
                    "kg": s.get("kg"),
                    "first_sale": s.get("first_out"),
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

    kg_empaque_mes = sum(to_float(r.get("kg")) for r in empaque_diario)
    lotes_empaque_mes_kg = sum(int(r.get("lotes") or 0) for r in empaque_diario)

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
        "lotes_apertura": [],
        "ventas_por_lote": ventas_por_lote,
        "entradas_pos_adj_por_lote": entradas_pos_adj_por_lote,
        "entradas_pos_adj_diario": entradas_pos_adj_diario,
        "ventas_diario_producto": ventas_diario_producto,
        "ajustes_neg_adj_diario": ajustes_neg_adj_diario,
        "ajustes_neg_analisis_raw": ajustes_neg_analisis_raw,
        "movimientos_integridad": movimientos_integridad,
        "kg_stock_inicial": kg_stock_inicial_apertura,
        "lotes_stock_inicial": lotes_stock_inicial_apertura,
        "kg_ventas_stock_antiguo_mes": to_float(ventas_stock_antiguo_total_kg),
        "lotes_ventas_stock_antiguo_mes": len(lotes_ventas_stock_antiguo),
        "kg_stock_apertura": 0.0,
        "lotes_stock_apertura": 0,
        "kg_ventas": to_float(kg_ventas),
        "lotes_ventas": len(lots_venta),
        "unsold_lots": unsold_lots,
        "lotes_empaque_mes": len(lot_snapshot),
        "lotes_stock_final": len(unsold_lots),
        "lotes_stock_final_bc": len(unsold_lots),
        "kg_stock_final": to_float(kg_stock_final),
        "sql_trace": {
            "view_or_tables": [f"BC API ({transport}) + Innova proc_packs"],
            "params": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "ile_history_from": BC_ILE_HISTORY_FROM.isoformat(),
                "transport": transport,
            },
            "queries": [
                {
                    "name": "bc_api_ile_eg_hybrid",
                    "query": (
                        "API/OData ItemLedgerEntries E/G/Z desde historico ILE + enrich Innova "
                        "(stock vivo = empaque<=fin sin 1a salida)"
                    ),
                }
            ],
        },
    }


def build_cruce_from_ile(
    rows: list[dict[str, Any]],
    lot_meta: dict[str, dict[str, Any]],
    start: dt.date,
    end: dt.date,
    *,
    transport: str,
) -> dict[str, Any]:
    """Cruce por lote (sin Order No. hasta API de albaranes). Todo como sin pedido."""
    ile_start = bc_ile_effective_start(start)
    sales = [
        r
        for r in rows
        if r["entry_type"] == 1
        and r["posting_date"] >= ile_start
        and r["posting_date"] <= end
        and r["lot"]
    ]
    by_lot_map: dict[str, dict[str, Any]] = {}
    for row in sales:
        lot = row["lot"]
        slot = by_lot_map.setdefault(
            lot,
            {
                "lot": lot,
                "order_no": None,
                "qty": 0.0,
                "kg": 0.0,
                "qty_con_pedido": 0.0,
                "qty_sin_pedido": 0.0,
                "kg_con_pedido": 0.0,
                "kg_sin_pedido": 0.0,
                "lineas_ile": 0,
            },
        )
        qty = abs(to_float(row["quantity"]))
        kg = abs(to_float(row["kilos"]))
        slot["qty"] += qty
        slot["kg"] += kg
        slot["qty_sin_pedido"] += qty
        slot["kg_sin_pedido"] += kg
        slot["lineas_ile"] += 1
    for lot, slot in by_lot_map.items():
        meta = lot_meta.get(lot) or {}
        if meta.get("kg"):
            # alinear kg de cruce al peso Innova del lote
            slot["kg"] = to_float(meta["kg"])
            slot["kg_sin_pedido"] = to_float(meta["kg"])
            slot["kg_con_pedido"] = 0.0
    by_lot = [by_lot_map[k] for k in sorted(by_lot_map)]
    by_lot_order = [
        {
            "lot": r["lot"],
            "order_no": None,
            "qty": r["qty"],
            "kg": r["kg"],
            "posting_date_min": None,
            "posting_date_max": None,
            "lineas_ile": r["lineas_ile"],
        }
        for r in by_lot
    ]
    totals = {
        "lotes_bc": len(by_lot),
        "qty_total": sum(to_float(r["qty"]) for r in by_lot),
        "qty_con_pedido": 0.0,
        "qty_sin_pedido": sum(to_float(r["qty_sin_pedido"]) for r in by_lot),
        "kg_total": sum(to_float(r["kg"]) for r in by_lot),
        "kg_con_pedido": 0.0,
        "kg_sin_pedido": sum(to_float(r["kg_sin_pedido"]) for r in by_lot),
        "lineas_ile": sum(int(r["lineas_ile"] or 0) for r in by_lot),
    }
    return {
        "by_lot": by_lot,
        "by_lot_order": by_lot_order,
        "totals": totals,
        "sql_trace": {
            "view_or_tables": [f"BC API ({transport}) + Innova"],
            "params": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "note": "Order No. no disponible hasta API albaranes / custom",
            },
            "queries": [{"name": "bc_api_cruce_hybrid", "query": "ILE Sale E/G via API"}],
        },
    }


def fetch_bc_balance_eg_api(
    innova_conn: pymssql.Connection,
    start: dt.date,
    end: dt.date,
    *,
    verbose: bool = True,
) -> dict[str, Any]:
    _cruce, balance = fetch_bc_bundle_api(
        innova_conn, start, end, verbose=verbose
    )
    return balance


def fetch_bc_salidas_pedido_api(
    innova_conn: pymssql.Connection,
    start: dt.date,
    end: dt.date,
    *,
    verbose: bool = True,
    ile_rows: list[dict[str, Any]] | None = None,
    lot_meta: dict[str, dict[str, Any]] | None = None,
    transport: str = "api",
) -> dict[str, Any]:
    if ile_rows is None:
        client = BcIleApiClient()
        if verbose:
            print("  Descargando ILE E/G/Z via API/OData (cruce)...")
        raw = client.fetch_ile_eg(start, end, verbose=verbose)
        transport = client.transport or transport
        lots = {r["lot"] for r in raw if r.get("lot")}
        lot_meta = fetch_innova_lot_enrichment(innova_conn, lots)
        ile_rows = enrich_ile_rows(raw, lot_meta)
    assert lot_meta is not None
    return build_cruce_from_ile(ile_rows, lot_meta, start, end, transport=transport)


def download_ile_eg_api(
    start: dt.date,
    end: dt.date,
    *,
    verbose: bool = True,
) -> tuple[list[dict[str, Any]], str]:
    """Descarga ILE desde historico (no solo el periodo) para stock vivo al cierre.

    `start` se conserva en la firma (periodo del informe); el posting se pide desde
    `BC_ILE_HISTORY_FROM` hasta `end` para incluir arrastre empacado antes del periodo.
    """
    ile_from = BC_ILE_HISTORY_FROM
    client = BcIleApiClient()
    if verbose:
        print(
            f"  Descargando ILE E/G/Z via API/OData "
            f"({ile_from.isoformat()}..{end.isoformat()}; "
            f"periodo informe {start.isoformat()}..{end.isoformat()})..."
        )
    raw = client.fetch_ile_eg(ile_from, end, verbose=verbose)
    return raw, client.transport or "api"


def build_bundle_from_ile(
    innova_conn: pymssql.Connection,
    raw: list[dict[str, Any]],
    start: dt.date,
    end: dt.date,
    *,
    transport: str,
    verbose: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    lots = {r["lot"] for r in raw if r.get("lot")}
    if verbose:
        print(
            f"  Enriqueciendo lotes desde Innova "
            f"(prday {BC_ILE_HISTORY_FROM.isoformat()}..{end.isoformat()}, "
            f"{len(lots):,} lotes ILE)..."
        )
    lot_meta_all = fetch_innova_lot_enrichment(
        innova_conn, None, prday_from=BC_ILE_HISTORY_FROM, prday_to=end
    )
    lot_meta = {lot: lot_meta_all[lot] for lot in lots if lot in lot_meta_all}
    for lot, meta in lot_meta_all.items():
        prday = _as_date(meta.get("prday_min"))
        if prday is not None and start <= prday <= end:
            lot_meta.setdefault(lot, meta)
    if verbose:
        print(f"  Lotes con meta Innova: {len(lot_meta):,} (universo rango {len(lot_meta_all):,})")
    enriched = enrich_ile_rows(raw, lot_meta)
    cruce = build_cruce_from_ile(enriched, lot_meta, start, end, transport=transport)
    balance = build_balance_from_ile(
        enriched, lot_meta, start, end, transport=transport
    )
    return cruce, balance


def fetch_bc_bundle_api(
    innova_conn: pymssql.Connection,
    start: dt.date,
    end: dt.date,
    *,
    verbose: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Descarga ILE por API y enriquece con la conexion Innova ya abierta.

    Preferir `download_ile_eg_api` + reconectar Innova + `build_bundle_from_ile`
    en ejecuciones largas (la conexion SQL no debe quedar idle durante OData).
    """
    raw, transport = download_ile_eg_api(start, end, verbose=verbose)
    return build_bundle_from_ile(
        innova_conn, raw, start, end, transport=transport, verbose=verbose
    )