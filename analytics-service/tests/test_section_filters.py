import copy
import unittest
from unittest.mock import patch

from app import generator as api
import test_workspace_access as fixtures


class SectionFilterTests(unittest.TestCase):
    tearDown = fixtures.WorkspaceAccessTests.tearDown
    headers = fixtures.WorkspaceAccessTests.headers
    publish = fixtures.WorkspaceAccessTests.publish

    def setUp(self):
        fixtures.WorkspaceAccessTests.setUp(self)
        config = {'sheet': 'Datos', 'x_col': 'Mes', 'y_col': 'Total', 'aggregation': 'sum',
                  'chart_type': 'bar', 'filters': {'Año': ['2026']}}
        self.widgets = [
            {'id': 'monthly', 'title': 'Por mes', 'config': config},
            {'id': 'total', 'title': 'Total intervenciones', 'config': {**config, 'chart_type': 'indicator', 'x_col': 'Total'}},
            {'id': 'type', 'title': 'Por tipo', 'config': {**config, 'chart_type': 'pie', 'x_col': 'Tipo'}},
        ]
        self.db.analytics_workspaces.update_one({}, {'$set': {'widgets': self.widgets, 'filter_columns': ['Año', 'Mes', 'Tipo']}})
        self.rows = [['Año', 'Mes', 'Tipo', 'Total', 'Interno'], [2026, 'Enero', 'A', 10, 'privado'],
                     [2026, 'Febrero', 'B', 20, 'privado'], [2025, 'Enero', 'A', 100, 'excluido'],
                     [2026, 'Enero', 'B', 5, 'privado']]
        self.publish([self.owner, self.viewer])
        for mock in [patch.object(api, 'local_path', return_value='test-only'), patch.object(api, 'read_rows', return_value=self.rows)]:
            mock.start()
            self.addCleanup(mock.stop)

    def query(self, widget='total', filters=None):
        return self.client.post(f'/v2/workspaces/workspace-a/widgets/{widget}/chart',
                                headers=self.headers(self.viewer), json={'filters': filters or {}})

    def test_year_and_month_apply_to_indicator_and_all_charts_without_changing_saved_data(self):
        original = copy.deepcopy(self.db.analytics_workspaces.find_one())
        for widget in self.widgets:
            response = self.query(widget['id'], {'Año': ['2026'], 'Mes': ['Enero']})
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body['filtered_rows'], 2)
            self.assertEqual(sum(body['datasets'][0]['data']), 15)
            self.assertEqual(body['ignored_filters'], [])
            self.assertEqual(set(body['filter_options']), {'Año', 'Mes', 'Tipo'})
            self.assertEqual(body['filter_options']['Año']['values'], ['2026'])
            self.assertNotIn('excluido', response.text)
        self.assertEqual(self.query().json()['datasets'][0]['data'], [35])
        self.assertEqual(self.query(filters={'Año': ['2025']}).json()['filtered_rows'], 0)
        self.assertEqual(self.db.analytics_workspaces.find_one(), original)

    def test_unconfigured_measure_and_private_columns_cannot_be_requested(self):
        for column in ['Total', 'Interno', 'No existe']:
            self.assertEqual(self.query(filters={column: ['10']}).status_code, 422)
        self.db.analytics_workspaces.update_one({}, {'$set': {'filter_columns': []}})
        self.assertEqual(self.query().json()['filter_options'], {})
        self.assertEqual(self.query(filters={'Año': ['2026']}).status_code, 422)

    def test_options_follow_other_filters_but_not_their_own_selection(self):
        # January has only Individual; February also has Grupal. The 2025 row
        # must remain excluded even when the reader clears every filter.
        self.rows[:] = [['Año', 'Mes', 'Tipo', 'Total'], [2026, 'Enero', 'Individual', 10],
                        [2026, 'Febrero', 'Grupal', 20], [2026, 'Febrero', 'Individual', 5],
                        [2025, 'Enero', 'Grupal', 99]]
        for widget in self.widgets:
            body = self.query(widget['id'], {'Mes': ['Enero']}).json()
            self.assertEqual(body['filter_options']['Tipo']['values'], ['Individual'])
            self.assertEqual(body['filter_options']['Mes']['values'], ['Enero', 'Febrero'])
            self.assertEqual(body['filter_options']['Tipo']['unfiltered_total'], 2)
            self.assertEqual(body['datasets'][0]['data'], [10])
        options = self.query(filters={'Mes': ['Enero', 'Febrero'], 'Tipo': ['Individual']}).json()['filter_options']
        self.assertEqual(options['Tipo']['values'], ['Individual', 'Grupal'])
        self.assertEqual(options['Tipo']['available_selected'], ['Individual'])
        self.assertEqual(options['Mes']['values'], ['Enero', 'Febrero'])
        self.assertEqual(options['Mes']['available_selected'], ['Enero', 'Febrero'])
        self.assertEqual(self.query().json()['filter_options']['Tipo']['values'], ['Individual', 'Grupal'])

    def test_incompatible_selections_have_recovery_options_without_broadening_results(self):
        original = copy.deepcopy(self.db.analytics_workspaces.find_one())
        # A is only available in January, so these two selections conflict.
        body = self.query(filters={'Mes': ['Febrero'], 'Tipo': ['A']}).json()
        self.assertEqual(body['filtered_rows'], 0)
        self.assertEqual(body['filter_options']['Tipo']['values'], ['B'])
        self.assertEqual(body['filter_options']['Tipo']['available_selected'], [])
        self.assertEqual(body['filter_options']['Mes']['values'], ['Enero'])
        self.assertEqual(body['filter_options']['Mes']['available_selected'], [])
        self.assertEqual(self.db.analytics_workspaces.find_one(), original)

    def test_empty_saved_subset_has_no_suggestions_even_with_no_viewer_filters(self):
        self.db.analytics_workspaces.update_one({}, {'$set': {'widgets.1.config.filters': {'Año': ['1999']}}})
        body = self.query(filters={'Tipo': ['A']}).json()
        self.assertEqual(body['filtered_rows'], 0)
        for option in body['filter_options'].values():
            self.assertEqual(option['values'], [])
            self.assertEqual(option['total'], 0)
            self.assertEqual(option['available_selected'], [])

    def test_incompatible_columns_are_reported_and_other_filters_still_apply(self):
        self.db.analytics_workspaces.update_one({}, {'$set': {'widgets.1.config': {
            'sheet': 'Otra', 'chart_type': 'indicator', 'x_col': 'Total', 'y_col': 'Total', 'aggregation': 'sum', 'filters': {},
        }}})
        with patch.object(api, 'read_rows', return_value=[['Tipo', 'Total'], ['A', 7], ['B', 8]]):
            response = self.query(filters={'Año': ['2026'], 'Mes': ['Enero'], 'Tipo': ['A']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['datasets'][0]['data'], [7])
        self.assertEqual(response.json()['ignored_filters'], ['Año', 'Mes'])
        self.assertEqual(set(response.json()['filter_options']), {'Tipo'})

    def test_existing_section_supports_shared_fields_without_a_migration(self):
        self.db.analytics_workspaces.update_one({}, {'$unset': {'filter_columns': ''}})
        self.assertEqual(self.query(filters={'Mes': ['Enero']}).json()['datasets'][0]['data'], [15])
        self.assertEqual(self.query(filters={'Interno': ['privado']}).status_code, 422)

    def test_only_author_can_save_filter_choices_and_empty_selection_persists(self):
        body = {'name': 'Sección', 'source_id': 'private-file', 'widgets': self.widgets, 'filter_columns': ['Año', 'Mes']}
        url = '/v2/workspaces/workspace-a'
        self.assertEqual(self.client.put(url, headers=self.headers(self.viewer), json=body).status_code, 404)
        response = self.client.put(url, headers=self.headers(self.owner), json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(url + '/view', headers=self.headers(self.viewer)).json()['filter_columns'], ['Año', 'Mes'])
        body['filter_columns'] = []
        self.assertEqual(self.client.put(url, headers=self.headers(self.owner), json=body).status_code, 200)
        self.assertEqual(self.db.analytics_workspaces.find_one()['filter_columns'], [])
        for columns in [[''] , ['A' * 501], [str(i) for i in range(21)]]:
            body['filter_columns'] = columns
            self.assertEqual(self.client.put(url, headers=self.headers(self.owner), json=body).status_code, 422)

    def test_permissions_are_checked_before_reading_filter_suggestions(self):
        self.db.users.update_one({'_id': self.viewer}, {'$set': {'access': []}})
        with patch.object(api, 'table_from_source') as read:
            self.assertEqual(self.query(filters={'Año': ['2026']}).status_code, 404)
            read.assert_not_called()
