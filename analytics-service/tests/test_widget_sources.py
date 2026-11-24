import copy
import unittest
from unittest.mock import patch

from app import generator as api
from app.data_engine import table
import test_workspace_access as fixtures


class WidgetSourceTests(unittest.TestCase):
    setUp = fixtures.WorkspaceAccessTests.setUp
    tearDown = fixtures.WorkspaceAccessTests.tearDown
    headers = fixtures.WorkspaceAccessTests.headers
    publish = fixtures.WorkspaceAccessTests.publish

    def configure(self):
        self.db.analytics_sources.insert_one({'_id': 'population', 'owner': str(self.owner),
                                             'kind': 'google', 'name': 'Población'})
        config = {'sheet': 'TABLA', 'chart_type': 'indicator', 'x_col': 'Año', 'y_col': 'Total'}
        widgets = [{'id': 'mortality', 'config': config},
                   {'id': 'population', 'source_id': 'population', 'config': config}]
        self.db.analytics_workspaces.update_one({}, {'$set': {'widgets': widgets, 'filter_columns': ['Año']}})
        self.publish([self.owner, self.viewer])
        return widgets

    def test_two_sources_with_identical_sheet_names_keep_separate_data_and_viewer_credentials(self):
        self.configure()
        def read(source, config, token):
            values = [30, 40] if source['_id'] == 'private-file' else [600, 700]
            if source['_id'] == 'population':
                self.assertEqual(token, 'viewer-google-token')
            return table([['Año', 'Total'], [2024, values[0]], [2025, values[1]]], 1, '.', {})
        with patch.object(api, 'table_from_source', side_effect=read) as load, \
             patch.object(api.google_oauth, 'access_token', return_value='viewer-google-token') as token:
            response = self.client.post('/v2/workspaces/workspace-a/render', headers=self.headers(self.viewer),
                                        json={'filters': {'Año': ['2025']}})
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body['widgets']['mortality']['data']['datasets'][0]['data'], [40])
            self.assertEqual(body['widgets']['population']['data']['datasets'][0]['data'], [700])
            self.assertTrue(body['workspace']['requires_google'])
            token.assert_called_once_with(self.db, str(self.viewer))
            self.assertEqual(load.call_count, 2)
            response = self.client.post('/v2/workspaces/workspace-a/render', headers=self.headers(self.viewer),
                json={'snapshot_id': body['snapshot_id'], 'filters': {'Año': ['2024']}})
            self.assertEqual(response.json()['widgets']['population']['data']['datasets'][0]['data'], [600])
            self.assertEqual(load.call_count, 2)
            single = self.client.post('/v2/workspaces/workspace-a/widgets/population/chart',
                headers=self.headers(self.viewer), json={'filters': {'Año': ['2025']}})
            self.assertEqual(single.json()['datasets'][0]['data'], [700])
        api.snapshots.discard(body['snapshot_id'])

    def test_cannot_save_another_users_source_or_view_it_directly(self):
        widgets = self.configure()
        self.db.analytics_sources.insert_one({'_id': 'foreign', 'owner': str(self.other), 'kind': 'google'})
        widgets[1]['source_id'] = 'foreign'
        payload = {'name': 'Test', 'source_id': 'private-file', 'widgets': widgets}
        for method, path in [('post', '/v2/workspaces'), ('put', '/v2/workspaces/workspace-a')]:
            response = getattr(self.client, method)(path, headers=self.headers(self.owner), json=payload)
            self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.client.get('/v2/sources/population/sheets', headers=self.headers(self.viewer)).status_code, 404)

    def test_changing_widget_source_invalidates_snapshot_fingerprint(self):
        self.configure()
        before = self.db.analytics_workspaces.find_one()
        after = copy.deepcopy(before)
        after['widgets'][1]['source_id'] = 'another-source'
        self.assertNotEqual(api.workspace_fingerprint(before), api.workspace_fingerprint(after))
