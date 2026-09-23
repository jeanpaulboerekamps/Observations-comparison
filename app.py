from __future__ import annotations

from datetime import date
import base64
import html
import json
import hmac
import zlib

import folium
import numpy as np
import pandas as pd
import streamlit as st
from folium.plugins import Draw
from shapely.geometry import shape
from streamlit_folium import st_folium

from core import buffer_geometry_km
from dispatch import dispatch_index
from review import (CHOICES, NO_COMMENT_CHOICES, authorization_url, comment_body, exchange_code,
                    load_reviews, manual_review_record, pair_ids, publish_review, save_review)

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
        "manual_reviews": {},
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


def save_manual_choice(database_url, review_key, persistent, ids, choice, status):
    record = manual_review_record(*ids, choice, status)
    if persistent:
        record = save_review(database_url, review_key, record)
    else:
        st.session_state.manual_reviews[ids] = record
    return record


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

st.markdown('<span class="release-badge">Prototype 0.6.4 · actuele kruisverwijzingen</span>', unsafe_allow_html=True)
st.title("🔎 Waarnemingen Gelijkeniszoeker")
st.markdown(
    '<div class="intro"><b>Vind waarnemingen die mogelijk van dezelfde soort zijn.</b><br>'
    'De toepassing rangschikt foto’s op visuele overeenkomst, maar stelt geen soortnaam voor.</div>',
    unsafe_allow_html=True,
)

# iNaturalist authorization is per browser session; the public app never posts
# as the owner until their own iNaturalist account has authorized it.
oauth_id = st.secrets.get("INAT_CLIENT_ID", "")
oauth_secret = st.secrets.get("INAT_CLIENT_SECRET", "")
oauth_redirect = st.secrets.get("INAT_REDIRECT_URI", "")
reviewer_login = st.secrets.get("REVIEWER_INAT_LOGIN", "")
review_key = st.secrets.get("SUPABASE_SECRET_KEY", "")
review_ready = all((oauth_id, oauth_secret, oauth_redirect, reviewer_login, review_key))
if review_ready:
    if "code" in st.query_params and "state" in st.query_params:
        try:
            token, user, saved_area = exchange_code(
                oauth_id, oauth_secret, oauth_redirect,
                st.query_params["state"], st.query_params["code"],
            )
            if user.get("login", "").lower() != reviewer_login.lower():
                raise ValueError("Dit iNaturalist-account is niet ingesteld als beoordelaar.")
            st.session_state.inat_access_token = token
            st.session_state.inat_user = user
            st.query_params.clear()
            if saved_area:
                st.query_params["gebied"] = saved_area
                restore_remembered_area()
            st.success(f"Aangemeld bij iNaturalist als {user['login']}.")
        except Exception as exc:
            st.query_params.clear()
            st.error(f"Aanmelden bij iNaturalist mislukte: {exc}")
    if st.session_state.get("inat_access_token"):
        login_col, logout_col = st.columns([3, 1])
        login_col.success(f"Beoordelaar: {st.session_state.inat_user['login']}")
        if logout_col.button("Afmelden", key="inat_logout"):
            st.session_state.pop("inat_access_token", None)
            st.session_state.pop("inat_user", None)
            st.rerun()
    else:
        st.link_button("🔐 Aanmelden met iNaturalist om paren te beoordelen",
                       authorization_url(oauth_id, oauth_secret, oauth_redirect,
                                         st.query_params.get("gebied", "")))
else:
    st.caption("Beoordeel en plaats de opmerkingen handmatig. Rechtstreeks plaatsen wordt pas mogelijk met iNaturalist API-toegang.")

manual_password = st.secrets.get("REVIEW_PASSPHRASE", "")
manual_persistent = bool(review_key and manual_password and len(manual_password) >= 16)
manual_access = not manual_persistent
if manual_persistent:
    if st.session_state.get("manual_access"):
        manual_access = True
        st.success("Handmatige beoordeling ontgrendeld; je keuzes worden bewaard.")
        if st.button("Beoordeling vergrendelen"):
            st.session_state.manual_access = False
            st.rerun()
    else:
        with st.form("manual_login"):
            entered = st.text_input("Toegangscode voor de handmatige beoordeling", type="password")
            submitted = st.form_submit_button("Beoordeling ontgrendelen")
        if submitted:
            if hmac.compare_digest(entered, manual_password):
                st.session_state.manual_access = True
                st.rerun()
            else:
                st.error("De toegangscode klopt niet.")
else:
    st.info("Je kunt de handmatige werkwijze alvast uitproberen. Zonder opslaginstelling "
            "blijven keuzes alleen bewaard zolang dit app-tabblad open is; download je voortgang als CSV.")

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
from indexed import (MODEL_VERSION, already_linked_pair, coverage as load_coverage,
                     current_comment_links, currently_linked_pair,
                     observations as load_indexed, parse_embedding)
from shapely.geometry import Point

def settings():
    return st.secrets.get("SUPABASE_URL", ""), st.secrets.get("SUPABASE_PUBLISHABLE_KEY", "")

try:
    database_url, publishable_key = settings()
    if not database_url or not publishable_key:
        st.error("Supabase is nog niet ingesteld. Voeg SUPABASE_URL en SUPABASE_PUBLISHABLE_KEY toe aan de Streamlit-secrets.")
        st.stop()
    coverages = load_coverage(database_url, publishable_key)
except Exception as exc:
    st.error(f"De opgeslagen index kon niet worden gelezen: {exc}")
    st.stop()

if not coverages:
    st.info("Er is nog geen volledige index beschikbaar. De beheerder kan eerst een gebied en orde indexeren.")
    st.stop()

orders = {int(item["order_id"]): item["order_name"] for item in coverages}
order_id = st.selectbox("1. Orde (verplicht)", sorted(orders), format_func=lambda value: orders[value])
distance_km = st.radio("2. Zoekafstand rondom het geselecteerde gebied", [0, 100, 1000],
                       index=0, format_func=lambda n: f"{n} km", horizontal=True)
available = [item for item in coverages if int(item["order_id"]) == order_id
             and item["model_version"] == MODEL_VERSION]
all_for_order = [item for item in coverages if int(item["order_id"]) == order_id]
earliest = min(date.fromisoformat(item["first_date"]) for item in all_for_order)
latest = max(date.fromisoformat(item["last_date"]) for item in all_for_order)
chosen_dates = st.date_input("3. Waarnemingsperiode", (earliest, latest),
                             min_value=earliest, max_value=latest)
if len(chosen_dates) != 2:
    st.info("Kies ook een einddatum voor de periode.")
    st.stop()
start, end = chosen_dates
threshold = st.radio("Minimum visuele score", [80, 90], horizontal=True,
                     format_func=lambda n: f"{n} of hoger",
                     help="Dit is een modelschaal; de score is geen kanspercentage.")

active_area = st.session_state.active_area
has_area = bool(active_area and active_area in st.session_state.areas)
if has_area:
    target_geometry = normalize_geometry_longitudes(st.session_state.areas[active_area])
    search_geometry = buffer_geometry_km(target_geometry, distance_km)
    with st.expander("Gebied en zoekzone bekijken", expanded=False):
        preview_map(target_geometry, search_geometry, distance_km)
else:
    st.info("Selecteer of teken eerst een gebied.")

matching = ([item for item in available
             if date.fromisoformat(item["first_date"]) <= start
             and date.fromisoformat(item["last_date"]) >= end
             and shape(item["geometry"]).covers(shape(search_geometry))] if has_area else [])
if has_area and not matching:
    st.warning("Voor dit gebied, de zoekafstand en periode is nog geen volledige index beschikbaar. "
               "Een eerdere index met alleen ongedetermineerde waarnemingen moet opnieuw worden opgebouwd.")
if has_area:
    github_token = st.secrets.get("GITHUB_DISPATCH_TOKEN", "")
    with st.expander("Index beheren", expanded=not bool(matching)):
        if github_token and manual_persistent and manual_access:
            dispatch_signature = (active_area, order_id, distance_km, start, end)
            previously_started = st.session_state.get("dispatched_index") == dispatch_signature
            if previously_started:
                st.info("De indexeeractie is gestart. Ververs de pagina wanneer de actie klaar is.")
            if matching:
                st.caption("Een nieuwe indexeeractie haalt ook nieuwe opmerkingen op, "
                           "en gebruikt opgeslagen beeldkenmerken opnieuw als de foto ongewijzigd is.")
            if st.button("🗂️ Index voor dit gebied en deze orde " +
                         ("vernieuwen" if matching else "opbouwen"),
                         disabled=previously_started):
                try:
                    run_url = dispatch_index(github_token, search_geometry, order_id,
                                             orders[order_id], start, end)
                    st.session_state.dispatched_index = dispatch_signature
                    st.success("De indexeeractie is gestart; je kunt de app later verversen.")
                    st.link_button("Voortgang bekijken", run_url)
                except Exception as exc:
                    st.error(f"De indexeeractie kon niet worden gestart: {exc}")
        elif github_token and manual_persistent:
            st.info("Ontgrendel hierboven de beoordeling om een indexeeractie te starten.")
        else:
            st.info("De beheerder moet eenmalig GITHUB_DISPATCH_TOKEN en REVIEW_PASSPHRASE "
                    "instellen in de Streamlit-secrets om vanuit de app een index te starten.")

signature = (active_area, order_id, distance_km, start, end, threshold,
             tuple(sorted((row["id"], row["updated_at"]) for row in matching)))
if st.session_state.get("index_signature") != signature:
    st.session_state.pop("index_pairs", None)
    st.session_state.index_signature = signature
if st.button("🔎 Alle geïndexeerde waarnemingen vergelijken", type="primary",
             disabled=not matching):
    zone = shape(search_geometry)
    try:
        with st.status("Opgeslagen beeldkenmerken vergelijken…", expanded=True) as status:
            records = load_indexed(database_url, publishable_key, order_id,
                                   start.isoformat(), end.isoformat())
            target_shape = shape(target_geometry)
            candidates = {}
            for row in records:
                point = Point(row["longitude"], row["latitude"])
                if row["model_version"] == MODEL_VERSION and zone.covers(point):
                    candidates[int(row["id"])] = row
            targets = [row for row in candidates.values()
                       if row["observation"].get("needs_species_id")
                       and target_shape.covers(Point(row["longitude"], row["latitude"]))]
            others = list(candidates.values())
            st.write(f"{len(targets)} waarnemingen in het begingebied, "
                     f"{len(others)} in de vergelijkingszone.")
            if len(others) > 20000:
                st.error("Deze zoekset is te groot voor een volledige vergelijking op de huidige server. "
                         "Kies een kleiner gebied of kortere periode.")
                st.stop()
            pairs = []
            if targets and len(others) > 1:
                b = np.asarray([parse_embedding(row["embedding"]) for row in others], dtype=np.float32)
                a = np.asarray([parse_embedding(row["embedding"]) for row in targets], dtype=np.float32)
                b /= np.maximum(np.linalg.norm(b, axis=1, keepdims=True), 1e-12)
                a /= np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-12)
                seen = set()
                for offset in range(0, len(a), 128):
                    scores = (a[offset:offset + 128] @ b.T) * 100
                    for i, j in zip(*np.where(scores >= threshold)):
                        left = targets[offset + int(i)]
                        right = others[int(j)]
                        if left["id"] == right["id"]:
                            continue
                        if already_linked_pair(left, right):
                            continue
                        key = tuple(sorted((left["id"], right["id"])))
                        if key in seen:
                            continue
                        seen.add(key)
                        pairs.append((float(scores[i, j]), left["observation"], right["observation"]))
                pairs.sort(key=lambda item: item[0], reverse=True)
            if pairs:
                status.update(label="Bestaande kruisverwijzingen controleren…")
                pair_observation_ids = {
                    int(observation["id"])
                    for _, left, right in pairs
                    for observation in (left, right)
                }
                try:
                    fresh_links = current_comment_links(pair_observation_ids)
                    before = len(pairs)
                    pairs = [pair for pair in pairs
                             if not currently_linked_pair(pair[1], pair[2], fresh_links)]
                    removed = before - len(pairs)
                    if removed:
                        st.write(f"{removed} eerder gekoppelde paren overgeslagen.")
                except Exception as exc:
                    st.warning("De actuele opmerkingen konden niet volledig worden gecontroleerd. "
                               "De opgeslagen kruisverwijzingen zijn wel toegepast; probeer de "
                               f"vergelijking later nogmaals. ({exc})")
            st.session_state.index_pairs = pairs
            st.session_state.index_counts = (len(targets), len(others))
            status.update(label="Vergelijking gereed", state="complete")
    except Exception as exc:
        st.error(f"De vergelijking is mislukt: {exc}")

if "index_pairs" in st.session_state:
    pairs = st.session_state.index_pairs
    target_count, candidate_count = st.session_state.index_counts
    st.subheader("Vergelijkingsresultaten")
    a, b, c = st.columns(3)
    a.metric("In het begingebied", target_count)
    b.metric("In de vergelijkingszone", candidate_count)
    c.metric("Sterke waarnemingsparen", len(pairs))
    if not pairs:
        st.info("Er zijn geen paren boven deze drempel gevonden in de volledig geïndexeerde zoekset.")
    else:
        st.caption("De score meet visuele overeenkomst. Controleer soortkenmerken zelf; de app stelt geen soortnaam vast.")
        reviewer = st.session_state.get("inat_user") if review_ready else None
        review_enabled = bool(reviewer and st.session_state.get("inat_access_token"))
        manual_mode = bool(manual_access and not review_enabled)
        reviews = {}
        review_ids = list({pair_ids(left["id"], right["id"])
                           for _, left, right in pairs})
        if review_enabled:
            try:
                reviews = load_reviews(database_url, review_key, reviewer["id"], review_ids)
            except Exception as exc:
                st.error(f"Beoordelingen konden niet worden geladen: {exc}")
                review_enabled = False
        elif manual_mode:
            if manual_persistent:
                try:
                    reviews = load_reviews(database_url, review_key, 0, review_ids)
                except Exception as exc:
                    st.error(f"Handmatige beoordelingen konden niet worden geladen: {exc}")
                    manual_mode = False
            else:
                reviews = {ids: value for ids, value in st.session_state.manual_reviews.items()
                           if ids in review_ids}
        visible = [(score, left, right) for score, left, right in pairs
                   if reviews.get(pair_ids(left["id"], right["id"]), {}).get("status") != "complete"]
        finished_count = sum(row["status"] == "complete" for row in reviews.values())
        st.caption(f"{finished_count} afgerond · {len(visible)} nog te beoordelen")
        pages = max(1, (len(visible) + 9) // 10)
        page = st.number_input("Pagina (10 paren per pagina)", min_value=1,
                               max_value=pages, value=1, step=1)
        for n, (score, left, right) in enumerate(visible[(page - 1) * 10:page * 10],
                                                (page - 1) * 10 + 1):
            if int(left["id"]) > int(right["id"]):
                left, right = right, left  # Consistent order after a search setting changes.
            ids = pair_ids(left["id"], right["id"])
            prior = reviews.get(ids)
            st.markdown(f"### Paar {n} · score {score:.1f}")
            l, r = st.columns(2)
            with l:
                render_observation(left)
            with r:
                render_observation(right)
            if manual_mode and prior:
                st.info(f"Keuze: {CHOICES[prior['choice']][0]}")
                if prior["choice"] in NO_COMMENT_CHOICES:
                    st.caption("Afgerond; er zijn geen opmerkingen nodig.")
                elif prior["status"] == "complete":
                    st.caption("Je hebt aangegeven dat je beide opmerkingen hebt geplaatst.")
                else:
                    first = prior["status"] == "pending"
                    current, other = (left, right) if first else (right, left)
                    step = 1 if first else 2
                    st.markdown(f"**Stap {step} van 2 — waarneming #{current['id']}**")
                    st.caption("Tik in het tekstvak en kies Selecteer alles → Kopieer. "
                               "Open daarna de waarneming; iNaturalist gebruikt het account "
                               "waarmee je in deze browser bent aangemeld.")
                    st.text_area("Tekst voor iNaturalist", value=comment_body(prior["choice"], other["id"]),
                                 height=120, key=f"copy_text_{ids}_{step}")
                    st.link_button(f"Open waarneming #{current['id']} in nieuw tabblad", current["uri"])
                    if st.button(f"Ik heb de opmerking op #{current['id']} geplaatst",
                                 key=f"manual_next_{ids}_{step}"):
                        try:
                            save_manual_choice(database_url, review_key, manual_persistent,
                                               ids, prior["choice"],
                                               "uncertain" if first else "complete")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Voortgang kon niet worden opgeslagen: {exc}")
            elif manual_mode:
                with st.form(f"manual_{ids[0]}_{ids[1]}"):
                    choice = st.radio("Jouw beoordeling", list(CHOICES),
                                      format_func=lambda k: CHOICES[k][0],
                                      index=None, key=f"manual_choice_{ids}")
                    confirmed = st.form_submit_button("Keuze vastleggen")
                if confirmed and choice:
                    try:
                        save_manual_choice(database_url, review_key, manual_persistent,
                                           ids, choice, "complete" if choice in NO_COMMENT_CHOICES else "pending")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Keuze kon niet worden opgeslagen: {exc}")
                elif confirmed:
                    st.warning("Kies eerst één van de vijf beoordelingen.")
            elif prior:
                st.info(f"Beoordeling: {CHOICES[prior['choice']][0]} · "
                        f"{('beide opmerkingen geplaatst' if prior['status'] == 'complete' else 'controle nodig' if prior['status'] == 'uncertain' else 'nog niet volledig')}.")
            elif review_enabled:
                with st.form(f"review_{ids[0]}_{ids[1]}"):
                    choice = st.radio("Jouw beoordeling", list(CHOICES),
                                      format_func=lambda k: CHOICES[k][0],
                                      index=None, key=f"choice_{ids[0]}_{ids[1]}")
                    if choice in CHOICES and choice not in NO_COMMENT_CHOICES:
                        st.caption("Opmerking op beide waarnemingen:")
                        st.code(comment_body(choice, ids[1]) + "\n" +
                                comment_body(choice, ids[0]), language=None)
                    confirmed = st.form_submit_button(
                        "Beoordeling opslaan en opmerkingen plaatsen" if choice not in NO_COMMENT_CHOICES else "Beoordeling opslaan",
                    )
                if confirmed and choice:
                    try:
                        publish_review(database_url, review_key,
                                       st.session_state.inat_access_token, reviewer["id"],
                                       *ids, choice)
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Niet volledig verwerkt: {exc}. Controleer de opmerkingen op iNaturalist "
                                 "voordat je opnieuw probeert. De al geplaatste opmerking wordt niet herhaald.")
                elif confirmed:
                    st.warning("Kies eerst één van de vijf beoordelingen.")
            if prior and prior["status"] == "pending" and review_enabled:
                if st.button("Ontbrekende opmerking opnieuw proberen", key=f"retry_{ids}"):
                    try:
                        publish_review(database_url, review_key,
                                       st.session_state.inat_access_token, reviewer["id"],
                                       *ids, prior["choice"], prior)
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Niet volledig verwerkt: {exc}. Bekijk beide waarnemingen voordat je opnieuw probeert.")
            st.divider()
        export = [{"waarneming_1": left["id"], "waarneming_2": right["id"],
                   "score": round(score, 3), "url_1": left["uri"], "url_2": right["uri"],
                   "beoordeling": (reviews.get(pair_ids(left["id"], right["id"])) or {}).get("choice", ""),
                   "voortgang": (reviews.get(pair_ids(left["id"], right["id"])) or {}).get("status", "")}
                  for score, left, right in pairs]
        st.download_button("⬇️ Alle paren en voortgang downloaden als CSV",
                           pd.DataFrame(export).to_csv(index=False).encode("utf-8-sig"),
                           "overeenkomsten.csv", "text/csv")

st.divider()
st.caption("Prototype 0.6.4 · beeldvergelijking uit de index · actuele kruisverwijzingen worden voor sterke paren gecontroleerd.")
