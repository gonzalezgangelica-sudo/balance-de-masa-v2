# Instrucciones — CALCULO_BIOMASA

Instalación, credenciales y generación del informe.  
**Cómo funciona** (lógica de negocio y pestañas): [FUNCIONAMIENTO.md](FUNCIONAMIENTO.md).  
**Reglas canónicas + SQL:** [PREMISAS.md](PREMISAS.md).  
**CHECK / estados:** [docs/KPI_DEFINICIONES.md](docs/KPI_DEFINICIONES.md).  
**API BC:** [docs/BC_API_AL_CONTRACT.md](docs/BC_API_AL_CONTRACT.md).  
Credenciales locales (no versionadas): `docs/CREDENCIALES_LOCAL.md`.

---

## 1. Qué hace

Genera `Reports/reporte_biomasa_YYYYMMDD_YYYYMMDD.html` con Innova + Business Central (E/G/Z).

---

## 2. Instalación (una vez por PC)

**Requisitos:** Windows 10/11 · Python 3.11+ · red a Innova (y API BC si `BC_SOURCE=api`).

```bat
crear_entorno.bat
```

1. Crea `.venv` e instala dependencias.  
2. Si no hay `.env`, cópielo desde `.env.example`.  
3. Edite `.env` (opción 2 del menú o Bloc de notas).

Menú:

```bat
Iniciar_Reporte_Biomasa.bat
```

---

## 3. Credenciales (`.env`)

**No compartir ni versionar** `.env`.

### Innova (obligatorio)

```
DB_SERVER=...
DB_NAME=Innova
DB_USER=AEV
DB_PASSWORD=...
```

Alta solo-lectura (DBA):

```bat
python scripts/crear_usuario_innova_biomasa.py --login INICIALES --update-env
```

### Business Central (recomendado: API)

```
BC_SOURCE=api
CLIENT_ID=...
TENANT_ID=...
CLIENT_SECRET=...
BC_ENVIRONMENT=Produccion
COMPANY_ID=...
COMPANY_NAME=Stolt Sea Farm, S.A.
```

Si la API AL custom no está publicada, el cliente usa **ODataV4** + enrich Innova.  
Opcional: `BC_SERVER` / `BC_DATABASE` / `BC_USER` / `BC_PASSWORD` para Conversion o `BC_SOURCE=sql`.

---

## 4. Generar el informe

```bat
ejecutar_reporte.bat 01/06/2026 30/06/2026
```

Sin fechas (las pide):

```bat
ejecutar_reporte.bat
```

Con Python:

```bat
.venv\Scripts\python.exe generar_reporte_biomasa.py --start 01/06/2026 --end 30/06/2026
```

| Flag | Efecto |
|------|--------|
| `--bc-source api` / `sql` | Fuerza fuente BC |
| `--skip-bc` | Solo Innova |
| `--output ruta.html` | Ruta de salida |

La descarga BC de un mes completo puede tardar varios minutos.

---

## 5. Recordatorio de lectura del HTML

- Balance de stock: **1 lote = 1 caja** · CHECK = **real − teórico** · estados **A/B/C/D**.  
- **Movimientos ILE** usa `ABS(Quantity)`: no confundir con el balance de stock.  
- Pestaña **Análisis ILE**: auditoría Type 1/2/3.  
- Detalle completo: [FUNCIONAMIENTO.md](FUNCIONAMIENTO.md).

---

## 6. Problemas frecuentes

| Síntoma | Qué hacer |
|---------|-----------|
| Error login Innova | Revisar `DB_*` en `.env`; VPN/red |
| Error OAuth / BC API | Revisar `CLIENT_*`, `TENANT_ID`, `COMPANY_ID` |
| Timeout BC | Subir `BC_TIMEOUT` o reintentar |
| CHECK cajas ≠ 0 | Ver producto en Balance por tipo; [FUNCIONAMIENTO.md](FUNCIONAMIENTO.md) §9 |
| Total cajas = 0 y productos ≠ 0 | Estado **B** (compensado) |
| Cajas ≠ 0 y kg ≈ 0 | Estado **D** (inconsistencia) |
| Sin HTML | Mirar `Reports\` o `logs\` |
| Ejecución desde UNC `\\servidor\...` | Mapear unidad o copiar a disco local |

---

## 7. Distribución a usuarios (AEV / JUY)

En el PC de administración:

```bat
python scripts/preparar_distribucion_usuarios.py
```

Entregar solo `distribucion\AEV` o `distribucion\JUY` (incluyen `.env` del usuario; **no** subir a Git).

---

## 8. Mantenimiento

- Reglas → **PREMISAS.md** + código (`PREMISA_*` / `SQL_*`).  
- Flujo / pestañas → **FUNCIONAMIENTO.md**.  
- KPIs → **docs/KPI_DEFINICIONES.md**.  
- Historial → **docs/CAMBIOS_LOCAL.md**.
