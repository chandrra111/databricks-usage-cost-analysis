/* =====================================================================================
   Sanity / reconciliation checks
   -------------------------------------------------------------------------------------
   File    : sql/04_reconciliation_checks.sql
   Run these after creating the views. Replace <catalog>.<schema>.
   ===================================================================================== */

-- 1) Does the attribution view add up to raw billing? (difference should be ~0)
WITH raw AS (
  SELECT
    TRUNC(CAST(u.usage_date AS DATE), 'MM')                    AS month_start,
    SUM(u.usage_quantity)                                      AS dbus,
    SUM(u.usage_quantity * COALESCE(lp.pricing.default, 0))    AS cost_usd
  FROM system.billing.usage u
  LEFT JOIN system.billing.list_prices lp
    ON  u.sku_name = lp.sku_name
    AND lp.currency_code = 'USD'
    AND u.usage_date >= lp.price_start_time
    AND (u.usage_date < lp.price_end_time OR lp.price_end_time IS NULL)
  WHERE CAST(u.usage_date AS DATE) >= DATE_TRUNC('month', DATEADD(month, -6, CURRENT_DATE))
  GROUP BY 1
),
vw AS (
  SELECT
    TRUNC(usage_date, 'MM')      AS month_start,
    SUM(allocated_dbus)          AS dbus,
    SUM(total_usage_price_usd)   AS cost_usd
  FROM <catalog>.<schema>.vw_billing_usage_with_attribution
  GROUP BY 1
)
SELECT
  r.month_start,
  ROUND(r.cost_usd, 2)                  AS raw_cost_usd,
  ROUND(v.cost_usd, 2)                  AS view_cost_usd,
  ROUND(v.cost_usd - r.cost_usd, 2)     AS diff_usd,
  ROUND(v.dbus - r.dbus, 2)             AS diff_dbus
FROM raw r
LEFT JOIN vw v USING (month_start)
ORDER BY 1;

-- 2) How much cost is attributed with each confidence level?
SELECT
  attribution_confidence,
  attribution_method,
  ROUND(SUM(total_usage_price_usd), 2) AS cost_usd
FROM <catalog>.<schema>.vw_billing_usage_with_attribution
GROUP BY ALL
ORDER BY cost_usd DESC;

-- 3) SKUs with usage but no list price (cost would show as 0)
SELECT DISTINCT sku_name
FROM <catalog>.<schema>.vw_billing_usage_with_attribution
WHERE list_price = 0 AND allocated_dbus > 0;

-- 4) Months missing an FX rate (should return no rows)
SELECT DISTINCT calendar_month
FROM <catalog>.<schema>.vw_billing_usage_local_currency
WHERE exchange_rate IS NULL;
