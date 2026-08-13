"""Tests del desvío teórico vs stock real BC y balance de cajas A/B/C."""
from __future__ import annotations

import unittest

from generar_reporte_biomasa import (
    BC_DESVIO_PCT_AMARILLO,
    BC_DESVIO_PCT_VERDE,
    build_innova_caja_por_tipo,
    classify_cajas_balance_estado,
    classify_desvio_pct,
    compute_desvio_stock,
    find_compensated_cajas_pairs,
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


class TestCajasBalanceABC(unittest.TestCase):
    def test_estado_a_correcto(self) -> None:
        detalle = [
            {"tipo_key": "A", "cod_producto": "A", "cajas_check": 0},
            {"tipo_key": "B", "cod_producto": "B", "cajas_check": 0},
        ]
        est = classify_cajas_balance_estado(0, detalle)
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
        est = classify_cajas_balance_estado(0, detalle)
        self.assertEqual(est["estado"], "B")
        self.assertEqual(est["semaforo"], "amarillo")
        self.assertEqual(est["productos_con_desvio"], 2)
        self.assertIn("compensado", est["label"].lower())
        pairs = find_compensated_cajas_pairs(detalle)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["magnitud"], 56)
        self.assertEqual(pairs[0]["producto_neg"], "RG1520M")
        self.assertEqual(pairs[0]["producto_pos"], "RP0002P")

    def test_estado_c_desvio_real(self) -> None:
        detalle = [
            {"tipo_key": "A", "cod_producto": "A", "cajas_check": -10},
        ]
        est = classify_cajas_balance_estado(-10, detalle)
        self.assertEqual(est["estado"], "C")
        self.assertEqual(est["semaforo"], "rojo")
        self.assertFalse(est["check_ok"])


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
        # Sin Item No. BC → conversion
        sin_bc = build_innova_caja_por_tipo(innova_lotes, innova_mat, conversion, {})
        self.assertIn("RG1520M", sin_bc)
        self.assertEqual(sin_bc["RG1520M"]["packs"], 56)

        # Con Item No. BC distinto → Item No. gana
        con_bc = build_innova_caja_por_tipo(
            innova_lotes, innova_mat, conversion, {"L001": "RP0002P"}
        )
        self.assertIn("RP0002P", con_bc)
        self.assertNotIn("RG1520M", con_bc)
        self.assertEqual(con_bc["RP0002P"]["packs"], 56)
        self.assertEqual(con_bc["RP0002P"]["enlace_origen"], "item_no_bc")

    def test_resolve_prioridad_item_no(self) -> None:
        conversion = {"MAT1": {"cod_producto": "VIA_CONV"}}
        cod, origen, _ = resolve_cod_producto_bc("MAT1", "", "ITEM_BC", conversion)
        self.assertEqual(cod, "ITEM_BC")
        self.assertEqual(origen, "item_no_bc")
        cod2, origen2, _ = resolve_cod_producto_bc("MAT1", "", "", conversion)
        self.assertEqual(cod2, "VIA_CONV")
        self.assertEqual(origen2, "conversion_bascula")


if __name__ == "__main__":
    unittest.main()
