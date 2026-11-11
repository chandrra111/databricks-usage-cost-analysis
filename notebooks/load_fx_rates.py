# Databricks notebook source
# MAGIC %md
# MAGIC # Load daily FX rates (USD → local currency) from the Frankfurter API
# MAGIC
# MAGIC **Author:** Balachandra Srinevasalu · MIT License
# MAGIC
# MAGIC Databricks list prices are in USD. This notebook pulls daily reference rates
# MAGIC (European Central Bank, published via the free [Frankfurter API](https://frankfurter.dev),
# MAGIC no API key) and MERGEs them into `<catalog>.<schema>.fx_rates_daily`, which
# MAGIC `sql/02_fx_rates_tables.sql` turns into one rate per month
# MAGIC (*last available rate on or before month end*).
# MAGIC
# MAGIC **Schedule it** as a daily Job (e.g. one hour before your dashboard refresh).
# MAGIC
# MAGIC **Requirements:** the cluster / serverless compute needs outbound internet access to
# MAGIC `api.frankfurter.dev`. If egress is blocked, load rates from your finance system
# MAGIC into the same table instead; nothing downstream changes.

# COMMAND ----------

# Widgets make the notebook reusable for any organisation / currency.
dbutils.widgets.text("catalog", "main", "Catalog")
dbutils.widgets.text("schema", "finops", "Schema")
dbutils.widgets.text("currencies", "INR", "Quote currencies (comma separated, e.g. INR,EUR,GBP)")
dbutils.widgets.text("lookback_days", "800", "Days of history to (re)load")

catalog = dbutils.widgets.get("catalog").strip()
schema = dbutils.widgets.get("schema").strip()
currencies = [c.strip().upper() for c in dbutils.widgets.get("currencies").split(",") if c.strip()]
lookback_days = int(dbutils.widgets.get("lookback_days"))
target_table = f"{catalog}.{schema}.fx_rates_daily"

# COMMAND ----------

import datetime as dt
import requests
from pyspark.sql import Row

API = "https://api.frankfurter.dev/v1"   # v1 is stable and documented; v2 also exists
BASE = "USD"

start = (dt.date.today() - dt.timedelta(days=lookback_days)).isoformat()
# Time-series endpoint: /v1/<start>..   (open-ended = up to the latest fixing)
resp = requests.get(
    f"{API}/{start}..",
    params={"base": BASE, "symbols": ",".join(currencies)},
    timeout=30,
)
resp.raise_for_status()
payload = resp.json()
# payload["rates"] looks like {"2026-10-01": {"INR": 95.1}, "2026-10-02": {"INR": 95.3}, ...}

now = dt.datetime.utcnow()
rows = [
    Row(
        rate_date=dt.date.fromisoformat(d),
        base_currency=BASE,
        quote_currency=ccy,
        rate=float(rate),
        source="frankfurter.dev (ECB reference rate)",
        loaded_at=now,
    )
    for d, by_ccy in payload.get("rates", {}).items()
    for ccy, rate in by_ccy.items()
]
if not rows:
    raise ValueError(f"No rates returned for {currencies}. Check the currency codes are supported by the ECB set.")

df = spark.createDataFrame(rows)
print(f"Fetched {df.count()} rate rows for {currencies} since {start}")
display(df.orderBy("rate_date", ascending=False).limit(10))

# COMMAND ----------

# Create the table if it does not exist yet (same DDL as sql/02_fx_rates_tables.sql)
spark.sql(f"""
CREATE TABLE IF NOT EXISTS {target_table} (
  rate_date DATE, base_currency STRING, quote_currency STRING,
  rate DOUBLE, source STRING, loaded_at TIMESTAMP)
""")

# Idempotent upsert: re-running never creates duplicates and picks up ECB corrections.
df.createOrReplaceTempView("fx_incoming")
spark.sql(f"""
MERGE INTO {target_table} t
USING fx_incoming s
  ON  t.rate_date = s.rate_date
  AND t.base_currency = s.base_currency
  AND t.quote_currency = s.quote_currency
WHEN MATCHED AND t.rate <> s.rate THEN UPDATE SET t.rate = s.rate, t.loaded_at = s.loaded_at, t.source = s.source
WHEN NOT MATCHED THEN INSERT *
""")

display(spark.sql(f"""
SELECT quote_currency, MIN(rate_date) AS first_date, MAX(rate_date) AS last_date, COUNT(*) AS days
FROM {target_table} GROUP BY quote_currency
"""))
