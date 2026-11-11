/* =====================================================================================
   Cost view in local currency
   -------------------------------------------------------------------------------------
   File    : sql/03_vw_billing_usage_local_currency.sql
   Author  : Balachandra Srinevasalu (https://github.com/chandrra111)

   Joins the attribution view (01) to the monthly FX view (02) and adds:
     calendar_month, exchange_rate, rate_effective_date, total_usage_price_local

   Set the currency in ONE place: the quote_currency filter below (default 'INR').
   Run sql/01 and sql/02 first and load rates with notebooks/load_fx_rates.py.
   ===================================================================================== */

CREATE OR REPLACE VIEW <catalog>.<schema>.vw_billing_usage_local_currency AS
SELECT
  u.*,
  DATE_FORMAT(u.usage_date, 'yyyy-MM')               AS calendar_month,
  fx.quote_currency                                  AS local_currency,
  fx.exchange_rate,
  fx.rate_effective_date,
  u.total_usage_price_usd * fx.exchange_rate         AS total_usage_price_local
FROM <catalog>.<schema>.vw_billing_usage_with_attribution u
LEFT JOIN <catalog>.<schema>.vw_fx_rates_monthly fx
  ON  fx.month_start    = TRUNC(u.usage_date, 'MM')
  AND fx.base_currency  = 'USD'
  AND fx.quote_currency = 'INR';      -- <== change to EUR / GBP / AUD / ... as needed
