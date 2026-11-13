/* =====================================================================================
   Databricks Usage & Cost Attribution View
   -------------------------------------------------------------------------------------
   File    : sql/01_vw_billing_usage_with_attribution.sql
   Author  : Balachandra Srinevasalu (https://github.com/chandrra111)
   License : MIT (see LICENSE)

   PURPOSE
   -------
   Turns Databricks *system tables* into one tidy, report-ready dataset that answers:
     - How much did we spend (DBUs and list-price USD) per day / workspace / SKU / product?
     - WHO drove that spend? (user, service principal, or system identity)
     - WHERE did it come from? (SQL Warehouse, job cluster, serverless notebook, job ...)
     - Through WHICH tool? (SQL Editor, Power BI, Tableau, Genie, APIs, Notebooks ...)

   The hard part this view solves is SQL Warehouse attribution. A warehouse is shared:
   billing only tells you "warehouse X cost $Y today", not who ran the queries. This view
   splits each warehouse's daily cost across the people who used it, in proportion to the
   number of queries each person ran that day, or optionally their total query duration
   (from system.query.history; see cte_config).
   Everything else (jobs, clusters, serverless notebooks ...) is attributed directly from
   the identity metadata already present in system.billing.usage.

   HOW TO USE
   ----------
   1. Replace <catalog>.<schema> below with a catalog/schema you can create views in.
   2. Run this script on a SQL Warehouse (or a notebook with Unity Catalog).
   3. Point Power BI / the HTML dashboard / any BI tool at the view.
   No organisation-specific values are hard-coded; it works on any Unity Catalog-enabled
   Databricks account (Azure, AWS or GCP).

   PREREQUISITES
   -------------
   - Unity Catalog enabled, and system schemas enabled by an account admin:
       system.billing   (usage, list_prices)
       system.compute   (warehouses, clusters)
       system.query     (history)
       system.access    (workspaces_latest)
   - The user/SP that creates and reads the view needs USE + SELECT on those schemas.

   IMPORTANT NOTES ON COST
   -----------------------
   - Cost = usage_quantity (DBUs) x LIST price from system.billing.list_prices (USD).
     It does NOT include negotiated discounts / commit pricing, and on classic
     (non-serverless) compute it does NOT include the cloud VM / storage / network bill
     your cloud provider charges separately. Treat it as a consistent *relative* measure
     and an estimate of the Databricks (DBU) portion of the bill.
   - Local currency conversion (e.g. USD -> INR) is done downstream; see
     sql/03_vw_billing_usage_local_currency.sql and docs/CURRENCY_CONVERSION.md.

   OUTPUT GRAIN
   ------------
   SQL Warehouse rows : one row per day x warehouse x usage line x user x client app x
                        statement type x statement preview.
   Other compute rows : one row per day x workspace x identity x cluster/notebook/job x SKU.
   Always aggregate (SUM) the measures; never assume one row per day.
   ===================================================================================== */

CREATE OR REPLACE VIEW <catalog>.<schema>.vw_billing_usage_with_attribution AS
-- @@VIEW_BODY_START@@  (marker used by python/run_cost_report.py --inline; keep this line)
WITH

/* -------------------------------------------------------------------------------------
   0) Configuration: how shared SQL Warehouse cost is split between users
   'QUERY_COUNT' (default) : by the number of queries each user ran. Simple to explain.
   'DURATION'              : by total query task duration (total_task_duration_ms).
                             Fairer for mixed workloads, where one heavy query uses far
                             more compute than many quick ones. Databricks does not
                             publish DBUs per query, so duration is the closest proxy.
   Change the literal below and re-create the view to switch.
   ------------------------------------------------------------------------------------- */
cte_config AS (
  SELECT 'QUERY_COUNT' AS allocation_basis      -- <== 'QUERY_COUNT' or 'DURATION'
),

/* -------------------------------------------------------------------------------------
   1) Reporting window
   Rolling window: from the 1st day of the month N months ago up to today.
   Change the "-6" to widen/narrow the window. Wider windows = more data to scan.
   ------------------------------------------------------------------------------------- */
cte_calendar AS (
  SELECT
    DATE_TRUNC('month', DATEADD(month, -6, CURRENT_DATE)) AS start_date,
    CURRENT_DATE                                          AS end_date
),

/* -------------------------------------------------------------------------------------
   2) Latest definition of every live SQL Warehouse (name, type, size)
   system.compute.warehouses is a change log (one row per config change), so keep only
   the most recent row per warehouse_id and drop warehouses that have been deleted.
   Note: deleted warehouses therefore show as 'N/A' for name in historic rows.
   ------------------------------------------------------------------------------------- */
cte_warehouses AS (
  SELECT
    warehouse_id,
    warehouse_name,
    warehouse_type,
    warehouse_size
  FROM
    system.compute.warehouses
  QUALIFY
    ROW_NUMBER() OVER (PARTITION BY warehouse_id ORDER BY change_time DESC) = 1
    AND delete_time IS NULL
),

/* -------------------------------------------------------------------------------------
   3) Latest definition of every live cluster (name + owner)
   Same change-log pattern as warehouses. Ordered by change_time so a renamed cluster
   picks up its current name.
   ------------------------------------------------------------------------------------- */
cte_clusters AS (
  SELECT
    cluster_id,
    cluster_name,
    owned_by AS cluster_owner
  FROM
    system.compute.clusters
  QUALIFY
    ROW_NUMBER() OVER (PARTITION BY cluster_id ORDER BY change_time DESC) = 1
    AND delete_time IS NULL
),

/* -------------------------------------------------------------------------------------
   4) SQL Warehouse usage and list-price cost per day
   - Only billing rows that carry a warehouse_id.
   - Joined to list_prices on SKU and the price validity window that covers usage_date,
     so historic price changes are respected.
   - warehouse_owner = first non-null of run_as / owned_by / created_by. Used as the
     fallback owner when a warehouse burned cost but ran no attributable queries.
   ------------------------------------------------------------------------------------- */
cte_warehouse_usage AS (
  SELECT
    u.account_id,
    CAST(u.usage_date AS DATE)                        AS usage_date,
    u.workspace_id,
    u.identity_metadata.run_as                        AS run_as,
    u.identity_metadata.created_by                    AS created_by,
    u.identity_metadata.owned_by                      AS owned_by,
    COALESCE(
      u.identity_metadata.run_as,
      u.identity_metadata.owned_by,
      u.identity_metadata.created_by
    )                                                 AS warehouse_owner,
    u.usage_metadata.warehouse_id                     AS warehouse_id,
    u.sku_name,
    u.billing_origin_product,
    u.product_features.is_serverless                  AS is_serverless,
    SUM(u.usage_quantity)                             AS usage_quantity,   -- DBUs
    SUM(u.usage_quantity * COALESCE(lp.pricing.default, 0)) AS total_cost_usd,
    AVG(COALESCE(lp.pricing.default, 0))              AS avg_list_price
  FROM
    system.billing.usage u
      LEFT JOIN system.billing.list_prices lp
        ON  u.sku_name   = lp.sku_name
        AND lp.currency_code = 'USD'                  -- list prices are published in USD
        AND u.usage_date >= lp.price_start_time
        AND (u.usage_date < lp.price_end_time OR lp.price_end_time IS NULL)
      JOIN cte_calendar p
        ON CAST(u.usage_date AS DATE) BETWEEN p.start_date AND p.end_date
  WHERE
    u.usage_metadata.warehouse_id IS NOT NULL
  GROUP BY ALL
),

/* -------------------------------------------------------------------------------------
   5) Query activity per user per warehouse per day (system.query.history)
   This is the "who used the warehouse" signal. We keep client_application and
   statement_type so the report can show which TOOL drove the cost, plus a 500-char
   preview of the SQL for drill-down / root-cause analysis.
   ------------------------------------------------------------------------------------- */
cte_query_activity AS (
  SELECT
    CAST(q.start_time AS DATE)        AS query_date,
    q.workspace_id,
    q.compute.warehouse_id            AS warehouse_id,
    q.executed_by,
    q.compute.type                    AS compute_type,
    q.client_application,
    q.statement_type,
    LEFT(q.statement_text, 500)       AS statement_text_preview,
    COUNT(*)                          AS query_count,
    SUM(q.total_task_duration_ms)     AS total_duration_ms
  FROM
    system.query.history q
      JOIN cte_calendar p
        ON CAST(q.start_time AS DATE) BETWEEN p.start_date AND p.end_date
  WHERE
    q.compute.warehouse_id IS NOT NULL
    AND q.executed_by IS NOT NULL
  GROUP BY ALL
),

/* -------------------------------------------------------------------------------------
   6a) Attach query activity to each warehouse usage line and total the activity behind
       that SAME usage line.
   allocation_weight = the user's share driver, chosen by cte_config.allocation_basis:
       query count, or total query duration in ms.
   The windows are partitioned at the exact grain of cte_warehouse_usage (one usage
   line), so if a warehouse has more than one billing line on a day (e.g. two SKUs) each
   line's cost is split independently and nothing is double-counted.
   ------------------------------------------------------------------------------------- */
cte_warehouse_allocation_base AS (
  SELECT
    w.*,
    q.executed_by,
    q.query_count,
    q.total_duration_ms,
    q.compute_type,
    q.client_application,
    q.statement_type,
    q.statement_text_preview,
    cfg.allocation_basis,
    CASE
      WHEN cfg.allocation_basis = 'DURATION' THEN COALESCE(q.total_duration_ms, 0)
      ELSE q.query_count
    END AS allocation_weight,
    SUM(
      CASE
        WHEN cfg.allocation_basis = 'DURATION' THEN COALESCE(q.total_duration_ms, 0)
        ELSE q.query_count
      END
    ) OVER (
      PARTITION BY w.account_id, w.usage_date, w.workspace_id, w.warehouse_id,
                   w.sku_name, w.billing_origin_product, w.is_serverless,
                   w.run_as, w.created_by, w.owned_by
    ) AS total_allocation_weight,
    SUM(q.query_count) OVER (
      PARTITION BY w.account_id, w.usage_date, w.workspace_id, w.warehouse_id,
                   w.sku_name, w.billing_origin_product, w.is_serverless,
                   w.run_as, w.created_by, w.owned_by
    ) AS total_warehouse_queries
  FROM
    cte_warehouse_usage w
      CROSS JOIN cte_config cfg
      LEFT JOIN cte_query_activity q
        ON  w.usage_date   = q.query_date
        AND w.workspace_id = q.workspace_id
        AND w.warehouse_id = q.warehouse_id
),

/* -------------------------------------------------------------------------------------
   6b) Proportional allocation
   allocated_cost = (user's weight / total weight on that usage line) x line cost
   Example (QUERY_COUNT): warehouse cost $100; Asha ran 30 queries, a BI service
   principal ran 70 -> Asha $30, service principal $70.
   Example (DURATION): same day, but Asha's 30 queries took 2 minutes in total and the
   service principal's 70 took 18 minutes -> Asha $10, service principal $90.
   Fallbacks keep the view reconciled to billing:
   - DURATION chosen but no duration recorded on the line -> split by query count.
   - No query recorded at all (idle uptime / warm-up / auto-stop delay) -> the full
     cost is kept and later attributed to the warehouse owner.
   ------------------------------------------------------------------------------------- */
cte_warehouse_allocation AS (
  SELECT
    b.*,
    CASE
      WHEN b.total_allocation_weight > 0
        THEN (b.allocation_weight * 1.0 / b.total_allocation_weight) * b.total_cost_usd
      WHEN b.total_warehouse_queries > 0
        THEN (b.query_count * 1.0 / b.total_warehouse_queries) * b.total_cost_usd
      ELSE b.total_cost_usd                           -- idle cost, not lost
    END AS allocated_cost_usd,
    CASE
      WHEN b.total_allocation_weight > 0
        THEN (b.allocation_weight * 1.0 / b.total_allocation_weight) * b.usage_quantity
      WHEN b.total_warehouse_queries > 0
        THEN (b.query_count * 1.0 / b.total_warehouse_queries) * b.usage_quantity
      ELSE b.usage_quantity
    END AS allocated_dbus
  FROM
    cte_warehouse_allocation_base b
),

/* -------------------------------------------------------------------------------------
   7) Everything that is NOT a SQL Warehouse: jobs, clusters, serverless notebooks,
      apps, vector search, model serving, predictive optimisation, networking ...
   These billing rows already carry identity metadata, so attribution is direct.
   compute_category note: any row with a cluster_id is labelled 'Job Cluster'. That
   includes all-purpose (interactive) clusters; use billing_origin_product
   (ALL_PURPOSE vs JOBS) in the report if you need to separate them.
   ------------------------------------------------------------------------------------- */
cte_non_warehouse_usage AS (
  SELECT
    u.account_id,
    CAST(u.usage_date AS DATE)                AS usage_date,
    u.workspace_id,
    u.identity_metadata.run_as                AS run_as,
    u.identity_metadata.created_by            AS created_by,
    u.identity_metadata.owned_by              AS owned_by,
    COALESCE(
      u.identity_metadata.run_as,
      u.identity_metadata.owned_by,
      u.identity_metadata.created_by
    )                                         AS user_email,
    u.usage_metadata.cluster_id               AS cluster_id,
    u.usage_metadata.notebook_id              AS notebook_id,
    u.usage_metadata.job_id                   AS job_id,
    u.sku_name,
    u.billing_origin_product,
    u.product_features.is_serverless          AS is_serverless,
    CASE
      WHEN u.usage_metadata.cluster_id IS NOT NULL THEN 'Job Cluster'
      WHEN u.usage_metadata.notebook_id IS NOT NULL
           AND u.product_features.is_serverless = true THEN 'Serverless Notebook'
      WHEN u.usage_metadata.job_id IS NOT NULL THEN 'Job'
      ELSE 'Other'
    END                                       AS compute_category,
    SUM(u.usage_quantity)                     AS usage_quantity,
    SUM(u.usage_quantity * COALESCE(lp.pricing.default, 0)) AS total_cost_usd,
    AVG(COALESCE(lp.pricing.default, 0))      AS avg_list_price
  FROM
    system.billing.usage u
      LEFT JOIN system.billing.list_prices lp
        ON  u.sku_name   = lp.sku_name
        AND lp.currency_code = 'USD'
        AND u.usage_date >= lp.price_start_time
        AND (u.usage_date < lp.price_end_time OR lp.price_end_time IS NULL)
      JOIN cte_calendar p
        ON CAST(u.usage_date AS DATE) BETWEEN p.start_date AND p.end_date
  WHERE
    u.usage_metadata.warehouse_id IS NULL
  GROUP BY ALL
),

/* -------------------------------------------------------------------------------------
   8) Combine both halves into one schema
   ------------------------------------------------------------------------------------- */
cte_combined AS (

  /* 8a) SQL Warehouse cost, allocated to the people/SPs who ran queries */
  SELECT
    a.account_id,
    a.usage_date,
    a.workspace_id,
    ws.workspace_name,
    'SQL Warehouse'                         AS compute_category,
    a.warehouse_id,
    wh.warehouse_name,
    NULL                                    AS cluster_id,
    NULL                                    AS cluster_name,
    NULL                                    AS cluster_owner,
    NULL                                    AS notebook_id,
    NULL                                    AS job_id,
    a.run_as,
    a.created_by,
    a.owned_by,
    a.warehouse_owner,
    -- query executor; falls back to the warehouse owner for idle (no-query) cost
    COALESCE(a.executed_by, a.warehouse_owner) AS user_email,
    a.sku_name,
    a.billing_origin_product,
    a.is_serverless,
    a.query_count,
    a.total_duration_ms,
    a.compute_type,
    a.client_application,
    a.statement_type,
    a.statement_text_preview,
    a.allocated_dbus                        AS usage_quantity,
    a.allocated_cost_usd                    AS total_cost_usd,
    a.avg_list_price                        AS list_price,

    -- Friendly "where did the query come from" bucket, derived from client_application.
    -- Order matters: the first matching rule wins. Extend this list for your own tools.
    CASE
      WHEN a.executed_by IS NULL THEN 'Idle / No Query'
      WHEN a.client_application IN ('node', 'undici', 'axios', 'PostmanRuntime') THEN 'API'
      WHEN a.client_application LIKE '%Connector%' THEN 'API'
      WHEN a.client_application LIKE '%API%' OR a.client_application LIKE '%SDK%' THEN 'API'
      WHEN a.client_application = 'Databricks SQL Editor' THEN 'SQL Editor'
      WHEN a.client_application LIKE '%Dashboard%' THEN 'Dashboard'
      WHEN a.client_application LIKE '%PowerBI%' OR a.client_application = 'PowerBI' THEN 'Power BI'
      WHEN a.client_application LIKE '%Tableau%' THEN 'Tableau'
      WHEN a.client_application LIKE '%Notebook%' THEN 'Notebooks'
      WHEN a.client_application LIKE '%Jobs%' THEN 'Jobs'
      WHEN a.client_application LIKE '%DataCloud%' THEN 'Data Federation'
      WHEN a.client_application LIKE '%Genie%' THEN 'Genie AI'
      WHEN a.client_application LIKE '%Catalog%' THEN 'Catalog Explorer'
      ELSE COALESCE(a.client_application, 'Unknown')
    END                                     AS query_source,

    CASE
      WHEN a.executed_by IS NULL THEN 'Unallocated - Idle Warehouse (Owner)'
      ELSE 'Allocated - Query Activity'
    END                                     AS attribution_method
  FROM
    cte_warehouse_allocation a
      LEFT JOIN system.access.workspaces_latest ws
        ON a.workspace_id = ws.workspace_id
      LEFT JOIN cte_warehouses wh
        ON a.warehouse_id = wh.warehouse_id

  UNION ALL

  /* 8b) Non-warehouse cost, attributed directly from billing identity metadata */
  SELECT
    n.account_id,
    n.usage_date,
    n.workspace_id,
    ws.workspace_name,
    n.compute_category,
    NULL                                    AS warehouse_id,
    NULL                                    AS warehouse_name,
    n.cluster_id,
    c.cluster_name,
    c.cluster_owner,
    n.notebook_id,
    n.job_id,
    n.run_as,
    n.created_by,
    n.owned_by,
    n.user_email                            AS warehouse_owner,
    n.user_email,
    n.sku_name,
    n.billing_origin_product,
    n.is_serverless,
    0                                       AS query_count,
    0                                       AS total_duration_ms,
    'Unknown'                               AS compute_type,
    'Unknown'                               AS client_application,
    'Unknown'                               AS statement_type,
    'N/A'                                   AS statement_text_preview,
    n.usage_quantity,
    n.total_cost_usd,
    n.avg_list_price                        AS list_price,
    'Unknown'                               AS query_source,
    'Direct - Billing Metadata'             AS attribution_method
  FROM
    cte_non_warehouse_usage n
      LEFT JOIN system.access.workspaces_latest ws
        ON n.workspace_id = ws.workspace_id
      LEFT JOIN cte_clusters c
        ON n.cluster_id = c.cluster_id
)

/* -------------------------------------------------------------------------------------
   9) Final, BI-friendly projection
   - NULLs replaced with readable defaults so slicers don't show (Blank).
   - identity_type classifies the attributed identity:
       UUID pattern -> SERVICE_PRINCIPAL, contains '@' -> USER, 'System-User' -> SYSTEM.
   - attribution_confidence tells report users how much to trust each row.
   ------------------------------------------------------------------------------------- */
SELECT
  account_id,
  usage_date,
  workspace_id,
  COALESCE(workspace_name, 'Unknown Workspace')  AS workspace_name,
  compute_category,
  COALESCE(warehouse_id, 'N/A')                  AS warehouse_id,
  COALESCE(warehouse_name, 'N/A')                AS warehouse_name,
  COALESCE(cluster_id, 'N/A')                    AS cluster_id,
  COALESCE(cluster_name, 'N/A')                  AS cluster_name,
  COALESCE(cluster_owner, 'Unknown')             AS cluster_owner,
  COALESCE(notebook_id, 'N/A')                   AS notebook_id,
  COALESCE(job_id, 'N/A')                        AS job_id,
  COALESCE(run_as, 'Unknown')                    AS run_as,
  COALESCE(created_by, 'Unknown')                AS created_by,
  COALESCE(owned_by, 'Unknown')                  AS owned_by,
  COALESCE(warehouse_owner, 'Unknown')           AS warehouse_owner,
  COALESCE(user_email, 'Unknown')                AS executed_by,
  COALESCE(user_email, 'Unknown')                AS attributed_to,
  CASE
    WHEN user_email RLIKE '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
      THEN 'SERVICE_PRINCIPAL'
    WHEN user_email LIKE '%@%'        THEN 'USER'
    WHEN user_email = 'System-User'   THEN 'SYSTEM'
    ELSE 'Unknown'
  END                                            AS identity_type,
  sku_name,
  COALESCE(billing_origin_product, 'Unknown')    AS billing_origin_product,
  COALESCE(is_serverless, false)                 AS is_serverless,
  COALESCE(query_count, 0)                       AS query_count,
  COALESCE(total_duration_ms, 0)                 AS total_duration_ms,
  COALESCE(compute_type, 'Unknown')              AS compute_type,
  COALESCE(client_application, 'Unknown')        AS client_application,
  COALESCE(statement_type, 'Unknown')            AS statement_type,
  COALESCE(query_source, 'Unknown')              AS query_source,
  COALESCE(statement_text_preview, 'N/A')        AS statement_text_preview,
  attribution_method,
  CASE
    WHEN compute_category = 'SQL Warehouse' AND query_count > 0
      THEN 'High - Query History'
    WHEN compute_category IN ('Job Cluster', 'Serverless Notebook', 'Job')
         AND user_email IS NOT NULL
      THEN 'High - Billing Metadata'
    WHEN user_email IS NOT NULL THEN 'Medium - Billing Metadata'
    ELSE 'Low - No Attribution'
  END                                            AS attribution_confidence,
  COALESCE(
    warehouse_name,
    cluster_name,
    CONCAT('Notebook-', notebook_id),
    CONCAT('Job-', job_id),
    'No Compute Resource'
  )                                              AS compute_resource_name,
  usage_quantity                                 AS allocated_dbus,
  COALESCE(list_price, 0)                        AS list_price,
  total_cost_usd                                 AS total_usage_price_usd
FROM
  cte_combined;
