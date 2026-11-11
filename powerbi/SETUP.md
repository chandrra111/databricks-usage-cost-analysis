# Power BI: build once, share as a template (.pbit)

A `.pbit` template opens with a prompt for **Server hostname, HTTP path, Catalog, Schema and Currency**, so anyone can point the same report at their own Databricks workspace. The queries and measures are in this folder as plain text so they can be reviewed and version-controlled.

## 1. Prerequisites in Databricks

1. An account admin enables the system schemas: `billing`, `compute`, `query` and `access`.
2. Run `sql/01_vw_billing_usage_with_attribution.sql` (replace `<catalog>.<schema>`).
3. Grant the report identity `USE CATALOG`, `USE SCHEMA` and `SELECT` on the view. Because a view runs with its owner's rights, readers do not need direct access to `system.*`.
4. Note the warehouse's **Server hostname** and **HTTP path** (SQL Warehouse → Connection details).

## 2. Build the model in Power BI Desktop

1. **Transform data → Manage Parameters → New**: create the five text parameters in `power_query/00_parameters.pq` (`ServerHostname`, `HttpPath`, `Catalog`, `Schema`, `LocalCurrency`). Tick **Required**.
2. **New Source → Blank Query → Advanced Editor**: paste `01_billing_usage.pq` and name the query `billing_usage`.
3. Do the same with `02_fx_rates_monthly.pq` and name it `fx_rates_monthly`.
   - Credentials: Databricks → *Personal Access Token* or *Azure AD / OAuth*. `api.frankfurter.dev` → *Anonymous*, privacy level *Public*.
   - Already converting in Databricks with `sql/03`? Point `billing_usage` at `vw_billing_usage_local_currency` instead and skip the FX query.
4. **Model view**: relate `billing_usage[calendar_month]` (many) → `fx_rates_monthly[calendar_month]` (one), single direction.
5. Create the measures from `measures.dax`.

## 3. Suggested pages (mirrors the reference report)

| Page | Visuals |
|---|---|
| **Cover / About** | Title, what the report shows, FX rule, disclaimer, refresh schedule, `[Last Refreshed]` |
| **Cost Summary** | KPI cards (`Total Cost Local`, `Exchange Rate`, `Total Query Count`, `Allocated DBUs`). Bars: cost by `billing_origin_product`, `compute_category`, `identity_type`, workspace × warehouse, month. |
| **Daily Trends** | Columns by `usage_date` for cost, queries and duration (min) with conditional colour |
| **Cost Breakdown (RCA)** | Decomposition tree: `Total Cost Local` → workspace → warehouse → compute_category → identity_type → attributed_to → query_source |
| **Cost by User** | Search slicer on `attributed_to`, bar chart of cost and query count by user |
| **Details** | Matrix: `attributed_to` × `calendar_month` with heat-map formatting. Table drill-through with `statement_text_preview`. |

Slicers on every page: `calendar_month`, `workspace_name`, `warehouse_name`, `compute_category`, `billing_origin_product`, `query_source`.

## 4. Save as a template

1. Remove anything organisation-specific: logos, workspace names in titles, and screenshots.
2. **File → Export → Power BI template**. Add a description such as *"Enter your Databricks SQL Warehouse hostname and HTTP path"*.
3. The `.pbit` keeps the layout, queries, parameters and measures, but **no data and no credentials**. Commit it to `powerbi/` in this repo.

A new user opens the `.pbit`, enters their hostname and HTTP path, signs in, and the report refreshes against their workspace.

## 5. Publish and schedule

Publish to the Power BI Service and set credentials on the semantic model. For a SQL Warehouse that auto-stops, schedule the refresh at a quiet hour (for example, daily 10:00 local time). Scheduling `notebooks/load_fx_rates.py` an hour earlier keeps FX current if you convert in Databricks.
