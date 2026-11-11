# Converting Databricks cost from USD to your local currency (e.g. INR)

Databricks publishes list prices in **USD** (`system.billing.list_prices`). Finance teams usually report in their own currency. This repo uses one simple, auditable rule everywhere: in the SQL views, in Power BI and in the HTML dashboard.

## The rule

> **One exchange rate per calendar month = the last reference rate published on or before the month end.**
> For the current (still open) month, that is the latest rate available on the day you refresh.

```
local_cost(month) = Σ usd_cost(day in month) × rate(month)
rate(month)       = rate on MAX(rate_date) where rate_date ≤ last day of month
```

Worked example (illustrative numbers):

| Month | USD list-price cost | Last ECB rate on/before month end | Rate date | Local cost |
|---|---:|---:|---|---:|
| 2026-08 | 4,000.00 | 95.17 | 2026-08-31 (Mon) | ₹ 380,680 |
| 2026-09 | 5,000.00 | 95.83 | 2026-09-30 (Wed) | ₹ 479,150 |
| 2026-10 | 2,500.00 | 95.06 | 2026-10-30 (Fri) | ₹ 237,650 |

Notice 2026-10: the 31st was a Saturday, so there was no fixing. The rule automatically falls back to Friday's rate. For an open month (say 2026-11, refreshed on the 11th), the latest published rate is used and the figure firms up once the month closes.

When several months are selected, each month is converted at **its own** rate and then summed. The "exchange rate" KPI shown for a multi-month selection is the cost-weighted average (`local total ÷ USD total`).

## Where the rates come from

[Frankfurter](https://frankfurter.dev) is a free, open-source API serving the **European Central Bank (ECB) reference rates**. It needs no API key and covers about 30 currencies, including INR, EUR, GBP, AUD, CAD, SGD, JPY, CHF, ZAR, BRL and MXN.

```
GET https://api.frankfurter.dev/v1/2026-01-01..?base=USD&symbols=INR
{
  "base": "USD",
  "rates": {
    "2026-01-02": { "INR": 89.12 },
    "2026-01-05": { "INR": 89.30 },
    ...
  }
}
```

(The older host `api.frankfurter.app` still works. v1 is stable; a v2 API also exists.)

## Three ways it is implemented

| Where | File | How |
|---|---|---|
| **Databricks (SQL)** | `notebooks/load_fx_rates.py` → `sql/02_fx_rates_tables.sql` → `sql/03_vw_billing_usage_local_currency.sql` | A daily notebook MERGEs rates into a Delta table. A view picks the month-end rate, and a second view adds `exchange_rate` and `total_usage_price_local`. Good when many tools consume the same numbers. |
| **Power BI** | `powerbi/power_query/02_fx_rates_monthly.pq` + `powerbi/measures.dax` | Power Query calls Frankfurter directly, groups to month-end rates, and relates to the fact table on `calendar_month`. The DAX `SUMX` converts month by month. |
| **HTML dashboard** | `html/databricks-cost-dashboard.html` | The browser fetches rates for the months in the data, applies the same rule, and lets you switch currency or type a manual rate (useful when your finance team mandates a fixed budget rate). |

### Power BI Service refresh tip

Use `Web.Contents("https://api.frankfurter.dev", [RelativePath = ..., Query = ...])` exactly as in the `.pq` file. A static base URL is what lets the Service refresh a web source on a schedule. Set the source's privacy level to **Public** and authentication to **Anonymous**.

## Why your number will not exactly match the cloud invoice

These are expected differences, so state them on the report (the templates include a disclaimer):

1. **List vs. contract price.** System tables give list prices. Commit discounts (DBCU), enterprise agreements and promotions are applied on the invoice.
2. **DBUs only.** For classic (non-serverless) compute, the VMs, disks and network are billed by Azure/AWS/GCP separately and do not appear in `system.billing.usage`.
3. **FX source and timing.** Your cloud provider or bank may use a different rate source, date or markup than the ECB month-end reference rate.
4. **Rounding and refresh cadence.** Billing data can arrive up to a few hours late, and invoices round differently.

Use the converted figure as a consistent management view for trends, attribution and chargeback. For the exact figure, reconcile against the invoice.

## Using your finance team's rate instead

- **SQL:** insert your rates into `fx_rates_daily` (one row per month end is enough) instead of running the notebook.
- **Power BI:** replace `02_fx_rates_monthly.pq` with a table holding `calendar_month` and `exchange_rate`.
- **HTML:** type the rate into **Manual rate**.
