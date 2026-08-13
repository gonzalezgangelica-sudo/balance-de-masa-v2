"""Tests del desvío teórico vs stock real BC y balance de cajas A/B/C/D."""
from __future__ import annotations

import unittest

from generar_reporte_biomasa import (
    BC_DESVIO_PCT_AMARILLO,
    BC_DESVIO_PCT_VERDE,
    build_innova_caja_por_tipo,
    classify_cajas_balance_estado,
    classify_desvio_pct,
    classify_producto_desvio_cajas_kg,
    compute_desvio_stock,
    find_compensated_cajas_pairs,
    find_innova_lotes_repetidos,
    resolve_cod_producto_bc,
)


class TestDesvioBc(unittest.TestCase):
    def test_igual_cero_verde(self) -> None:
        d = compute_desvio_stock(10_000.0, 10_000.0)
        self.assertEqual(d["desvio_kg"], 0.0)
        self.assertEqual(d["desvio_pct"], 0.0)
        self.assertEqual(d["semaforo"], "verde")
        self.assertTrue(d["check_ok"])

    def test_dentro_tolerancia_verde(self) -> None:
        teo = 10_000.0
        real = teo * (1 + 0.004)
        d = compute_desvio_stock(teo, real)
        self.assertAlmostEqual(d["desvio_pct"], 0.4, places=6)
        self.assertEqual(d["semaforo"], "verde")
        self.assertLessEqual(abs(d["desvio_pct"]), BC_DESVIO_PCT_VERDE)

    def test_banda_amarilla(self) -> None:
        teo = 10_000.0
        real = teo * (1 + 0.0075)
        d = compute_desvio_stock(teo, real)
        self.assertAlmostEqual(d["desvio_pct"], 0.75, places=6)
        self.assertEqual(d["semaforo"], "amarillo")
        self.assertGreater(abs(d["desvio_pct"]), BC_DESVIO_PCT_VERDE)
        self.assertLessEqual(abs(d["desvio_pct"]), BC_DESVIO_PCT_AMARILLO)

    def test_banda_roja(self) -> None:
        teo = 10_000.0
        real = teo * (1 - 0.015)
        d = compute_desvio_stock(teo, real)
        self.assertAlmostEqual(d["desvio_pct"], -1.5, places=6)
        self.assertEqual(d["semaforo"], "rojo")
        self.assertGreater(abs(d["desvio_pct"]), BC_DESVIO_PCT_AMARILLO)

    def test_teorico_cero_ambos_cero_verde(self) -> None:
        d = compute_desvio_stock(0.0, 0.0)
        self.assertEqual(d["desvio_kg"], 0.0)
        self.assertIsNone(d["desvio_pct"])
        self.assertEqual(d["semaforo"], "verde")

    def test_teorico_cero_real_positivo_rojo(self) -> None:
        d = compute_desvio_stock(0.0, 100.0)
        self.assertEqual(d["desvio_kg"], 100.0)
        self.assertIsNone(d["desvio_pct"])
        self.assertEqual(d["semaforo"], "rojo")
        self.assertFalse(d["check_ok"])

    def test_desvio_signo_real_menos_teorico(self) -> None:
        d = compute_desvio_stock(100.0, 110.0)
        self.assertEqual(d["desvio_kg"], 10.0)

    def test_classify_desvio_pct(self) -> None:
        self.assertEqual(classify_desvio_pct(0.0), "verde")
        self.assertEqual(classify_desvio_pct(0.5), "verde")
        self.assertEqual(classify_desvio_pct(0.51), "amarillo")
        self.assertEqual(classify_desvio_pct(1.0), "amarillo")
        self.assertEqual(classify_desvio_pct(1.01), "rojo")
        self.assertEqual(classify_desvio_pct(None, desvio_kg=0.0), "verde")
        self.assertEqual(classify_desvio_pct(None, desvio_kg=1.0), "rojo")


class TestCajasBalanceABCD(unittest.TestCase):
    def test_estado_a_correcto(self) -> None:
        detalle = [
            {"tipo_key": "A", "cod_producto": "A", "cajas_check": 0},
            {"tipo_key": "B", "cod_producto": "B", "cajas_check": 0},
        ]
        est = classify_cajas_balance_estado(0, detalle, kg_check=0.0, detalle_kg=[])
        self.assertEqual(est["estado"], "A")
        self.assertEqual(est["semaforo"], "verde")
        self.assertEqual(est["productos_con_desvio"], 0)
        self.assertTrue(est["check_ok"])

    def test_estado_b_compensado(self) -> None:
        detalle = [
            {
                "tipo_key": "RG1520M",
                "cod_producto": "RG1520M",
                "tipo_nombre": "Neg",
                "cajas_check": -56,
            },
            {
                "tipo_key": "RP0002P",
                "cod_producto": "RP0002P",
                "tipo_nombre": "Pos",
                "cajas_check": 56,
            },
        ]
        est = classify_cajas_balance_estado(0, detalle, kg_check=0.0)
        self.assertEqual(est["estado"], "B")
        self.assertEqual(est["semaforo"], "amarillo")
        self.assertEqual(est["productos_con_desvio"], 2)
        self.assertIn("compensado", est["label"].lower())

        # Sin evidencia de lote → no se publica el par
        self.assertEqual(find_compensated_cajas_pairs(detalle), [])

        # Con evidencia lote Conversion→Item No.
        lot_detalle = [
            {
                "lot": "L123",
                "item_no": "RP0002P",
                "material": "MAT1",
                "pattern": "",
                "kg_bc": 10.0,
            }
        ]
        conversion = {"MAT1": {"cod_producto": "RG1520M"}}
        pairs = find_compensated_cajas_pairs(
            detalle, lot_detalle=lot_detalle, conversion_by_bascula=conversion
        )
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["magnitud"], 56)
        self.assertEqual(pairs[0]["producto_neg"], "RG1520M")
        self.assertEqual(pairs[0]["producto_pos"], "RP0002P")
        self.assertEqual(pairs[0]["evidencia"], "lote_sku_remap")
        self.assertIn("L123", pairs[0]["lotes"])

    def test_estado_c_desvio_real(self) -> None:
        detalle = [
            {"tipo_key": "A", "cod_producto": "A", "cajas_check": -10},
        ]
        # kg también desvía → C (no D)
        est = classify_cajas_balance_estado(
            -10,
            detalle,
            kg_check=-50.0,
            detalle_kg=[{"desvio_kg": -50.0}],
        )
        self.assertEqual(est["estado"], "C")
        self.assertEqual(est["semaforo"], "rojo")
        self.assertFalse(est["check_ok"])

    def test_estado_d_inconsistencia_cajas_kg(self) -> None:
        detalle = [
            {"tipo_key": "A", "cod_producto": "A", "cajas_check": -11},
        ]
        kg_detalle = [
            {"tipo_key": "A", "desvio_kg": 0.0},
        ]
        est = classify_cajas_balance_estado(
            -11, detalle, kg_check=0.0, detalle_kg=kg_detalle
        )
        self.assertEqual(est["estado"], "D")
        self.assertTrue(est["inconsistencia_cajas_kg"])
        self.assertIn("inconsistencia", est["label"].lower())


class TestMapeoItemNoBc(unittest.TestCase):
    def test_lote_en_bc_usa_item_no(self) -> None:
        """Innova con conversion distinta; si el lote está en BC prevalece Item No."""
        conversion = {
            "MAT1": {"cod_producto": "RG1520M", "item_description": "Via conversion"},
        }
        innova_lotes = [{"lot": "L001", "kg": 100.0, "packs": 56}]
        innova_mat = [
            {
                "lot": "L001",
                "material": "MAT1",
                "material_nombre": "Mat1",
                "pattern": "",
                "kg_innova": 100.0,
                "packs": 56,
            }
        ]
        sin_bc = build_innova_caja_por_tipo(innova_lotes, innova_mat, conversion, {})
        self.assertIn("RG1520M", sin_bc)
        self.assertEqual(sin_bc["RG1520M"]["packs"], 56)
        self.assertEqual(sin_bc["RG1520M"]["cajas"], 1)  # 1 lote = 1 caja

        con_bc = build_innova_caja_por_tipo(
            innova_lotes, innova_mat, conversion, {"L001": "RP0002P"}
        )
        self.assertIn("RP0002P", con_bc)
        self.assertNotIn("RG1520M", con_bc)
        self.assertEqual(con_bc["RP0002P"]["packs"], 56)
        self.assertEqual(con_bc["RP0002P"]["cajas"], 1)
        self.assertEqual(con_bc["RP0002P"]["enlace_origen"], "item_no_bc")

    def test_solo_innova_usa_conversion(self) -> None:
        conversion = {"MAT1": {"cod_producto": "VIA_CONV"}}
        innova_lotes = [{"lot": "SOLO1", "kg": 5.0, "packs": 2}]
        innova_mat = [
            {
                "lot": "SOLO1",
                "material": "MAT1",
                "material_nombre": "Mat",
                "pattern": "",
                "kg_innova": 5.0,
                "packs": 2,
            }
        ]
        out = build_innova_caja_por_tipo(innova_lotes, innova_mat, conversion, {})
        self.assertIn("VIA_CONV", out)
        self.assertEqual(out["VIA_CONV"]["cajas"], 1)
        self.assertEqual(out["VIA_CONV"]["packs"], 2)

    def test_resolve_prioridad_item_no(self) -> None:
        conversion = {"MAT1": {"cod_producto": "VIA_CONV"}}
        cod, origen, _ = resolve_cod_producto_bc("MAT1", "", "ITEM_BC", conversion)
        self.assertEqual(cod, "ITEM_BC")
        self.assertEqual(origen, "item_no_bc")
        cod2, origen2, _ = resolve_cod_producto_bc("MAT1", "", "", conversion)
        self.assertEqual(cod2, "VIA_CONV")
        self.assertEqual(origen2, "conversion_bascula")


class TestClasificacionProducto(unittest.TestCase):
    def test_mapeo(self) -> None:
        self.assertEqual(
            classify_producto_desvio_cajas_kg(-1, 0.0, tiene_evidencia_mapeo=True),
            "A",
        )

    def test_packs_vs_lotes(self) -> None:
        self.assertEqual(
            classify_producto_desvio_cajas_kg(-1, 0.0, packs_vs_lotes_gap=2),
            "D",
        )

    def test_inconsistencia(self) -> None:
        self.assertEqual(classify_producto_desvio_cajas_kg(-1, 0.0), "C")

    def test_diferencia_real(self) -> None:
        self.assertEqual(classify_producto_desvio_cajas_kg(-1, -5.0), "B")


class TestLotesRepetidos(unittest.TestCase):
    def test_number_unico_sin_alerta(self) -> None:
        innova = [
            {"lot": "L1", "fecha": "2026-08-10", "kg": 5.0, "packs": 1},
            {"lot": "L2", "fecha": "2026-08-10", "kg": 6.0, "packs": 1},
        ]
        out = find_innova_lotes_repetidos(innova)
        self.assertFalse(out["has_alert"])
        self.assertEqual(out["n_lotes"], 0)
        self.assertEqual(out["detalle"], [])

    def test_number_repetido_dos_veces(self) -> None:
        innova = [
            {"lot": "LOTE123", "fecha": "2026-08-10", "kg": 10.0, "packs": 2},
        ]
        mat = [
            {
                "lot": "LOTE123",
                "material": "MAT1",
                "material_nombre": "Prod A",
                "pattern": "RG1520M",
            }
        ]
        out = find_innova_lotes_repetidos(innova, mat)
        self.assertTrue(out["has_alert"])
        self.assertEqual(out["n_lotes"], 1)
        self.assertEqual(out["detalle"][0]["lot"], "LOTE123")
        self.assertEqual(out["detalle"][0]["apariciones"], 2)
        self.assertEqual(out["detalle"][0]["producto"], "RG1520M")

    def test_number_repetido_tres_veces(self) -> None:
        innova = [
            {"lot": "LOTE123", "fecha": "2026-08-11", "kg": 12.0, "packs": 3},
        ]
        out = find_innova_lotes_repetidos(innova)
        self.assertTrue(out["has_alert"])
        self.assertEqual(out["detalle"][0]["apariciones"], 3)

    def test_varios_numbers_repetidos(self) -> None:
        innova = [
            {"lot": "LOTE123", "fecha": "2026-08-10", "kg": 1.0, "packs": 3},
            {"lot": "LOTE456", "fecha": "2026-08-11", "kg": 2.0, "packs": 2},
            {"lot": "OK1", "fecha": "2026-08-11", "kg": 3.0, "packs": 1},
        ]
        out = find_innova_lotes_repetidos(innova)
        self.assertEqual(out["n_lotes"], 2)
        lots = {r["lot"]: r["apariciones"] for r in out["detalle"]}
        self.assertEqual(lots["LOTE123"], 3)
        self.assertEqual(lots["LOTE456"], 2)
        self.assertNotIn("OK1", lots)

    def test_repetido_no_aumenta_cajas_balance(self) -> None:
        """packs=2 en un lote → sigue siendo 1 caja en agregación por tipo."""
        conversion = {"MAT1": {"cod_producto": "SKU1"}}
        innova_lotes = [{"lot": "LOTE123", "kg": 10.0, "packs": 2}]
        innova_mat = [
            {
                "lot": "LOTE123",
                "material": "MAT1",
                "material_nombre": "X",
                "pattern": "",
                "kg_innova": 10.0,
                "packs": 2,
            }
        ]
        by_tipo = build_innova_caja_por_tipo(innova_lotes, innova_mat, conversion, {})
        self.assertEqual(by_tipo["SKU1"]["cajas"], 1)
        self.assertEqual(by_tipo["SKU1"]["packs"], 2)
        alert = find_innova_lotes_repetidos(innova_lotes, innova_mat)
        self.assertTrue(alert["has_alert"])

    def test_alerta_aunque_check_cero(self) -> None:
        """La alerta es independiente del CHECK global."""
        innova = [{"lot": "DUP", "fecha": "2026-08-10", "kg": 1.0, "packs": 2}]
        alert = find_innova_lotes_repetidos(innova)
        self.assertTrue(alert["has_alert"])
        est = classify_cajas_balance_estado(
            0,
            [{"tipo_key": "A", "cajas_check": 0}],
            kg_check=0.0,
            detalle_kg=[],
        )
        self.assertEqual(est["estado"], "A")
        self.assertTrue(est["check_ok"])


if __name__ == "__main__":
    unittest.main()
