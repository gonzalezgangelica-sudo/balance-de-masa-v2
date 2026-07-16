# Premisas de calculo de biomasa

Documento canonico del proyecto **CALCULO_BIOMASA**. Cualquier cambio en reglas de negocio debe actualizarse aqui y en `generar_reporte_biomasa.py` (constantes `PREMISA_*` y `SQL_*`).

## Terminologia planta

| Termino planta | Significado | En Innova / reporte |
|----------------|-------------|---------------------|
| **TINA** | **Entrada** de biomasa | `proc_packs` / `proc_matxacts` con `pkpackaging = 3` |
| **CAJA** | **Salida** de producto | `proc_packs` con `pkpackaging <> 3` o NULL y `rtype = 1` |

**Fecha de produccion:** en todas las premisas confirmadas se usa **`prday`** a medianoche (`YYYY-MM-DD 00:00:00.000`) del dia de produccion.

---

## Estado de premisas

| # | Premisa | Estado |
|---|---------|--------|
| 1 | **Entradas TINA** — `proc_packs`, `pkpackaging = 3`, `rtype IN ('1','12')`, fecha `prday` | Confirmada (implementada) |
| 2 | **Tinas procesadas** — `proc_matxacts`, `pkpackaging = 3`, `xactpath IN ('1')`, fecha `prday` | Confirmada (implementada) |
| 3 | **Salidas CAJA** — `proc_packs`, `pkpackaging <> 3` o NULL, `rtype = 1`, fecha `prday` | Confirmada (implementada) |
| 4 | **Stock de tinas** — `proc_packs`, `pkpackaging = 3`, `rtype IN ('1')`, fecha `prday` | Confirmada (implementada) |
| 5 | **Merma** — balance: Entradas TINA − Salidas CAJA − Stock de tinas | Confirmada (implementada) |
| 6 | **Cruce BC / pedidos** — enlace por lote; ventas ILE; pedido desde albaran | Confirmada (implementada) |
| 7 | Stock inventario / arrastre | Pendiente |
| 8 | **Balance BC E/G** — stock inicial, salidas, ventas, teorico/real y check (almacenes E y G) | Confirmada (implementada) |

> Las reglas anteriores basadas en `regtime`, filtro `%tina%` en nombre de material, o totales de marzo 2026 con logica legacy **quedan sustituidas** por las premisas de esta seccion.

---

## ALERTA — Limitacion conocida: producto VAP

**Inconsistencia grave no resuelta (informar siempre en resultados).**

El producto **VAP** entra registrado como **tina** (`pkpackaging = 3`) pero **no se procesa** en planta con el flujo habitual de las demas especies. Esa entrada:

- Se contabiliza en **Entradas TINA** (premisa 1).
- Queda en **Stock de tinas** (`rtype = 1`, premisa 4) de forma **ficticia**, como si no se hubiera procesado.
- **Desvirtua** stock, merma y balance de masa del periodo.

**Estado:** no existe correccion automatizada en el calculo. Hasta que negocio/Innova definan exclusion o reclasificacion del VAP, **todos los reportes, tablas y exportaciones deben incluir esta nota al pie** para que no se olvide al interpretar cifras.

**Texto obligatorio en pie de resultados:**

> *Nota: El producto VAP entra por tinas pero no se procesa; se acumula en stock de tinas de forma ficticia y distorsiona entradas, stock y merma. Limitacion conocida — sin correccion disponible de momento.*

---

## Premisa 1 — Entradas TINA

Kg y unidades de biomasa registrada como **entrada de tina** en un dia de produccion.

### Reglas

| Elemento | Valor |
|----------|--------|
| Tabla | `dbo.proc_packs` |
| JOIN | `dbo.proc_materials` por `material` |
| Material | `mat.pkpackaging = 3` |
| Tipo movimiento | `pk.rtype IN ('1', '12')` — ambos significan entrada de tinas |
| Fecha del dia | `pk.prday` (medianoche) |
| Kg | `SUM(pk.weight)` |
| Unidades | `COUNT(*)` — se reporta como **Nº de Tinas** |

### SQL de referencia (un dia)

```sql
-- Entrada tinas
SELECT
  SUM(CAST(pk.weight AS float)) AS kg_entrada_tina,
  COUNT(*) AS tinas_entrada
FROM dbo.proc_packs pk
INNER JOIN dbo.proc_materials mat ON pk.material = mat.material
WHERE pk.prday = '2026-03-02 00:00:00.000'
  AND mat.pkpackaging = 3
  AND pk.rtype IN ('1', '12');
```

### SQL de referencia (rango, agrupado por dia)

```sql
SELECT
  pk.prday AS fecha_produccion,
  SUM(CAST(pk.weight AS float)) AS kg_entrada_tina,
  COUNT(*) AS tinas_entrada
FROM dbo.proc_packs pk
INNER JOIN dbo.proc_materials mat ON pk.material = mat.material
WHERE pk.prday >= @start
  AND pk.prday < DATEADD(day, 1, @end)
  AND mat.pkpackaging = 3
  AND pk.rtype IN ('1', '12')
GROUP BY pk.prday
ORDER BY pk.prday;
```

### Totales de control (marzo 2026, premisa nueva)

| Metrica | Valor | Unidad |
|---------|------:|--------|
| Entradas TINA | 654.953,24 | kg |
| Tinas entrada | 2.699 | Nº de Tinas |

---

## Premisa 2 — Tinas procesadas

Kg de tina **consumida en planta** (procesada) en un dia de produccion. No es salida CAJA.

### Reglas

| Elemento | Valor |
|----------|--------|
| Tabla | `dbo.proc_matxacts` |
| JOIN | `dbo.proc_materials` por `material` |
| Material | `mat.pkpackaging = 3` |
| Ruta de transaccion | `pk.xactpath IN ('1')` |
| Fecha del dia | `pk.prday` (medianoche) |
| Kg | `SUM(pk.weight)` |

No se usa JOIN a `proc_packs` ni filtro por nombre de material (`%tina%`). No se usa `regtime`.

### SQL de referencia (un dia)

```sql
-- Tinas procesadas
SELECT SUM(CAST(pk.weight AS float)) AS kg_tinas_procesadas
FROM dbo.proc_matxacts pk
INNER JOIN dbo.proc_materials mat ON pk.material = mat.material
WHERE pk.prday = '2026-03-02 00:00:00.000'
  AND mat.pkpackaging = 3
  AND pk.xactpath IN ('1');
```

### SQL de referencia (rango, agrupado por dia)

```sql
SELECT
  pk.prday AS fecha_produccion,
  SUM(CAST(pk.weight AS float)) AS kg_tinas_procesadas,
  COUNT(*) AS movs_procesado
FROM dbo.proc_matxacts pk
INNER JOIN dbo.proc_materials mat ON pk.material = mat.material
WHERE pk.prday >= @start
  AND pk.prday < DATEADD(day, 1, @end)
  AND mat.pkpackaging = 3
  AND pk.xactpath IN ('1')
GROUP BY pk.prday
ORDER BY pk.prday;
```

### Totales de control (marzo 2026, premisa nueva)

| Metrica | Valor | Unidad |
|---------|------:|--------|
| Tinas procesadas | 424.729,00 | kg |

---

## Premisa 3 — Salidas CAJA

Kg y unidades de producto registrado como **salida en caja** en un dia de produccion. El formato de salida en planta es siempre **caja**.

### Reglas

| Elemento | Valor |
|----------|--------|
| Tabla | `dbo.proc_packs` |
| JOIN | `dbo.proc_materials` por `material` |
| Material | `m.pkpackaging <> 3 OR m.pkpackaging IS NULL` |
| Tipo movimiento | `p.rtype = 1` |
| Fecha del dia | `p.prday` (medianoche) |
| Kg | `SUM(p.weight)` |
| Unidades | `COUNT(*)` — se reporta como **Nº de Cajas** |

No se usa `regtime`.

### SQL de referencia (un dia)

```sql
-- Salidas cajas
SELECT
  SUM(CAST(p.weight AS float)) AS kg_salida_caja,
  COUNT(*) AS cajas_salida
FROM dbo.proc_packs p
JOIN dbo.proc_materials m ON m.material = p.material
WHERE p.prday = '2026-03-02 00:00:00.000'
  AND (m.pkpackaging <> 3 OR m.pkpackaging IS NULL)
  AND p.rtype = 1;
```

### SQL de referencia (rango, agrupado por dia)

```sql
SELECT
  CAST(p.prday AS date) AS fecha,
  SUM(CAST(p.weight AS float)) AS kg_salida_caja,
  COUNT(*) AS cajas_salida
FROM dbo.proc_packs p
JOIN dbo.proc_materials m ON m.material = p.material
WHERE p.prday >= @start
  AND p.prday < DATEADD(day, 1, @end)
  AND (m.pkpackaging <> 3 OR m.pkpackaging IS NULL)
  AND p.rtype = 1
GROUP BY CAST(p.prday AS date)
ORDER BY fecha;
```

### Totales de control (marzo 2026, premisa nueva)

| Metrica | Valor | Unidad |
|---------|------:|--------|
| Salidas CAJA | 474.322,21 | kg |
| Nº de Cajas | 77.531 | cajas |

---

## Premisa 4 — Stock de tinas

Tinas que **han entrado en el dia de produccion y no se han procesado**. Se consulta directamente en Innova; no se calcula como diferencia entre entradas y procesadas.

### Reglas

| Elemento | Valor |
|----------|--------|
| Tabla | `dbo.proc_packs` |
| JOIN | `dbo.proc_materials` por `material` |
| Material | `mat.pkpackaging = 3` |
| Tipo movimiento | `pk.rtype IN ('1')` — solo `1` (no incluye `12` de entradas totales) |
| Fecha del dia | `pk.prday` (medianoche) |
| Kg | `SUM(pk.weight)` |
| Unidades | `SUM(pk.nregs)` — se reporta como **Nº de Tinas** |

> **Nota:** Entradas TINA (premisa 1) usan `rtype IN ('1','12')`. Stock usa solo `rtype = 1`. El `12` en entradas no forma parte del stock del dia.

> **Alerta VAP:** el producto VAP entra por tinas pero no se procesa; se acumula en este stock de forma ficticia. Ver seccion **ALERTA — Limitacion conocida: producto VAP** al inicio del documento.

### SQL de referencia (un dia)

```sql
-- Stock tinas
SELECT
  SUM(CAST(pk.nregs AS float)) AS nregs_stock_tina,
  SUM(CAST(pk.weight AS float)) AS kg_stock_tina
FROM dbo.proc_packs pk
INNER JOIN dbo.proc_materials mat ON pk.material = mat.material
WHERE pk.prday = '2026-03-02 00:00:00.000'
  AND mat.pkpackaging = 3
  AND pk.rtype IN ('1');
```

### SQL de referencia (rango, agrupado por dia)

```sql
SELECT
  CAST(pk.prday AS date) AS fecha,
  SUM(CAST(pk.nregs AS float)) AS nregs_stock_tina,
  SUM(CAST(pk.weight AS float)) AS kg_stock_tina
FROM dbo.proc_packs pk
INNER JOIN dbo.proc_materials mat ON pk.material = mat.material
WHERE pk.prday >= @start
  AND pk.prday < DATEADD(day, 1, @end)
  AND mat.pkpackaging = 3
  AND pk.rtype IN ('1')
GROUP BY CAST(pk.prday AS date)
ORDER BY fecha;
```

### Totales de control (marzo 2026, premisa nueva)

| Metrica | Valor | Unidad |
|---------|------:|--------|
| Stock de tinas | 225.334,24 | kg |
| Nº de Tinas (stock) | 1.028 | tinas |

---

## Premisa 5 — Merma

Desperdicio o diferencia de masa del periodo. **No es un `rtype` de Innova** ni una consulta directa: se **calcula** a partir de las premisas 1, 3 y 4.

### Definicion de negocio

La merma cierra el **balance de masa** del dia (o del periodo):

| Concepto | Que representa |
|----------|----------------|
| **Entradas TINA** | Toda la biomasa que entra (`rtype` 1 y 12) |
| **Salidas CAJA** | Producto que sale en cajas |
| **Stock de tinas** | Lo que entra y **no** se procesa ese dia (`rtype` 1) |
| **Merma** | Lo que falta para cuadrar: entrada − salida − stock |

### Formulas (canonicas)

Balance de masa:

```
Entrada TINA = Salidas CAJA + Stock de tinas + Merma
```

```
Merma (kg) = Entrada TINA − Salidas CAJA − Stock de tinas
```

```
% Merma = Merma / Entrada TINA × 100
```

### Calculo diario

Para cada `prday`, aplicar las premisas 1, 3 y 4 del mismo dia y calcular `merma_kg` con la formula anterior.

### Interpretacion del signo

| Signo | Lectura operativa |
|-------|-------------------|
| **Merma > 0** | Hay perdida de masa: entra mas biomasa de la que sale en caja y queda en stock |
| **Merma < 0** | Las salidas en caja superan (en kg) la entrada del dia mas el stock generado ese dia; suele indicar **consumo de stock de dias anteriores** o mayor rendimiento contable del procesado arrastrado |

### No usar como formula canonica

```
Merma ≠ Tinas procesadas − Salidas CAJA
```

Esa expresion **no cierra** con las premisas confirmadas, porque **Tinas procesadas** (premisa 2) puede incluir consumo de tinas que entraron en dias previos, y las entradas `rtype = 12` no son stock pero si son entrada total.

---

## Explicacion del resultado — marzo 2026

Totales del mes con las cinco premisas:

| Metrica | kg | Origen |
|---------|---:|--------|
| Entradas TINA | 654.953,24 | Premisa 1 (`rtype` 1 + 12) |
| — de las cuales `rtype = 1` (stock) | 225.334,24 | = Premisa 4 |
| — de las cuales `rtype = 12` (a proceso) | 429.619,00 | Parte de entrada total |
| Tinas procesadas | 424.729,00 | Premisa 2 |
| Salidas CAJA | 474.322,21 | Premisa 3 |
| Stock de tinas | 225.334,24 | Premisa 4 |
| **Merma** | **−44.703,21** | Premisa 5 |

```
654.953,24 = 474.322,21 + 225.334,24 + (−44.703,21)  ✓
```

### Por que el stock no es «Entradas − Procesadas»

| Calculo | Marzo (kg) |
|---------|----------:|
| Entradas TINA − Tinas procesadas | 230.224,24 |
| Stock de tinas (premisa 4, `rtype = 1`) | 225.334,24 |
| **Diferencia** | **−4.890,00** |

La diferencia se explica porque:

1. **Entradas totales** incluyen `rtype = 12` (429.619 kg en marzo): tinas que entran y van **directo al proceso**, no a stock.
2. **Stock** solo cuenta `rtype = 1` (225.334,24 kg): tinas que entran y **no** se procesan ese dia.
3. **Tinas procesadas** (424.729 kg) no coinciden exactamente con entrada `rtype = 12` (429.619 kg): hay **4.890 kg** de procesado atribuible a **stock de dias anteriores** (arrastre), no a entrada del mismo dia.

Por eso algunos dias muestran stock derivado negativo (13/03, 18/03) si se hace `Entradas − Procesadas`, pero el stock SQL (`rtype = 1`) sigue siendo positivo.

### Por que no usar «Procesadas − Salidas» como merma

| Formula | Marzo (kg) |
|---------|----------:|
| **Merma canonica** (Entradas − Salidas − Stock) | **−44.703,21** |
| Procesadas − Salidas | −49.593,21 |
| **Diferencia** | **−4.890,00** |

La cifra alternativa mezcla el consumo de stock arrastrado con la merma del periodo. La premisa 5 usa el balance con **stock del dia** (`rtype = 1`).

### Lectura de negocio de marzo

- Entraron **654.953 kg** de tinas; de ellos **225.334 kg** quedaron en stock (`rtype = 1`) y **429.619 kg** se registraron como entrada a proceso (`rtype = 12`).
- Salieron **474.322 kg** en cajas, **mas** que las tinas procesadas del mes (424.729 kg): se consumio biomasa de stock previo.
- La **merma −44.703 kg** (−6,82 % sobre entradas) indica que, en el balance del mes, las salidas en caja superan en 44.703 kg lo contabilizado como entrada mas stock generado en el periodo.

### Totales de control merma (marzo 2026)

| Metrica | Valor |
|---------|------:|
| Merma (kg) | −44.703,21 |
| % Merma / entradas | −6,82 % |

---

## Premisa 6 — Cruce Business Central (pedidos)

Cruce **por lote/caja** entre salidas Innova y ventas en Business Central (BC). **Se mantiene la premisa antigua** validada previamente; no depende de `prday` en el lado BC.

### Objetivo

- Saber que salidas Innova tienen **venta contabilizada en BC**.
- Comparar **kg Innova** vs **kg BC** en lotes enlazados.
- Clasificar ventas BC **con pedido** vs **sin pedido** (`[Order No.]` del albaran).

### Reglas

| Elemento | Regla |
|----------|--------|
| **Clave de enlace** | `dbo.proc_packs.number` = `bc.[Item Ledger Entry].[Lot No.]` |
| **Lado Innova** | Packs de **salida CAJA** (premisa 3) con `number` informado |
| **Lado BC — ventas** | `bc.[Item Ledger Entry]` con `[Entry Type] = 1` |
| **Almacenes BC** | `[Location Code]` en **E** y **G** unicamente |
| **Kg BC** | `ABS([Kilos])` en Item Ledger Entry |
| **Unidades BC** | `ABS([Quantity])` en Item Ledger Entry |
| **Pedido** | `[Order No.]` de `bc.[Sales Shipment Line]` del mismo `[Document No.]` que el ILE |
| **Con pedido** | `[Order No.]` informado (no vacio) en el albaran |
| **Sin pedido** | `[Order No.]` vacio o NULL |
| **Filtro periodo BC** | `[Posting Date]` del ILE dentro del rango del informe |
| **Cruce temporal** | **Por lote**, no por fecha: la contabilizacion BC puede ser otro dia del mes |

> **Implementacion actual en codigo:** el detalle Innova del cruce agrupa lotes por `proc_packs.regtime` (legacy). Las **metricas de salida del periodo** usaran `prday` (premisa 3) cuando se actualice el script; el **enlace y la logica BC** no cambian.

### SQL Innova — lotes de salida con codigo (lado enlace)

Alineado a premisa 3 (`prday`, `rtype = 1`):

```sql
-- Lotes Innova para cruce BC (salidas CAJA con number)
SELECT
  CAST(p.prday AS date) AS fecha,
  CAST(p.number AS varchar(50)) AS lot,
  SUM(CAST(p.weight AS float)) AS kg_innova,
  COUNT(*) AS packs_salida
FROM dbo.proc_packs p
JOIN dbo.proc_materials m ON m.material = p.material
WHERE p.prday >= @start
  AND p.prday < DATEADD(day, 1, @end)
  AND (m.pkpackaging <> 3 OR m.pkpackaging IS NULL)
  AND p.rtype = 1
  AND NULLIF(LTRIM(RTRIM(CAST(p.number AS varchar(50)))), '') IS NOT NULL
GROUP BY CAST(p.prday AS date), CAST(p.number AS varchar(50))
ORDER BY fecha, lot;
```

### SQL BC — ventas por lote con pedido (premisa legacy)

```sql
-- Ventas BC por lote en el periodo (Entry Type = 1)
WITH doc_order AS (
  SELECT
    ssl.[Document No.] AS document_no,
    MAX(NULLIF(LTRIM(RTRIM(ssl.[Order No.])), '')) AS [Order No.]
  FROM bc.[Sales Shipment Line] ssl
  WHERE ssl.[Posting Date] >= @start
    AND ssl.[Posting Date] < DATEADD(day, 1, @end)
  GROUP BY ssl.[Document No.]
)
SELECT
  CAST(ile.[Lot No.] AS varchar(50)) AS lot,
  MAX(NULLIF(LTRIM(RTRIM(sl.[Order No.])), '')) AS order_no,
  SUM(ABS(CAST(ile.[Quantity] AS float))) AS qty,
  SUM(ABS(CAST(ile.[Kilos] AS float))) AS kg,
  SUM(
    CASE
      WHEN NULLIF(LTRIM(RTRIM(sl.[Order No.])), '') IS NOT NULL
      THEN ABS(CAST(ile.[Quantity] AS float))
      ELSE 0.0
    END
  ) AS qty_con_pedido,
  SUM(
    CASE
      WHEN NULLIF(LTRIM(RTRIM(sl.[Order No.])), '') IS NULL
      THEN ABS(CAST(ile.[Quantity] AS float))
      ELSE 0.0
    END
  ) AS qty_sin_pedido,
  SUM(
    CASE
      WHEN NULLIF(LTRIM(RTRIM(sl.[Order No.])), '') IS NOT NULL
      THEN ABS(CAST(ile.[Kilos] AS float))
      ELSE 0.0
    END
  ) AS kg_con_pedido,
  SUM(
    CASE
      WHEN NULLIF(LTRIM(RTRIM(sl.[Order No.])), '') IS NULL
      THEN ABS(CAST(ile.[Kilos] AS float))
      ELSE 0.0
    END
  ) AS kg_sin_pedido,
  COUNT(*) AS lineas_ile
FROM bc.[Item Ledger Entry] ile
LEFT JOIN doc_order sl ON sl.document_no = ile.[Document No.]
WHERE ile.[Posting Date] >= @start
  AND ile.[Posting Date] < DATEADD(day, 1, @end)
  AND ile.[Entry Type] = 1
  AND ile.[Location Code] IN ('E', 'G')
  AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
GROUP BY CAST(ile.[Lot No.] AS varchar(50))
ORDER BY lot;
```

### Logica del cruce

1. Cargar lotes Innova (salida CAJA con `number`) y lotes BC (ventas ILE del periodo).
2. Enlazar por igualdad `number` = `[Lot No.]`.
3. Por cada dia Innova (fecha de salida), contar lotes, kg enlazados y diferencia `kg_innova − kg_bc`.
4. Acumular **con pedido** / **sin pedido** desde los campos BC del lote enlazado.

### KPIs del cruce

| KPI | Descripcion |
|-----|-------------|
| Lotes salida Innova | Packs CAJA con `number` en el periodo |
| Lotes enlazados BC | Lotes Innova con match en ILE |
| % enlazados | Lotes enlazados / lotes Innova |
| Kg Innova enlazado | Suma `weight` Innova en lotes con match |
| Kg BC enlazado | Suma `ABS([Kilos])` BC en esos lotes |
| Diferencia kg | Kg Innova enlazado − Kg BC enlazado |
| BC con/sin pedido | Unidades y kg segun `[Order No.]` del albaran |

### Referencia marzo 2026 (informe legacy, `regtime`)

Cifras orientativas del cruce anterior (~87 % lotes enlazados). Regenerar con premisa 3 + BC al actualizar el script.

| Metrica | Valor orientativo |
|---------|-------------------|
| Lotes salida Innova | ~80.737 |
| Lotes enlazados BC | ~69.775 (~87 %) |
| Kg Innova enlazado | ~422.511 |
| Kg BC enlazado (ILE) | ~423.209 |
| Diferencia (I − BC) | ~−698 kg |

### Limitaciones

- Lotes sin `number` en Innova no cruzan.
- Venta BC en fecha distinta a la salida Innova sigue enlazando por lote.
- El cruce **no corrige** la alerta VAP (afecta tinas/stock, no este enlace de cajas).

---

## Premisa 8 — Balance BC almacenes E y G

Balance de masa en Business Central para **Location Code E y G** unicamente.

### Reglas principales

| Concepto | Definicion |
|----------|------------|
| **Stock inicial (dia)** | ILE: `[Fecha empaque]` anterior al dia; venta o ajuste negativo (Entry Type 1/3) en ese dia o posteriores |
| **Stock final teorico** | **Stock inicial + Salidas Innova − Ventas** |
| **Stock final real** | Empaque del periodo hasta ese dia sin venta hasta ese dia (kg BC por lote) |
| **Encadenamiento** | Stock final del dia N = stock inicial del dia N+1 |
| **Salidas Innova** | Salidas CAJA del dia (`proc_packs`, premisa 3) |
| **Ventas BC** | ILE `[Entry Type] = 1`, `[Posting Date]` del dia, almacenes E/G |
| **Stock apertura** | Empaque anterior al periodo sin venta previa en E/G |
| **Check** | Stock final teorico − Stock final real |
| **Alcance check** | Solo lotes con empaque o movimiento ILE en el mes del periodo |
| **Historico ILE** | Consultas acotadas desde **2026-01-01** (`[Posting Date]` / `[Fecha empaque]`) para evitar timeout en BC |

### Fines de semana y festivos

En **fines de semana** (y dias sin actividad comercial):

- **No hay** Salidas Innova ni Ventas BC → esas columnas quedan a **0 kg**.
- **Stock inicial (dia)** = consulta ILE (empaque anterior; salida en dia o despues).
- **Stock final teorico** y **stock final real** se mantienen si no hay movimiento (arrastre).

La tabla del informe incluye **todos los dias del mes** (laborables y fines de semana) para mostrar el cierre diario completo.

### Desglose por tipo de producto y lote

| Nivel | Clave | Campos |
|-------|-------|--------|
| **Tipo producto** | `bc.[Conversion productos].[Cod. producto]` | Enlace: Innova `material` = `Cod. bascula`; fallback `pattern` / `[Item No.]` |
| **Balance por tipo (cajas)** | Por Cod. producto / `[Item No.]` | Stock inicial / **Entradas BC (ajustes +)** / **Ventas BC** / Stock teorico / Stock real / Check en **nº de cajas** |
| **Lote** | `proc_packs.number` = BC `[Lot No.]` | Fecha empaque, kg Innova, kg BC, kg ventas, estado (stock final / vendido / apertura) |
| **Item BC** | `[Item No.]` / `[Description]` en ILE | Coincide con `Cod. producto` cuando hay conversion |

**Enlace producto Innova ↔ BC:**

| Campo | Origen |
|-------|--------|
| **Cod. bascula** | `bc.[Conversion productos].[Cod. bascula]` = `proc_materials.material` (Innova) |
| **Cod. producto** | `bc.[Conversion productos].[Cod. producto]` ≈ ILE `[Item No.]` (ej. `RF1520M`) |
| **Pattern Innova** | `proc_materials.pattern` — respaldo si no hay fila en Conversion |

**Unidades en cajas (pestaña Balance por tipo):**

- **Entradas BC** = ajustes positivos ILE (`[Entry Type] = 2`, almacenes E/G): `SUM(ABS([Quantity]))` por `[Item No.]` / Cod. producto.
- **Ventas BC** = ventas ILE (`[Entry Type] = 1`, almacenes E/G): `SUM(ABS([Quantity]))` por `[Item No.]` / Cod. producto.
- **Stock teorico** = Stock inicial + Entradas − Ventas.
- Stock inicial dia 1 = lotes ILE en stock al inicio (empaque anterior; salida ese dia o despues); 1 lote = 1 caja.
- Stock final real = lotes ILE en stock al cierre (incluye arrastre de dias anteriores); 1 lote = 1 caja.
- **Encadenamiento:** stock final teorico dia N = stock inicial dia N+1.
- Formula: Stock teorico = Stock inicial + Salidas − Ventas; Check = teorico − real.

En la pestaña **Balance BC E/G** hay dos tablas adicionales:

1. **Por tipo de producto** — resumen agrupado con expansion de lotes.
2. **Detalle por lote** — listado completo exportable a Excel.

### Implementacion

Constante `PREMISA_BC_BALANCE_EG_REGLAS` en `generar_reporte_biomasa.py`, funciones `fetch_bc_balance_eg` y `attach_bc_balance_eg_to_report`.

---

## Formulas derivadas (arrastre)

```
Stock inventario cierre = Stock inicial + Entradas TINA − Tinas procesadas
```

---

## Pendiente

- Premisa **stock inventario / arrastre** entre meses (premisa 7)
- Actualizar arrastre CLI para usar stock de tinas en encadenamiento

> Premisa 8 (Balance BC E/G) documentada arriba; mantener alineada con `PREMISA_BC_BALANCE_EG_REGLAS`.

## Mantenimiento

1. Actualizar maestro Innova
2. Actualizar este documento
3. Actualizar `PREMISA_*` y `SQL_*` en `generar_reporte_biomasa.py`
4. Regenerar mes de referencia y contrastar totales con negocio
