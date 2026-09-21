"""Pure geometry and observation helpers for the similarity explorer."""

from __future__ import annotations

from pyproj import CRS, Transformer
from shapely.geometry import Point, mapping, shape
from shapely.ops import transform


SPECIES_OR_LOWER_RANKS = {
    "species", "subspecies", "variety", "subvariety", "form", "hybrid", "infrahybrid",
}


def is_not_identified_to_species(observation: dict) -> bool:
    """Return True when an observation has a coarser taxon than species."""
    taxon = observation.get("taxon") or {}
    return bool(taxon.get("id")) and taxon.get("rank") not in SPECIES_OR_LOWER_RANKS


def point_from_observation(observation: dict) -> Point | None:
    geojson = observation.get("geojson") or {}
    coordinates = geojson.get("coordinates") or []
    if len(coordinates) < 2:
        return None
    try:
        return Point(float(coordinates[0]), float(coordinates[1]))
    except (TypeError, ValueError):
        return None


def observations_in_geometry(observations: list[dict], geometry: dict) -> list[dict]:
    """Deduplicate observations and retain points covered by the geometry."""
    polygon = shape(geometry)
    inside: dict[int, dict] = {}
    for observation in observations:
        point = point_from_observation(observation)
        observation_id = observation.get("id")
        if point is None or observation_id is None or not polygon.covers(point):
            continue
        inside[int(observation_id)] = observation
    return list(inside.values())


def buffer_geometry_km(geometry: dict, distance_km: int | float) -> dict:
    """Create a metre-accurate local buffer and return WGS84 GeoJSON geometry."""
    distance_km = float(distance_km)
    if distance_km <= 0:
        return geometry

    source = shape(geometry)
    center = source.centroid
    local_crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={center.y} +lon_0={center.x} +datum=WGS84 +units=m +no_defs"
    )
    forward = Transformer.from_crs("EPSG:4326", local_crs, always_xy=True).transform
    backward = Transformer.from_crs(local_crs, "EPSG:4326", always_xy=True).transform
    buffered = transform(backward, transform(forward, source).buffer(distance_km * 1000))
    return mapping(buffered)


def bounds_for_api(geometry: dict) -> tuple[float, float, float, float]:
    """Return API bbox in south, west, north, east order."""
    west, south, east, north = shape(geometry).bounds
    return south, west, north, east

