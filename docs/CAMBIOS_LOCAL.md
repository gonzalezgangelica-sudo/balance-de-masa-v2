# Cambios locales — CALCULO_BIOMASA

Historial breve. La lógica vigente está en [FUNCIONAMIENTO.md](../FUNCIONAMIENTO.md) y [PREMISAS.md](../PREMISAS.md); no reescribir reglas aquí.

---

## 2026-08-14 — Documentación limpia (sin solapes)

- Nuevo **FUNCIONAMIENTO.md** (flujo, balances, pestañas, lectura de desvíos).
- README / INSTRUCCIONES / docs/README alineados: un rol por fichero.
- PREMISAS y KPI: estados A–D, 1 lote = 1 caja, producción = Innova ∩ BC.
- Informes regenerados junio/julio 2026 con lógica actual.

---

## 2026-08-13 — Cajas: 1 lote = 1 caja + estados A/B/C/D

### Causa del CHECK −11 cajas con kg = 0 (10–12/08)

El teórico de cajas usaba `COUNT(*)` packs Innova; el real usaba 1 lote = 1 caja.  
Gap packs−lotes = 11 → CHECK cajas −11 con kg cuadrado.

### Corrección

- Teórico cajas = **1 por lote Innova** (packs informativo).
- Producto: Item No. BC si lote en ILE; conversion solo solo-Innova.
- Estados A/B/C + **D** (inconsistencia cajas/kg).
- Pares ±X solo con evidencia de lote.

### Resultado 10–12/08 tras corrección

CHECK cajas = 0 · estado **A** · producción 10.711 (= lotes, no 10.722 packs).

---

## 2026-08-12 — Almacén Z incluido en stock BC

Balance / cruce / stock BC: almacenes **E, G y Z**.

---

## 2026-08-05 — Etiquetado Item No. ILE

Prioridad: **`Item No.` ILE** si el lote está en BC; Conversion solo si no hay Item No.
