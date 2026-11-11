#!/usr/bin/env python3
"""
Generate synthetic demo data with the same columns as vw_billing_usage_with_attribution.
Used by the Power BI template's "Demo" mode and for screenshots.

All names are fictional (example.com users, random service-principal IDs).
Author: Balachandra Srinevasalu · MIT License

    python sample_data/generate_demo_data.py            # writes sample_data/demo_billing_usage.csv
"""
import csv
import datetime as dt
import random
from pathlib import Path

random.seed(42)
START, END = dt.date(2026, 5, 1), dt.date(2026, 11, 10)

WORKSPACES = ["analytics-prod", "data-eng-prod", "data-eng-dev", "genai-sandbox", "finance-bi-prod"]
USERS = ["asha.k@example.com", "rahul.m@example.com", "meera.s@example.com", "john.d@example.com",
         "li.wei@example.com", "fatima.z@example.com", "carlos.r@example.com", "nina.p@example.com",
         "omar.h@example.com", "sara.l@example.com"]
SPS = ["3f2b9c1e-7a44-4d1b-9e2f-0c8a5b6d7e11", "9a1c2d3e-4b5f-4a6b-8c7d-1e2f3a4b5c6d",
       "c0ffee00-1234-4abc-9def-001122334455"]
WAREHOUSES = [("bi-serving-wh", "analytics-prod"), ("adhoc-sql-wh", "data-eng-prod"),
              ("genie-wh", "genai-sandbox"), ("finance-sql-wh", "finance-bi-prod")]
# (query_source, client_application, share of warehouse cost)
SOURCES = [("Power BI", "PowerBI", 0.34), ("SQL Editor", "Databricks SQL Editor", 0.22),
           ("Dashboard", "Databricks Dashboard", 0.14), ("Genie AI", "Genie", 0.12),
           ("API", "Databricks SQL Connector for Python", 0.10), ("Notebooks", "Databricks Notebook", 0.08)]
# (compute_category, billing_origin_product, sku, resource, base daily USD)
NON_WH = [("Job Cluster", "JOBS", "PREMIUM_JOBS_COMPUTE", "nightly-ingest", 30),
          ("Job Cluster", "ALL_PURPOSE", "PREMIUM_ALL_PURPOSE_COMPUTE", "shared-interactive", 26),
          ("Serverless Notebook", "INTERACTIVE", "PREMIUM_SERVERLESS_COMPUTE", "Notebook-1172", 6),
          ("Other", "APPS", "PREMIUM_ALL_PURPOSE_SERVERLESS_COMPUTE", "No Compute Resource", 12),
          ("Other", "VECTOR_SEARCH", "PREMIUM_VECTOR_SEARCH_ENDPOINT", "No Compute Resource", 7),
          ("Other", "PREDICTIVE_OPTIMIZATION", "PREMIUM_JOBS_SERVERLESS_COMPUTE", "No Compute Resource", 2.5),
          ("Job", "JOBS", "PREMIUM_JOBS_SERVERLESS_COMPUTE", "Job-88213", 14)]

COLS = ["usage_date", "workspace_name", "compute_category", "warehouse_name", "compute_resource_name",
        "billing_origin_product", "sku_name", "attributed_to", "identity_type", "query_source",
        "client_application", "statement_type", "is_serverless", "attribution_method",
        "attribution_confidence", "allocated_dbus", "list_price", "query_count",
        "total_duration_ms", "total_usage_price_usd"]


def idt(who):
    return "USER" if "@" in who else "SERVICE_PRINCIPAL"


rows = []
span = (END - START).days
d = START
while d <= END:
    weekend = d.weekday() >= 5
    wk = 0.35 if weekend else 1.0
    growth = 1 + (d - START).days / span * 0.6
    for wh, ws in WAREHOUSES:
        price = 0.70
        for src, app, share in SOURCES:
            who = random.choice(SPS) if src in ("Power BI", "API") else random.choice(USERS)
            usd = (18 + random.random() * 30) * share * wk * growth
            q = max(1, round(usd * (8 + random.random() * 12)))
            rows.append([d.isoformat(), ws, "SQL Warehouse", wh, wh,
                         "GENIE" if wh == "genie-wh" else "SQL", "PREMIUM_SERVERLESS_SQL_COMPUTE",
                         who, idt(who), src, app, "SELECT", "true", "Allocated - Query Activity",
                         "High - Query History", round(usd / price, 4), price, q,
                         round(q * (900 + random.random() * 4000)), round(usd, 4)])
        if weekend and random.random() < 0.5:   # some idle weekend uptime
            usd = 3 + random.random() * 4
            rows.append([d.isoformat(), ws, "SQL Warehouse", wh, wh, "SQL", "PREMIUM_SERVERLESS_SQL_COMPUTE",
                         "platform.admin@example.com", "USER", "Idle / No Query", "Unknown", "Unknown", "true",
                         "Unallocated - Idle Warehouse (Owner)", "Medium - Billing Metadata",
                         round(usd / price, 4), price, 0, 0, round(usd, 4)])
    for cat, prod, sku, res, base in NON_WH:
        who = SPS[0] if prod == "JOBS" else random.choice(USERS[:4])
        price = 0.40
        usd = base * (0.6 + random.random() * 0.8) * (wk if prod == "ALL_PURPOSE" else 1) * growth
        rows.append([d.isoformat(), random.choice(WORKSPACES[1:4]), cat, "N/A", res, prod, sku, who, idt(who),
                     "Unknown", "Unknown", "Unknown", "false" if cat == "Job Cluster" else "true",
                     "Direct - Billing Metadata", "High - Billing Metadata" if cat != "Other" else "Medium - Billing Metadata",
                     round(usd / price, 4), price, 0, 0, round(usd, 4)])
    d += dt.timedelta(days=1)

out = Path(__file__).with_name("demo_billing_usage.csv")
with out.open("w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(COLS)
    w.writerows(rows)
print(f"wrote {len(rows):,} rows to {out}")
