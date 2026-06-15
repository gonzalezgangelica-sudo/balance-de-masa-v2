# Premisas de calculo de biomasa

Documento canonico del proyecto **CALCULO_BIOMASA**. Cualquier cambio en reglas de negocio debe actualizarse aqui y en `generar_reporte_biomasa.py` (constantes `PREMISA_*` y `SQL_*`).

## Estado de validacion

**Premisas validadas por negocio** (referencia marzo 2026, modo legacy). Implementadas en el reporte HTML y en las constantes SQL del script.

| # | Premisa | Estado |
|---|---------|--------|
| 1 | Entrada = `proc_materials.pkpackaging = 3` | Validada |
| 2 | Salida = `pkpackaging <> 3` o NULL | Validada |
| 3 | Stock en entrada = `pkpackaging = 3` y `proc_packs.rtype <> 12` | Validada |
| 4 | Merma en entrada = `pkpackaging = 3` y `proc_packs.rtype = 12` | Validada |
| 5 | Cajas = consumo TINA en `proc_matxacts` (`xactpath = 1`) | Validada |

Totales de control (marzo 2026):

| Metrica | kg | Fuente |
|---------|-----|--------|
| Entradas | 665.081,58 | `proc_packs` + `proc_materials`, `regtime` |
| Salidas | 492.968,78 | `proc_packs` + `proc_materials`, `regtime` |
| Stock (entrada) | 235.462,58 | Entradas con `rtype <> 12` |
| Merma (entrada) | 429.619,00 | Entradas con `rtype = 12` |
| Diferencia E-S | 172.112,80 | Entradas - Salidas |
| Cajas | 424.729,00 | `proc_matxacts`, `%tina%` |

Identidad: **Entradas = Stock + Merma**.

## 1. Clasificacion entrada / salida

La clasificacion se basa en **`pkpackaging`** de **`dbo.proc_materials`**:

| Tipo | Condicion | SQL (alias `m` = proc_materials) |
|------|-----------|----------------------------------|
| **Entrada** | `pkpackaging = 3` | `m.pkpackaging = 3` |
| **Salida** | Cualquier otro valor o NULL | `m.pkpackaging <> 3 OR m.pkpackaging IS NULL` |

JOIN entre el movimiento de peso y `proc_materials` por `material`.

### Fuente de datos y fecha (modo legacy, por defecto)

| Metrica | Fuente | Campo fecha |
|---------|--------|-------------|
| Entradas | `dbo.proc_packs` + `dbo.proc_materials` | `regtime` |
| Salidas | `dbo.proc_packs` + `dbo.proc_materials` | `regtime` |
| Stock / merma | `dbo.proc_packs` + `dbo.proc_materials` | `regtime` |
| Cajas | `dbo.proc_matxacts` (`xactpath = 1`) | `regtime` |
| Arrastre (legacy) | `dbo.vw_stolt` + `dbo.proc_materials` | `fdespesque` |

Modo alternativo `--data-source vw_stolt_despesque`: entradas y salidas diarias desde `dbo.vw_stolt` por `fdespesque`, con JOIN a `proc_materials` y la misma premisa `pkpackaging`. Stock/merma por `rtype` solo en modo legacy (`proc_packs`).

## 2. Stock y merma (desglose de entradas)

Solo aplica a **entradas de biomasa** (`pkpackaging = 3`) en **`dbo.proc_packs`**:

| Concepto | Condicion | SQL |
|----------|-----------|-----|
| **Stock** | `rtype <> 12` (o NULL) | `m.pkpackaging = 3 AND (p.rtype <> 12 OR p.rtype IS NULL)` |
| **Merma** | `rtype = 12` | `m.pkpackaging = 3 AND p.rtype = 12` |

- **Entradas = Stock + Merma** (siempre).
- **Stock** aqui = peso de entrada registrado como stock en Innova (`rtype` distinto de 12), no inventario fisico de cierre.
- **Merma** = peso de entrada con `rtype = 12` (en la practica, casi todo el peso TINA por calibre).
- **Diferencia E-S** (Entradas - Salidas) es independiente: las salidas son materiales con `pkpackaging <> 3`.

Composicion tipica del stock de entrada (marzo 2026, referencia): filetes/producto E (~60%), entero tinas congelacion (~17%), GG/ENTERO (~17%), TINA residual (~6%).

## 3. Cajas (kg)

Consumo de materiales **TINA** en `proc_matxacts` con `xactpath = 1`.

Criterio: `LOWER(m.name) LIKE '%tina%'` (independiente de `pkpackaging`).

## 4. Diferencia, balance y arrastre

| Metrica | Formula |
|---------|---------|
| Diferencia (kg) | Entradas - Cajas |
| Balance E-S | Entradas - Salidas |
| Stock sin procesar (legacy) | Arrastre acumulado en `vw_stolt` por `fdespesque` |
| Stock sin procesar (`vw_stolt_despesque`) | Arrastre acumulado de (Entradas - Cajas) |

## 5. Validacion opcional de stock fisico

Parametros `--stock-inicial` y `--stock-final-fisico`:

- Stock final teorico = Stock inicial + Entradas - Cajas
- Ajuste conciliacion = Stock final teorico - Stock final fisico

## 6. Cruce Business Central (salidas con / sin pedido)

Conexion Azure SQL a Business Central (variables `BC_SERVER`, `BC_DATABASE`, `BC_USER`, `BC_PASSWORD` en `.env`).

| Concepto | Regla |
|----------|--------|
| **Clave de enlace** | `dbo.proc_packs.number` (codigo de lote/caja) = `bc.[Item Ledger Entry].[Lot No.]` |
| Fuente BC ventas | `bc.[Item Ledger Entry]`, `Entry Type = 1` |
| Kilos BC | Campo `[Kilos]` (valor absoluto), comparado con `proc_packs.weight` |
| Pedido BC | `[Order No.]` del albaran (`bc.[Sales Shipment Line]`, mismo `[Document No.]`) |
| Con pedido | `[Order No.]` informado |
| Sin pedido | `[Order No.]` vacio o NULL |
| Salidas Innova | `proc_packs` + `proc_materials`, premisa salida (`pkpackaging <> 3`), agrupadas por `number` |

El reporte muestra, por dia de regtime Innova:

- Lotes de salida Innova (`number`)
- Lotes enlazados en BC (`Lot No.`)
- Kg Innova enlazados (`proc_packs.weight`)
- Kg BC enlazados (`[Kilos]` ILE)
- Diferencia kg (Innova − BC) en lotes enlazados
- Unidades BC con/sin pedido (solo lotes enlazados)

Referencia marzo 2026: ~87% de lotes Innova tienen correspondencia en BC (`72930 / 83648` lotes distintos).

## 7. Salidas del reporte

El HTML incluye:

- KPIs y graficas diarias.
- Tabla detalle (entradas, cajas, salidas, balance, arrastre).
- Tabla **Entradas, salidas, stock y merma** (exportable a Excel).
- Top materiales de entrada y salida.
- Bloque visible con premisas y enlace a este documento.

## Mantenimiento

Al cambiar reglas o maestros de materiales:

1. Actualizar maestro en Innova (`pkpackaging`, nombres, etc.).
2. Actualizar este documento.
3. Actualizar `PREMISA_*` y `SQL_*` en `generar_reporte_biomasa.py`.
4. Regenerar reporte de un mes de referencia y contrastar totales.
