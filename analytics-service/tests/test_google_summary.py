import unittest
from unittest.mock import patch

from app import generator as api
from app.data_engine import DataError, chart, table
from app.google_summary import summarize_google


class GoogleSummaryTests(unittest.TestCase):
    def setUp(self):
        self.raw = [['Year', 'Month', 'Order', 'Professional', 'Coverage', 'Patient'],
                    [2026, 'Jan', 1, 'A', 'Public', 'private-1'],
                    [2026, 'Jan', 1, 'A', 'Public', 'private-2'],
                    [2026, 'Jan', 3, 'A', 'Public', None],
                    [2026, 'Jan', 1, 'B', 'Other', 'private-1'],
                    [2025, 'Dec', 12, 'A', 'Public', 'private-3'],
                    [2026, 'Feb', 2, 'A', 'Other', ''], []]
        self.config = api.ReadConfig(sheet="Data's tab", types={'Year': 'number'}, google_summary={
            'dimensions': ['Year', 'Month', 'Professional', 'Coverage'],
            'counts': {'Attendances': 'Patient'}, 'max_values': {'Order': 'Order'}, 'row_count': 'Rows'})
        self.requests = []

    def read(self, source, token, sheet=None, *, row_range=None):
        self.assertEqual((source['_id'], token), ('authorized', 'viewer-token'))
        self.requests.append(row_range)
        if sheet is None:
            return {'sheets': [{'properties': {'title': self.config.sheet, 'gridProperties': {'rowCount': len(self.raw), 'columnCount': len(self.raw[0])}}}]}
        start, end, width = row_range
        return {'values': [row[:width] for row in self.raw[start-1:end]]}

    def run_read(self):
        with patch('app.google_summary.BLOCK_ROWS', 2):
            return summarize_google({'_id': 'authorized'}, self.config, 'viewer-token', self.read)

    def test_all_blocks_null_counts_duplicates_and_maxima(self):
        result, meta = self.run_read()
        self.assertEqual(self.requests, [None, (1, 1, 6), (2, 3, 6), (4, 5, 6), (6, 7, 6), (8, 8, 6)])
        self.assertEqual(meta['source_row_count'], 6)
        january = result[(result.Year == 2026) & (result.Month == 'Jan') & (result.Professional == 'A')].iloc[0]
        self.assertEqual((january.Attendances, january.Rows, january.Order), (2, 3, 3))
        self.assertNotIn('private-', str(result.to_dict()) + str(meta))
        self.assertNotIn('Patient', meta['columns'])

    def test_matches_raw_count_after_combined_filters_and_other_grouping(self):
        summary, _ = self.run_read()
        # Sheets exposes empty cells as empty strings in interior columns;
        # COUNT in its connector treats these as missing, not as patients.
        raw, _ = table([[None if value == '' else value for value in row] for row in self.raw])
        for filters in [{}, {'Year': ['2026']}, {'Year': ['2026'], 'Coverage': ['Public']}]:
            cfg = dict(chart_type='pie', x_col='Professional', aggregation='count', count_col='Patient', filters=filters,
                       category_limit=2, include_other=True, preserve_empty_categories=True)
            before = chart(raw, cfg)
            after = chart(summary, {**cfg, 'aggregation': 'sum', 'y_col': 'Attendances', 'count_col': ''})
            self.assertEqual(before['labels'], after['labels'])
            self.assertEqual(before['datasets'][0]['data'], after['datasets'][0]['data'])

    def test_record_count_includes_rows_populated_only_in_unused_columns(self):
        self.raw[0].append('Updated')
        self.raw.append([None] * 6 + ['2026-11-16'])
        result, meta = self.run_read()
        self.assertEqual(meta['source_row_count'], 7)
        self.assertEqual(result.Rows.sum(), 7)
        self.assertEqual(result.Attendances.sum(), 4)
        self.assertNotIn('Updated', result.columns)

    def test_rejects_limits_missing_headers_and_partial_failures(self):
        for name, value in [('MAX_GROUPS', 1), ('MAX_SOURCE_ROWS', 2), ('MAX_SOURCE_CELLS', 6), ('MAX_GROUP_BYTES', 1), ('MAX_SECONDS', -1)]:
            with self.subTest(name=name), patch('app.google_summary.'+name, value), self.assertRaises(DataError):
                self.run_read()
        self.raw[0][-1] = 'Wrong'
        with self.assertRaises(DataError):
            self.run_read()
        self.raw[0][-1] = 'Patient'
        def failed_read(source, token, sheet=None, **kwargs):
            if kwargs.get('row_range', (0,))[0] == 4:
                raise DataError('Failed block')
            return self.read(source, token, sheet, **kwargs)
        with patch('app.google_summary.BLOCK_ROWS', 2), self.assertRaisesRegex(DataError, 'Failed block'):
            summarize_google({'_id': 'authorized'}, self.config, 'viewer-token', failed_read)

    def test_read_identity_access_and_output_alias_validation(self):
        doc = {'_id': 'authorized', 'kind': 'google'}
        with patch.object(api, 'google_get', side_effect=self.read):
            result, _ = api.table_from_source(doc, self.config, 'viewer-token')
        self.assertEqual(len(result), 4)
        self.assertNotEqual(api.read_config_key(self.config), api.read_config_key(api.ReadConfig(sheet=self.config.sheet)))
        for source in [{'kind': 'upload'}, {'kind': 'google', 'access_mode': 'public'}]:
            with self.assertRaises(DataError):
                api.table_from_source(source, self.config, '')
        self.config.google_summary.row_count = 'Attendances'
        with self.assertRaises(DataError):
            self.run_read()

    def test_conflicting_read_modes_are_rejected(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            api.ReadConfig(**{**self.config.model_dump(), 'aggregate_join': {
                'sheet': 'Other', 'keys': ['Year'], 'left_sums': ['Attendances'], 'right_sums': {'Prior': 'Count'}}})

    def test_section_preserves_record_totals_and_all_professional_choices(self):
        from app.section_snapshots import SectionSnapshot
        for index in range(120):
            self.raw.append([2026, 'Jan', 1, f'Professional {index}', 'Other', f'private-{index}'])
        frame, metadata = self.run_read()
        widget = {'id': 'total', 'config': {**self.config.model_dump(), 'chart_type': 'indicator', 'x_col': 'Year', 'y_col': 'Attendances'}}
        workspace = {'source_id': 'authorized', 'widgets': [widget], 'filter_columns': ['Year', 'Month', 'Professional']}
        snapshot = SectionSnapshot('snapshot', 'workspace', 'viewer', 'fingerprint', {api.widget_read_key(widget): (frame, metadata)}, 0)
        rendered, options = api.render_section_snapshot(workspace, snapshot, {'Year': ['2026'], 'Month': ['Jan']})
        self.assertEqual(len(options['Professional']['values']), 122)
        self.assertEqual(rendered['total']['data']['filtered_rows'], 124)
        self.assertEqual(rendered['total']['data']['datasets'][0]['data'], [123])

    def test_google_request_quotes_sheet_and_bounds_rows(self):
        from urllib.parse import unquote
        with patch.object(api.httpx, 'stream') as stream:
            response = stream.return_value.__enter__.return_value
            response.status_code = 200
            response.iter_bytes.return_value = [b'{"values": []}']
            api.google_get({'spreadsheet_id': 'authorized'}, 'viewer-token', "Data's tab", row_range=(2, 5, 9))
            self.assertTrue(unquote(stream.call_args.args[1]).endswith("/values/'Data''s tab'!A2:I5"))
            self.assertEqual(stream.call_args.kwargs['headers'], {'Authorization': 'Bearer viewer-token'})
            with self.assertRaises(DataError):
                api.google_get({'spreadsheet_id': 'authorized'}, 'viewer-token', 'Data', row_range=(0, 2, 9))
