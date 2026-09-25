"""Read-only access to the prebuilt Supabase observation index."""
from __future__ import annotations

import json
import re
import time
import requests


# The embedding model is unchanged; this version marks indexes that include
# species-level observations and a snapshot of links in iNaturalist comments.
MODEL_VERSION = "efficientnet_b0_imagenet1k_v1_full_order_links_v2"
INAT_API = "https://api.inaturalist.org/v1/observations"
OBSERVATION_LINK = re.compile(
    r"https?://(?:www\.)?inaturalist\.org/observations/(\d+)(?!\d)", re.I)


def get_rows(url, key, table, params):
    if not url or not key:
        raise ValueError("Supabase URL of publishable key ontbreekt.")
    endpoint = url.rstrip("/") + "/rest/v1/" + table
    headers = {"apikey": key}
    rows = []
    for page in range(10000):
        response = requests.get(endpoint, params={**params, "limit": 1000, "offset": page * 1000},
                                headers=headers, timeout=40)
        if not response.ok:
            raise RuntimeError(f"Supabase {response.status_code}: {response.text}")
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


def _inat_detail_rows(identifiers, session):
    """Load public observation details, splitting if iNaturalist rejects a batch."""
    endpoint = INAT_API + "/" + ",".join(map(str, identifiers))
    for attempt in range(5):
        response = session.get(endpoint, timeout=45)
        if response.status_code == 422 and len(identifiers) > 1:
            middle = len(identifiers) // 2
            return (_inat_detail_rows(identifiers[:middle], session) +
                    _inat_detail_rows(identifiers[middle:], session))
        if response.status_code == 429 or response.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        response.raise_for_status()
        return response.json().get("results", [])
    raise RuntimeError("iNaturalist gaf na herhaalde pogingen geen antwoord.")


def current_comment_links(observation_ids):
    """Check comments only for observations in strong candidate pairs."""
    identifiers = sorted({int(value) for value in observation_ids})
    links = {identifier: [] for identifier in identifiers}
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Observations-comparison/0.6.4 (checking public comments)"})
    for start in range(0, len(identifiers), 25):
        if start:
            time.sleep(1.1)
        rows = _inat_detail_rows(identifiers[start:start + 25], session)
        for row in rows:
            comments = row.get("comments") or []
            if (row.get("comments_count") is not None
                    and len(comments) < int(row["comments_count"])):
                raise RuntimeError(f"Niet alle opmerkingen voor #{row['id']} werden teruggegeven.")
            links[int(row["id"])] = sorted({
                int(match)
                for comment in comments
                for match in OBSERVATION_LINK.findall(comment.get("body") or "")
            })
    return links


def currently_linked_pair(left, right, links):
    """Check an exact pair against freshly loaded public comment links."""
    left_id, right_id = int(left["id"]), int(right["id"])
    return right_id in links.get(left_id, []) or left_id in links.get(right_id, [])
