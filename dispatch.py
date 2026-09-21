"""Start the existing index workflow from a protected Streamlit session."""
from __future__ import annotations

import hashlib
import json

import requests
from shapely.geometry import shape


REPO = "jeanpaulboerekamps/Observations-comparison"
WORKFLOW = "build-index.yml"


def dispatch_index(token, geometry, order_id, order_name, start, end):
    if not token:
        raise ValueError("GitHub-token ontbreekt.")
    area = shape(geometry)
    if area.is_empty or not area.is_valid or area.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError("Teken of kies eerst een geldig gebied.")
    payload = json.dumps(geometry, separators=(",", ":"), ensure_ascii=True)
    if len(payload.encode("utf-8")) > 60000:
        raise ValueError("Dit gebied heeft te veel punten voor één aanvraag. Vereenvoudig de gebiedsgrens.")
    label = "app-" + hashlib.sha256(payload.encode()).hexdigest()[:16]
    endpoint = f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches"
    response = requests.post(endpoint, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }, json={"ref": "main", "inputs": {
        "area_geojson": payload, "area_label": label,
        "order_id": str(int(order_id)), "order_name": str(order_name),
        "start_date": start.isoformat(), "end_date": end.isoformat(),
    }}, timeout=30)
    if response.status_code not in (200, 204):
        raise RuntimeError(f"GitHub heeft de indexeeractie niet gestart (HTTP {response.status_code}).")
    return (response.json().get("html_url") if response.status_code == 200
            else f"https://github.com/{REPO}/actions/workflows/{WORKFLOW}")
