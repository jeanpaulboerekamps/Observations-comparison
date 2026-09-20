from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import base64
import html
from io import BytesIO
import json
import logging
import math
import sys
import threading
import time
import zlib

import folium
import numpy as np
import pandas as pd
import requests
import streamlit as st
from PIL import Image
from folium.plugins import Draw
from shapely.geometry import shape
from streamlit_folium import st_folium

from core import (
    bounds_for_api,
    buffer_geometry_km,
    is_not_identified_to_species,
    observations_in_geometry,
)


OBS_API = "https://api.inaturalist.org/v1/observations"
TAXA_AUTOCOMPLETE_API = "https://api.inaturalist.org/v1/taxa/autocomplete"
MIN_REQUEST_INTERVAL = 1.02
MAX_FOCAL_OBSERVATIONS = 100
MAX_PHOTOS_PER_OBSERVATION = 2
REQUEST_LOCK = threading.Lock()
LAST_REQUEST_AT = 0.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("waarnemingen-gelijkeniszoeker")

st.set_page_config(
    page_title="Waarnemingen Gelijkeniszoeker",
    page_icon="🔎",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
.block-container {padding-top:1.3rem;padding-bottom:4rem;max-width:1220px}
div.stButton > button, div.stDownloadButton > button {
  min-height:48px;font-size:1rem;border-radius:12px;width:100%
}
.release-badge {display:inline-block;padding:.25rem .65rem;border-radius:999px;
  background:#e8f5e9;color:#245d2b;font-weight:700;margin-bottom:.55rem}
.intro {padding:1rem 1.15rem;border-radius:18px;margin:.2rem 0 1rem;
  border:1px solid rgba(46,125,50,.2);background:linear-gradient(135deg,#eef8ff,#f2fbf4)}
.active-area {padding:.7rem .9rem;border-radius:14px;background:rgba(33,150,243,.08);
  border-left:4px solid #2196f3;margin:.2rem 0 .7rem}
.match-score {display:inline-block;padding:.2rem .55rem;border-radius:999px;
  background:rgba(46,125,50,.13);font-weight:700;margin-bottom:.35rem}
[data-testid="stFileUploaderDropzone"] {padding:.15rem 0;border:0;background:transparent}
[data-testid="stFileUploaderDropzoneInstructions"] {display:none}
[data-testid="stFileUploaderDropzone"] button {font-size:0;min-height:46px}
[data-testid="stFileUploaderDropzone"] button::after {content:"Kies gebied";font-size:1rem}
[data-testid="stFileUploaderFile"] {display:none}
@media (max-width:768px){.block-container{padding-left:.8rem;padding-right:.8rem}}
</style>
""",
    unsafe_allow_html=True,
)


def init_state():
    defaults = {
        "areas": {},
        "active_area": None,
        "show_area_creator": False,
        "last_area_upload": None,
        "order_candidates": [],
        "comparison_observations": None,
        "focal_observations": None,
        "comparison_vectors": None,
        "search_meta": {},
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def clear_results():
    st.session_state.comparison_observations = None
    st.session_state.focal_observations = None
    st.session_state.comparison_vectors = None
    st.session_state.search_meta = {}


def normalize_geometry_longitudes(geometry):
    """Map wrapped Leaflet longitudes back to the standard -180..180 range."""
    if not isinstance(geometry, dict):
        return geometry

    def normalize_coordinates(value):
        if (
            isinstance(value, (list, tuple))
            and len(value) >= 2
            and isinstance(value[0], (int, float))
            and isinstance(value[1], (int, float))
        ):
            longitude = ((float(value[0]) + 180.0) % 360.0) - 180.0
            return [longitude, *value[1:]]
        if isinstance(value, (list, tuple)):
            return [normalize_coordinates(item) for item in value]
        return value

    normalized = dict(geometry)
    if "coordinates" in normalized:
        normalized["coordinates"] = normalize_coordinates(normalized["coordinates"])
    if "geometries" in normalized:
        normalized["geometries"] = [
            normalize_geometry_longitudes(item) for item in normalized["geometries"]
        ]
    return normalized


def remember_area(name, geometry):
    try:
        payload = json.dumps(
            {"name": name, "geometry": normalize_geometry_longitudes(geometry)},
            separators=(",", ":"),
        )
        token = base64.urlsafe_b64encode(
            zlib.compress(payload.encode("utf-8"), 9)
        ).decode().rstrip("=")
        st.query_params["gebied"] = token
    except Exception:
        pass


def restore_remembered_area():
    if st.session_state.areas:
        return
    token = st.query_params.get("gebied")
    if not token:
        return
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(
            zlib.decompress(base64.urlsafe_b64decode(padded)).decode("utf-8")
        )
        name = str(payload["name"])
        geometry = normalize_geometry_longitudes(payload["geometry"])
        candidate = shape(geometry)
        if candidate.is_empty or not candidate.is_valid:
            return
        st.session_state.areas[name] = geometry
        st.session_state.active_area = name
    except Exception:
        pass


def area_geojson(name, geometry):
    return json.dumps(
        {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"name": name},
                "geometry": normalize_geometry_longitudes(geometry),
            }],
        },
        ensure_ascii=False,
        indent=2,
    )


def area_filename(name):
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_")
    return f"{safe or 'zoekgebied'}.geojson"


def request_json(url, params, timeout=(10, 45)):
    global LAST_REQUEST_AT
    last_error = None
    for attempt in range(4):
        try:
            with REQUEST_LOCK:
                wait = MIN_REQUEST_INTERVAL - (time.monotonic() - LAST_REQUEST_AT)
                if wait > 0:
                    time.sleep(wait)
                LAST_REQUEST_AT = time.monotonic()
            response = requests.get(
                url,
                params=params,
                timeout=timeout,
                headers={"User-Agent": "Waarnemingen-Gelijkeniszoeker/0.1"},
            )
            if response.status_code == 429 or response.status_code >= 500:
                time.sleep(0.8 * (2 ** attempt))
                continue
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
            time.sleep(0.6 * (2 ** attempt))
    raise RuntimeError(f"iNaturalist kon niet worden bereikt: {last_error}")


@st.cache_data(ttl=86400, show_spinner=False)
def search_orders(query):
    query = (query or "").strip()
    if len(query) < 2:
        return []
    results = {}
    for locale in ("nl", "en"):
        payload = request_json(TAXA_AUTOCOMPLETE_API, {
            "q": query,
            "rank": "order",
            "per_page": 30,
            "locale": locale,
        })
        for taxon in payload.get("results", []):
            if taxon.get("rank") != "order" or not taxon.get("id"):
                continue
            taxon_id = int(taxon["id"])
            scientific = taxon.get("name") or ""
            common = taxon.get("preferred_common_name") or ""
            title = f"{common} ({scientific})" if common and common != scientific else scientific
            current = results.get(taxon_id)
            if not current or (common and current["label"] == current["scientific_name"]):
                results[taxon_id] = {
                    "id": taxon_id,
                    "label": title,
                    "scientific_name": scientific,
                }
    return sorted(results.values(), key=lambda item: item["label"].lower())


def compact_photo(photo):
    if not photo:
        return None
    url = str(photo.get("medium_url") or photo.get("url") or "").replace("square", "medium")
    if not url:
        return None
    return {
        "url": url,
        "attribution": photo.get("attribution") or "",
        "license_code": photo.get("license_code") or "",
    }


def compact_observation(observation):
    photos = [compact_photo(photo) for photo in observation.get("photos") or []]
    taxon = observation.get("taxon") or {}
    return {
        "id": observation.get("id"),
        "observed_on": observation.get("observed_on") or "",
        "created_at": observation.get("created_at") or "",
        "geojson": observation.get("geojson"),
        "taxon": {
            "id": taxon.get("id"),
            "rank": taxon.get("rank"),
            "name": taxon.get("name"),
            "preferred_common_name": taxon.get("preferred_common_name"),
        },
        "photos": [photo for photo in photos if photo][:MAX_PHOTOS_PER_OBSERVATION],
        "uri": observation.get("uri") or f"https://www.inaturalist.org/observations/{observation.get('id')}",
        "user": (observation.get("user") or {}).get("login") or "",
    }


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_observations(base_params_tuple, bbox, max_rows):
    """Fetch a deliberately bounded newest-first candidate set."""
    base = dict(base_params_tuple)
    south, west, north, east = bbox
    first_params = dict(base)
    first_params.update({
        "swlat": south, "swlng": west, "nelat": north, "nelng": east,
        "page": 1, "per_page": 200,
    })
    first = request_json(OBS_API, first_params)
    total = int(first.get("total_results", 0) or 0)
    page_count = min(50, max(1, math.ceil(min(total, max_rows) / 200)))
    pages = {1: first.get("results", [])}
    for page in range(2, page_count + 1):
        params = dict(first_params)
        params["page"] = page
        pages[page] = request_json(OBS_API, params).get("results", [])
    rows = [
        compact_observation(item)
        for page in range(1, page_count + 1)
        for item in pages.get(page, [])
    ]
    return rows[:max_rows], total, total > max_rows


def eligible_observations(observations, geometry, limit):
    selected = [
        observation
        for observation in observations_in_geometry(observations, geometry)
        if observation.get("photos") and is_not_identified_to_species(observation)
    ]
    selected.sort(
        key=lambda item: item.get("observed_on") or item.get("created_at") or "",
        reverse=True,
    )
    return selected[:limit], len(selected) > limit


@st.cache_resource(show_spinner=False)
def load_embedding_model():
    import torch
    from torchvision.models import EfficientNet_B0_Weights, efficientnet_b0

    weights = EfficientNet_B0_Weights.DEFAULT
    model = efficientnet_b0(weights=weights)
    model.classifier = torch.nn.Identity()
    model.eval()
    return model, weights.transforms()


def download_image(url):
    try:
        response = requests.get(
            url,
            timeout=(8, 30),
            headers={"User-Agent": "Waarnemingen-Gelijkeniszoeker/0.1"},
        )
        response.raise_for_status()
        return url, Image.open(BytesIO(response.content)).convert("RGB")
    except Exception as exc:
        log.warning("Foto kon niet worden geladen (%s): %s", url, exc)
        return url, None


@st.cache_data(ttl=604800, max_entries=8, show_spinner=False)
def embed_photo_urls(urls_tuple):
    """Download photos and embed them in batches; image bytes are not retained."""
    import torch

    model, preprocess = load_embedding_model()
    images = {}
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(urls_tuple)))) as executor:
        futures = [executor.submit(download_image, url) for url in urls_tuple]
        for future in as_completed(futures):
            url, image = future.result()
            if image is not None:
                images[url] = image

    vectors = {}
    ordered_urls = [url for url in urls_tuple if url in images]
    for start in range(0, len(ordered_urls), 16):
        batch_urls = ordered_urls[start:start + 16]
        batch = torch.stack([preprocess(images[url]) for url in batch_urls])
        with torch.inference_mode():
            output = model(batch).cpu().numpy().astype("float32")
        output = output / np.maximum(np.linalg.norm(output, axis=1, keepdims=True), 1e-12)
        for url, vector in zip(batch_urls, output):
            vectors[url] = vector
    return vectors


def observation_embeddings(observations):
    urls = tuple(dict.fromkeys(
        photo["url"]
        for observation in observations
        for photo in observation.get("photos") or []
    ))
    photo_vectors = embed_photo_urls(urls)
    observation_vectors = {}
    for observation in observations:
        vectors = [
            photo_vectors[photo["url"]]
            for photo in observation.get("photos") or []
            if photo["url"] in photo_vectors
        ]
        if not vectors:
            continue
        vector = np.mean(np.stack(vectors), axis=0)
        vector = vector / max(float(np.linalg.norm(vector)), 1e-12)
        observation_vectors[int(observation["id"])] = vector.astype("float32")
    return observation_vectors


def observation_title(observation):
    taxon = observation.get("taxon") or {}
    name = taxon.get("preferred_common_name") or taxon.get("name") or "Onbekend taxon"
    return f"#{observation['id']} · {observation.get('observed_on') or 'datum onbekend'} · {name}"


def render_observation(observation, score=None):
    photo = (observation.get("photos") or [{}])[0]
    if photo.get("url"):
        st.image(photo["url"], width="stretch")
    if score is not None:
        st.markdown(
            f'<span class="match-score">Overeenkomstsscore {score:.1f}</span>',
            unsafe_allow_html=True,
        )
    taxon = observation.get("taxon") or {}
    name = taxon.get("preferred_common_name") or taxon.get("name") or "Onbekend taxon"
    st.markdown(f"**{html.escape(str(name))}**")
    st.caption(
        f"{taxon.get('rank') or 'rang onbekend'} · {observation.get('observed_on') or 'datum onbekend'}"
    )
    st.link_button("Open op iNaturalist", observation["uri"])
    attribution = photo.get("attribution") or ""
    license_code = photo.get("license_code") or ""
    if attribution or license_code:
        st.caption(" · ".join(part for part in (attribution, license_code) if part))


def preview_map(target_geometry, search_geometry, distance_km):
    target_shape = shape(target_geometry)
    display_shape = shape(search_geometry)
    center = [target_shape.centroid.y, target_shape.centroid.x]
    map_object = folium.Map(location=center, zoom_start=10, tiles="OpenStreetMap", control_scale=True)
    if distance_km > 0:
        folium.GeoJson(
            search_geometry,
            name="Zoekzone",
            style_function=lambda _: {
                "color": "#4caf50", "weight": 2, "fillColor": "#81c784", "fillOpacity": .16,
            },
        ).add_to(map_object)
    folium.GeoJson(
        target_geometry,
        name="Geselecteerd gebied",
        style_function=lambda _: {
            "color": "#1565c0", "weight": 3, "fillColor": "#42a5f5", "fillOpacity": .25,
        },
    ).add_to(map_object)
    west, south, east, north = display_shape.bounds
    map_object.fit_bounds([[south, west], [north, east]])
    st_folium(map_object, height=380, use_container_width=True, key="search_preview", returned_objects=[])


init_state()
restore_remembered_area()

st.markdown('<span class="release-badge">Prototype 0.2 · minimumscore toegevoegd</span>', unsafe_allow_html=True)
st.title("🔎 Waarnemingen Gelijkeniszoeker")
st.markdown(
    '<div class="intro"><b>Vind waarnemingen die mogelijk van dezelfde soort zijn.</b><br>'
    'De toepassing rangschikt foto’s op visuele overeenkomst, maar stelt geen soortnaam voor.</div>',
    unsafe_allow_html=True,
)

pick_col, new_col = st.columns([3, 1])
with pick_col:
    uploaded = st.file_uploader(
        "Selecteer een bewaard gebied",
        type=["geojson", "json"],
        key="similarity_area_upload",
    )
with new_col:
    st.write("")
    if st.button("➕ Nieuw gebied maken", key="toggle_area_creator"):
        st.session_state.show_area_creator = not st.session_state.show_area_creator

if uploaded is not None:
    upload_key = (uploaded.name, uploaded.size)
    if st.session_state.last_area_upload != upload_key:
        try:
            payload = json.loads(uploaded.getvalue().decode("utf-8"))
            features = (
                payload.get("features") or []
                if payload.get("type") == "FeatureCollection"
                else [payload] if payload.get("type") == "Feature" else []
            )
            imported = []
            for number, feature in enumerate(features, 1):
                geometry = normalize_geometry_longitudes(feature.get("geometry"))
                if not geometry:
                    continue
                candidate = shape(geometry)
                if candidate.is_empty or not candidate.is_valid:
                    continue
                name = str((feature.get("properties") or {}).get("name") or f"Gebied {number}").strip()
                st.session_state.areas[name] = geometry
                imported.append(name)
            if not imported:
                st.warning("In dit bestand is geen bruikbaar gebied gevonden.")
            else:
                st.session_state.active_area = imported[0]
                st.session_state.last_area_upload = upload_key
                remember_area(imported[0], st.session_state.areas[imported[0]])
                clear_results()
                st.rerun()
        except Exception as exc:
            st.error(f"Dit GeoJSON-bestand kon niet worden geopend: {exc}")

if st.session_state.areas:
    names = list(st.session_state.areas)
    current = st.session_state.active_area if st.session_state.active_area in names else names[0]
    if len(names) > 1:
        selected_area_name = st.selectbox("Actief gebied", names, index=names.index(current))
        if selected_area_name != st.session_state.active_area:
            st.session_state.active_area = selected_area_name
            remember_area(selected_area_name, st.session_state.areas[selected_area_name])
            clear_results()
            st.rerun()
    else:
        st.session_state.active_area = current
    st.markdown(
        f'<div class="active-area"><b>Actief gebied:</b> {html.escape(st.session_state.active_area)}</div>',
        unsafe_allow_html=True,
    )
    st.download_button(
        "💾 Actief gebied bewaren",
        area_geojson(st.session_state.active_area, st.session_state.areas[st.session_state.active_area]),
        area_filename(st.session_state.active_area),
        "application/geo+json",
    )

if st.session_state.show_area_creator:
    with st.container(border=True):
        st.subheader("Nieuw gebied maken")
        area_name = st.text_input("Naam van het gebied", placeholder="Bijvoorbeeld: De Biesbosch")
        center, zoom = [52.1, 5.3], 8
        if st.session_state.active_area in st.session_state.areas:
            current_geometry = shape(st.session_state.areas[st.session_state.active_area])
            center = [current_geometry.centroid.y, current_geometry.centroid.x]
            zoom = 13
        map_object = folium.Map(location=center, zoom_start=zoom, tiles="OpenStreetMap", control_scale=True)
        Draw(
            export=False,
            position="topleft",
            draw_options={
                "polyline": False, "circle": False, "circlemarker": False, "marker": False,
                "polygon": {"allowIntersection": False, "showArea": True}, "rectangle": True,
            },
            edit_options={"edit": True, "remove": True},
        ).add_to(map_object)
        map_state = st_folium(
            map_object,
            height=520,
            use_container_width=True,
            key="similarity_draw_map",
            returned_objects=["all_drawings"],
        )
        drawings = map_state.get("all_drawings") or []
        drawn_geometry = normalize_geometry_longitudes(drawings[-1].get("geometry")) if drawings else None
        clean_name = area_name.strip()
        ready = bool(clean_name and drawn_geometry)
        use_col, save_col = st.columns(2)
        with use_col:
            use_area = st.button("✅ Gebied gebruiken", type="primary", key="use_drawn_area")
        with save_col:
            st.download_button(
                "💾 Gebied bewaren",
                area_geojson(clean_name, drawn_geometry) if ready else "",
                area_filename(clean_name),
                "application/geo+json",
                disabled=not ready,
            )
        if use_area:
            if not clean_name:
                st.error("Geef het gebied eerst een naam.")
            elif not drawn_geometry:
                st.error("Teken eerst een gebied op de kaart.")
            elif not shape(drawn_geometry).is_valid:
                st.error("Het getekende gebied is niet geldig.")
            else:
                st.session_state.areas[clean_name] = drawn_geometry
                st.session_state.active_area = clean_name
                remember_area(clean_name, drawn_geometry)
                st.session_state.show_area_creator = False
                clear_results()
                st.rerun()

st.divider()
st.subheader("Zoekinstellingen")
st.markdown("**1. Kies verplicht een orde**")
st.caption("Zoek op de Nederlandse, Engelse of wetenschappelijke naam van een orde.")
with st.form("order_search_form", clear_on_submit=False):
    order_search_col, order_button_col = st.columns([3, 1])
    with order_search_col:
        order_query = st.text_input(
            "Orde zoeken",
            placeholder="Bijvoorbeeld: Lepidoptera of vlinders",
            label_visibility="collapsed",
        )
    with order_button_col:
        search_order = st.form_submit_button("Zoeken", use_container_width=True)
if search_order:
    st.session_state.pop("selected_order_option", None)
    if len(order_query.strip()) < 2:
        st.session_state.order_candidates = []
        st.warning("Typ minimaal twee tekens om een orde te zoeken.")
    else:
        with st.spinner("Ordes zoeken…"):
            st.session_state.order_candidates = search_orders(order_query)
        if not st.session_state.order_candidates:
            st.warning("Geen orde gevonden. Probeer een andere naam.")

selected_order = None
if st.session_state.order_candidates:
    selected_order = st.selectbox(
        "Orde",
        [None] + st.session_state.order_candidates,
        key="selected_order_option",
        format_func=lambda item: "Kies een orde…" if item is None else item["label"],
    )

settings_a, settings_b, settings_c = st.columns(3)
with settings_a:
    distance_km = st.radio(
        "2. Zoekafstand rondom het gebied",
        [0, 100, 1000],
        index=0,
        format_func=lambda value: f"{value} km",
        horizontal=True,
    )
with settings_b:
    current_year = date.today().year
    year_range = st.slider(
        "3. Waarnemingsjaren",
        2008,
        current_year,
        (max(2008, current_year - 9), current_year),
    )
with settings_c:
    comparison_limit = st.selectbox(
        "4. Maximum vergelijkingsset",
        [100, 250, 500],
        index=1,
        help="Een begrenzing voorkomt onnodige API- en fotobelasting.",
    )

active_area = st.session_state.active_area
has_area = bool(active_area and active_area in st.session_state.areas)
if has_area:
    target_geometry = normalize_geometry_longitudes(st.session_state.areas[active_area])
    search_geometry = buffer_geometry_km(target_geometry, distance_km)
    with st.expander("Gebied en zoekzone bekijken", expanded=False):
        preview_map(target_geometry, search_geometry, distance_km)

can_search = bool(has_area and selected_order)
if not can_search:
    st.caption("Selecteer een gebied en een orde om te kunnen zoeken.")

if st.button("🔎 Vergelijkbare waarnemingen zoeken", type="primary", disabled=not can_search):
    clear_results()
    target_geometry = normalize_geometry_longitudes(st.session_state.areas[active_area])
    search_geometry = buffer_geometry_km(target_geometry, distance_km)
    base_params = {
        "d1": f"{year_range[0]}-01-01",
        "d2": f"{year_range[1]}-12-31",
        "geo": "true",
        "photos": "true",
        "taxon_id": int(selected_order["id"]),
        "locale": "en",
        "order_by": "observed_on",
        "order": "desc",
    }
    base_tuple = tuple(sorted(base_params.items()))
    raw_limit = max(1200, comparison_limit * 4)

    try:
        with st.status("Waarnemingen verzamelen…", expanded=True) as status:
            st.write("Waarnemingen in het geselecteerde gebied ophalen…")
            target_raw, target_api_total, target_api_limited = fetch_observations(
                base_tuple, bounds_for_api(target_geometry), raw_limit
            )
            focal, focal_limited = eligible_observations(
                target_raw, target_geometry, MAX_FOCAL_OBSERVATIONS
            )

            if distance_km == 0:
                comparison_raw = target_raw
                comparison_api_total = target_api_total
                comparison_api_limited = target_api_limited
            else:
                st.write(f"Vergelijkingswaarnemingen binnen {distance_km} km ophalen…")
                comparison_raw, comparison_api_total, comparison_api_limited = fetch_observations(
                    base_tuple, bounds_for_api(search_geometry), raw_limit
                )
            comparison, comparison_limited = eligible_observations(
                comparison_raw, search_geometry, comparison_limit
            )

            all_observations = {
                int(observation["id"]): observation
                for observation in [*comparison, *focal]
            }
            if not focal:
                status.update(label="Geen geschikte waarnemingen gevonden", state="error")
                st.warning(
                    "Binnen het geselecteerde gebied zijn geen waarnemingen met foto gevonden "
                    "die tot deze orde behoren maar nog niet tot soort zijn geïdentificeerd."
                )
            elif len(all_observations) < 2:
                status.update(label="Te weinig waarnemingen gevonden", state="error")
                st.warning("Er zijn minstens twee geschikte waarnemingen nodig om te vergelijken.")
            else:
                st.write(f"Beeldkenmerken voor {len(all_observations):,} waarnemingen berekenen…")
                vectors = observation_embeddings(list(all_observations.values()))
                if len(vectors) < 2:
                    status.update(label="Foto’s konden niet worden verwerkt", state="error")
                    st.warning("Van minder dan twee waarnemingen kon een foto worden verwerkt.")
                else:
                    st.session_state.focal_observations = focal
                    st.session_state.comparison_observations = comparison
                    st.session_state.comparison_vectors = vectors
                    st.session_state.search_meta = {
                        "area": active_area,
                        "order": selected_order,
                        "distance_km": distance_km,
                        "years": year_range,
                        "comparison_limit": comparison_limit,
                        "target_api_total": target_api_total,
                        "comparison_api_total": comparison_api_total,
                        "limited": any((
                            target_api_limited, comparison_api_limited,
                            focal_limited, comparison_limited,
                        )),
                    }
                    status.update(label="Visuele vergelijking gereed", state="complete")
    except Exception as exc:
        st.error(f"De vergelijking kon niet worden uitgevoerd: {exc}")

focal_observations = st.session_state.focal_observations
comparison_observations = st.session_state.comparison_observations
vectors = st.session_state.comparison_vectors

if focal_observations and comparison_observations and vectors:
    meta = st.session_state.search_meta
    current_order_id = int(selected_order["id"]) if selected_order else None
    result_order_id = int((meta.get("order") or {}).get("id")) if meta.get("order") else None
    results_are_current = (
        meta.get("area") == active_area
        and result_order_id == current_order_id
        and meta.get("distance_km") == distance_km
        and tuple(meta.get("years") or ()) == tuple(year_range)
        and meta.get("comparison_limit") == comparison_limit
    )
    st.divider()
    st.subheader("Vergelijkingsresultaten")
    if not results_are_current:
        st.warning(
            "De zoekinstellingen zijn gewijzigd. Start de zoekopdracht opnieuw om de resultaten bij te werken."
        )
    metric_a, metric_b, metric_c = st.columns(3)
    metric_a.metric("Te onderzoeken", len(focal_observations))
    metric_b.metric("Vergelijkingsset", len(comparison_observations))
    metric_c.metric("Zoekafstand", f"{meta.get('distance_km', 0)} km")
    if meta.get("limited"):
        st.info(
            "De zoekset is begrensd om iNaturalist en de fotoservers niet onnodig te belasten. "
            "De nieuwste passende waarnemingen zijn gebruikt."
        )

    embeddable_focal = [
        observation for observation in focal_observations if int(observation["id"]) in vectors
    ]
    if not embeddable_focal:
        st.warning("De foto’s van de gevonden waarnemingen konden niet worden verwerkt.")
    else:
        selected_focal = st.selectbox(
            "Kies een waarneming om te vergelijken",
            [None] + embeddable_focal,
            index=0,
            format_func=lambda observation: (
                "Selecteer zelf een waarneming…"
                if observation is None
                else observation_title(observation)
            ),
        )
        if selected_focal is None:
            st.info(
                "Er is nog geen waarneming gekozen. Open het menu hierboven en selecteer "
                "de waarneming waarvoor je mogelijke overeenkomsten wilt zien."
            )
        else:
            filter_col, count_col = st.columns(2)
            with filter_col:
                minimum_score = st.select_slider(
                    "Minimum overeenkomstsscore",
                    options=list(range(50, 100, 5)),
                    value=80,
                    help=(
                        "Dit is een modelschaal van 0–100, geen waarschijnlijkheidspercentage. "
                        "Een hogere grens toont minder en strengere resultaten."
                    ),
                )
            with count_col:
                top_k = st.slider("Maximum aantal overeenkomsten", 3, 20, 8)

            focal_id = int(selected_focal["id"])
            focal_vector = vectors[focal_id]
            scored = []
            for candidate in comparison_observations:
                candidate_id = int(candidate["id"])
                if candidate_id == focal_id or candidate_id not in vectors:
                    continue
                cosine = float(np.dot(focal_vector, vectors[candidate_id]))
                score = max(0.0, cosine) * 100.0
                if score >= minimum_score:
                    scored.append((score, candidate))
            scored.sort(key=lambda item: item[0], reverse=True)
            matches = scored[:top_k]

            focal_col, explanation_col = st.columns([1, 2])
            with focal_col:
                st.markdown("### Gekozen waarneming")
                render_observation(selected_focal)
            with explanation_col:
                st.markdown("### Mogelijke overeenkomsten")
                st.caption(
                    "Alleen resultaten met minimaal de ingestelde overeenkomstsscore worden getoond. "
                    "De score is geen kansberekening en geen identificatie. Controleer vormkenmerken, "
                    "levensstadium, achtergrond en fotokwaliteit altijd zelf."
                )

            if not matches:
                st.info(
                    f"Geen andere waarneming behaalt de minimumscore van {minimum_score}. "
                    "Dat betekent dat het model binnen deze vergelijkingsset geen sterke visuele "
                    "overeenkomst heeft gevonden. Je kunt de grens desgewenst verlagen."
                )
            else:
                for start in range(0, len(matches), 4):
                    columns = st.columns(4)
                    for column, (score, candidate) in zip(columns, matches[start:start + 4]):
                        with column:
                            render_observation(candidate, score=score)

                export_rows = [{
                    "bron_waarneming": focal_id,
                    "vergelijkbare_waarneming": int(candidate["id"]),
                    "overeenkomstsscore": round(score, 3),
                    "minimumscore": minimum_score,
                    "datum": candidate.get("observed_on") or "",
                    "huidige_identificatie": (candidate.get("taxon") or {}).get("name") or "",
                    "url": candidate.get("uri") or "",
                } for score, candidate in matches]
                st.download_button(
                    "⬇️ Overeenkomsten downloaden als CSV",
                    pd.DataFrame(export_rows).to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"overeenkomsten_{focal_id}.csv",
                    mime="text/csv",
                )

st.divider()
st.caption(
    "Prototype 0.2 · openbare gegevens van iNaturalist · foto’s worden alleen gebruikt om "
    "tijdelijke beeldkenmerken te berekenen; de toepassing stelt geen soortnamen voor."
)
