"""Read-only access to the prebuilt Supabase observation index."""
from __future__ import annotations

import json
import requests


# The embedding model is unchanged; this version marks indexes that include
# species-level observations and a snapshot of links in iNaturalist comments.
MODEL_VERSION = "efficientnet_b0_imagenet1k_v1_full_order_links_v2"


def get_rows(url, key, table, params):
    if not url or not key:
        raise ValueError("Supabase URL of publishable key ontbreekt.")
    endpoint = url.rstrip("/") + "/rest/v1/" + table
    headers = {"apikey": key}
    rows = []
    for page in range(10000):
        response = requests.get(endpoint, params={**params, "limit": 1000, "offset": page * 1000},
                                headers=headers, timeout=40)
        response.raise_for_status()
        batch = response.json()
        rows.extend(batch)
        if len(batch) < 1000:
            return rows
    raise RuntimeError("Index te groot voor deze zoekopdracht; beperk jaren of gebied.")


def coverage(url, key):
    return get_rows(url, key, "index_coverage",
                    {"select": "id,name,order_id,order_name,geometry,first_date,last_date,model_version,indexed_count,updated_at",
                     "status": "eq.complete"})


def observations(url, key, order_id, start, end):
    return get_rows(url, key, "index_observations",
                    {"select": "id,order_id,observed_on,latitude,longitude,observation,embedding,model_version",
                     "order_id": f"eq.{int(order_id)}",
                     "and": f"(observed_on.gte.{start},observed_on.lte.{end})",
                     "order": "id.asc"})


def parse_embedding(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def already_linked_pair(left, right):
    """Ignore a pair when either observation comments on that exact partner."""
    return (int(right["id"]) in (left["observation"].get("comment_links") or [])
            or int(left["id"]) in (right["observation"].get("comment_links") or []))
