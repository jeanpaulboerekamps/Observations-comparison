import os
import unittest
from datetime import date
from unittest.mock import Mock, patch

from dispatch import dispatch_index
from indexer import comment_links, compact, links_from_comments, main


class IndexScopeTests(unittest.TestCase):
    def test_compact_keeps_both_roles_and_existing_comment_links(self):
        base = {"id": 123, "photos": [{"url": "https://example.org/square.jpg"}],
                "taxon": {"id": 1, "rank": "family", "name": "X"}}
        self.assertTrue(compact(base, [456])["needs_species_id"])
        self.assertEqual(compact(base, [456])["comment_links"], [456])
        base["taxon"]["rank"] = "species"
        base["quality_grade"] = "research"
        self.assertFalse(compact(base)["needs_species_id"])
        self.assertEqual(compact(base)["quality_grade"], "research")

    @patch("indexer.embed")
    @patch("indexer.comment_links")
    @patch("indexer.pages")
    @patch("indexer.supabase")
    @patch("indexer.read_area_input")
    def test_full_order_includes_research_grade(self, read_area, database, pages, links, embed):
        read_area.return_value = {"type": "Polygon", "coordinates": [
            [[0, 0], [2, 0], [2, 2], [0, 0]]]}
        records = []
        def db(table, method, payload=None, params=None):
            if table == "index_coverage" and method == "GET":
                return []
            if table == "index_coverage" and method == "POST":
                return [{"id": 1}]
            if table == "index_observations" and method == "GET":
                return []
            if table == "index_observations" and method == "POST":
                records.extend(payload)
                return []
            return []
        database.side_effect = db
        rows = [{"id": i, "geojson": {"coordinates": [1, 1]},
                 "observed_on": "2025-01-01", "photos": [{"url": "a.jpg"}],
                 "taxon": {"id": 1, "rank": rank}, "quality_grade": grade}
                for i, rank, grade in ((1, "family", "needs_id"),
                                       (2, "species", "research"))]
        pages.return_value = iter([(rows, 2)])
        links.return_value = {}
        embed.side_effect = lambda items: ((item, Mock(tolist=lambda: [0.1, 0.2])) for item in items)
        with patch("sys.argv", ["indexer", "--area-json-env", "AREA_GEOJSON",
                                "--area-label", "test", "--order-id", "1",
                                "--order-name", "test", "--start", "2025-01-01",
                                "--end", "2025-12-31"]), patch.dict(os.environ, {"AREA_GEOJSON": "{}"}):
            main()
        self.assertEqual({row["id"] for row in records}, {1, 2})
        self.assertFalse(records[1]["observation"]["needs_species_id"])

    def test_comment_links_use_exact_observation_ids(self):
        links = links_from_comments([
            {"body": "Related observation: https://www.inaturalist.org/observations/456"},
            {"body": "See https://inaturalist.org/observations/789 and /observations/45"},
        ])
        self.assertEqual(links, [456, 789])

    @patch("indexer.inat_details")
    def test_comment_details_are_fetched_in_batches(self, details):
        details.side_effect = lambda ids: [{"id": i, "comments": [
            {"body": "https://www.inaturalist.org/observations/555"}]} for i in ids]
        items = [{"id": i, "comments_count": 1} for i in range(101)]
        result = comment_links(items)
        self.assertEqual(details.call_count, 3)
        self.assertEqual(result[100], [555])

    @patch("dispatch.requests.post")
    def test_app_dispatches_geometry_as_workflow_input(self, post):
        post.return_value = Mock(status_code=204)
        polygon = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
        dispatch_index("dummy", polygon, 47157, "Lepidoptera", date(2025, 1, 1), date(2025, 12, 31))
        inputs = post.call_args.kwargs["json"]["inputs"]
        self.assertIn('"type":"Polygon"', inputs["area_geojson"])
        self.assertEqual(inputs["order_id"], "47157")


if __name__ == "__main__":
    unittest.main()
