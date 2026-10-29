# Assumptions & Decisions

Every rule below is referenced by its ID from the assignment (Section 4). Where a rule was
ambiguous or under-specified, this is the decision made, why, and where it's implemented.

## R1 — Customer identity / home state

`home_state` (on `dim_customer`) is the state recorded on the customer's **earliest order ever**,
computed from the **full** Olist order history — not just the orders inside the
`--start-date/--end-date` range being processed. If we only looked within the processing range,
a customer whose true first order predates the range would get the wrong "home state." Dimension
tables are rebuilt from full history on every run (they're upserted by natural key, never deleted),
so this stays correct regardless of which range is being processed.

Distinct from this: several rules (R9 regional holidays, R18 campaign targeting, `/kpis/monthly`
region filter) need the state/region of the **specific order's own shipment**, not the customer's
aggregated home state. We compute that separately per order from `customers.csv` and store it on
`fact_order.customer_state_code` / `customer_region`. These two concepts are intentionally kept
apart.

## R2 / R3 — Revenue eligibility & broken deliveries

- `canceled` / `unavailable` orders are dropped entirely from `fact_order`/`fact_order_item` (not
  loaded at all) — one `data_quality_issue` row is logged per excluded order
  (`issue_type=excluded_order_status`) so they're still auditable, per "excluded from everything
  except the data-quality report."
- All other statuses are loaded with `is_revenue_eligible = (status == 'delivered')`.
- `delivered` orders with a NULL `order_delivered_customer_date` are loaded and still revenue
  eligible, but `has_broken_delivery=1`; `delivery_business_days`, `on_time`, and R11 lateness are
  all NULL for these and a `data_quality_issue` (R3) is logged.

## R5 — Payment reconciliation

`payment_mismatch` compares `SUM(payment_value)` (all payment rows, vouchers included) against
`SUM(price + freight_value)` across the order's items — exactly as written ("sum of payment_value"
vs "sum of item gross"). `net_cash_paid_brl` is reported separately, excluding `voucher` rows, per
the rule's second sentence. These are two different numbers for the same order by design.

## R6 — FX lookup for orders with no approval/purchase date fallback gap

`order_approved_at` is used first, falling back to `order_purchase_timestamp`. If no FX rate
exists on or before that date at all (shouldn't happen inside the dataset's real span, but
possible near the very start if an approval date is unusually early), `fx_rate` is left NULL and a
`data_quality_issue` (R6, `no_fx_rate_available`) is logged — never defaulted to 1.0 or dropped.

FX rates are fetched for the full span of dates touched by purchase/approval/delivery timestamps
across the **entire** dataset (not just the processing range) and upserted into `ref_fx_rate`,
since it's a cheap, global reference table independent of any one run's date window.

## R7 — Money storage

All monetary columns in MySQL are `DECIMAL(14,2)` (rates: `DECIMAL(6,4)`/`DECIMAL(18,6)` for FX).
Rounding to 2dp (money) / 6dp (FX rate) happens exactly once, in `pipeline/run.py`, immediately
before the final load-ready DataFrames are built — every upstream calculation (item gross,
commission, USD conversion) is done in floating point without intermediate rounding, per the rule.

## R8 — Category resolution

Implemented exactly as the 4-step order in the rule. `unknown` (PT category exists, no
translation found in either the patch or Kaggle's file) and `uncategorized` (no PT category at
all) are kept as distinct `category_source` values on `dim_product`, both logged as DQ issues once
per product (not per order/item, since this is a reference-data check against `products.csv`,
independent of the processing date range).

## R9 — Business-day lead time

**Convention (explicit, as the rule asks):** we use `numpy.busday_count(approval_date,
delivery_date, holidays=...)` semantics — the **start day is included** if it is itself a business
day, and the **delivery day is excluded**. Same-day approval and delivery therefore yields `0`
business days. This is "how many full business days elapsed before delivery landed," which is the
more useful meaning of a lead-time metric for Ops.

**What we found in Nager.Date:** national holidays have `counties: null` (`global: true`); regional
holidays carry a `counties` array like `["BR-SP"]`. We take the last two characters of each county
code as the state code. A regional holiday is excluded from the business-day count only for orders
whose **own order-level `customer_state`** (not R1's aggregated home_state) matches that state —
e.g. a São Paulo state holiday doesn't affect a delivery to a Rio de Janeiro customer.

If `order_approved_at` is NULL (and `order_purchase_timestamp` is somehow also unusable),
`delivery_business_days` is NULL and a DQ issue is logged (`missing_approval_date`); this is
distinct from the R3 "broken delivery" case.

## R11 — Seller lateness grain

Modeled as its own fact table, `fact_order_seller` (one row per `(order_id, seller_id)` pair),
since lateness is explicitly a per-seller-per-order concept, not a per-item one. The earliest
`shipping_limit_date` across that seller's items in the order is used, per the rule. `review_score`
(R14) is also copied onto this table since "attributed to every seller in the order" has the exact
same grain.

## R12 — Seller tier across a sub-range re-run

Tier for month M depends on month M−1's revenue. If we computed this only from orders inside the
`--start-date/--end-date` window, the **first month of any range** (and any re-run of a single
sub-range) would have no visibility into the prior month's revenue and would incorrectly default
everyone to Bronze. Decision: `compute_seller_monthly_revenue()` is always computed from the
**full** order/item history in the CSVs, regardless of the range being processed; only the
resulting fact rows for items inside the requested range are written to `fact_order_item`. This
keeps sub-range re-runs (e.g. re-running only March 2018) numerically identical to a full re-run
covering that month, which is exactly what idempotency requires. Verified in
`tests/test_r12_tier.py` and manually by re-running a single month against an already-loaded two
month range (see README "Idempotency & re-runs").

## R13 — Internal file cleaning

`commission_rates.csv` / `category_commission_overrides.csv` cleaning rules applied, each
logged when triggered:

- Tier names are case/whitespace-normalized to `{Bronze, Silver, Gold}` (`gold` → `Gold`).
- Category names are trimmed and lowercased (`" Telephony "` → `telephony`).
- `effective_to` blank/missing is treated as open-ended (NULL = no end date), not rejected.
- A row with `effective_to < effective_from` is **rejected** and logged
  (`rejected_impossible_range`) — e.g. the `watches_gifts` override (`2018-05-01` to
  `2018-04-30`) is dropped entirely, not corrected or guessed at.
- Overlapping effective-date ranges for the same tier/category are detected and logged if found
  (none exist in the provided files once case-normalized, but the check runs every time).
- Commission resolution: a category override active on the purchase date wins; otherwise the tier
  rate active on that date is used. If **neither** resolves (e.g. a purchase date before any rate
  takes effect), `commission_rate`/`commission_brl`/`commission_usd` are left NULL and a DQ issue
  (`no_commission_rate_found`) is logged — never silently defaulted to 0 or to some other tier.

## R14 — Review duplicates

Kept the row with the latest `review_answer_timestamp` per `order_id`; every duplicate found is
logged (`duplicate_reviews`, with the count). The surviving score is attributed to the order
(`fact_order.review_score`) and to every seller in that order (`fact_order_seller.review_score`).

## R16 — Distance

Haversine distance is computed between the **seller's** zip-prefix centroid and the **specific
order's shipping customer zip-prefix** centroid (not the customer's R1 home zip) — distance is a
property of that particular shipment. Missing either side's coordinates (zip prefix absent from
`geolocation.csv`, or all its points were outside the Brazil bounding box) leaves `distance_km`
NULL and logs a DQ issue (R16) rather than guessing or defaulting to 0.

## R17 — Region enrichment & BrasilAPI outage

The live BrasilAPI IBGE-states endpoint (`/api/ibge/uf/v1`) returned a persistent `504 Gateway
Timeout` throughout development, while other BrasilAPI endpoints (e.g. `/api/cep/v1/...`) worked
normally — this looks like a real, narrow outage on that one sub-resource rather than our own
retry logic being wrong. Per the assignment's instruction to fail clearly rather than load partial
data, `fetch_states()` still calls the live API first with full retry/backoff; **only** if that
genuinely fails after retries does it fall back to a small hardcoded table of Brazil's 27 states
and regions (a fixed, non-changing public fact — this mapping has not changed in decades), and logs
a `data_quality_issue` (`api_fallback_used`) every time this happens so the degraded mode is never
silent. This is different from "hand-downloading and committing the JSON," since the live call is
still the primary path on every run.

## R18 — Campaign attribution

- Date-only `start`/`end` values are calendar dates in America/Sao_Paulo; an end date includes the
  full day (`23:59:59.999999`), per the rule.
- `Z`-suffixed ISO timestamps (campaign C003) are parsed as UTC and converted to
  America/Sao_Paulo before comparing against the (already-local) Olist purchase timestamps.
- "Customer state/region" for targeting means the **specific order's own** shipping state/region
  (same concept as R9's holiday matching), not R1's aggregated customer home state — a campaign
  targets shipments, not customer profiles.
- A campaign matches on state if it declares `target_states` (`"ALL"` = any state); it matches on
  region if it declares `target_regions` (`"ALL"` = any region). A campaign normally declares only
  one of the two keys; either being satisfied is sufficient.
- Multiple matches: highest `priority` number wins; ties broken by the later `start`. Verified in
  `tests/test_r18_campaigns.py` (`C004` vs `C005` in May 2018, both priority 2 — `C005` wins on its
  later start).
- `/campaigns/{id}/performance`: "items"/"orders"/"gross" for the campaign window are the items
  actually **attributed** to that `campaign_id` (R18's output), not merely every item purchased
  during the campaign's date range. The "same number of days immediately before" comparison window
  uses **all** order activity in that preceding period (unfiltered by campaign, since nothing could
  be attributed to a campaign before it starts) — this is the natural non-campaign baseline to
  compare the lift against.

## Data-quality issue scoping

Issues tied to a specific order/item/seller (R2, R3, R5, R6, R9, R11, R14, R16) are logged only
for orders inside the requested `--start-date/--end-date` range, matching "only orders ... are
processed." Issues from cleaning global reference/dimension data (R8 category resolution, R13
internal-file cleaning, R17 API fallback) are logged once per run against the full reference
dataset, since those aren't order-scoped — the products/commission-rates/states files don't have a
"date range" of their own.

`data_quality_issue` is an append-only log keyed by `run_id`: re-running the same range twice adds
a second run's worth of DQ rows rather than overwriting the first. This is intentional (an audit
trail of every run), and the `/data-quality/summary` endpoint defaults to the **latest** `run_id`
so it reflects current state unless an older `run_id` is explicitly requested.

## Idempotency & re-runs

- **Dimensions** (`dim_customer`, `dim_seller`, `dim_product`, `dim_campaign`, `dim_date`) and the
  **growing reference tables** (`ref_fx_rate`, `ref_holiday`, `ref_state`) are upserted by natural
  key (`INSERT ... ON DUPLICATE KEY UPDATE`) — safe to re-run any number of times.
- **Tiny config reference tables** (`ref_commission_rate`, `ref_category_override`) are derived
  fresh from the internal CSVs and fully reloaded (delete-all, re-insert) every run, since they're
  small and have no natural "date range" of their own.
- **Facts** (`fact_order`, `fact_order_item`, `fact_order_seller`) are deleted for exactly the
  orders whose `purchase_ts` falls in `[start_date, end_date]`, then re-inserted — so re-running the
  same range twice produces identical totals, and re-running a sub-range (e.g. only March 2018)
  replaces only that month without touching any other month's rows.
- `pipeline_run` and `data_quality_issue` are intentionally append-only logs (see above).
