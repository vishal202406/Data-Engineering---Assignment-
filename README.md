# Marketplace Seller Performance Pipeline

A small ETL pipeline + read-only API for a Brazilian marketplace's seller payouts and delivery
performance, built on the [Olist public dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce).

See [`ASSUMPTIONS.md`](ASSUMPTIONS.md) for every decision made on an ambiguous rule, and
[`RESULTS.md`](RESULTS.md) for the numbers from the required 2017-01-01..2018-08-31 run.

## Project layout

```
pipeline/
  extract/     Kaggle CSVs, internal files, the 3 public APIs (retry + local cache)
  transform/   Business rules R1-R18, one module per topic
  load/        MySQL DDL application + idempotent loader
  api/         FastAPI app (routers, Pydantic schemas)
  common/      config, DB engine, logging, Decimal rounding helpers
  run.py       orchestrator: python -m pipeline.run --start-date ... --end-date ...
migrations/001_schema.sql   full DDL (dimensions, facts, reference tables, DQ/run logs)
data/internal/              the 4 human-maintained files from Section 2.3
tests/                      unit tests for R6, R9, R12, R13, R18
```

## Setup

Requirements: Python 3.11+, MySQL 8.x, the Olist CSVs downloaded into a folder (default:
`./archive`, configurable via `.env`).

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# MySQL: create a database + user, then apply the schema
mysql -u root -p -e "CREATE DATABASE marketplace CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql -u root -p marketplace < migrations/001_schema.sql

# copy .env.example -> .env and fill in your MySQL credentials + file paths
copy .env.example .env
```

## Running the pipeline

```powershell
python -m pipeline.run --start-date 2017-01-01 --end-date 2018-08-31
```

Only orders whose `order_purchase_timestamp` falls in `[start-date, end-date]` are loaded as
facts. The three public APIs (Frankfurter FX rates, Nager.Date holidays, BrasilAPI state/region)
are called every run; responses are cached under `data/cache/` so re-running doesn't re-hit them.
Raw extracted data (as-received, before any business rule) is snapshotted to `data/raw/` as
Parquet files (`stg_*`).

If an API is unreachable after 3 retries with backoff, the run aborts with a clear error instead
of loading partial data — **except** BrasilAPI's state list, which falls back to a small
hardcoded table after retries fail (see `ASSUMPTIONS.md` R17 for why: a real, narrow outage was
observed on that one endpoint during development, and Brazil's 27 states don't change).

### Idempotency & re-runs

- Re-running the exact same date range twice does not duplicate rows or change totals (facts are
  deleted-then-reinserted for that range; dimensions/reference tables are upserted by natural key).
- Re-running a sub-range (e.g. only `2018-03-01..2018-03-31`) replaces exactly that month's facts
  without touching any other month's rows — verified manually (load Jan+Feb+Mar, re-run Mar alone,
  confirm Jan/Feb counts unchanged) and via `tests/test_r12_tier.py` for the seller-tier
  month-dependency specifically.

## Running the API

```powershell
.venv\Scripts\python -m uvicorn pipeline.api.main:app --reload --port 8000
```

Interactive docs at `http://127.0.0.1:8000/docs`. Example calls:

```powershell
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/orders/ec7a019261fce44180373d45b442d78f
curl "http://127.0.0.1:8000/sellers/0015a82c2db000af6aaaf3ae2ecb0532/scorecard?month=2018-03"
curl "http://127.0.0.1:8000/sellers/top?month=201803&metric=commission_usd&limit=5"
curl "http://127.0.0.1:8000/kpis/monthly?start=2018-01&end=2018-03&region=Sul"
curl http://127.0.0.1:8000/campaigns/C003/performance
curl http://127.0.0.1:8000/data-quality/summary
```

## Tests

```powershell
.venv\Scripts\python -m pytest tests/ -v
```

Covers the trickiest rules end to end: R6 (FX fallback to most-recent-earlier rate, never
later), R9 (business-day counting convention, national vs. regional holidays), R12 (seller tier
depends on the *previous* month, with no sales defaulting to Bronze), R13 (internal-file
cleaning, category-override precedence, and a regression test for an end-of-day boundary bug
found during development), and R18 (campaign window/timezone/priority-tiebreak logic).

## What would you change if data volume grew 100x and the pipeline had to run daily?

The current design loads every CSV fully into pandas in memory and recomputes every reference
aggregate (like seller monthly revenue) from the full history on every run — fine at ~100k rows,
not at 10-100M+ rows run daily. Concretely:

1. **Move the transform off a single pandas process and into the warehouse.** Load raw data into
   staging tables (as the `stg_*`/`data/raw` layer already anticipates) and do the heavy
   aggregations (seller monthly revenue, FX/holiday joins, commission resolution) as SQL
   (or a distributed engine like Spark/DuckDB-on-partitioned-Parquet) instead of row-wise pandas
   `apply()` — the per-order Python loop in `compute_delivery_metrics` and the per-row campaign
   matching would become the first bottlenecks.
2. **Make seller-tier revenue incremental, not full-history-recomputed.** Persist
   `seller_monthly_revenue` as its own table, updated incrementally (only the just-closed month)
   rather than re-aggregated from every order on every run.
3. **Partition the fact tables by month** (MySQL partitioning or a columnar warehouse) so a daily
   incremental load only touches the current day/month's partition, and the delete-then-insert
   idempotency strategy stays fast instead of scanning the whole table by date range.
4. **Orchestrate as a DAG with per-stage retries/backfill** (Airflow/Dagster) instead of one
   monolithic `run()` — a daily job needs to re-process only yesterday's orders plus any
   late-arriving corrections, with alerting on the data-quality thresholds rather than a human
   reading `RESULTS.md`.
5. **Move the three API calls to a scheduled, separately-cached refresh** (FX/holidays/state
   lookups don't need to be re-fetched on every pipeline run; a daily cron with its own cache
   table decouples API availability from the pipeline's critical path).
6. **Add read replicas / a caching layer (Redis) in front of the FastAPI service** once dashboard
   query volume grows, since the aggregation queries (e.g. `/kpis/monthly`) would be scanning
   much larger fact tables.
