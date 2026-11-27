from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from app import generator as api
from app.data_engine import table, chart
import test_workspace_access as fixtures


class EditorContractTests(unittest.TestCase):
    setUp = fixtures.WorkspaceAccessTests.setUp
    tearDown = fixtures.WorkspaceAccessTests.tearDown
    headers = fixtures.WorkspaceAccessTests.headers
    publish = fixtures.WorkspaceAccessTests.publish

    def read(self):
        response = self.client.get('/v2/workspaces/workspace-a', headers=self.headers(self.owner))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def edit(self, changes, revision=None):
        return self.client.patch('/v2/workspaces/workspace-a', headers=self.headers(self.owner),
                                 json={'revision': revision or self.read()['editor_revision'], 'changes': changes})

    def test_open_noop_and_title_preserve_raw_document_with_opaque_properties(self):
        self.db.analytics_workspaces.update_one({}, {'$set': {'widgets.0.config.future_setting': {'v': None},
            'widgets.0.config.indicator_ratio': None, 'widgets.0.config.empty_text': '', 'opaque_section': {'x': []}}})
        before = self.db.analytics_workspaces.find_one()
        self.read()
        self.assertEqual(json.dumps(self.db.analytics_workspaces.find_one()), json.dumps(before))
        with patch.object(self.db.analytics_workspaces, 'update_one', wraps=self.db.analytics_workspaces.update_one) as update:
            response = self.edit([])
            self.assertEqual(response.status_code, 200, response.text)
            update.assert_not_called()
            with patch.object(api, 'load_table', side_effect=AssertionError('No se debe leer una fuente para cambiar un título')):
                response = self.edit([{'action': 'set', 'widget': 'chart-a', 'path': ['title'], 'value': 'Nuevo título'}])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(update.call_args.args[1], {'$set': {'widgets.0.title': 'Nuevo título'}})
        before['widgets'][0]['title'] = 'Nuevo título'
        self.assertEqual(json.dumps(self.db.analytics_workspaces.find_one()), json.dumps(before))
        self.assertEqual(self.read()['editor_document']['widgets'][0]['title'], 'Nuevo título')

    def test_title_can_preserve_legacy_null_and_absence_without_normalizing(self):
        self.db.analytics_workspaces.update_one({}, {'$set': {'widgets.0.config.types': None, 'widgets.0.config.y_cols': None}, '$unset': {'widgets.0.library': ''}})
        before = self.db.analytics_workspaces.find_one()
        response = self.edit([{'action': 'set', 'widget': 'chart-a', 'path': ['title'], 'value': 'Otro'}])
        self.assertEqual(response.status_code, 200, response.text)
        after = self.db.analytics_workspaces.find_one()
        before['widgets'][0]['title'] = 'Otro'
        self.assertEqual(after, before)

    def test_two_editors_conflict_and_atomic_guard_catches_concurrent_write(self):
        revision = self.read()['editor_revision']
        change = [{'action': 'set', 'widget': 'chart-a', 'path': ['title'], 'value': 'Primero'}]
        self.assertEqual(self.edit(change, revision).status_code, 200)
        self.assertEqual(self.edit(change, revision).status_code, 409)
        original_validate = api.validate_editor_data
        def concurrent(candidate, existing, owner, google_token=''):
            original_validate(candidate, existing, owner, google_token)
            self.db.analytics_workspaces.update_one({}, {'$set': {'name': 'Cambio simultáneo'}})
        with patch.object(api, 'validate_editor_data', side_effect=concurrent):
            response = self.edit([{**change[0], 'value': 'Segundo'}])
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.db.analytics_workspaces.find_one()['widgets'][0]['title'], 'Primero')

    def test_case_save_reopen_and_render_respects_single_year_default(self):
        fields = [{'name': 'Importe', 'mode': 'row', 'expression': 'CASE WHEN [AÑO] = 2024 THEN "$ 61 M" WHEN [AÑO] = 2025 THEN "$ 891 M" ELSE NULL END'}]
        cfg = {'sheet': 'Costos', 'chart_type': 'indicator', 'x_col': 'AÑO', 'y_col': 'Importe', 'aggregation': 'none', 'calculated_fields': fields}
        frame, metadata = table([['AÑO'], [2024], [2025], [2025]])
        changes = [{'action': 'set', 'widget': 'chart-a', 'path': ['config'], 'value': cfg},
                   {'action': 'set', 'path': ['filter_columns'], 'value': ['AÑO']},
                   {'action': 'set', 'path': ['filter_settings'], 'value': {'AÑO': 'single'}},
                   {'action': 'set', 'path': ['default_filters'], 'value': {'AÑO': ['2025']}}]
        with patch.object(api, 'load_table', return_value=(frame, metadata)):
            response = self.edit(changes)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.read()['editor_document']['widgets'][0]['config'], cfg)
        self.publish([self.owner, self.viewer])
        with patch.object(api, 'table_from_source', return_value=(frame, metadata)):
            rendered = self.client.post('/v2/workspaces/workspace-a/render', headers=self.headers(self.viewer), json={'filters': {'AÑO': ['2025']}})
            self.assertEqual(rendered.status_code, 200, rendered.text)
            self.assertEqual(rendered.json()['widgets']['chart-a']['data']['indicator_text'], '$ 891 M')
            invalid = self.client.post('/v2/workspaces/workspace-a/render', headers=self.headers(self.viewer), json={'filters': {'AÑO': ['2024', '2025']}})
            self.assertEqual(invalid.status_code, 422)

    def test_formula_trial_applies_restrictions_and_filters_before_sampling(self):
        frame, metadata = table([['AÑO', 'VALOR', 'GESTION'], [2024, 10, 'ESTATAL'], [2025, 20, 'PRIVADA'], [2025, 30, 'OTRA']])
        cfg = {'sheet': 'Datos', 'selected_filters': {'AÑO': ['2025']}, 'chart_filters': {'GESTION': {'exclude': ['PRIVADA']}}}
        field = {'name': 'Total', 'expression': 'SUM([VALOR])', 'mode': 'aggregate'}
        with patch.object(api, 'load_table', return_value=(frame, metadata)):
            response = self.client.post('/v2/sources/private-file/formula-trial', headers=self.headers(self.owner), json={'config': cfg, 'field': field})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['values'], [30])
        self.assertEqual(response.json()['sample_rows'], 1)

    def test_invalid_dependencies_do_not_write_and_foreign_sources_are_denied(self):
        before = self.db.analytics_workspaces.find_one()
        frame, metadata = table([['x', 'y'], ['a', 1]])
        fields = [{'name': 'A', 'expression': '[B]+1', 'mode': 'row'}, {'name': 'B', 'expression': '[A]+1', 'mode': 'row'}]
        with patch.object(api, 'load_table', return_value=(frame, metadata)):
            response = self.edit([{'action': 'set', 'widget': 'chart-a', 'path': ['config', 'calculated_fields'], 'value': fields}])
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.db.analytics_workspaces.find_one(), before)
        self.assertEqual(self.edit([{'action': 'set', 'widget': 'chart-a', 'path': ['source_id'], 'value': 'foreign'}]).status_code, 404)

    def test_reorder_add_remove_and_other_sheet_read_options_survive(self):
        widget = deepcopy(self.workspace['widgets'][0])
        widget.update(id='other', source_id='private-file', title='Otra hoja')
        widget['config'].update(sheet='Otra', header_row=3, decimal='.', types={'y': 'number'}, computed_columns={'Nombre': [{'column': 'x'}]})
        response = self.edit([{'action': 'insert', 'widget': 'other', 'value': widget}, {'action': 'reorder', 'value': ['other', 'chart-a']}])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.read()['editor_document']['widgets'][0], widget)
        self.assertEqual(self.edit([{'action': 'remove', 'widget': 'chart-a'}]).status_code, 200)
        self.assertEqual(self.db.analytics_workspaces.find_one()['widgets'], [widget])

    def test_catalogue_configs_retain_all_fields_on_title_only_edits(self):
        from pathlib import Path
        candidates = [Path(__file__).resolve().parents[2] / 'migrations' / 'looker' / 'salud.json']
        verified = 0
        def visit(value):
            if isinstance(value, dict):
                if isinstance(value.get('widgets'), list):
                    yield value
                for item in value.values():
                    yield from visit(item)
            elif isinstance(value, list):
                for item in value:
                    yield from visit(item)
        for path in candidates:
            try:
                documents = list(visit(json.loads(path.read_text(encoding='utf-8'))))
            except (ValueError, UnicodeError):
                continue
            for document in documents:
                for widget in document['widgets']:
                    if not isinstance(widget, dict) or not widget.get('id') or not isinstance(widget.get('config'), dict):
                        continue
                    raw = {**self.workspace, **document, '_id': 'workspace-a', 'owner': str(self.owner), 'widgets': [widget]}
                    self.db.analytics_workspaces.replace_one({'_id': 'workspace-a'}, raw)
                    response = self.edit([{'action': 'set', 'widget': widget['id'], 'path': ['title'], 'value': 'Título de prueba'}])
                    self.assertEqual(response.status_code, 200, response.text)
                    raw['widgets'][0]['title'] = 'Título de prueba'
                    self.assertEqual(self.db.analytics_workspaces.find_one(), raw)
                    verified += 1
        self.assertGreater(verified, 0, 'El catálogo de producción debe estar incluido en la verificación.')
        print(f'Configuraciones JSON de producción verificadas: {verified}')

    def test_equivalent_reads_share_data_but_different_headers_and_sheets_do_not(self):
        widgets = [deepcopy(self.workspace['widgets'][0]) for _ in range(3)]
        widgets[1].update(id='explicit', source_id='private-file')
        widgets[2].update(id='another-sheet', source_id='private-file')
        widgets[2]['config'].update(sheet='Otra', header_row=3, decimal='.')
        self.db.analytics_workspaces.update_one({}, {'$set': {'widgets': widgets, 'filter_columns': ['x']}})
        self.publish([self.owner, self.viewer])
        frame, metadata = table([['x', 'y'], ['a', 10]])
        with patch.object(api, 'table_from_source', return_value=(frame, metadata)) as read:
            response = self.client.post('/v2/workspaces/workspace-a/render', headers=self.headers(self.viewer), json={'filters': {}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(read.call_count, 2)
        self.assertTrue(all('data' in item for item in response.json()['widgets'].values()))

    def test_unavailable_individual_source_isolated_from_other_charts(self):
        widgets = deepcopy(self.workspace['widgets'])
        widgets.append({**deepcopy(widgets[0]), 'id': 'missing', 'source_id': 'no-source'})
        self.db.analytics_workspaces.update_one({}, {'$set': {'widgets': widgets, 'filter_columns': ['x']}})
        self.publish([self.owner, self.viewer])
        frame, metadata = table([['x', 'y'], ['a', 10]])
        with patch.object(api, 'table_from_source', return_value=(frame, metadata)):
            response = self.client.post('/v2/workspaces/workspace-a/render', headers=self.headers(self.viewer), json={'filters': {}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['widgets']['chart-a']['data']['datasets'][0]['data'], [10])
        self.assertIn('error', response.json()['widgets']['missing'])
