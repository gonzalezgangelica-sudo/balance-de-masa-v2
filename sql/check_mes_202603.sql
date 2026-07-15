/*
  CHECK MENSUAL BC E/G — Marzo 2026
  Solo lotes con empaque o movimiento en el mes.
  Stock inicial dia D: empaque < D y venta/ajuste neg. (Type 1/3) en D o despues.
  Historico ILE acotado desde 2026-01-01.
*/

DECLARE @start date = '2026-03-01';
DECLARE @end   date = '2026-03-31';
DECLARE @ile_from date = '2026-01-01';

-- Lotes del mes (empaque o posting en marzo)
WITH lots_mes AS (
  SELECT DISTINCT CAST(ile.[Lot No.] AS varchar(50)) AS lot
  FROM bc.[Item Ledger Entry] ile
  WHERE ile.[Location Code] IN ('E', 'G')
    AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
    AND ile.[Posting Date] >= @ile_from
    AND ile.[Fecha empaque] >= @ile_from
    AND (
      (ile.[Fecha empaque] >= @start AND ile.[Fecha empaque] < DATEADD(day, 1, @end))
      OR (ile.[Posting Date] >= @start AND ile.[Posting Date] < DATEADD(day, 1, @end))
    )
),
lot_base AS (
  SELECT
    CAST(ile.[Lot No.] AS varchar(50)) AS lot,
    MIN(CAST(ile.[Fecha empaque] AS date)) AS fe_empaque,
    MAX(ABS(CAST(ile.[Kilos] AS float))) AS kg
  FROM bc.[Item Ledger Entry] ile
  WHERE ile.[Location Code] IN ('E', 'G')
    AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
    AND ile.[Fecha empaque] >= @ile_from
    AND ile.[Fecha empaque] IS NOT NULL
    AND ile.[Fecha empaque] < DATEADD(day, 1, @end)
    AND CAST(ile.[Lot No.] AS varchar(50)) IN (SELECT lot FROM lots_mes)
  GROUP BY CAST(ile.[Lot No.] AS varchar(50))
),
lot_first_out AS (
  SELECT
    CAST(ile.[Lot No.] AS varchar(50)) AS lot,
    MIN(CAST(ile.[Posting Date] AS date)) AS first_out
  FROM bc.[Item Ledger Entry] ile
  WHERE ile.[Location Code] IN ('E', 'G')
    AND NULLIF(LTRIM(RTRIM(ile.[Lot No.])), '') IS NOT NULL
    AND ile.[Posting Date] >= @ile_from
    AND ile.[Entry Type] IN (1, 3)
    AND CAST(ile.[Lot No.] AS varchar(50)) IN (SELECT lot FROM lot_base)
  GROUP BY CAST(ile.[Lot No.] AS varchar(50))
),
dias AS (
  SELECT CAST(DATEADD(day, n, @start) AS date) AS fecha
  FROM (
    SELECT TOP (DATEDIFF(day, @start, @end) + 1)
      ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS n
    FROM sys.all_objects
  ) x
)
SELECT
  d.fecha,
  COALESCE(SUM(b.kg), 0) AS kg_stock_inicial,
  COUNT(b.lot) AS lotes_stock_inicial
FROM dias d
LEFT JOIN lot_base b ON b.fe_empaque < d.fecha
LEFT JOIN lot_first_out o ON o.lot = b.lot
WHERE b.lot IS NULL
   OR o.first_out IS NULL
   OR o.first_out >= d.fecha
GROUP BY d.fecha
ORDER BY d.fecha;
