# Premisas de calculo de biomasa

Documento canonico del proyecto **CALCULO_BIOMASA**. Cualquier cambio en reglas de negocio debe actualizarse aqui y en `generar_reporte_biomasa.py` (constantes `PREMISA_*` y `SQL_*`).

## Terminologia planta

| Termino planta | Significado | En Innova / reporte |
|----------------|-------------|---------------------|
| **TINA** | **Entrada** de biomasa | `proc_packs` con `pkpackaging = 3`; procesado en `proc_matxacts` |
| **CAJA** | **Salida** de producto | `proc_packs` con `pkpackaging <> 3` o NULL |

No confundir: el kg de **TINA procesada** (`proc_matxacts`) no es salida CAJA; la salida CAJA son los packs de salida en `proc_packs`.

## Estado de validacion

**Premisas validadas por negocio** (referencia marzo 2026, modo legacy). Implementadas en el reporte HTML y en las constantes SQL del script.

| # | Premisa | Estado |
|---|---------|--------|
| 1 | Entrada TINA = `proc_materials.pkpackaging = 3` | Validada |
| 2 | Salida CAJA = `pkpackaging <> 3` o NULL | Validada |
| 3 | Stock = inventario TINA al cierre del periodo (sin procesar) | Validada |
| 4 | Merma = desperdicio del procesado; **no** es una etiqueta Innova (`rtype`) | Validada |
| 5 | **Merma (kg) = Entrada TINA − Salidas CAJA − Stock** | Validada |
| 6 | TINA procesada (kg) en `proc_matxacts`; fecha = `proc_packs.regtime` de la TINA | Validada |

Totales de control (marzo 2026, con `--arrastre-mensual`):

| Metrica | kg | Fuente |
|---------|-----|--------|
| Entradas TINA | 665.081,58 | `proc_packs` entrada, `regtime` |
| Salidas CAJA | 492.968,78 | `proc_packs` salida, `regtime` |
| Stock inventario cierre | 678.373,33 | Stock inicial + Entradas − TINA procesada |
| **Merma** | **−506.260,53** | Entradas − Salidas − Stock |
| TINA procesada | 429.619,00 | `proc_matxacts` + TINA; fecha `proc_packs.regtime` |
| Balance TINA − CAJA | 172.112,80 | Entradas TINA − Salidas CAJA |

Comprobacion balance: `Entrada = Salida + Stock + Merma` → `665.081,58 = 492.968,78 + 678.373,33 + (−506.260,53)`.

Merma negativa en un mes indica que las salidas CAJA consumieron stock arrastrado de periodos anteriores ademas de la entrada del mes; usar `--arrastre-mensual` para un stock de cierre coherente.

## 1. Clasificacion entrada / salida

La clasificacion se basa en **`pkpackaging`** de **`dbo.proc_materials`**:

| Tipo planta | Condicion Innova | SQL (alias `m` = proc_materials) |
|-------------|------------------|----------------------------------|
| **Entrada TINA** | `pkpackaging = 3` | `m.pkpackaging = 3` |
| **Salida CAJA** | Cualquier otro valor o NULL | `m.pkpackaging <> 3 OR m.pkpackaging IS NULL` |

JOIN entre el movimiento de peso y `proc_materials` por `material`.

### Fuente de datos y fecha (modo legacy, por defecto)

| Metrica | Fuente | Campo fecha |
|---------|--------|-------------|
| Entradas TINA | `dbo.proc_packs` + `dbo.proc_materials` | `regtime` |
| Salidas CAJA | `dbo.proc_packs` + `dbo.proc_materials` | `regtime` |
| TINA procesada (kg) | `proc_matxacts` + TINA (`pack = id`) | `proc_packs.regtime` de la tina |
| Stock / Merma (balance) | Calculado | Inventario al cierre del periodo |
| Arrastre (legacy) | `dbo.vw_stolt` + `dbo.proc_materials` | `fdespesque` |

Modo alternativo `--data-source vw_stolt_despesque`: entradas y salidas diarias desde `dbo.vw_stolt` por `fdespesque`.

## 2. Stock y merma (balance de masa)

### Definicion de negocio (planta)

| Concepto | Que es |
|----------|--------|
| **Entrada TINA** | Biomasa que entra |
| **Salida CAJA** | Producto que sale |
| **Stock** | Lo que queda en inventario TINA (sin procesar) al cierre |
| **Merma** | **Desperdicio del procesado** — no es stock |

Balance de masa:

```
Entrada TINA = Salidas CAJA + Stock + Merma
```

Por tanto:

```
Merma = Entrada TINA − Salidas CAJA − Stock
```

**Stock** en esta formula es el inventario TINA al **cierre del periodo**:

```
Stock cierre = Stock inicial + Entradas TINA − TINA procesada
```

Con `--arrastre-mensual`, el stock inicial se encadena desde el mes anterior. Con `--stock-final-fisico`, el stock de la formula puede sustituirse por la medicion de planta.

**Nota:** `proc_packs.rtype` en Innova **no define merma** en este proyecto. No usar `rtype = 12` como merma.

## 3. TINA procesada (kg)

Metrica de **entrada TINA** en `proc_matxacts` (`xactpath = 1`, material `%tina%`). No es salida CAJA.

**Fecha diaria:** `proc_packs.regtime` de la tina (`proc_matxacts.pack = proc_packs.id`).

Metrica auxiliar de control: `Diferencia = Entradas TINA − TINA procesada` (kg que quedan sin procesar en el periodo, antes de arrastre).

## 4. Diferencia, balance y arrastre

| Metrica | Formula |
|---------|---------|
| Diferencia (kg) | Entradas TINA − TINA procesada |
| Balance TINA − CAJA | Entradas TINA − Salidas CAJA |
| Stock sin procesar (legacy) | Arrastre en `vw_stolt` por `fdespesque` |
| Stock sin procesar (`vw_stolt_despesque`) | Arrastre de (Entradas TINA − TINA procesada) |

### Arrastre mensual entre periodos (CLI)

Encadenamiento de inventario TINA mes a mes (no saldo historico total):

| Concepto | Formula / regla |
|----------|-----------------|
| Stock cierre (mes M) | Stock apertura(M) + Entradas TINA(M) − TINA procesada(M) |
| Stock apertura (mes M) | Stock cierre(M−1) |
| Mes ancla | `--stock-ancla` (default 01/01 del ano del periodo) con `--stock-ancla-kg` (default 0) |
| Tinas arrastradas | Packs entrada creados en M−1 (`proc_packs.regtime`) consumidos en el periodo (`proc_matxacts.regtime`) |

Activar con `--arrastre-mensual` o `--stock-inicial auto`.

Ejemplo feb→mar 2026: ~43 packs / ~11.715 kg de tinas de febrero procesadas en marzo.

## 5. Validacion opcional de stock fisico

Parametros `--stock-inicial` y `--stock-final-fisico`:

- Stock final teorico = Stock inicial + Entradas TINA − TINA procesada
- Ajuste conciliacion = Stock final teorico − Stock final fisico
- Si se informa stock fisico, se usa en el calculo de merma del balance

## 6. Cruce Business Central

| Concepto | Regla |
|----------|--------|
| Clave de enlace | `proc_packs.number` = `bc.[Item Ledger Entry].[Lot No.]` |
| Ventas BC | `Entry Type = 1`; kg = `ABS([Kilos])` |
| Pedido | `[Order No.]` de `Sales Shipment Line` |
| Salidas | Packs CAJA Innova agrupados por `number` |

Referencia marzo 2026: ~87% lotes Innova con correspondencia en BC.

## 7. Salidas del reporte

- KPIs, graficas, detalle diario
- Tabla entradas / salidas / stock / merma (exportable Excel)
- Top materiales, premisas visibles, cruce BC

## Mantenimiento

1. Actualizar maestro Innova
2. Actualizar este documento
3. Actualizar `PREMISA_*` y `SQL_*` en `generar_reporte_biomasa.py`
4. Regenerar mes de referencia y contrastar totales
