# CALCULO_BIOMASA

Script para generar un reporte HTML de biomasa desde SQL Server, con KPIs, tablas, graficas y exportacion a Excel.

## Que hace

- Consulta datos de entradas, cajas y salidas en SQL Server.
- Genera reporte HTML con:
  - KPIs de biomasa.
  - Graficas interactivas (con boton de maximizar por grafica).
  - Tabla de detalle diario.
  - Tabla entradas / salidas / stock / merma.
  - Top materiales de entrada y salida.
- Exportacion de tablas:
  - Exportar cada tabla a Excel.
  - Exportar todo en un unico `.xlsx` con 3 hojas (una por tabla) desde el boton superior derecho.

## Requisitos

- Python 3.11+
- Acceso a SQL Server
- Dependencias Python:
  - `pymssql`
  - `keyring` (opcional, recomendado)

## Instalacion rapida

```bash
python -m pip install -r requirements.txt
```

## Ejecucion

```bash
python generar_reporte_biomasa.py --start 01/04/2026 --end 30/04/2026 --user TU_USUARIO --password TU_PASSWORD
```

Modo por fecha de despesque usando solo `vw_stolt`:

```bash
python generar_reporte_biomasa.py --start 01/03/2026 --end 31/03/2026 --data-source vw_stolt_despesque
```

Por defecto se usa el modo **legacy** (`proc_packs` / `proc_matxacts` por `regtime`). Ver [PREMISAS.md](PREMISAS.md) para la clasificacion de entradas/salidas en ambos modos.

Validacion opcional de stock:

```bash
python generar_reporte_biomasa.py --start 01/04/2026 --end 30/04/2026 --stock-inicial 120000 --stock-final-fisico 117500
```

Ecuacion aplicada:
- Stock final teorico sin procesar = Stock inicial + Entradas - Cajas
- Ajuste conciliacion = Stock final teorico - Stock final fisico
- Stock sin procesar diario = acumulado de (Entradas - Cajas) para reflejar arrastre a dias siguientes

Tambien puedes usar variables de entorno:

- `DB_SERVER`
- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`

Tambien puedes definirlas en un archivo `.env` en la raiz del proyecto:

```env
DB_SERVER=192.168.14.236
DB_NAME=Innova
DB_USER=TU_USUARIO
DB_PASSWORD=TU_PASSWORD

# Business Central (Azure SQL)
BC_SERVER=bitmap-ssfprod-sqlsvr-01.database.windows.net
BC_DATABASE=bitmap-ssfprod-QLSRVPDE-sqldb
BC_USER=bitmap-ssfprod-dbsvc-01
BC_PASSWORD=TU_PASSWORD_BC
```

Tambien se admiten lineas `Server Name:`, `Database:`, `User:` y `Password:` para BC en `.env`.

Ejemplo (PowerShell):

```powershell
$env:DB_USER="sa"
$env:DB_PASSWORD="***"
python .\generar_reporte_biomasa.py --start 01/04/2026 --end 30/04/2026
```

## Salida

El reporte se guarda en:

- `Reports/reporte_biomasa_YYYYMMDD_YYYYMMDD.html`

## Premisas de negocio

**Estado: premisas validadas** (referencia marzo 2026). Documento canonico: **[PREMISAS.md](PREMISAS.md)**. Implementacion: constantes `PREMISA_*` y `SQL_*` en `generar_reporte_biomasa.py`.

| Concepto | Regla |
|----------|--------|
| Entrada | `proc_materials.pkpackaging = 3` |
| Salida | `pkpackaging <> 3` o NULL |
| Stock (entrada) | `pkpackaging = 3` y `proc_packs.rtype <> 12` |
| Merma (entrada) | `pkpackaging = 3` y `proc_packs.rtype = 12` |
| Cajas | Consumo TINA en `proc_matxacts` (`xactpath = 1`, `%tina%`) |

Entradas = Stock + Merma. Fuentes y totales de control en [PREMISAS.md](PREMISAS.md).

Cada reporte HTML incluye un bloque visible con las premisas y enlace al documento.

## Notas funcionales

- Fechas en formato espanol: `dd/mm/aaaa`.
- Etiquetas principales en el reporte:
  - Entradas de biomasa
  - Cajas (kg)
  - Salidas de biomasa
  - Diferencia
  - Validacion de stock (si se informan parametros de stock)
