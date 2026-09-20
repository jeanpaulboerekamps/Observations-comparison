from shapely.geometry import Polygon, mapping, shape

from core import buffer_geometry_km, is_not_identified_to_species, observations_in_geometry


def test_rank_filter_excludes_species_and_lower():
    assert is_not_identified_to_species({"taxon": {"id": 1, "rank": "genus"}})
    assert is_not_identified_to_species({"taxon": {"id": 1, "rank": "family"}})
    assert not is_not_identified_to_species({"taxon": {"id": 1, "rank": "species"}})
    assert not is_not_identified_to_species({"taxon": {"id": 1, "rank": "subspecies"}})
    assert not is_not_identified_to_species({"taxon": {}})


def test_polygon_filter_is_exact_and_deduplicates():
    geometry = mapping(Polygon([(4, 52), (5, 52), (5, 53), (4, 53)]))
    observations = [
        {"id": 10, "geojson": {"coordinates": [4.5, 52.5]}},
        {"id": 10, "geojson": {"coordinates": [4.5, 52.5]}},
        {"id": 11, "geojson": {"coordinates": [5.5, 52.5]}},
    ]
    assert [item["id"] for item in observations_in_geometry(observations, geometry)] == [10]


def test_zero_buffer_returns_original_geometry():
    geometry = mapping(Polygon([(4, 52), (5, 52), (5, 53), (4, 53)]))
    assert buffer_geometry_km(geometry, 0) is geometry


def test_positive_buffer_expands_bounds():
    geometry = mapping(Polygon([(4, 52), (5, 52), (5, 53), (4, 53)]))
    original_bounds = shape(geometry).bounds
    buffered_bounds = shape(buffer_geometry_km(geometry, 100)).bounds
    assert buffered_bounds[0] < original_bounds[0]
    assert buffered_bounds[1] < original_bounds[1]
    assert buffered_bounds[2] > original_bounds[2]
    assert buffered_bounds[3] > original_bounds[3]
