import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from app import formosa_boundary as boundary
from app import generator as api


POLYGON = {
    "type": "FeatureCollection",
    "features": [{
        "type": "Feature",
        "properties": {"id": "34", "nombre": "Formosa"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-61.0, -26.0], [-58.0, -26.0], [-58.0, -23.0], [-61.0, -26.0]]],
        },
    }],
}
POINT = {
    "type": "FeatureCollection",
    "features": [{
        "type": "Feature",
        "properties": {"id": "34", "nombre": "Formosa"},
        "geometry": {"type": "Point", "coordinates": [-59.93, -24.89]},
    }],
}


class MemoryCollection:
    def __init__(self):
        self.document = None

    def find_one(self, query):
        if self.document and self.document.get("_id") == query.get("_id"):
            return deepcopy(self.document)
        return None

    def insert_one(self, document):
        self.document = deepcopy(document)

    def replace_one(self, query, document, upsert=False):
        self.document = deepcopy(document)


class MemoryDatabase:
    def __init__(self):
        self.analytics_geodata_cache = MemoryCollection()


class BoundaryServiceTests(unittest.TestCase):
    def setUp(self):
        self.db = MemoryDatabase()
        self.now = datetime(2026, 10, 19, tzinfo=timezone.utc)

    def test_point_is_rejected_as_provincial_boundary(self):
        with self.assertRaises(boundary.BoundarySourceError):
            boundary._boundary(POINT, "georef")

    def test_ign_is_primary_and_success_is_persisted(self):
        with patch.object(boundary, "_download_json", return_value=POLYGON) as download:
            result = boundary.load_formosa_boundary(self.db, self.now)
        self.assertEqual(result["features"][0]["geometry"]["type"], "Polygon")
        self.assertEqual(result["features"][0]["properties"]["fuente"], "ign")
        download.assert_called_once_with(boundary.IGN_URL)
        saved = self.db.analytics_geodata_cache.find_one({"_id": boundary.CACHE_ID})
        self.assertEqual(saved["source"], "ign")

    def test_georef_complete_download_is_used_after_ign_failure(self):
        with patch.object(boundary, "_download_json", side_effect=[boundary.BoundarySourceError("offline"), POLYGON]) as download:
            result = boundary.load_formosa_boundary(self.db, self.now)
        self.assertEqual(result["features"][0]["properties"]["fuente"], "georef")
        self.assertEqual([call.args[0] for call in download.call_args_list], [boundary.IGN_URL, boundary.GEOREF_URL])

    def test_fresh_cache_avoids_external_requests(self):
        cached = boundary._boundary(POLYGON, "ign")
        self.db.analytics_geodata_cache.insert_one({
            "_id": boundary.CACHE_ID,
            "geojson": cached,
            "source": "ign",
            "updated_at": self.now - timedelta(days=1),
        })
        with patch.object(boundary, "_download_json") as download:
            result = boundary.load_formosa_boundary(self.db, self.now)
        self.assertEqual(result["features"][0]["geometry"]["type"], "Polygon")
        download.assert_not_called()

    def test_stale_cache_is_returned_when_both_sources_fail(self):
        cached = boundary._boundary(POLYGON, "ign")
        self.db.analytics_geodata_cache.insert_one({
            "_id": boundary.CACHE_ID,
            "geojson": cached,
            "source": "ign",
            "updated_at": self.now - timedelta(days=30),
        })
        with patch.object(boundary, "_download_json", side_effect=boundary.BoundarySourceError("offline")) as download:
            result = boundary.load_formosa_boundary(self.db, self.now)
        self.assertEqual(result, cached)
        self.assertEqual(download.call_count, 2)

    def test_unavailable_sources_without_cache_return_502(self):
        with patch.object(boundary, "_download_json", side_effect=boundary.BoundarySourceError("offline")):
            with self.assertRaises(HTTPException) as error:
                boundary.load_formosa_boundary(self.db, self.now)
        self.assertEqual(error.exception.status_code, 502)

    def test_untrusted_redirect_is_not_followed(self):
        requests = []
        client_class = httpx.Client

        def handler(request):
            requests.append(request)
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})

        def client(**options):
            return client_class(transport=httpx.MockTransport(handler), **options)

        with patch.object(boundary.httpx, "Client", side_effect=client), self.assertRaises(boundary.BoundarySourceError):
            boundary._download_json(boundary.GEOREF_URL)
        self.assertEqual(len(requests), 1)


class BoundaryEndpointTests(unittest.TestCase):
    def test_endpoint_returns_the_validated_service_result(self):
        database = MemoryDatabase()
        expected = boundary._boundary(POLYGON, "ign")
        with patch.object(api, "db", return_value=database), patch.object(api, "load_formosa_boundary", return_value=expected):
            self.assertEqual(api.formosa_boundary("user-a"), expected)


if __name__ == "__main__":
    unittest.main()
