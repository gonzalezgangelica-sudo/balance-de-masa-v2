# Cambios locales — CALCULO_BIOMASA

Registro de cambios guardados **en local** (commit Git). El push a remoto puede no estar disponible.

---

## 2026-08-13 — Balance cajas: Item No. BC + estados A/B/C

### Cambio

- Producción Innova por tipo: si el lote está en ILE → **`Item No.` BC**; Conversion solo para solo-Innova.
- CHECK cajas = **real − teórico** (misma convención que desvío kg).
- Estados globales: **A** (correcto), **B** (total 0 con productos CHECK≠0: compensado), **C** (total ≠ 0).
- Pares ±X visibles; el detalle por producto no se oculta.

### Ficheros

- `generar_reporte_biomasa.py`, `tests/test_desvio_bc.py`
- `PREMISAS.md`, `INSTRUCCIONES.md`, `README.md`

---

## 2026-08-12 — Almacén Z incluido en stock BC

**Rama:** `main`

### Cambio

El balance / cruce / stock de Business Central pasa de almacenes **E y G** a **E, G y Z**.

- Constante `BC_STOCK_LOCATIONS = ("E", "G", "Z")` en `generar_reporte_biomasa.py`
- Filtro SQL `SQL_BC_LOCATION_EG` y API OData/custom (`bc_api_client.fetch_ile_eg`)
- Textos de informe y documentación (PREMISAS, INSTRUCCIONES, README)

### Nota usuarios

Los logins Innova (AEV, JUY, biomasa_ro) no cambian: solo ven más stock BC al regenerar el informe con el código actualizado. Actualizar también la carpeta `distribucion/JUY` al copiar el paquete.

---

## 2026-08-05 — Etiquetado de producto stock E/G/Z (Item No.)

Prioridad vigente (también en producción Innova por tipo): **`Item No.` ILE** si el lote está en BC; Conversion / pattern solo si no hay Item No. Ver entrada 2026-08-13.
