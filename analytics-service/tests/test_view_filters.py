import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import generator as api
import test_workspace_access as fixtures


class ViewFilterTests(unittest.TestCase):
    setUp = fixtures.WorkspaceAccessTests.setUp
    tearDown = fixtures.WorkspaceAccessTests.tearDown
    headers = fixtures.WorkspaceAccessTests.headers
    publish = fixtures.WorkspaceAccessTests.publish
    view = fixtures.WorkspaceAccessTests.view

    def filtered(self, filters=None, **extra):
        return self.client.post('/v2/workspaces/workspace-a/widgets/chart-a/chart', headers=self.headers(self.viewer), json={'filters': filters or {}, **extra})

    def test_filters_narrow_saved_data_do_not_mutate_or_disclose_excluded_rows(self):
        self.publish([self.owner, self.viewer])
        original = copy.deepcopy(self.db.analytics_workspaces.find_one())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.csv'
            path.write_text('x,y,secret\na,0,internal\na,10,internal\nb,999,hidden\n')
            with patch.object(api, 'local_path', return_value=path):
                response = self.filtered({'y': ['0']})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['datasets'][0]['data'], [0])
                self.assertEqual(response.json()['filtered_rows'], 1)
                self.assertEqual(response.json()['filter_options']['x']['values'], ['a'])
                self.assertEqual(response.json()['filter_options']['y']['values'], ['0', '10'])
                self.assertNotIn('secret', response.json()['filter_options'])
                self.assertNotIn('999', response.text)
                self.assertEqual(self.filtered({'x': ['b']}).json()['filtered_rows'], 0)
                self.assertEqual(self.filtered().json()['datasets'][0]['data'], [10])
                self.assertEqual(self.filtered({'secret': ['hidden']}).status_code, 422)
                self.assertEqual(self.filtered({}, chart_type='table').status_code, 422)
                self.assertEqual(self.filtered({}, sheet='Other').status_code, 422)
        self.assertEqual(self.db.analytics_workspaces.find_one(), original)

    def test_null_boolean_date_and_large_suggestion_lists(self):
        self.publish([self.owner, self.viewer])
        config = {**self.workspace['widgets'][0]['config'], 'filters': {}, 'types': {'x': 'text'}}
        self.db.analytics_workspaces.update_one({}, {'$set': {'widgets.0.config': config}})
        rows = [['x', 'y']] + [[f'Value {i}', i] for i in range(120)] + [[None, 0]]
        with patch.object(api, 'read_rows', return_value=rows), patch.object(api, 'local_path', return_value='unused'):
            response = self.filtered({'x': ['Value 119']})
            self.assertEqual(response.json()['datasets'][0]['data'], [119])
            self.assertEqual(response.json()['filter_options']['x']['total'], 121)
            self.assertEqual(len(response.json()['filter_options']['x']['values']), 100)
            self.assertEqual(self.filtered({'x': ['None']}).json()['filtered_rows'], 1)
        for value, types, selected in [(True, {}, 'True'), ('2026-09-24', {'x': 'date'}, '2026-09-24T00:00:00')]:
            self.db.analytics_workspaces.update_one({}, {'$set': {'widgets.0.config.types': types}})
            with patch.object(api, 'read_rows', return_value=[['x', 'y'], [value, 10]]), patch.object(api, 'local_path', return_value='unused'):
                self.assertEqual(self.filtered({'x': [selected]}).json()['filtered_rows'], 1)

    def test_revoked_access_is_checked_before_reading_data_and_limits_are_enforced(self):
        self.publish([self.owner, self.viewer])
        self.assertEqual(self.filtered({'x': ['a'] * 101}).status_code, 422)
        self.db.users.update_one({'_id': self.viewer}, {'$set': {'access': []}})
        with patch.object(api, 'table_from_source') as read:
            self.assertEqual(self.filtered({'x': ['a']}).status_code, 404)
            read.assert_not_called()
