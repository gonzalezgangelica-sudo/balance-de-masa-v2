# Cambios locales — CALCULO_BIOMASA

Registro de cambios guardados **en local** (commit Git). El push a remoto puede no estar disponible.

---

## 2026-08-05 — Etiquetado de producto stock E/G (Item No.)

**Commit local:** `3149f9f` (+ commit de documentación de este fichero)  
**Rama:** `main`  
**Informe regenerado:** `Reports/reporte_biomasa_20260701_20260731.html`

### Problema

En la pestaña **Stock final BC E/G**, algunos productos no coincidían con el patrón/nombre esperado (p. ej. códigos LR* con descripción de filete).  
En **Stock inicial** los mismos lotes se veían bien.

### Causa (informe, no master data)

El informe resolvía el código de producto así:

1. `bc.[Conversion productos]` (bascula Innova → Cod. producto)  
2. Pattern / Item No. como respaldo  

En **stock final** casi todos los lotes traen báscula Innova → ganaba Conversion y podía **diferir** del `Item No.` real del movimiento ILE.  
En **stock inicial** muchas filas iban sin báscula → se usaba ya el **Item No.** del lote → etiquetas correctas.

Contraste julio 2026 (Innova + ILE vía API): el `Item No.` del lote en BC coincidía con el pattern Innova; Conversion aportaba otro Cod. producto. Conversion e Innova pueden ser válidos cada uno para su uso; para **stock de almacén E/G** manda el SKU del ILE.

### Solución

Prioridad de `resolve_cod_producto_bc` en `generar_reporte_biomasa.py`:

1. **`Item No.` BC del lote (ILE)** — misma fuente que stock inicial  
2. Conversion por báscula — solo si no hay Item No.  
3. Pattern Innova / material — respaldos  

Si el enlace es `item_no_bc`, la descripción mostrada prioriza la del ítem BC.

### Resultado (julio 2026)

| Métrica | Antes | Después |
|---------|------:|--------:|
| Cajas stock final | 8.002 | 8.002 (igual) |
| Kg stock final | 44.744,82 | 44.744,82 (igual) |
| Descuadres Cod. vs pattern (familia) | 7+ | **0** |

### Ficheros tocados

- `generar_reporte_biomasa.py` — prioridad Item No. + nombre BC  
- `PREMISAS.md` — reglas de enlace producto  
- `README.md` / `INSTRUCCIONES.md` — uso y FAQ  
- `bc_ile_hybrid.py`, `generar_documento_funcional.py`, `scripts/generar_pdf_instrucciones.py` — alineación documental previa en el mismo commit de producto  

### Cómo regenerar

```bat
ejecutar_reporte.bat 01/07/2026 31/07/2026
```

### Nota remoto

`git push` a `origin` (`jjgonzalez80/CALCULO_BIOMASA`) falló (*repository not found*). El trabajo queda **solo en local** hasta corregir URL/credenciales del remoto.
