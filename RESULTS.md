# Results — run over 2017-01-01 to 2018-08-31

Generated from `pipeline_run` id 1 (this run), via direct MySQL queries against the loaded
`marketplace` schema. Reproduce with:

```powershell
python -m pipeline.run --start-date 2017-01-01 --end-date 2018-08-31
```

## Total orders loaded, and orders excluded per R2 by status

| | count |
|---|---|
| **Total orders loaded** (all statuses except canceled/unavailable) | **97,910** |
| of which `delivered` (revenue-eligible) | 96,211 |
| of which `shipped` | 1,097 |
| of which `processing` | 299 |
| of which `invoiced` | 296 |
| of which `created` | 5 |
| of which `approved` | 2 |
| **Excluded per R2** — `canceled` | **580** |
| **Excluded per R2** — `unavailable` | **602** |

Raw orders with `order_purchase_timestamp` in range: 99,092 (97,910 loaded + 1,182 excluded).

## Data-quality issue counts per rule ID

| rule_id | issue_type | count |
|---|---|---|
| R2 | excluded_order_status | 1,182 |
| R3 | broken_delivered_order | 8 |
| R5 | payment_mismatch | 247 |
| R8 | category_uncategorized | 610 |
| R13 | corrected_tier_case | 1 |
| R13 | corrected_category_whitespace_case | 1 |
| R13 | rejected_impossible_range | 1 |
| R14 | duplicate_reviews | 539 |
| R16 | distance_not_computable | 549 |
| R17 | api_fallback_used | 1 |

(R6, R9, R18 had zero issues on this run — every order had a resolvable FX rate and a usable
approval date, and no items were orphaned from campaign evaluation.)

## Total gross USD for January 2018

**$343,395.22**

## Top 5 sellers by commission_usd for March 2018

| rank | seller_id | commission_usd |
|---|---|---|
| 1 | `7d13fca15225358621be4086e1eb0964` | $1,013.89 |
| 2 | `7c67e1448b00f6e969d365cea6b010ab` | $936.73 |
| 3 | `4869f7a5dfa277a7dca6462dcf3b52b2` | $935.79 |
| 4 | `5dceca129747e92ff8ef7a997dc4f8ca` | $732.04 |
| 5 | `ba90964cff9b9e0e6f32b23b82465f7b` | $613.49 |

## On-time % per region for the year 2018

| region | on_time_pct | orders (with a known on-time status) |
|---|---|---|
| Centro-Oeste | 92.07% | 3,153 |
| Nordeste | 85.56% | 4,799 |
| Norte | 87.64% | 883 |
| Sudeste | 93.05% | 37,303 |
| Sul | 93.29% | 7,393 |

(Region is the **order's own** shipping region — see `ASSUMPTIONS.md` R1/R9 — not the customer's
aggregated home region. Orders with `has_broken_delivery=1` (R3) or no estimated-delivery date are
excluded from the denominator, per R3.)

## Items attributed to each campaign (C001–C005)

| campaign_id | name | items |
|---|---|---|
| C001 | Black Friday 2017 | 2,875 |
| C002 | Back to School SP | 70 |
| C003 | Tech Week | 196 |
| C004 | Mother's Day | 926 |
| C005 | Mother's Day South | 35 |

## Every row rejected or corrected in the internal files, and why

| file | row | action | reason |
|---|---|---|---|
| `commission_rates.csv` | row 8 (`gold,0.09,2018-01-01,`) | **Corrected** | Tier value `gold` is lowercase; normalized to canonical `Gold` so it matches the `Bronze`/`Silver`/`Gold` enum used everywhere else. |
| `category_commission_overrides.csv` | row 4 (`" Telephony ",0.07,2018-01-01,,renegotiated`) | **Corrected** | Category name had leading/trailing whitespace and inconsistent casing; normalized to `telephony` (trimmed, lowercased) to match the category naming convention used by `category_name_en` elsewhere. |
| `category_commission_overrides.csv` | row 6 (`watches_gifts,0.20,2018-05-01,2018-04-30,entered by sales team`) | **Rejected** | `effective_to` (2018-04-30) is before `effective_from` (2018-05-01) — an impossible date range, almost certainly a typo for the inverse dates. Dropped entirely rather than guessed at; `watches_gifts` items fall back to the seller's tier rate instead for the period this row would have covered. |

No rows were rejected from `commission_rates.csv` outright, and no overlapping effective-date
ranges were found for any tier or category once the above corrections were applied (both files
cleanly partition time with no gaps feeding into `R13: no_commission_rate_found` after the
boundary-date fix described in `ASSUMPTIONS.md`).
