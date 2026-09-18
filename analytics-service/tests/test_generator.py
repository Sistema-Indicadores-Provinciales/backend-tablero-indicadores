import json
import math
import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import jwt
import pandas as pd
from openpyxl import Workbook
from fastapi.testclient import TestClient
from app.data_engine import DataError, read_rows, table, chart, number, safe, bounded, CHART_TYPES
from app import generator as api
from main import app

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.rows = [["Mes", "Valor", "Grupo"], [2, 0, "A"], [1, 10, "A"], [1, 20, "A"], [1, 8, "B"], [2, None, "B"]]
        self.df, self.meta = table(self.rows)
        self.cfg = {"x_col": "Mes", "y_col": "Valor", "aggregation": "sum", "chart_type": "bar"}

    def test_zeros_and_sum_mean_are_distinct(self):
        out = chart(self.df, self.cfg)
        self.assertEqual(out["labels"], [1, 2])
        self.assertEqual(out["datasets"][0]["data"], [38, 0])
        out = chart(self.df, {**self.cfg, "aggregation": "mean"})
        self.assertAlmostEqual(out["datasets"][0]["data"][0], 38 / 3)
        json.dumps(out, allow_nan=False)

    def test_count_does_not_require_numeric_data(self):
        df, _ = table([["Texto"], ["A"], ["A"], ["B"]])
        out = chart(df, {"x_col": "Texto", "aggregation": "count"})
        self.assertEqual(out["datasets"][0]["data"], [2, 1])

    def test_no_silent_numeric_column_substitution(self):
        with self.assertRaises(DataError):
            chart(self.df, {**self.cfg, "y_col": "Grupo"})

    def test_all_aggregations(self):
        expected = {"sum": 38, "mean": 38/3, "median": 10, "min": 8, "max": 20, "distinct": 3, "count": 3}
        for agg, value in expected.items():
            with self.subTest(agg=agg):
                self.assertAlmostEqual(chart(self.df, {**self.cfg, "aggregation": agg})["datasets"][0]["data"][0], value)

    def test_every_chart_type(self):
        for kind in CHART_TYPES:
            with self.subTest(kind=kind):
                out = chart(self.df, {**self.cfg, "chart_type": kind})
                json.dumps(out, allow_nan=False)

    def test_pie_rejects_negative(self):
        df, _ = table([["x", "y"], ["a", -1], ["b", 2]])
        with self.assertRaises(DataError):
            chart(df, {"x_col": "x", "y_col": "y", "aggregation": "sum", "chart_type": "pie"})

    def test_scatter_keeps_each_observation(self):
        out = chart(self.df, {**self.cfg, "chart_type": "scatter"})
        self.assertEqual(len(out["datasets"][0]["data"]), 4)
        self.assertEqual(out["datasets"][0]["x"], [2, 1, 1, 1])

    def test_grouping_retains_zero_and_missing(self):
        out = chart(self.df, {**self.cfg, "group_col": "Grupo"})
        self.assertEqual(out["datasets"][0]["data"], [30, 0])
        self.assertEqual(out["datasets"][1]["data"], [8, None])

    def test_group_same_as_axis(self):
        out = chart(self.df, {**self.cfg, "group_col": "Mes"})
        self.assertEqual(len(out["datasets"]), 2)

    def test_filters_and_no_results(self):
        out = chart(self.df, {**self.cfg, "filters": {"Grupo": ["B"]}})
        self.assertEqual(out["datasets"][0]["data"], [8])
        self.assertEqual(chart(self.df, {**self.cfg, "filters": {"Grupo": ["Z"]}})["filtered_rows"], 0)
        with self.assertRaises(DataError):
            chart(self.df, {**self.cfg, "filters": {"No existe": ["Z"]}})

    def test_formats_currency_percent_and_nonfinite(self):
        self.assertEqual(number("$ 1.234,56"), 1234.56)
        self.assertEqual(number("1,234.56", "."), 1234.56)
        self.assertEqual(number("(25,5)"), -25.5)
        self.assertEqual(number("12,5%"), .125)
        self.assertIsNone(number("abc"))
        self.assertIsNone(number("1e999"))
        self.assertIsNone(safe(float('inf')))
        self.assertIsNone(safe(float('nan')))

    def test_duplicate_blank_and_leading_zero(self):
        df, meta = table([["ID", "ID", None], ["001", "002", 0]])
        self.assertEqual(meta["columns"], ["ID", "ID (2)", "Columna 3"])
        self.assertEqual(df.iloc[0, 0], "001")

    def test_mixed_data_explicit_conversion(self):
        df, meta = table([["x"], ["2,5"], ["desconocido"], [None]])
        self.assertEqual(meta["column_meta"]["x"]["type"], "text")
        df, meta = table([["x"], ["2,5"], ["desconocido"]], types={"x": "number"})
        self.assertEqual(df.iloc[0, 0], 2.5)
        self.assertIsNone(df.iloc[1, 0])
        self.assertEqual(meta["column_meta"]["x"]["invalid"], 1)

    def test_headers_none_and_offset(self):
        df, _ = table([["Título"], ["A", "B"], ["x", 3]], header=2)
        self.assertEqual(list(df.columns), ["A", "B"])
        df, _ = table([["x", 3]], header=0)
        self.assertEqual(len(df), 1)
        with self.assertRaises(DataError): table([], header=1)
        with self.assertRaises(DataError): table([["A"]], header=2)

    def test_boolean_date_and_month_bucket(self):
        df, meta = table([["Fecha", "SíNo", "Valor"], ["2025-01-02", True, 2], ["2025-01-20", False, 3]])
        self.assertEqual(meta["column_meta"]["SíNo"]["type"], "boolean")
        out = chart(df, {**self.cfg, "x_col": "Fecha", "date_bucket": "month"})
        self.assertEqual(out["labels"], ["2025-01"])
        self.assertEqual(out["datasets"][0]["data"], [5])

    def test_indicator_mean_not_mean_of_means(self):
        out = chart(self.df, {**self.cfg, "chart_type": "indicator", "aggregation": "mean"})
        self.assertEqual(out["datasets"][0]["data"], [9.5])

    def test_table_returns_columns_and_zeros(self):
        out = chart(self.df, {**self.cfg, "chart_type": "table"})
        self.assertEqual(out["records"][0]["Valor"], 0)
        self.assertIn("Grupo", out["columns"])

    def test_csv_and_real_workbook(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.csv'
            path.write_text('Nombre;Total\nÁrbol;"1.234,56"\n', encoding='utf-8-sig')
            df, _ = table(read_rows(path, 'Datos'))
            self.assertEqual(df.iloc[0, 1], 1234.56)
            book = Workbook(); sheet = book.active; sheet.title = 'Datos'
            for row in self.rows: sheet.append(row)
            path = Path(directory) / 'test.XLSX'; book.save(path)
            self.assertEqual(read_rows(path), ['Datos'])
            df, _ = table(read_rows(path, 'Datos'))
            self.assertEqual(chart(df, self.cfg)['datasets'][0]['data'], [38, 0])

    def test_bounded_input(self):
        with self.assertRaises(DataError): bounded([[0] * 257])
        with patch('app.data_engine.MAX_ROWS', 2):
            with self.assertRaises(DataError): bounded([[1], [2], [3]])

    def test_actual_coparticipacion(self):
        path = Path('data/economia/BD ECONOMIA - COPARTICIPACION.xlsx')
        df, meta = table(read_rows(path, 'CFI Y TOTALES - FSA'))
        self.assertGreater(meta['row_count'], 900)
        out = chart(df, {'x_col': 'MES', 'y_col': 'TOTAL', 'aggregation': 'sum', 'group_col': 'CATEGORIA'})
        self.assertTrue(out['datasets'])
        json.dumps(out, allow_nan=False)

class APITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        self.env = patch.dict(os.environ, {'JWT_SECRET': 'test-only-secret-at-least-32-characters'})
        self.env.start()
        self.token = jwt.encode({'sub': 'user-a', 'exp': time.time() + 60, 'tokenUse': 'access'}, os.environ['JWT_SECRET'], algorithm='HS256')
        self.headers = {'Authorization': 'Bearer ' + self.token}
    def tearDown(self): self.env.stop()

    def test_unauthenticated(self):
        self.assertEqual(self.client.get('/v2/sources').status_code, 401)

    def test_refresh_token_cannot_read_sources(self):
        token = jwt.encode({'sub': 'user-a', 'exp': time.time() + 60, 'tokenUse': 'refresh'}, os.environ['JWT_SECRET'], algorithm='HS256')
        self.assertEqual(self.client.get('/v2/sources', headers={'Authorization': 'Bearer ' + token}).status_code, 401)

    def test_owner_filter_and_not_found(self):
        database = MagicMock(); database.analytics_sources.find_one.return_value = None
        with patch.object(api, 'db', return_value=database):
            response = self.client.get('/v2/sources/not-mine/sheets', headers=self.headers)
        self.assertEqual(response.status_code, 404)
        query = database.analytics_sources.find_one.call_args.args[0]
        self.assertEqual(query['$or'], [{'owner': 'user-a'}, {'kind': 'system'}])

    def test_spreadsheet_urls_are_not_arbitrary_fetches(self):
        for url in ['http://127.0.0.1/', 'https://docs.google.com.evil.org/spreadsheets/d/' + 'a'*30, 'https://example.com/x']:
            with self.assertRaises(DataError): api.spreadsheet_id(url)
        self.assertEqual(api.spreadsheet_id('https://docs.google.com/spreadsheets/d/' + 'a'*30 + '/edit'), 'a'*30)

    def test_invalid_configuration_is_422(self):
        response = self.client.post('/v2/sources/test/preview', headers=self.headers, json={'sheet': 'Hoja', 'header_row': -1})
        self.assertEqual(response.status_code, 422)

    def test_preview_and_chart_over_http(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'a.csv'; path.write_text('x,y\na,0\nb,4\n')
            with patch.object(api, 'source_for', return_value={'kind': 'upload', 'path': str(path)}), patch.object(api, 'local_path', return_value=path):
                preview = self.client.post('/v2/sources/test/preview', headers=self.headers, json={'sheet': 'Datos'})
                self.assertEqual(preview.status_code, 200, preview.text)
                response = self.client.post('/v2/sources/test/chart', headers=self.headers, json={'sheet': 'Datos', 'x_col': 'x', 'y_col': 'y', 'aggregation': 'sum'})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['datasets'][0]['data'], [0, 4])

    def test_google_token_never_persisted(self):
        database = MagicMock()
        with patch.object(api, 'db', return_value=database), patch.object(api, 'read_public_rows', side_effect=api.HTTPException(409, 'Privada')), patch.object(api, 'google_get', return_value={'properties': {'title': 'Prueba'}, 'sheets': [{'properties': {'title': 'Datos'}}]}):
            response = self.client.post('/v2/sources/google', headers={**self.headers, 'X-Google-Access-Token': 'private-google-token'}, json={'url': 'a'*30})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn('private-google-token', json.dumps(database.analytics_sources.insert_one.call_args.args[0]))

    def test_save_workspace_owner(self):
        database = MagicMock()
        with patch.object(api, 'db', return_value=database), patch.object(api, 'source_for', return_value={}):
            response = self.client.post('/v2/workspaces', headers=self.headers, json={'name': 'Personal', 'source_id': 'my-source', 'widgets': []})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(database.analytics_workspaces.insert_one.call_args.args[0]['owner'], 'user-a')

    def test_upload_registers_private_source_and_opaque_filename(self):
        database = MagicMock()
        with tempfile.TemporaryDirectory() as directory, patch.object(api, 'UPLOADS', Path(directory)), patch.object(api, 'db', return_value=database):
            response = self.client.post('/v2/sources/upload', headers=self.headers, files={'file': ('../report.csv', b'x,y\\na,0\\n'.replace(b'\\n', b'\n'), 'text/csv')})
            self.assertEqual(response.status_code, 200, response.text)
            doc = database.analytics_sources.insert_one.call_args.args[0]
            self.assertEqual(doc['owner'], 'user-a')
            self.assertEqual(Path(doc['path']).name, doc['path'])
            self.assertTrue((Path(directory) / doc['path']).exists())
            self.assertNotIn('path', response.json())

    def test_corrupt_upload_returns_friendly_error_and_removes_file(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(api, 'UPLOADS', Path(directory)):
            response = self.client.post('/v2/sources/upload', headers=self.headers, files={'file': ('bad.xlsx', b'not an excel', 'application/octet-stream')})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_google_permission_error_is_actionable(self):
        import httpx
        with patch.object(api.httpx, 'stream') as stream:
            stream.return_value.__enter__.return_value = httpx.Response(403)
            with self.assertRaises(Exception) as error:
                api.google_get({'spreadsheet_id': 'a' * 30}, 'token')
            self.assertEqual(error.exception.status_code, 409)

    def test_google_values_response_is_bounded_and_read_only(self):
        import httpx
        response = httpx.Response(200, json={'values': [['x'], [1]]})
        with patch.object(api.httpx, 'stream') as stream:
            stream.return_value.__enter__.return_value = response
            self.assertEqual(api.google_get({'spreadsheet_id': 'a' * 30}, 'token', "Hoja's"), {'values': [['x'], [1]]})
            self.assertEqual(stream.call_args.args[0], 'GET')
            self.assertTrue(stream.call_args.args[1].startswith('https://sheets.googleapis.com/'))
        response = httpx.Response(200, content=b'123456')
        with patch.object(api, 'MAX_BYTES', 3), patch.object(api.httpx, 'stream') as stream:
            stream.return_value.__enter__.return_value = response
            with self.assertRaises(DataError): api.google_get({'spreadsheet_id': 'a' * 30}, 'token')

    def test_cannot_overwrite_other_users_workspace(self):
        database = MagicMock(); database.analytics_workspaces.find_one.return_value = None
        with patch.object(api, 'db', return_value=database), patch.object(api, 'source_for', return_value={}):
            response = self.client.put('/v2/workspaces/not-mine', headers=self.headers, json={'name': 'Test', 'source_id': 'mine', 'widgets': []})
            self.assertEqual(response.status_code, 404)
            self.assertEqual(database.analytics_workspaces.find_one.call_args.args[0], {'_id': 'not-mine', 'owner': 'user-a', 'deletedAt': None})
            database.analytics_workspaces.update_one.assert_not_called()

    def test_missing_local_file(self):
        with self.assertRaises(Exception) as ctx:
            api.local_path({'kind': 'upload', 'path': '../not-allowed.csv'})
        self.assertEqual(ctx.exception.status_code, 404)

if __name__ == '__main__': unittest.main()
