#!/usr/bin/env python3
"""
Databricks Cost Analysis - one-command extractor + HTML report generator
=========================================================================
Author : Balachandra Srinevasalu (https://github.com/chandrra111)
License: MIT

Give it your workspace host, a SQL Warehouse HTTP path and a token. It:
  1. connects to Databricks with the official databricks-sql-connector,
  2. runs the attribution logic (either from the view you created with
     sql/01_vw_billing_usage_with_attribution.sql, or inline from that file if
     you cannot create views),
  3. aggregates to one row per day x workspace x resource x identity x source,
  4. writes a CSV and a self-contained HTML dashboard with the data embedded.

Open the HTML in any browser. It converts USD to your currency (default INR) using
month-end ECB rates from api.frankfurter.dev.

Install:
    pip install databricks-sql-connector

Examples:
    # Using the view (recommended)
    export DATABRICKS_TOKEN=dapi...
    python run_cost_report.py \
        --host adb-1234567890123456.7.azuredatabricks.net \
        --http-path /sql/1.0/warehouses/abcdef1234567890 \
        --view main.finops.vw_billing_usage_with_attribution

    # No permission to create views? Run the SQL inline:
    python run_cost_report.py --host ... --http-path ... --inline

Permissions: the token's identity needs SELECT on system.billing, system.compute,
system.query and system.access (or on the view). Use a service principal for
scheduled runs. Never commit tokens; prefer the DATABRICKS_TOKEN env variable.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_SQL = REPO / "sql" / "01_vw_billing_usage_with_attribution.sql"
DEFAULT_TEMPLATE = REPO / "html" / "databricks-cost-dashboard.html"
EMBED_TOKEN = "null; /*__EMBEDDED_DATA__*/"

# Aggregation run on top of the view. Keeps the file small enough for a browser
# (statement text and per-statement grain are dropped; the view keeps them for drill-down).
AGG_SQL = """
SELECT
  CAST(usage_date AS STRING)           AS usage_date,
  workspace_name,
  compute_category,
  compute_resource_name,
  billing_origin_product,
  sku_name,
  attributed_to,
  identity_type,
  query_source,
  SUM(allocated_dbus)                  AS allocated_dbus,
  SUM(query_count)                     AS query_count,
  SUM(total_usage_price_usd)           AS total_usage_price_usd
FROM {source}
WHERE usage_date >= DATE_TRUNC('month', DATEADD(month, -{months}, CURRENT_DATE))
GROUP BY ALL
HAVING SUM(total_usage_price_usd) <> 0 OR SUM(query_count) <> 0
"""


def inline_view_body(sql_path: Path, basis: str | None = None) -> str:
    """Return the SELECT body of the view file (everything after the marker line)."""
    text = sql_path.read_text(encoding="utf-8")
    marker = "-- @@VIEW_BODY_START@@"
    if marker not in text:
        sys.exit(f"Marker '{marker}' not found in {sql_path}")
    body = text.split(marker, 1)[1].split("\n", 1)[1]
    if basis:
        literal = "'QUERY_COUNT' AS allocation_basis"
        if literal not in body:
            sys.exit("allocation_basis setting not found in the view SQL.")
        body = body.replace(literal, f"'{basis}' AS allocation_basis", 1)
    return body.strip().rstrip(";")


def to_jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


def main() -> None:
    p = argparse.ArgumentParser(description="Extract Databricks usage & cost and build an HTML dashboard.")
    p.add_argument("--host", required=True, help="Workspace host, e.g. adb-123.7.azuredatabricks.net (no https://)")
    p.add_argument("--http-path", required=True, help="SQL Warehouse HTTP path, e.g. /sql/1.0/warehouses/abc123")
    p.add_argument("--token", default=os.getenv("DATABRICKS_TOKEN"), help="PAT or OAuth token (default: $DATABRICKS_TOKEN)")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--view", help="Fully qualified view name, e.g. main.finops.vw_billing_usage_with_attribution")
    src.add_argument("--inline", action="store_true", help="Run the view SQL inline (no view needed)")
    p.add_argument("--sql-file", default=str(DEFAULT_SQL), help="View SQL file used with --inline")
    p.add_argument("--allocation-basis", choices=["QUERY_COUNT", "DURATION"], default=None,
                   help="With --inline: split shared warehouse cost by query count (default) or query duration")
    p.add_argument("--months", type=int, default=6, help="Months of history (the view itself caps at its own window)")
    p.add_argument("--currency", default="INR", help="Default display currency in the dashboard (ISO code)")
    p.add_argument("--out", default="databricks_cost_report", help="Output file prefix (writes .csv and .html)")
    p.add_argument("--template", default=str(DEFAULT_TEMPLATE), help="HTML template path")
    a = p.parse_args()

    if not a.token:
        sys.exit("No token. Pass --token or set DATABRICKS_TOKEN.")
    if a.allocation_basis and not a.inline:
        sys.exit("--allocation-basis only applies with --inline. For a view, edit cte_config in the SQL and re-create it.")
    if not a.view and not a.inline:
        sys.exit("Choose a source: --view <catalog.schema.view> or --inline")

    try:
        from databricks import sql as dbsql
    except ImportError:
        sys.exit("Missing dependency: pip install databricks-sql-connector")

    host = a.host.replace("https://", "").rstrip("/")
    source = a.view if a.view else f"(\n{inline_view_body(Path(a.sql_file), a.allocation_basis)}\n) AS v"
    query = AGG_SQL.format(source=source, months=int(a.months))

    print(f"Connecting to {host} ...")
    with dbsql.connect(server_hostname=host, http_path=a.http_path, access_token=a.token) as conn:
        with conn.cursor() as cur:
            print("Running cost attribution query (first run can take a minute) ...")
            cur.execute(query)
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, map(to_jsonable, r))) for r in cur.fetchall()]

    print(f"Fetched {len(rows):,} aggregated rows")
    total = sum(r["total_usage_price_usd"] or 0 for r in rows)
    print(f"Total list-price cost: USD {total:,.2f}")

    # 1) CSV
    csv_path = Path(f"{a.out}.csv")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    # 2) HTML with embedded data
    template = Path(a.template).read_text(encoding="utf-8")
    if EMBED_TOKEN not in template:
        sys.exit("Embed token not found in the HTML template.")
    payload = {
        "rows": rows,
        "currency": a.currency.upper(),
        "meta": {
            "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source": host,
        },
    }
    # "</" is escaped so no value can close the <script> tag
    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    html_path = Path(f"{a.out}.html")
    html_path.write_text(template.replace(EMBED_TOKEN, blob + ";", 1), encoding="utf-8")

    print(f"Wrote {csv_path} and {html_path}. Open the HTML in your browser.")


if __name__ == "__main__":
    main()
