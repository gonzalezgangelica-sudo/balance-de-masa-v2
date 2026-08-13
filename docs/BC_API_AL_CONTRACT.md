# Recomendación técnica — API BC para biomasa (evitar errores)

**Estado:** recomendación documentada en el proyecto. **No es una petición enviada a IT** ni un pendiente operativo. El informe **ya funciona** con ODataV4 + Innova.

## Por qué existe este documento

Sirve para **no cometer el error** de basar el balance E/G/Z en la API estándar v2.0 de Business Central.

| Opción | ¿Sirve para biomasa E/G? | Motivo |
|--------|--------------------------|--------|
| API v2.0 `itemLedgerEntries` | **No** | No expone `Lot No.` ni `Location Code` |
| ODataV4 `ItemLedgerEntries` | **Sí (actual)** | Trae lote y almacén; kilos/`prday` desde Innova |
| API AL custom (futuro) | **Sí (ideal)** | Podría incluir `Id. usuario`, Kilos, Fecha empaque |

Si alguien “migra a API v2.0” sin lote/almacén, el cruce y el balance E/G/Z **dejarían de ser fiables**.

## Qué usa el informe hoy

1. Intenta API AL custom (`BC_API_PUBLISHER` / `GROUP` / `VERSION`) **si está publicada**.
2. Si no, **ODataV4** automáticamente.
3. Enriquece cada lote con Innova (`prday`, `weight`).

No hace falta ninguna acción de IT para operar el reporte.

## Si en el futuro se publica una API AL (opcional)

Campos útiles (referencia, no requisito actual):

| Campo API | Uso |
|-----------|-----|
| `lotNumber`, `locationCode` (E/G) | Obligatorios para no romper el modelo |
| `entryType`, `postingDate`, `quantity`, `itemNumber` | Balance / análisis |
| `idUsuario` | Análisis Type 3 por usuario (hoy OData no lo trae → `(sin usuario)`) |
| `kilos`, `fechaEmpaque` | Opcionales; si faltan, Innova los cubre |

Ruta orientativa: `/api/stolt/biomasa/v1.0/companies({id})/itemLedgerEntries`

Variables ya previstas en `.env.example`: `BC_API_PUBLISHER`, `BC_API_GROUP`, `BC_API_VERSION`, `BC_API_ENTITY`.

## Resumen

- **Guardar** esta nota para evitar errores de diseño.  
- **No** tratarla como ticket abierto ni como envío a BT Cloud.  
- Operación diaria: OData + Innova es la vía válida y documentada.
