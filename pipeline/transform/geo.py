"""R15 (zip-prefix centroids), R16 (haversine distance), R17 (state/region enrichment)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.transform.dq import DQCollector

LAT_MIN, LAT_MAX = -33.75, 5.27
LNG_MIN, LNG_MAX = -73.99, -34.79
EARTH_RADIUS_KM = 6371.0


def compute_zip_centroids(geolocation: pd.DataFrame) -> pd.DataFrame:
    """R15: drop points outside Brazil's bounding box, then take the median lat/lng per zip prefix."""
    in_bbox = geolocation[
        geolocation["geolocation_lat"].between(LAT_MIN, LAT_MAX)
        & geolocation["geolocation_lng"].between(LNG_MIN, LNG_MAX)
    ]
    centroids = (
        in_bbox.groupby("geolocation_zip_code_prefix")[["geolocation_lat", "geolocation_lng"]]
        .median()
        .reset_index()
        .rename(columns={
            "geolocation_zip_code_prefix": "zip_prefix",
            "geolocation_lat": "latitude",
            "geolocation_lng": "longitude",
        })
    )
    return centroids


def haversine_km(lat1: pd.Series, lng1: pd.Series, lat2: pd.Series, lng2: pd.Series) -> pd.Series:
    lat1r, lng1r, lat2r, lng2r = map(np.radians, [lat1, lng1, lat2, lng2])
    dlat = lat2r - lat1r
    dlng = lng2r - lng1r
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1r) * np.cos(lat2r) * np.sin(dlng / 2) ** 2
    c = 2 * np.arcsin(np.sqrt(a))
    return EARTH_RADIUS_KM * c


def compute_item_distances(items: pd.DataFrame, dq: DQCollector) -> pd.Series:
    """items must have seller_lat, seller_lng, customer_lat, customer_lng columns (R16).
    Returns a distance_km Series (NaN where either side has no coordinates; logged).
    """
    has_seller = items["seller_lat"].notna() & items["seller_lng"].notna()
    has_customer = items["customer_lat"].notna() & items["customer_lng"].notna()
    computable = has_seller & has_customer

    distance = pd.Series(np.nan, index=items.index)
    distance.loc[computable] = haversine_km(
        items.loc[computable, "seller_lat"], items.loc[computable, "seller_lng"],
        items.loc[computable, "customer_lat"], items.loc[computable, "customer_lng"],
    )

    missing = items[~computable]
    for _, row in missing.iterrows():
        dq.log("R16", "order_item", f"{row['order_id']}:{row['order_item_id']}", "distance_not_computable",
               "Missing seller and/or customer zip-prefix coordinates after geolocation lookup.")

    return distance


def build_dim_seller(sellers: pd.DataFrame, centroids: pd.DataFrame, states: pd.DataFrame) -> pd.DataFrame:
    dim = sellers[["seller_id", "seller_zip_code_prefix", "seller_state"]].rename(
        columns={"seller_zip_code_prefix": "seller_zip_prefix", "seller_state": "seller_state_code"}
    )
    dim = dim.merge(
        centroids, how="left", left_on="seller_zip_prefix", right_on="zip_prefix"
    ).drop(columns=["zip_prefix"])
    dim = dim.merge(states, how="left", left_on="seller_state_code", right_on="state_code")
    dim = dim.rename(columns={"state_name": "seller_state_name", "region": "seller_region"})
    return dim[[
        "seller_id", "seller_zip_prefix", "seller_state_code", "seller_state_name",
        "seller_region", "latitude", "longitude",
    ]]
