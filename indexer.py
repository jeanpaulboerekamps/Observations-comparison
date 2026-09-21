"""Maintainer-only index builder. Run from GitHub Actions, never from Streamlit."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import math
import os
import time

import numpy as np
import requests
from shapely.geometry import shape

from core import bounds_for_api, is_not_identified_to_species, observations_in_geometry
from indexed import MODEL_VERSION

API = "https://api.inaturalist.org/v1/observations"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Observations-comparison/0.4 (indexing; public observations)"})
LAST_REQUEST = 0.0


def inat(params):
    global LAST_REQUEST
    for attempt in range(5):
        time.sleep(max(0, 1.1 - (time.monotonic() - LAST_REQUEST)))
        LAST_REQUEST = time.monotonic()
        try:
            r = SESSION.get(API, params=params, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("iNaturalist gaf na herhaalde pogingen geen resultaat")


def supabase(table, method, payload=None, params=None):
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + table
    key = os.environ["SUPABASE_SECRET_KEY"]
    headers = {"apikey": key, "Content-Type": "application/json",
               "Prefer": "resolution=merge-duplicates,return=representation"}
    for attempt in range(4):
        response = SESSION.request(method, url, headers=headers, json=payload, params=params, timeout=90)
        if response.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 ** attempt)
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError(f"Databaseantwoord: HTTP {response.status_code}")


def read_area(path):
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    geometry = (data.get("features") or [{}])[0].get("geometry") if data.get("type") == "FeatureCollection" else data.get("geometry", data)
    if not geometry or not shape(geometry).is_valid or shape(geometry).is_empty:
        raise ValueError("GeoJSON bevat geen geldig gebied")
    return geometry


def pages(params, first_day, last_day):
    """Split dense date ranges so no iNaturalist 10k offset cap truncates results."""
    base = {**params, "d1": first_day.isoformat(), "d2": last_day.isoformat(),
            "per_page": 200, "page": 1}
    first = inat(base)
    total = int(first.get("total_results", 0))
    if total > 9800:
        if first_day == last_day:
            raise RuntimeError(f"Meer dan 9800 waarnemingen op {first_day}; verklein het indexgebied.")
        mid = first_day + timedelta(days=(last_day - first_day).days // 2)
        yield from pages(params, first_day, mid)
        yield from pages(params, mid + timedelta(days=1), last_day)
        return
    yield first.get("results", []), total
    for page in range(2, math.ceil(total / 200) + 1):
        yield inat({**base, "page": page}).get("results", []), 0


def embed(rows):
    import torch
    from PIL import Image
    from io import BytesIO
    from torchvision.models import EfficientNet_B0_Weights, efficientnet_b0

    weights = EfficientNet_B0_Weights.DEFAULT
    model = efficientnet_b0(weights=weights)
    model.classifier = torch.nn.Identity()
    model.eval()
    preprocess = weights.transforms()
    prepared = []
    for item in rows:
        photo = (item.get("photos") or [{}])[0]
        url = str(photo.get("medium_url") or photo.get("url") or "").replace("square", "medium")
        if not url:
            continue
        try:
            response = SESSION.get(url, timeout=30)
            response.raise_for_status()
            prepared.append((item, preprocess(Image.open(BytesIO(response.content)).convert("RGB"))))
        except (requests.RequestException, OSError) as exc:
            print(f"Foto niet beschikbaar: {item['id']}: {exc}", flush=True)
    for start in range(0, len(prepared), 16):
        batch = prepared[start:start + 16]
        with torch.inference_mode():
            vectors = model(torch.stack([tensor for _, tensor in batch])).cpu().numpy()
        vectors = vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
        for (item, _), vector in zip(batch, vectors):
            yield item, vector


def compact(item):
    photo = (item.get("photos") or [{}])[0]
    taxon = item.get("taxon") or {}
    return {
        "id": item["id"], "observed_on": item.get("observed_on"),
        "taxon": {"rank": taxon.get("rank"), "name": taxon.get("name"),
                  "preferred_common_name": taxon.get("preferred_common_name")},
        "photos": [{"url": str(photo.get("medium_url") or photo.get("url") or "").replace("square", "medium"),
                    "attribution": photo.get("attribution"), "license_code": photo.get("license_code")}],
        "uri": item.get("uri") or f"https://www.inaturalist.org/observations/{item['id']}",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--area", required=True)
    parser.add_argument("--order-id", type=int, required=True)
    parser.add_argument("--order-name", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    geometry = read_area(args.area)
    box = bounds_for_api(geometry)
    south, west, north, east = box
    existing = supabase("index_coverage", "GET",
                        params={"select": "*", "name": "eq." + args.area,
                                "order_id": "eq." + str(args.order_id),
                                "first_date": "eq." + args.start.isoformat(),
                                "last_date": "eq." + args.end.isoformat()})
    if existing:
        coverage_id = existing[0]["id"]
        supabase("index_coverage", "PATCH",
                 {"geometry": geometry, "status": "building", "updated_at": date.today().isoformat()},
                 {"id": "eq." + str(coverage_id)})
    else:
        coverage_id = supabase("index_coverage", "POST", {
            "name": args.area, "order_id": args.order_id, "order_name": args.order_name,
            "geometry": geometry, "first_date": args.start.isoformat(),
            "last_date": args.end.isoformat(), "status": "building",
            "model_version": MODEL_VERSION,
        })[0]["id"]
    count = scanned = failed = 0
    try:
        params = {"geo": "true", "photos": "true", "taxon_id": args.order_id,
                  "swlat": south, "swlng": west, "nelat": north, "nelng": east,
                  "order_by": "id", "order": "asc"}
        for batch, total in pages(params, args.start, args.end):
            scanned += total
            eligible = [row for row in observations_in_geometry(batch, geometry)
                        if row.get("photos") and is_not_identified_to_species(row)]
            records = []
            for item, vector in embed(eligible):
                coords = (item.get("geojson") or {}).get("coordinates")
                if not coords or len(coords) < 2 or not item.get("observed_on"):
                    continue
                records.append({
                    "id": item["id"], "order_id": args.order_id, "observed_on": item["observed_on"],
                    "longitude": coords[0], "latitude": coords[1],
                    "observation": compact(item), "embedding": "[" + ",".join(map(str, vector.tolist())) + "]",
                    "model_version": MODEL_VERSION,
                })
            failed += len(eligible) - len(records)
            if records:
                supabase("index_observations", "POST", records, {"on_conflict": "id"})
                count += len(records)
            print(f"API: {scanned}, verwerkt: {count}, fotofouten: {failed}", flush=True)
        if failed:
            raise RuntimeError(f"{failed} foto's konden niet worden geïndexeerd; index niet vrijgegeven.")
        supabase("index_coverage", "PATCH",
                 {"status": "complete", "source_count": scanned, "indexed_count": count,
                  "failure_count": 0}, {"id": "eq." + str(coverage_id)})
    except Exception:
        supabase("index_coverage", "PATCH",
                 {"status": "failed", "source_count": scanned, "indexed_count": count,
                  "failure_count": failed}, {"id": "eq." + str(coverage_id)})
        raise


if __name__ == "__main__":
    main()
