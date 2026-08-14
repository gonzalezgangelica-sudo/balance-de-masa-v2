# Definiciones de KPIs y columnas del informe

El HTML muestra títulos y valores. Las definiciones viven **aquí** (no se duplican como textos largos en el informe).  
Contexto de uso: [FUNCIONAMIENTO.md](../FUNCIONAMIENTO.md). Reglas formales: [PREMISAS.md](../PREMISAS.md).

---

## Regla: 1 lote = 1 caja

En el balance BC E/G/Z:

- **1 lote** (`proc_packs.number` = ILE `[Lot No.]`) = **1 caja**
- Teórico y real de cajas cuentan **lotes**, no `COUNT(*)` de filas Innova (`packs`)
- Si un `number` aparece más de una vez → alerta **LOTE REPETIDO — REVISAR** (no cambia el cálculo)

El KPI Innova «Nº de Cajas» (`COUNT(*)` packs, premisa 3) es **informativo**; el balance de almacén no lo usa.

---

## CHECK / desvío

- **CHECK cajas** = **real − teórico**
- **CHECK kg** = **real − teórico**
- **0** → cuadra · **&lt; 0** falta real · **&gt; 0** exceso real

---

## Producto

`Lote → Item No.`

1. Si el lote está en ILE → **`Item No.` BC**
2. Si no (solo Innova) → Conversion / pattern / material

---

## Balance teórico

| | Cajas | Kg |
|--|-------|-----|
| Inicial | Lotes en stock inicial | Suma kg BC |
| Producción | Innova CAJA ∩ BC: **1 lote = 1 caja** | `SUM(weight)` Innova |
| Primera salida | Type 1/3 una vez por lote | kg del lote |
| Teórico | Inicial + Producción − Primera salida | Igual |
| Real | Snapshot E/G/Z al cierre | Igual |
| CHECK | real − teórico | real − teórico |

`packs` (`COUNT(*)`) no entra en el teórico de cajas.

---

## Estados globales

| Estado | Condición | Semáforo |
|--------|-----------|----------|
| **A** | CHECK cajas = 0, todos productos cajas = 0, CHECK kg ≈ 0 | Verde |
| **B** | CHECK cajas global = 0 y algún producto ≠ 0 | Amarillo — desvío por producto compensado |
| **C** | CHECK cajas global ≠ 0 | Rojo |
| **D** | CHECK cajas ≠ 0 y CHECK kg ≈ 0 (productos kg ≈ 0) | Rojo — inconsistencia cajas/kg |

El verde de kg **no** oculta un CHECK cajas ≠ 0.

---

## Pares ±X

Solo con **evidencia de lote**: Conversion/Innova ≠ Item No. BC en el mismo lote.  
−X + X = 0 sin relación de lote **no** se marca como compensación.

---

## Clasificación por producto (A–E)

| Código | Significado |
|--------|-------------|
| A | Posible error de mapeo SKU/lote |
| B | Posible diferencia real de cajas (kg también desvía) |
| C | Inconsistencia cajas/kg |
| D | Gap packs vs lotes (unidad) |
| E | No determinable / sin desvío |

---

## Coherencia cajas ↔ kg

Si `CHECK cajas ≠ 0` y `CHECK kg = 0`, investigar mapeo / packs≠lotes / lógica antes de asumir diferencia física.
