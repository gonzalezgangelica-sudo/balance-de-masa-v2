/*
  Balance BC almacenes E/G — consultas para comparar con Excel
  Periodo ejemplo: marzo 2026 (ajusta @start / @end)

  Premisa balance diario:
    Stock inicial (dia)      = stock final del dia anterior (1.er dia = stock apertura)
    Stock final teorico      = Stock inicial + Salidas Innova - Ventas BC
    Stock final real         = empaque periodo sin venta hasta ese dia (ver snapshot lotes)
    Check                    = Stock final teorico - Stock final real

  Filtros BC:
    Location Code IN ('E','G')
    Ventas: Entry Type = 1
    Historico ILE desde 2026-01-01 (Posting Date / Fecha empaque)
*/

DECLARE @start date = '2026-03-01';
DECLARE @end   date = '2026-03-31';
DECLARE @ile_from date = '2026-01-01';

/* =============================================================================
   1. VENTAS BC DIARIO (alimenta columna Ventas)
   ============================================================================= */
SELECT
  CAST(ile.[Posting Date] AS date) AS fecha,
  SUM(ABS(CAST(ile.[Kilos] AS float))) AS kg,
  COUNT(DISTINCT ile.[Lot No.]) AS lotes
FROM bc.[Item Ledger Entry] ile
WHERE ile.[Posting Date] >= @start
  AND ile.[Posting Date] < DATEADD(day, 1, @end)
  AND ile.[Entry Type] = 1
  AND ile.[Location Code] IN ('E', 'G')
GROUP BY CAST(ile.[Posting Date] AS date)
ORDER BY fecha;

/* =============================================================================
   2. SALIDAS INNOVA DIARIO (alimenta columna Salidas Innova)
      Base: Innova proc_packs + proc_materials, prday
   ============================================================================= */
SELECT
  CAST(p.prday AS date) AS fecha,
  SUM(CAST(p.weight AS float)) AS kg_salida_innova,
  COUNT(*) AS packs_salida
FROM dbo.proc_packs p
JOIN dbo.proc_materials m ON m.material = p.material
WHERE p.prday >= @start
  AND p.prday < DATEADD(day, 1, @end)
  AND (m.pkpackaging <> 3 OR m.pkpackaging IS NULL)
  AND p.rtype = 1
GROUP BY CAST(p.prday AS date)
ORDER BY fecha;

/* =============================================================================
   3. EMPAQUE BC DIARIO (referencia BC; no entra en formula teorica)
   ============================================================================= */
WITH lot_empaque AS (
  SELECT
    CAST(ile.[Lot No.] AS varchar(50)) AS lot,
    MIN(CAST(ile.[Fecha empaque] AS date)) AS fecha,
    MAX(ABS(CAST(ile.[Kilos] AS float))) AS kg
  FROM bc.[Item Ledger Entry] ile
  WHERE ile.[Fecha empaque] >= @start
    AND ile.[Fecha empaque] < DATEADD(day, 1, @end)
    AND ile.[Location Code] IN ('E', 'G')
    AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
  GROUP BY CAST(ile.[Lot No.] AS varchar(50))
)
SELECT
  fecha,
  SUM(kg) AS kg,
  COUNT(*) AS lotes
FROM lot_empaque
GROUP BY fecha
ORDER BY fecha;

/* =============================================================================
   4. STOCK INICIAL ILE DIARIO (columna Stock inicial del balance)
      Regla: [Fecha empaque] < dia
             Y venta (Type 1) o ajuste negativo (Type 3) en ese dia o posteriores
      Paso 1: lotes + primera salida/ajuste
   ============================================================================= */
WITH lot_base AS (
  SELECT
    CAST(ile.[Lot No.] AS varchar(50)) AS lot,
    MIN(CAST(ile.[Fecha empaque] AS date)) AS fe_empaque,
    MAX(ABS(CAST(ile.[Kilos] AS float))) AS kg
  FROM bc.[Item Ledger Entry] ile
  WHERE ile.[Location Code] IN ('E', 'G')
    AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
    AND ile.[Fecha empaque] IS NOT NULL
  GROUP BY CAST(ile.[Lot No.] AS varchar(50))
),
lot_first_out AS (
  SELECT
    CAST(ile.[Lot No.] AS varchar(50)) AS lot,
    MIN(CAST(ile.[Posting Date] AS date)) AS first_out
  FROM bc.[Item Ledger Entry] ile
  WHERE ile.[Location Code] IN ('E', 'G')
    AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
    AND ile.[Entry Type] IN (1, 3)
  GROUP BY CAST(ile.[Lot No.] AS varchar(50))
)
SELECT
  b.lot,
  b.fe_empaque,
  b.kg,
  o.first_out
FROM lot_base b
LEFT JOIN lot_first_out o ON o.lot = b.lot
ORDER BY b.lot;
-- Paso 2 (Python/reporte): por cada dia D, SUM(kg) WHERE fe_empaque < D
--   AND (first_out IS NULL OR first_out >= D)

/* =============================================================================
   4b. STOCK APERTURA historica (opcional; omitida si timeout)
   ============================================================================= */
WITH lot_pre AS (
  SELECT
    CAST(ile.[Lot No.] AS varchar(50)) AS lot,
    MAX(ABS(CAST(ile.[Kilos] AS float))) AS kg
  FROM bc.[Item Ledger Entry] ile
  WHERE ile.[Fecha empaque] >= @ile_from
    AND ile.[Fecha empaque] < @start
    AND ile.[Location Code] IN ('E', 'G')
    AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
  GROUP BY CAST(ile.[Lot No.] AS varchar(50))
),
sold_before AS (
  SELECT DISTINCT CAST(ile.[Lot No.] AS varchar(50)) AS lot
  FROM bc.[Item Ledger Entry] ile
  WHERE ile.[Posting Date] >= @ile_from
    AND ile.[Posting Date] < @start
    AND ile.[Entry Type] = 1
    AND ile.[Location Code] IN ('E', 'G')
    AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
)
SELECT
  COALESCE(SUM(l.kg), 0) AS kg_stock_apertura,
  COUNT(*) AS lotes_stock_apertura
FROM lot_pre l
WHERE l.lot NOT IN (SELECT lot FROM sold_before);

/* =============================================================================
   5. STOCK FINAL REAL FIN DE MES (comparar con columna Stock final real)
      Lotes empaquetados en periodo sin venta en periodo
   ============================================================================= */
WITH mar_venta AS (
  SELECT DISTINCT CAST(ile.[Lot No.] AS varchar(50)) AS lot
  FROM bc.[Item Ledger Entry] ile
  WHERE ile.[Posting Date] >= @start
    AND ile.[Posting Date] < DATEADD(day, 1, @end)
    AND ile.[Entry Type] = 1
    AND ile.[Location Code] IN ('E', 'G')
    AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
),
stock_final_lot AS (
  SELECT
    CAST(ile.[Lot No.] AS varchar(50)) AS lot,
    MAX(ABS(CAST(ile.[Kilos] AS float))) AS kg
  FROM bc.[Item Ledger Entry] ile
  WHERE ile.[Fecha empaque] >= @start
    AND ile.[Fecha empaque] < DATEADD(day, 1, @end)
    AND ile.[Location Code] IN ('E', 'G')
    AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
    AND CAST(ile.[Lot No.] AS varchar(50)) NOT IN (SELECT lot FROM mar_venta)
  GROUP BY CAST(ile.[Lot No.] AS varchar(50))
)
SELECT
  COALESCE(SUM(kg), 0) AS kg_stock_final_real,
  COUNT(*) AS lotes_stock_final_real
FROM stock_final_lot;

/* =============================================================================
   6. SNAPSHOT LOTES (stock real diario se calcula en Python por dia)
      Regla: incluir lote si fe_empaque <= dia Y (sin venta O first_sale > dia)
   ============================================================================= */
WITH lot_empaque AS (
  SELECT
    CAST(ile.[Lot No.] AS varchar(50)) AS lot,
    MIN(CAST(ile.[Fecha empaque] AS date)) AS fe_empaque,
    MAX(ABS(CAST(ile.[Kilos] AS float))) AS kg,
    MAX(NULLIF(LTRIM(RTRIM(CAST(ile.[Item No.] AS varchar(50)))), '')) AS item_no,
    MAX(NULLIF(LTRIM(RTRIM(CAST(ile.[Description] AS varchar(250)))), '')) AS item_description
  FROM bc.[Item Ledger Entry] ile
  WHERE ile.[Fecha empaque] >= @start
    AND ile.[Fecha empaque] < DATEADD(day, 1, @end)
    AND ile.[Location Code] IN ('E', 'G')
    AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
  GROUP BY CAST(ile.[Lot No.] AS varchar(50))
),
lot_first_sale AS (
  SELECT
    CAST(ile.[Lot No.] AS varchar(50)) AS lot,
    MIN(CAST(ile.[Posting Date] AS date)) AS first_sale
  FROM bc.[Item Ledger Entry] ile
  WHERE ile.[Posting Date] >= @start
    AND ile.[Posting Date] < DATEADD(day, 1, @end)
    AND ile.[Entry Type] = 1
    AND ile.[Location Code] IN ('E', 'G')
    AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
  GROUP BY CAST(ile.[Lot No.] AS varchar(50))
)
SELECT
  e.lot,
  e.fe_empaque,
  e.kg,
  e.item_no,
  e.item_description,
  s.first_sale
FROM lot_empaque e
LEFT JOIN lot_first_sale s ON s.lot = e.lot
ORDER BY e.lot;

/* =============================================================================
   7. VENTAS BC TOTAL MES (control)
   ============================================================================= */
SELECT
  SUM(ABS(CAST(ile.[Kilos] AS float))) AS kg_ventas_mes,
  COUNT(DISTINCT ile.[Lot No.]) AS lotes_ventas_mes
FROM bc.[Item Ledger Entry] ile
WHERE ile.[Posting Date] >= @start
  AND ile.[Posting Date] < DATEADD(day, 1, @end)
  AND ile.[Entry Type] = 1
  AND ile.[Location Code] IN ('E', 'G');

/* =============================================================================
   8. REFERENCIA: ventas con empaque anterior (ya NO es stock inicial diario)
   ============================================================================= */
SELECT
  CAST(ile.[Posting Date] AS date) AS fecha,
  SUM(ABS(CAST(ile.[Kilos] AS float))) AS kg_ventas_stock_antiguo,
  COUNT(DISTINCT ile.[Lot No.]) AS lotes
FROM bc.[Item Ledger Entry] ile
WHERE ile.[Posting Date] >= @start
  AND ile.[Posting Date] < DATEADD(day, 1, @end)
  AND ile.[Entry Type] = 1
  AND ile.[Location Code] IN ('E', 'G')
  AND ile.[Fecha empaque] IS NOT NULL
  AND CAST(ile.[Fecha empaque] AS date) < CAST(ile.[Posting Date] AS date)
GROUP BY CAST(ile.[Posting Date] AS date)
ORDER BY fecha;

/* =============================================================================
   9. BALANCE DIARIO TEORICO (SQL para comparar con Excel)
      Stock inicial = stock final teorico dia anterior (1.er dia = apertura)
      Stock final teorico = Stock inicial + Salidas Innova - Ventas
   ============================================================================= */
WITH ventas AS (
  SELECT CAST(ile.[Posting Date] AS date) AS fecha,
         SUM(ABS(CAST(ile.[Kilos] AS float))) AS kg
  FROM bc.[Item Ledger Entry] ile
  WHERE ile.[Posting Date] >= @start AND ile.[Posting Date] < DATEADD(day, 1, @end)
    AND ile.[Entry Type] = 1 AND ile.[Location Code] IN ('E', 'G')
  GROUP BY CAST(ile.[Posting Date] AS date)
),
salidas AS (
  SELECT CAST(p.prday AS date) AS fecha,
         SUM(CAST(p.weight AS float)) AS kg
  FROM dbo.proc_packs p
  JOIN dbo.proc_materials m ON m.material = p.material
  WHERE p.prday >= @start AND p.prday < DATEADD(day, 1, @end)
    AND (m.pkpackaging <> 3 OR m.pkpackaging IS NULL) AND p.rtype = 1
  GROUP BY CAST(p.prday AS date)
),
dias AS (
  SELECT CAST(DATEADD(day, n, @start) AS date) AS fecha
  FROM (
    SELECT TOP (DATEDIFF(day, @start, @end) + 1)
      ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS n
    FROM sys.all_objects
  ) x
),
apertura AS (
  SELECT COALESCE((
    SELECT SUM(l.kg)
    FROM (
      SELECT CAST(ile.[Lot No.] AS varchar(50)) AS lot,
             MAX(ABS(CAST(ile.[Kilos] AS float))) AS kg
      FROM bc.[Item Ledger Entry] ile
      WHERE ile.[Fecha empaque] >= @ile_from
    AND ile.[Fecha empaque] < @start
        AND ile.[Location Code] IN ('E', 'G')
        AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
      GROUP BY CAST(ile.[Lot No.] AS varchar(50))
    ) l
    WHERE l.lot NOT IN (
      SELECT DISTINCT CAST(ile.[Lot No.] AS varchar(50))
      FROM bc.[Item Ledger Entry] ile
      WHERE ile.[Posting Date] >= @ile_from
    AND ile.[Posting Date] < @start
        AND ile.[Entry Type] = 1 AND ile.[Location Code] IN ('E', 'G')
    )
  ), 0) AS kg
),
diario AS (
  SELECT
    d.fecha,
    a.kg AS kg_apertura,
    COALESCE(s.kg, 0) AS kg_salidas_innova,
    COALESCE(v.kg, 0) AS kg_ventas_bc
  FROM dias d
  CROSS JOIN apertura a
  LEFT JOIN salidas s ON s.fecha = d.fecha
  LEFT JOIN ventas v ON v.fecha = d.fecha
),
teorico AS (
  SELECT
    fecha,
    kg_apertura,
    kg_salidas_innova,
    kg_ventas_bc,
    kg_apertura + SUM(kg_salidas_innova - kg_ventas_bc)
      OVER (ORDER BY fecha ROWS UNBOUNDED PRECEDING) AS kg_stock_final_teorico
  FROM diario
)
SELECT
  fecha,
  COALESCE(LAG(kg_stock_final_teorico) OVER (ORDER BY fecha), kg_apertura) AS kg_stock_inicial,
  kg_salidas_innova,
  kg_ventas_bc,
  kg_stock_final_teorico,
  kg_stock_final_teorico
    - (COALESCE(LAG(kg_stock_final_teorico) OVER (ORDER BY fecha), kg_apertura)
       + kg_salidas_innova - kg_ventas_bc) AS check_formula
FROM teorico
ORDER BY fecha;
