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

    def render(self, filters=None, snapshot_id=None, refresh=False):
        body = {'filters': filters or {}}
        if snapshot_id:
            body['snapshot_id'] = snapshot_id
        if refresh:
            body['refresh'] = True
        return self.client.post('/v2/workspaces/workspace-a/render',
                                headers=self.headers(self.viewer), json=body)

    def test_saving_an_early_number_format_keeps_strict_chart_configs_readable(self):
        collection = self.db.analytics_workspaces
        formats = {'Año': {'style': 'decimal', 'digits': 0, 'group_digits': False}}
        collection.update_one({}, {'$set': {'widgets.0.config.number_formats': formats}})

        def editor_save(*, reset=False):
            url = '/v2/workspaces/workspace-a'
            headers = self.headers(self.owner)
            editor = self.client.get(url, headers=headers).json()
            self.assertEqual(editor['widgets'][0]['config']['number_formats'], formats)
            if reset:
                del editor['widgets'][0]['config']['number_formats']
            response = self.client.put(url, headers=headers, json={
                key: editor[key] for key in ('name', 'source_id', 'widgets', 'filter_columns')
            })
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        filters = {'Mes': ['Enero']}
        before = self.render(filters).json()
        self.assertEqual(before['widgets']['total']['data']['datasets'][0]['data'], [15])
        saved = editor_save()
        self.assertEqual(saved['widgets'][0]['config']['number_formats'], formats)
        stored = collection.find_one()
        self.assertEqual(stored['widget_number_formats'], {'monthly': formats})
        self.assertTrue(all('number_formats' not in widget['config'] for widget in stored['widgets']))
        after = self.render(filters).json()
        self.assertEqual(after['widgets'], before['widgets'])
        self.assertEqual(after['filter_options'], before['filter_options'])
        self.assertEqual(after['workspace']['widgets'][0]['config']['number_formats'], formats)
        self.assertNotIn('number_formats', after['workspace']['widgets'][1]['config'])

        reset = editor_save(reset=True)
        self.assertNotIn('widget_number_formats', collection.find_one())
        self.assertNotIn('number_formats', reset['widgets'][0]['config'])
        self.assertEqual(self.render(filters).json()['widgets'], before['widgets'])

    def test_legacy_monthly_section_renders_and_saves_without_losing_its_indicator_condition(self):
        collection = self.db.analytics_workspaces
        legacy_defaults = {'calculated_fields': {}, 'chart_filters': {}, 'value_formats': {},
                           'group_remaining': False, 'table_distinct': False}
        legacy_widgets = [{**widget, 'config': {**widget['config'], **legacy_defaults}} for widget in self.widgets]
        legacy_widgets[1]['config']['indicator_condition'] = {'count_field': 'Mes', 'max_count': 1, 'hide_zero': False}
        collection.update_one({}, {'$set': {'widgets': legacy_widgets}})

        initial = self.render()
        self.assertEqual(initial.status_code, 200, initial.text)
        initial_body = initial.json()
        self.assertEqual(initial_body['widgets']['total']['data']['datasets'][0]['data'], [None])
        self.assertEqual(initial_body['widgets']['monthly']['data']['datasets'][0]['data'], [15, 20])
        self.assertEqual(collection.find_one()['widgets'], legacy_widgets)

        filters = {'Mes': ['Enero']}
        filtered = self.render(filters, initial_body['snapshot_id']).json()
        self.assertEqual(filtered['widgets']['total']['data']['datasets'][0]['data'], [15])
        self.assertEqual(self.query(filters=filters).json()['datasets'][0]['data'], [15])

        url = '/v2/workspaces/workspace-a'
        headers = self.headers(self.owner)
        editor = self.client.get(url, headers=headers).json()
        cfg = editor['widgets'][1]['config']
        self.assertEqual(cfg['indicator_single_value_columns'], ['Mes'])
        self.assertNotIn('indicator_condition', cfg)
        self.assertTrue(legacy_defaults.keys().isdisjoint(cfg))
        response = self.client.put(url, headers=headers, json={
            key: editor[key] for key in ('name', 'source_id', 'widgets', 'filter_columns')
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.render(filters).json()['widgets'], filtered['widgets'])

    def test_section_render_reuses_one_source_snapshot_until_manual_refresh(self):
        snapshot_ids = []
        try:
            with patch.object(api, 'table_from_source', wraps=api.table_from_source) as load:
                first = self.render()
                self.assertEqual(first.status_code, 200, first.text)
                first_body = first.json()
                snapshot_ids.append(first_body['snapshot_id'])
                self.assertEqual(load.call_count, 1)
                self.assertEqual(first_body['widgets']['total']['data']['datasets'][0]['data'], [35])

                filtered = self.render({'Mes': ['Enero']}, first_body['snapshot_id'])
                self.assertEqual(filtered.status_code, 200, filtered.text)
                self.assertEqual(load.call_count, 1)
                self.assertEqual(filtered.json()['widgets']['total']['data']['datasets'][0]['data'], [15])

                refreshed = self.render({'Mes': ['Enero']}, first_body['snapshot_id'], refresh=True)
                self.assertEqual(refreshed.status_code, 200, refreshed.text)
                snapshot_ids.append(refreshed.json()['snapshot_id'])
                self.assertNotEqual(refreshed.json()['snapshot_id'], first_body['snapshot_id'])
                self.assertEqual(load.call_count, 2)
        finally:
            for snapshot_id in snapshot_ids:
                api.snapshots.discard(snapshot_id)

    def test_missing_section_snapshot_requires_manual_refresh_without_reading_source(self):
        with patch.object(api, 'table_from_source') as read:
            response = self.render({'Mes': ['Enero']}, 'missing-snapshot')
        self.assertEqual(response.status_code, 409)
        self.assertIn('Actualizar datos', response.json()['detail'])
        read.assert_not_called()

    def test_facets_reused_only_with_identical_chart_restrictions_and_current_filters(self):
        with patch.object(api, 'contextual_filter_options', wraps=api.contextual_filter_options) as facets:
            first = self.render().json()
            self.assertEqual(facets.call_count, 1)
            filtered = self.render({'Mes': ['Enero']}, first['snapshot_id']).json()
            self.assertEqual(facets.call_count, 2)
            self.assertEqual(filtered['widgets']['total']['data']['datasets'][0]['data'], [15])
        api.snapshots.discard(first['snapshot_id'])
        self.db.analytics_workspaces.update_one({}, {'$set': {'widgets.1.config.filter_rules': [
            {'column': 'Tipo', 'operator': 'equal', 'value': 'A'}]}})
        with patch.object(api, 'contextual_filter_options', wraps=api.contextual_filter_options) as facets:
            restricted = self.render().json()
            self.assertEqual(facets.call_count, 2)
            self.assertEqual(restricted['widgets']['total']['data']['filter_options']['Tipo']['values'], ['A'])
            self.assertEqual(restricted['widgets']['monthly']['data']['filter_options']['Tipo']['values'], ['A', 'B'])
        api.snapshots.discard(restricted['snapshot_id'])

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

    def test_filter_options_sort_by_selected_field_and_follow_context(self):
        self.db.analytics_workspaces.update_one({}, {'$set': {'filter_sorts': {
            'Tipo': {'field': 'Total', 'aggregation': 'sum', 'direction': 'desc'},
        }}})
        options = self.query().json()['filter_options']['Tipo']
        self.assertEqual(options['values'], ['B', 'A'])
        self.assertTrue(options['sort_applied'])
        january = self.query(filters={'Mes': ['Enero']}).json()['filter_options']['Tipo']
        self.assertEqual(january['values'], ['A', 'B'])

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
            self.assertEqual(self.render(filters={'Año': ['2026']}).status_code, 404)
            read.assert_not_called()

    def test_control_rules_limit_choices_without_changing_chart_totals_or_saved_restrictions(self):
        rules = [{'column': 'Tipo', 'operator': 'not_contains', 'value': 'B'}]
        self.db.analytics_workspaces.update_one({}, {'$set': {'filter_option_rules': rules}})
        for filters, expected in [({}, 35), ({'Mes': ['Enero']}, 15), ({'Tipo': ['B']}, 25)]:
            single = self.query(filters=filters)
            response = self.render(filters)
            self.assertEqual(single.status_code, 200, single.text)
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            try:
                self.assertEqual(body['filter_options']['Tipo']['values'], ['A'])
                for data in [single.json(), body['widgets']['total']['data']]:
                    self.assertEqual(data['datasets'][0]['data'], [expected])
                    self.assertEqual(data['filter_options']['Tipo']['values'], ['A'])
                    self.assertEqual(data['filter_options']['Año']['values'], [] if filters.get('Tipo') else ['2026'])
            finally:
                api.snapshots.discard(body['snapshot_id'])

    def test_control_rules_persist_and_invalidate_previously_loaded_options(self):
        initial = self.render().json()
        payload = {'name': 'Sección', 'source_id': 'private-file', 'widgets': self.widgets,
                   'filter_columns': ['Año', 'Mes', 'Tipo'],
                   'filter_option_rules': [{'column': 'Tipo', 'operator': 'not_equal', 'value': 'B'}]}
        url = '/v2/workspaces/workspace-a'
        self.assertEqual(self.client.put(url, headers=self.headers(self.viewer), json=payload).status_code, 404)
        saved = self.client.put(url, headers=self.headers(self.owner), json=payload)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(self.client.get(url, headers=self.headers(self.owner)).json()['filter_option_rules'],
                         api.Workspace.model_validate(payload).model_dump()['filter_option_rules'])
        try:
            self.assertEqual(self.render(snapshot_id=initial['snapshot_id']).status_code, 409)
        finally:
            api.snapshots.discard(initial['snapshot_id'])
        for rules in [[{'column': 'Tipo', 'operator': 'execute', 'value': ''}], payload['filter_option_rules'] * 51]:
            payload['filter_option_rules'] = rules
            self.assertEqual(self.client.put(url, headers=self.headers(self.owner), json=payload).status_code, 422)
