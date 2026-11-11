# How the cost attribution view works

`sql/01_vw_billing_usage_with_attribution.sql` builds one dataset that answers **who spent what, where, and through which tool** on Databricks. It uses only Databricks system tables, so it works on any Unity Catalog account (Azure, AWS or GCP) with no organisation-specific configuration.

## The problem

`system.billing.usage` tells you that a SQL Warehouse cost $X on a day. It does not tell you *who* drove that cost, because a warehouse is shared by analysts, BI tools, Genie and APIs. Jobs and clusters are easier: their billing rows already carry `run_as` / `owned_by`.

## The approach

```
system.billing.usage ─┬─ warehouse rows ──► daily cost per warehouse ─┐
                      │                                                ├─► split by query share ─┐
system.query.history ─┴─ queries per user per warehouse per day ──────┘                          │
                                                                                                  ├─► UNION ─► final view
system.billing.usage ─── non-warehouse rows (jobs, clusters, notebooks, apps ...) ─► direct owner ┘
system.billing.list_prices ─ USD price valid on usage_date (both branches)
system.compute.warehouses / clusters, system.access.workspaces_latest ─ friendly names
```

### Step by step (CTE names match the SQL)

| # | CTE | What it does |
|---|---|---|
| 1 | `cte_calendar` | Rolling window: 1st of the month six months ago → today. Change `-6` to widen or narrow it. |
| 2 | `cte_warehouses` | Latest name, type and size per warehouse (the table is a change log, so `ROW_NUMBER` keeps the newest row). |
| 3 | `cte_clusters` | Latest name and owner per cluster, using the same pattern. |
| 4 | `cte_warehouse_usage` | Warehouse DBUs × the list price valid on that date = daily USD cost per warehouse usage line. |
| 5 | `cte_query_activity` | Query count and duration per user × warehouse × day × client app × statement type, plus a 500-character SQL preview. |
| 6a | `cte_warehouse_allocation_base` | Attaches query activity to each usage line and totals the queries behind that line. |
| 6b | `cte_warehouse_allocation` | `user cost = user queries ÷ total queries × line cost`. Idle cost (no queries) is kept whole. |
| 7 | `cte_non_warehouse_usage` | Jobs, clusters, serverless notebooks, apps, vector search, model serving and so on, attributed directly to `run_as` → `owned_by` → `created_by`. |
| 8 | `cte_combined` | Unions both halves into one schema and classifies `query_source` from `client_application` (Power BI, SQL Editor, Genie, API, Tableau ...). |
| 9 | final `SELECT` | Readable defaults instead of NULLs, `identity_type` (USER / SERVICE_PRINCIPAL / SYSTEM) and `attribution_confidence`. |

### Allocation example

A warehouse costs **$100** on a day. Three identities ran queries:

| Identity | Queries | Share | Allocated cost |
|---|---:|---:|---:|
| analyst@company.com | 30 | 30% | $30 |
| BI service principal | 60 | 60% | $60 |
| genie user | 10 | 10% | $10 |

Query count is used rather than duration because it is robust and easy to explain. If you prefer to weight by compute time, swap `query_count` for `total_duration_ms` in step 6.

## Key output columns

| Column | Meaning |
|---|---|
| `usage_date`, `workspace_name` | When and where |
| `compute_category` | SQL Warehouse / Job Cluster / Serverless Notebook / Job / Other |
| `compute_resource_name` | Warehouse, cluster, notebook or job name/id |
| `billing_origin_product`, `sku_name` | What Databricks billed (SQL, JOBS, ALL_PURPOSE, GENIE, APPS ...) |
| `attributed_to`, `identity_type` | Who the cost belongs to |
| `query_source`, `client_application` | Which tool drove warehouse cost |
| `query_count`, `total_duration_ms` | Activity behind the cost |
| `allocated_dbus`, `list_price`, `total_usage_price_usd` | DBUs and USD list-price cost (always SUM these) |
| `attribution_method`, `attribution_confidence` | How each row was attributed and how far to trust it |
| `statement_text_preview` | First 500 characters of the SQL, for root-cause drill-down |

## What changed in this version (and why)

| Change | Why |
|---|---|
| Allocation window partitioned at the usage-line grain | When a warehouse had more than one billing line in a day (e.g. two SKUs), query totals were counted once per line, which **under-stated** warehouse cost. In a test with two lines, the old logic reported half the true cost. |
| Idle warehouse cost kept and assigned to the warehouse owner | Previously, days when a warehouse ran but had no recorded queries (warm-up, auto-stop delay) were filtered out, so the view **did not reconcile** to billing. They now appear as `Unallocated - Idle Warehouse (Owner)` with `query_source = 'Idle / No Query'`. |
| `TO_DATE(x, 'YYYY-MM-DD')` → `CAST(x AS DATE)` | Same result. `YYYY`/`DD` mean week-year/day-of-year in Spark patterns, so the cast is clearer and safer. |
| Clusters ordered by `change_time` | Picks up the current name of renamed clusters. |
| `lp.currency_code = 'USD'` on the price join | Guards against duplicate price rows if other currencies are ever published. |
| `GROUP BY ALL` | Removes fragile positional `GROUP BY 1..14` (needs DBR 12.2+ or Databricks SQL). |

Run `sql/04_reconciliation_checks.sql` after deploying. The `diff_usd` column should be ≈ 0 for every month.

## Known limitations / ideas

- **List price, not invoice.** See [CURRENCY_CONVERSION.md](CURRENCY_CONVERSION.md#why-your-number-will-not-exactly-match-the-cloud-invoice).
- **`Job Cluster` includes all-purpose clusters.** Any billing row with a `cluster_id` is labelled `Job Cluster`. Split on `billing_origin_product` (`JOBS` vs `ALL_PURPOSE`) if you need to separate them.
- **Deleted warehouses and clusters** show as `N/A` for name in historic rows (live objects only). Remove `AND delete_time IS NULL` to keep their last known name.
- **Query history retention.** `system.query.history` covers SQL Warehouses and serverless compute. Warehouse days without query history fall back to the owner.
- **Tags.** Add `u.custom_tags` to both usage CTEs to enable cost-centre chargeback by tag.
