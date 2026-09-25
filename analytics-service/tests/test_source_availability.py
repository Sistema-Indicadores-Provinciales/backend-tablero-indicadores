import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import jwt
import mongomock
from fastapi.testclient import TestClient
from app import generator as api
from main import app


class SourceAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.uploads, self.system = root / 'uploads', root / 'system'
        self.uploads.mkdir()
        self.system.mkdir()
        self.db = mongomock.MongoClient().test
        for mock in [patch.object(api, 'UPLOADS', self.uploads), patch.object(api, 'SYSTEM', self.system),
                     patch.object(api, 'db', return_value=self.db),
                     patch.dict(os.environ, {'JWT_SECRET': 'availability-test-secret-at-least-32-characters'})]:
            mock.start()
            self.addCleanup(mock.stop)
        token = jwt.encode({'sub': 'owner', 'exp': time.time() + 600, 'tokenUse': 'access'},
                           os.environ['JWT_SECRET'], algorithm='HS256')
        self.headers = {'Authorization': 'Bearer ' + token}
        self.client = TestClient(app, raise_server_exceptions=False)

    def register(self, source_id, **overrides):
        doc = {'_id': source_id, 'name': source_id, 'owner': 'owner', 'kind': 'upload', 'path': source_id + '.csv', **overrides}
        self.db.analytics_sources.insert_one(doc)
        return doc

    def listed(self):
        response = self.client.get('/v2/sources', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        for doc in response.json():
            self.assertNotIn('path', doc)
            self.assertNotIn('owner', doc)
        return response.json()

    def test_lists_existing_files_and_google_without_exposing_other_users(self):
        self.register('available')
        self.register('missing')
        self.register('old-system', kind='system')
        self.register('other', owner='someone-else')
        self.register('private-sheet', kind='google', spreadsheet_id='a' * 30)
        self.register('public-sheet', kind='google', access_mode='public')
        self.register('other-sheet', kind='google', owner='someone-else')
        for source_id in ['available', 'other']:
            (self.uploads / (source_id + '.csv')).write_text('x,y\na,0\n')
        (self.system / 'bundled.csv').write_text('x,y\na,0\n')
        with patch.object(api, 'google_get') as remote, patch.object(api, 'read_public_rows') as public:
            listed = self.listed()
            remote.assert_not_called()
            public.assert_not_called()
        self.assertEqual({d['name'] for d in listed}, {'available', 'private-sheet', 'public-sheet', 'bundled.csv'})

    def test_missing_files_can_return_without_losing_metadata_or_workspaces(self):
        doc = self.register('recoverable')
        workspace = {'_id': 'saved', 'owner': 'owner', 'source_id': 'recoverable', 'widgets': [{'id': 'chart'}]}
        self.db.analytics_workspaces.insert_one(workspace)
        self.assertEqual(self.listed(), [])
        self.assertEqual(self.db.analytics_sources.find_one({'_id': doc['_id']}), doc)
        self.assertEqual(self.db.analytics_workspaces.find_one({'_id': 'saved'}), workspace)
        path = self.uploads / doc['path']
        path.write_text('x,y\na,0\n')
        self.assertEqual(self.listed()[0]['_id'], 'recoverable')
        path.unlink()
        self.assertEqual(self.listed(), [])
        self.assertEqual(self.client.get('/v2/sources/recoverable/sheets', headers=self.headers).status_code, 404)

    def test_bad_paths_do_not_break_the_list_or_escape_storage(self):
        outside = self.uploads.parent / 'outside.csv'
        outside.write_text('x,y\na,0\n')
        for index, path in enumerate([None, '', '../outside.csv', str(outside), 123, 'folder', 'invalid\x00.csv']):
            with self.subTest(path=path):
                self.register(str(index), path=path)
        self.db.analytics_sources.insert_one({'_id': 'no-path', 'kind': 'upload', 'owner': 'owner'})
        (self.uploads / 'folder').mkdir()
        self.assertEqual(self.listed(), [])
        self.assertEqual(self.db.analytics_sources.count_documents({}), 8)

    def test_permission_failure_is_unavailable_and_does_not_remove_metadata(self):
        self.register('unreadable')
        (self.uploads / 'unreadable.csv').write_text('x,y\na,0\n')
        with patch.object(Path, 'open', side_effect=PermissionError):
            self.assertEqual(self.listed(), [])
        self.assertEqual(len(self.listed()), 1)

    def test_missing_entries_do_not_use_up_the_visible_list_limit(self):
        for index in range(501):
            self.register(str(index))
        self.register('last')
        (self.uploads / 'last.csv').write_text('x,y\na,0\n')
        self.assertEqual([d['_id'] for d in self.listed()], ['last'])

    def test_uploaded_replacement_is_listed_and_can_generate_a_chart(self):
        self.register('old')
        response = self.client.post('/v2/sources/upload', headers=self.headers,
                                    files={'file': ('recovered.csv', b'x,y\na,0\nb,4\n', 'text/csv')})
        self.assertEqual(response.status_code, 200, response.text)
        source_id = response.json()['_id']
        self.assertEqual([d['_id'] for d in self.listed()], [source_id])
        chart = self.client.post(f'/v2/sources/{source_id}/chart', headers=self.headers,
                                json={'sheet': 'Datos', 'x_col': 'x', 'y_col': 'y', 'aggregation': 'sum'})
        self.assertEqual(chart.status_code, 200, chart.text)
        self.assertEqual(chart.json()['datasets'][0]['data'], [0, 4])
