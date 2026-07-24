# Contrato mínimo — API AL custom ILE (Business Central)

Destino correcto para el informe de biomasa. La API estándar v2.0 `itemLedgerEntries` **no** expone `Lot No.` ni `Location Code`, por lo que no sirve para almacenes E/G ni cruce por lote.

Hasta que BT Cloud / el equipo AL publique esta API, el informe usa **ODataV4 `ItemLedgerEntries`** (sí trae lote y almacén) + enriquecimiento Innova (`prday` / `weight`). Ese puente es temporal.

## Publisher / ruta propuesta

```
/api/stolt/biomasa/v1.0/companies({companyId})/itemLedgerEntries
```

Variables de entorno esperadas:

| Variable | Ejemplo |
|----------|---------|
| `BC_API_PUBLISHER` | `stolt` |
| `BC_API_GROUP` | `biomasa` |
| `BC_API_VERSION` | `v1.0` |
| `BC_API_ENTITY` | `itemLedgerEntries` |

## Campos obligatorios por movimiento

| Campo API (camelCase) | Origen BC | Uso informe |
|----------------------|-----------|-------------|
| `entryNumber` | Entry No. | Idempotencia / trazas |
| `entryType` | Entry Type (1=Sale, 2=Pos.Adj, 3=Neg.Adj) | Balance cajas / análisis |
| `postingDate` | Posting Date | Ventas / ajustes del día |
| `locationCode` | Location Code | Filtro **E** y **G** |
| `lotNumber` | Lot No. | Cruce con Innova `proc_packs.number` |
| `itemNumber` | Item No. | Balance por producto |
| `description` | Description | Etiquetas |
| `quantity` | Quantity | Cajas (ABS) |
| `documentNumber` | Document No. | Cruce pedido (opcional) |
| `idUsuario` | **Id. usuario** (custom ILE) | Análisis Type 3 por usuario |

## Campos recomendados (si se publican en la API)

| Campo API | Origen BC | Nota |
|-----------|-----------|------|
| `kilos` | Kilos (custom) | Si falta → Innova `SUM(weight)` por lote |
| `fechaEmpaque` | Fecha empaque (custom) | Si falta → Innova `MIN(prday)` por lote |

Con la premisa híbrida, `kilos` y `fechaEmpaque` pueden omitirse en la API; el informe los rellena desde Innova.

## Filtros que debe soportar

- `$filter` por `postingDate` (rango inclusive)
- `$filter` por `locationCode in ('E','G')` o equivalente
- `$filter` por `entryType in (1,2,3)` (opcional)
- Paginación OData (`@odata.nextLink` / `$skiptoken`)

## Entidades auxiliares (fase 2)

1. **Conversion productos** (`codBascula` → `codProducto`) — hoy solo en SQL BC.
2. **Sales shipment → Order No.** — cruce “con/sin pedido”; sin ella el cruce API marca salidas sin pedido.

## Criterio de aceptación

1. App registration `CLIENT_ID` con permiso a la API.
2. Probe: `GET .../itemLedgerEntries?$top=1` devuelve lote + almacén E/G.
3. Abril de referencia: check kg y cajas alineados con la vía SQL (tolerancia acordada).

Contacto: solicitar creación del page/API AL al equipo BT Cloud / DevOps BC.
