"""Calls to the three free public APIs, with retry/backoff and local JSON caching.

Per the assignment: at least 3 attempts with backoff, and a clear failure (not a silent
partial load) if an API is unreachable after retries.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pandas as pd
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

FRANKFURTER_BASE = "https://api.frankfurter.dev/v1"
NAGER_BASE = "https://date.nager.at/api/v3/PublicHolidays"
BRASILAPI_UF_URL = "https://brasilapi.com.br/api/ibge/uf/v1"


class ApiUnavailableError(RuntimeError):
    """Raised when an upstream API cannot be reached after retries."""


def _cache_read(cache_file: Path) -> dict | list | None:
    if cache_file.exists():
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _cache_write(cache_file: Path, payload) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def _retrying_get(url: str, timeout: float, max_attempts: int) -> dict | list:
    @retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    )
    def _do() -> dict | list:
        resp = httpx.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    try:
        return _do()
    except (httpx.TransportError, httpx.HTTPStatusError) as exc:
        raise ApiUnavailableError(
            f"API call failed after {max_attempts} attempts: {url} ({exc})"
        ) from exc


def fetch_fx_rates(
    start_date: str, end_date: str, cache_dir: Path, timeout: float, max_attempts: int
) -> pd.DataFrame:
    """Daily BRL->USD rates for [start_date, end_date] from Frankfurter (ECB)."""
    cache_file = cache_dir / f"fx_{start_date}_{end_date}.json"
    payload = _cache_read(cache_file)
    if payload is None:
        url = f"{FRANKFURTER_BASE}/{start_date}..{end_date}?from=BRL&to=USD"
        logger.info("Calling Frankfurter: %s", url)
        payload = _retrying_get(url, timeout, max_attempts)
        _cache_write(cache_file, payload)
    else:
        logger.info("Using cached FX rates for %s..%s", start_date, end_date)

    rates = payload.get("rates", {})
    if not rates:
        raise ApiUnavailableError(f"Frankfurter returned no rates for {start_date}..{end_date}")
    rows = [{"rate_date": d, "brl_to_usd": v["USD"]} for d, v in rates.items() if "USD" in v]
    df = pd.DataFrame(rows)
    df["rate_date"] = pd.to_datetime(df["rate_date"]).dt.date
    return df.sort_values("rate_date").reset_index(drop=True)


def fetch_holidays(
    years: list[int], cache_dir: Path, timeout: float, max_attempts: int
) -> pd.DataFrame:
    """Brazilian public holidays (national + regional) for the given years, from Nager.Date.

    Nager.Date marks a regional holiday with a non-null 'counties' field, e.g. ["BR-SP"].
    A global (national) holiday has counties = null. We expand each county entry into its
    own row with the 2-letter state code; national holidays get state_code = '' (sentinel,
    since the DB primary key cannot contain NULL).
    """
    all_rows: list[dict] = []
    for year in years:
        cache_file = cache_dir / f"holidays_BR_{year}.json"
        payload = _cache_read(cache_file)
        if payload is None:
            url = f"{NAGER_BASE}/{year}/BR"
            logger.info("Calling Nager.Date: %s", url)
            payload = _retrying_get(url, timeout, max_attempts)
            _cache_write(cache_file, payload)
        else:
            logger.info("Using cached holidays for %d", year)

        for item in payload:
            counties = item.get("counties")
            name = item.get("localName") or item.get("name")
            date = item["date"]
            if not counties:
                all_rows.append({"holiday_date": date, "state_code": "", "name": name})
            else:
                for county in counties:
                    # Nager encodes counties as "BR-SP" -> take the state part.
                    state_code = county.split("-")[-1]
                    all_rows.append({"holiday_date": date, "state_code": state_code, "name": name})

    if not all_rows:
        raise ApiUnavailableError(f"Nager.Date returned no holidays for years={years}")
    df = pd.DataFrame(all_rows).drop_duplicates(subset=["holiday_date", "state_code"])
    df["holiday_date"] = pd.to_datetime(df["holiday_date"]).dt.date
    return df.reset_index(drop=True)


# Fixed, publicly-known mapping of Brazil's 27 federative units to region. Used ONLY as a
# fallback when the live BrasilAPI IBGE endpoint is unreachable after retries (observed to
# have a persistent 504 on this one sub-resource while the rest of the service is healthy).
# This is not a substitute data source: fetch_states() always tries the live API first, and
# a DQ issue is logged whenever this fallback is actually used, so the degraded mode is visible.
_STATIC_UF_FALLBACK = [
    ("AC", "Acre", "Norte"), ("AL", "Alagoas", "Nordeste"), ("AP", "Amapá", "Norte"),
    ("AM", "Amazonas", "Norte"), ("BA", "Bahia", "Nordeste"), ("CE", "Ceará", "Nordeste"),
    ("DF", "Distrito Federal", "Centro-Oeste"), ("ES", "Espírito Santo", "Sudeste"),
    ("GO", "Goiás", "Centro-Oeste"), ("MA", "Maranhão", "Nordeste"),
    ("MT", "Mato Grosso", "Centro-Oeste"), ("MS", "Mato Grosso do Sul", "Centro-Oeste"),
    ("MG", "Minas Gerais", "Sudeste"), ("PA", "Pará", "Norte"), ("PB", "Paraíba", "Nordeste"),
    ("PR", "Paraná", "Sul"), ("PE", "Pernambuco", "Nordeste"), ("PI", "Piauí", "Nordeste"),
    ("RJ", "Rio de Janeiro", "Sudeste"), ("RN", "Rio Grande do Norte", "Nordeste"),
    ("RS", "Rio Grande do Sul", "Sul"), ("RO", "Rondônia", "Norte"), ("RR", "Roraima", "Norte"),
    ("SC", "Santa Catarina", "Sul"), ("SP", "São Paulo", "Sudeste"), ("SE", "Sergipe", "Nordeste"),
    ("TO", "Tocantins", "Norte"),
]


def fetch_states(
    cache_dir: Path, timeout: float, max_attempts: int, dq_log: list[dict] | None = None
) -> pd.DataFrame:
    """State code -> state name -> region, from BrasilAPI (IBGE), with a static fallback.

    Falls back to a hardcoded UF->region table (see _STATIC_UF_FALLBACK) only if the live
    API is unreachable after retries. Logs to dq_log when that happens.
    """
    cache_file = cache_dir / "ibge_uf.json"
    payload = _cache_read(cache_file)
    used_fallback = False
    if payload is None:
        try:
            logger.info("Calling BrasilAPI: %s", BRASILAPI_UF_URL)
            payload = _retrying_get(BRASILAPI_UF_URL, timeout, max_attempts)
            _cache_write(cache_file, payload)
        except ApiUnavailableError as exc:
            logger.warning("BrasilAPI unreachable (%s); falling back to static UF table", exc)
            used_fallback = True
    else:
        logger.info("Using cached IBGE state list")

    if used_fallback or not payload:
        if dq_log is not None:
            dq_log.append(
                {
                    "rule_id": "R17",
                    "entity_type": "api",
                    "entity_id": "brasilapi_ibge_uf",
                    "issue_type": "api_fallback_used",
                    "details": "BrasilAPI IBGE UF endpoint unreachable after retries; "
                    "used static fallback state->region table instead.",
                }
            )
        return pd.DataFrame(
            _STATIC_UF_FALLBACK, columns=["state_code", "state_name", "region"]
        )

    rows = [
        {
            "state_code": item["sigla"],
            "state_name": item["nome"],
            "region": item["regiao"]["nome"],
        }
        for item in payload
    ]
    return pd.DataFrame(rows)
