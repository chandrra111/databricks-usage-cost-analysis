/* =====================================================================================
   FX rate storage for converting USD list-price cost into a local currency
   -------------------------------------------------------------------------------------
   File    : sql/02_fx_rates_tables.sql
   Author  : Balachandra Srinevasalu (https://github.com/chandrra111)

   WHY
   ---
   system.billing.list_prices is in USD. Finance usually wants INR / EUR / GBP / AUD ...
   Databricks SQL cannot call a web API directly, so daily rates are loaded into a small
   Delta table by notebooks/load_fx_rates.py (source: Frankfurter API, ECB reference
   rates, free, no API key). This script creates that table and a MONTHLY rate view.

   RATE RULE (same rule used in the Power BI version)
   ---------
   One rate per calendar month = the last rate published ON OR BEFORE the month end.
   For the current (open) month that is simply the latest rate available today.
   Weekends/holidays have no ECB fixing, so "last available on or before" handles them.

   Replace <catalog>.<schema> with your own location.
   ===================================================================================== */

-- 1) Daily rates (one row per date per currency pair). Loaded/merged by the notebook.
CREATE TABLE IF NOT EXISTS <catalog>.<schema>.fx_rates_daily (
  rate_date       DATE      COMMENT 'ECB publication date',
  base_currency   STRING    COMMENT 'Always USD here (list prices are USD)',
  quote_currency  STRING    COMMENT 'Target currency, e.g. INR, EUR, GBP',
  rate            DOUBLE    COMMENT '1 base_currency = rate quote_currency',
  source          STRING    COMMENT 'Where the rate came from',
  loaded_at       TIMESTAMP COMMENT 'Load timestamp'
)
COMMENT 'Daily FX reference rates used to convert Databricks list-price USD cost';

-- 2) One rate per month per currency = last available rate on/before month end.
CREATE OR REPLACE VIEW <catalog>.<schema>.vw_fx_rates_monthly AS
WITH months AS (
  -- last 24 months up to the current month (widen if your cost view looks further back)
  SELECT EXPLODE(
           SEQUENCE(ADD_MONTHS(TRUNC(CURRENT_DATE, 'MM'), -24),
                    TRUNC(CURRENT_DATE, 'MM'),
                    INTERVAL 1 MONTH)
         ) AS month_start
),
candidates AS (
  SELECT
    m.month_start,
    f.base_currency,
    f.quote_currency,
    f.rate,
    f.rate_date,
    ROW_NUMBER() OVER (
      PARTITION BY m.month_start, f.base_currency, f.quote_currency
      ORDER BY f.rate_date DESC
    ) AS rn
  FROM months m
  JOIN <catalog>.<schema>.fx_rates_daily f
    ON  f.rate_date <= LEAST(LAST_DAY(m.month_start), CURRENT_DATE)   -- on/before month end
    AND f.rate_date >= DATE_SUB(m.month_start, 15)                    -- bounded look-back
)
SELECT
  month_start,
  DATE_FORMAT(month_start, 'yyyy-MM') AS calendar_month,
  base_currency,
  quote_currency,
  rate       AS exchange_rate,
  rate_date  AS rate_effective_date
FROM candidates
WHERE rn = 1;
