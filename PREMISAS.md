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
| 1 | **Entradas TINA** — `proc_packs`, `pkpackaging = 3`, `rtype IN ('1','12')`, fecha `prday` | Confirmada |
| 2 | **Tinas procesadas** — `proc_matxacts`, `pkpackaging = 3`, `xactpath IN ('1')`, fecha `prday` | Confirmada |
| 3 | **Salidas CAJA** — `proc_packs`, `pkpackaging <> 3` o NULL, `rtype = 1`, fecha `prday` | Confirmada |
| 4 | Stock de entrada | Confirmada (formula: Entradas TINA − Tinas procesadas) |
| 5 | Merma | Pendiente |
| 6 | Stock inventario / arrastre | Pendiente |

> Las reglas anteriores basadas en `regtime`, filtro `%tina%` en nombre de material, o totales de marzo 2026 con logica legacy **quedan sustituidas** por las premisas de esta seccion.

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

## Formulas derivadas

```
Stock de entrada = Entradas TINA − Tinas procesadas
```

```
Stock inventario cierre = Stock inicial + Entradas TINA − Tinas procesadas
```

Balance de masa:

```
Entrada TINA = Salidas CAJA + Stock de entrada + Merma
Merma = Entrada TINA − Salidas CAJA − Stock de entrada
```

Equivalente: `Merma = Tinas procesadas − Salidas CAJA`.

---

## Pendiente

- Validar totales marzo 2026 con las tres premisas (entradas, procesadas, salidas)
- Calcular y firmar **merma** de marzo con negocio
- Actualizar `generar_reporte_biomasa.py` para usar `prday` y estas queries
- Arrastre mensual y cruce BC (revisar cuando cierren premisas de stock)

## Mantenimiento

1. Actualizar maestro Innova
2. Actualizar este documento
3. Actualizar `PREMISA_*` y `SQL_*` en `generar_reporte_biomasa.py`
4. Regenerar mes de referencia y contrastar totales con negocio
