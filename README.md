# CALCULO_BIOMASA

Informe de biomasa (**Stolt Sea Farm**): consolida **Innova** (SQL Server) y **Business Central** (API OAuth / OData, o Azure SQL) en un HTML con KPIs, gráficas, exportación Excel y balance ERP por producto.

**Estado:** operativo. Fuente BC recomendada: `BC_SOURCE=api`.

---

## Documentación (sin solapes)

| Documento | Contenido |
|-----------|-----------|
| **[FUNCIONAMIENTO.md](FUNCIONAMIENTO.md)** | Cómo funciona el sistema (lectura principal) |
| **[INSTRUCCIONES.md](INSTRUCCIONES.md)** | Instalación, `.env`, generar informe |
| **[PREMISAS.md](PREMISAS.md)** | Reglas de negocio canónicas + SQL |
| **[docs/KPI_DEFINICIONES.md](docs/KPI_DEFINICIONES.md)** | CHECK, estados A–D, columnas |
| **[docs/CAMBIOS_LOCAL.md](docs/CAMBIOS_LOCAL.md)** | Historial de cambios |
| **[docs/BC_API_AL_CONTRACT.md](docs/BC_API_AL_CONTRACT.md)** | API BC: no usar v2.0 sin lote/almacén |
| `docs/CREDENCIALES_LOCAL.md` | Secretos locales (**no versionado**) |

---

## Despliegue Windows (resumen)

| Script | Función |
|--------|---------|
| `crear_entorno.bat` | Python, `.venv`, dependencias |
| `configurar_credenciales.bat` | Abre / crea `.env` |
| `ejecutar_reporte.bat` | Genera el informe y abre el HTML |
| `Iniciar_Reporte_Biomasa.bat` | Menú |

```bat
crear_entorno.bat
Iniciar_Reporte_Biomasa.bat
ejecutar_reporte.bat 01/06/2026 30/06/2026
```

Requisitos: Windows 10/11, Python 3.11+, red a Innova y BC, escritura en la carpeta del proyecto.

Credenciales solo en **`.env`** (plantilla `.env.example`). No distribuir `.env` por correo.

Detalle: [INSTRUCCIONES.md](INSTRUCCIONES.md).  
Paquetes listos AEV/JUY (local): `distribucion\AEV` y `distribucion\JUY` (no van a Git).

---

## Qué entrega

- HTML: `Reports/reporte_biomasa_YYYYMMDD_YYYYMMDD.html`
- Excel: botones de exportación en el propio HTML
- Pestañas: ver [FUNCIONAMIENTO.md](FUNCIONAMIENTO.md) §7

### Reglas clave (recordatorio)

- **1 lote = 1 caja** en el balance BC (no `COUNT(*)` packs)
- **CHECK** = real − teórico (kg y cajas)
- Almacenes **E / G / Z**
- Estados **A / B / C / D** — ver [docs/KPI_DEFINICIONES.md](docs/KPI_DEFINICIONES.md)

---

## CLI

```bat
.venv\Scripts\python.exe generar_reporte_biomasa.py --start 01/06/2026 --end 30/06/2026
.venv\Scripts\python.exe generar_reporte_biomasa.py --start 01/06/2026 --end 30/06/2026 --skip-bc
```

| Script | Uso |
|--------|-----|
| `generar_reporte_biomasa.py` | Informe principal |
| `contrastar_lote_innova_bc.py` | Contraste lote Innova vs BC |
| `scripts/crear_usuario_innova_biomasa.py` | Alta login Innova solo-lectura |
| `scripts/preparar_distribucion_usuarios.py` | Regenerar carpetas AEV/JUY |

---

## Estructura

```
CALCULO_BIOMASA/
├── Iniciar_Reporte_Biomasa.bat
├── crear_entorno.bat / ejecutar_reporte.bat / configurar_credenciales.bat
├── generar_reporte_biomasa.py / bc_api_client.py / bc_ile_hybrid.py
├── FUNCIONAMIENTO.md / PREMISAS.md / INSTRUCCIONES.md / README.md
├── requirements.txt / .env.example
├── docs/          # KPI, cambios, API BC, credenciales locales
├── scripts/       # Usuarios Innova, distribución
├── Reports/       # HTML (gitignored)
└── distribucion/  # AEV / JUY locales (gitignored)
```

---

## Mantenimiento

1. Cambiar reglas → actualizar **PREMISAS.md** y constantes `PREMISA_*` / `SQL_*` en código.  
2. Cambiar significado de KPIs → **docs/KPI_DEFINICIONES.md**.  
3. Cambiar flujo / pestañas → **FUNCIONAMIENTO.md**.  
4. Tras `requirements.txt`: `crear_entorno.bat` en cada puesto.  
5. Anotar cambios relevantes en **docs/CAMBIOS_LOCAL.md**.
