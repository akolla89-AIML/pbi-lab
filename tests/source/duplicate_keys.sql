-- Pre-refresh check: every key on the one side of a model relationship must be unique and not NULL.
-- A duplicate there makes the semantic model refresh fail, so this runs first and fails fast.
-- Returns one row per violation; zero rows means it is safe to refresh.
SELECT 'DimDate' AS table_name, 'DateKey' AS key_column,
       CAST(DateKey AS varchar(50)) AS key_value, COUNT(*) AS row_count
FROM dbo.DimDate
GROUP BY DateKey
HAVING COUNT(*) > 1 OR DateKey IS NULL
UNION ALL
SELECT 'DimGeography', 'GeographyKey', CAST(GeographyKey AS varchar(50)), COUNT(*)
FROM dbo.DimGeography
GROUP BY GeographyKey
HAVING COUNT(*) > 1 OR GeographyKey IS NULL
UNION ALL
SELECT 'DimCustomer', 'CustomerKey', CAST(CustomerKey AS varchar(50)), COUNT(*)
FROM dbo.DimCustomer
GROUP BY CustomerKey
HAVING COUNT(*) > 1 OR CustomerKey IS NULL
UNION ALL
SELECT 'DimProduct', 'ProductKey', CAST(ProductKey AS varchar(50)), COUNT(*)
FROM dbo.DimProduct
GROUP BY ProductKey
HAVING COUNT(*) > 1 OR ProductKey IS NULL