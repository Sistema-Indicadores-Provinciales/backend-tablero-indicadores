import os
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException
from app import google_public as public
from app import generator as api
from app.data_engine import DataError
import test_workspace_access as fixtures

SID = 'a' * 30
URL = f'https://docs.google.com/spreadsheets/d/{SID}/edit#gid=42'
PUBLISHED = f'https://docs.google.com/spreadsheets/d/e/{SID}/pubhtml?gid=42'
CLIENT = httpx.Client


class PublicDownloadTests(unittest.TestCase):
    def fetch(self, handler, source=None):
        def client(**options):
            return CLIENT(transport=httpx.MockTransport(handler), **options)
        with patch.object(public.httpx, 'Client', side_effect=client):
            return public.read_public_rows(source or public.parse_link(URL))

    def test_links_preserve_tab_and_discard_other_parameters(self):
        source = public.parse_link(URL.replace('/edit', '/edit?authuser=1&range=A1'))
        self.assertEqual(source, {'spreadsheet_id': SID, 'gid': '42'})
        self.assertEqual(public.export_url(source), f'https://docs.google.com/spreadsheets/d/{SID}/export?format=csv&gid=42')
        self.assertEqual(public.export_url(public.parse_link(PUBLISHED)), f'https://docs.google.com/spreadsheets/d/e/{SID}/pub?output=csv&gid=42')
        self.assertEqual(public.parse_link(URL.replace('/d/', '/u/0/d/')), source)
        self.assertEqual(public.parse_link(SID), {'spreadsheet_id': SID})

    def test_arbitrary_hosts_paths_credentials_and_invalid_tabs_are_rejected(self):
        for link in [URL.replace('https:', 'http:'), URL.replace('docs.google.com', 'docs.google.com.evil.test'),
                     URL.replace('docs.google.com', 'user@docs.google.com'), URL.replace('docs.google.com', 'docs.google.com:443'),
                     URL.replace('/spreadsheets/', '/document/'), URL.replace('gid=42', 'gid=../'),
                     URL.replace('gid=42', 'gid=42&gid=9'), 'https://127.0.0.1/' + SID]:
            with self.subTest(link=link), self.assertRaises(DataError):
                public.parse_link(link)

    def test_csv_preserves_zeros_identifiers_accents_and_quoted_multiline_cells(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, headers={'content-type': 'text/csv; charset=utf-8'},
                                  content='\ufeffID,Valor,Detalle\r\n001,0,"Árbol, azul\nsegunda línea"\r\n'.encode())
        rows = self.fetch(handler)
        self.assertEqual(rows[1], ['001', '0', 'Árbol, azul\nsegunda línea'])
        self.assertEqual(len(requests), 1)
        self.assertNotIn('authorization', requests[0].headers)
        self.assertNotIn('cookie', requests[0].headers)
        self.assertEqual(requests[0].url.params['gid'], '42')

    def test_only_google_export_redirects_are_followed(self):
        requests = []
        def handler(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(307, headers={'location': 'https://doc-0a-00-sheets.googleusercontent.com/export/data'})
            return httpx.Response(200, headers={'content-type': 'text/csv'}, content=b'x,y\na,0')
        self.assertEqual(self.fetch(handler)[1], ['a', '0'])
        self.assertEqual(len(requests), 2)

    def test_untrusted_redirects_are_not_requested(self):
        for location in ['http://docs.google.com/export', 'https://accounts.google.com/login', 'http://169.254.169.254/',
                         'https://doc-1.googleusercontent.com.evil.test/', 'https://example.com/data', 'https://docs.google.com:8443/']:
            requests = []
            def handler(request):
                requests.append(request)
                return httpx.Response(302, headers={'location': location})
            with self.subTest(location=location), self.assertRaises(HTTPException) as error:
                self.fetch(handler)
            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(len(requests), 1)

    def test_redirect_loops_and_upstream_errors_are_bounded(self):
        requests = []
        def loop(request):
            requests.append(request)
            return httpx.Response(302, headers={'location': '/loop'})
        with self.assertRaises(HTTPException) as error:
            self.fetch(loop)
        self.assertEqual(error.exception.status_code, 502)
        self.assertEqual(len(requests), 5)
        for status, expected in [(401, 409), (403, 409), (404, 409), (429, 429), (503, 502)]:
            with self.subTest(status=status), self.assertRaises(HTTPException) as error:
                self.fetch(lambda request: httpx.Response(status))
            self.assertEqual(error.exception.status_code, expected)

    def test_login_html_is_never_imported_as_data(self):
        for content_type in ['text/html', 'text/csv']:
            with self.subTest(content_type=content_type), self.assertRaises(HTTPException) as error:
                self.fetch(lambda request: httpx.Response(200, headers={'content-type': content_type}, content=b'  <!doctype html><html>Sign in</html>'))
            self.assertEqual(error.exception.status_code, 409)

    def test_download_bytes_and_table_size_are_limited(self):
        def csv_response(request):
            return httpx.Response(200, headers={'content-type': 'text/csv'}, content=b'x,y\na,0\nb,1')
        with patch.object(public, 'MAX_BYTES', 4), self.assertRaises(DataError):
            self.fetch(csv_response)
        with patch('app.data_engine.MAX_ROWS', 2), self.assertRaises(DataError):
            self.fetch(csv_response)
        with patch('app.data_engine.MAX_COLS', 1), self.assertRaises(DataError):
            self.fetch(csv_response)

    def test_empty_or_malformed_csv_is_rejected(self):
        for body in [b'', b'x\n"unfinished', b'\xff']:
            with self.subTest(body=body), self.assertRaises(DataError):
                self.fetch(lambda request: httpx.Response(200, headers={'content-type': 'text/csv'}, content=body))


class PublicSourceTests(unittest.TestCase):
    # Reuse the isolated Mongo/JWT fixture, without inheriting and rerunning its tests.
    setUp = fixtures.WorkspaceAccessTests.setUp
    tearDown = fixtures.WorkspaceAccessTests.tearDown
    headers = fixtures.WorkspaceAccessTests.headers
    publish = fixtures.WorkspaceAccessTests.publish
    view = fixtures.WorkspaceAccessTests.view

    def link(self, url=URL, token=''):
        return self.client.post('/v2/sources/google', headers={**self.headers(self.owner), 'X-Google-Access-Token': token}, json={'url': url})

    def test_public_source_preview_and_saved_chart_refresh_without_google_credentials(self):
        rows = [['x', 'y'], ['a', '0'], ['b', '4']]
        with patch.dict(os.environ, {'GOOGLE_SHEETS_API_KEY': ''}), patch.object(api, 'read_public_rows', return_value=rows) as read:
            linked = self.link()
            self.assertEqual(linked.status_code, 200, linked.text)
            sid = linked.json()['_id']
            self.assertEqual(linked.json()['access_mode'], 'public')
            self.assertEqual(self.client.get(f'/v2/sources/{sid}/sheets', headers=self.headers(self.owner)).json(), [public.SHEET_NAME])
            preview = self.client.post(f'/v2/sources/{sid}/preview', headers=self.headers(self.owner), json={'sheet': public.SHEET_NAME})
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.json()['preview'][0]['y'], 0)
            self.db.analytics_workspaces.update_one({'_id': 'workspace-a'}, {'$set': {'source_id': sid, 'widgets.0.config.sheet': public.SHEET_NAME}})
            self.assertEqual(self.publish([self.owner, self.viewer]).status_code, 200)
            self.assertEqual(self.view().json()['source_access_mode'], 'public')
            self.assertEqual(self.view(suffix='widgets/chart-a/chart').json()['datasets'][0]['data'], [0])
            rows[1][1] = '8'
            self.assertEqual(self.view(suffix='widgets/chart-a/chart').json()['datasets'][0]['data'], [8])
            self.assertEqual(read.call_count, 4)
            # Source exploration remains owner-only, and section permissions still apply.
            self.assertEqual(self.client.get(f'/v2/sources/{sid}/sheets', headers=self.headers(self.viewer)).status_code, 404)
            self.db.users.update_one({'_id': self.viewer}, {'$set': {'access': []}})
            self.assertEqual(self.view().status_code, 404)
            self.assertEqual(read.call_count, 4)
            read.side_effect = HTTPException(409, 'La hoja dejó de ser pública.')
            self.assertEqual(self.view(self.owner, suffix='widgets/chart-a/chart').status_code, 409)

    def test_public_links_stay_public_even_when_a_google_account_is_connected(self):
        for url in [URL, PUBLISHED]:
            with self.subTest(url=url), patch.object(api, 'read_public_rows', return_value=[['x'], ['0']]), patch.object(api, 'google_get') as private:
                response = self.link(url, 'must-not-persist')
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['access_mode'], 'public')
                private.assert_not_called()
                saved = self.db.analytics_sources.find_one({'_id': response.json()['_id']})
                self.assertNotIn('must-not-persist', str(saved))
                self.assertNotIn('values', saved)

    def test_private_sheet_requires_connection_and_published_link_cannot_fall_back_to_oauth(self):
        with patch.dict(os.environ, {'GOOGLE_SHEETS_API_KEY': ''}), patch.object(api, 'read_public_rows', side_effect=HTTPException(409, 'Conectá Google.')), patch.object(api, 'google_get') as private:
            self.assertEqual(self.link().status_code, 409)
            self.assertEqual(self.link(PUBLISHED, 'test-token').status_code, 409)
            private.assert_not_called()
            self.assertEqual(self.db.analytics_sources.count_documents({'kind': 'google'}), 0)
            private.return_value = {'properties': {'title': 'Privada'}, 'sheets': [{'properties': {'title': 'Datos'}}]}
            linked = self.link(URL, 'test-token')
            self.assertEqual(linked.status_code, 200, linked.text)
            self.assertNotIn('access_mode', linked.json())
            self.assertEqual(linked.json()['sheets'], ['Datos'])
            private.assert_called_once_with({'spreadsheet_id': SID, 'gid': '42'}, 'test-token')

    def test_public_status_available_without_api_key_and_invalid_link_never_saved(self):
        with patch.dict(os.environ, {'GOOGLE_SHEETS_API_KEY': ''}):
            self.assertTrue(self.client.get('/v2/google/status', headers=self.headers(self.owner)).json()['public_access'])
            self.assertEqual(self.link('https://example.com/sheet').status_code, 422)
            self.assertEqual(self.db.analytics_sources.count_documents({'kind': 'google'}), 0)
