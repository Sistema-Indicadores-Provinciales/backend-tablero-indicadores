import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import httpx
from fastapi import HTTPException
from app import generator as api, google_oauth as oauth
import test_workspace_access as fixtures

CLIENT_ID = '123-example.apps.googleusercontent.com'
SECRET = 'test-client-secret-for-google'
ORIGIN = 'https://indicators.example.test'


class ConnectionTests(unittest.TestCase):
    setUp = fixtures.WorkspaceAccessTests.setUp
    tearDown = fixtures.WorkspaceAccessTests.tearDown
    headers = fixtures.WorkspaceAccessTests.headers
    publish = fixtures.WorkspaceAccessTests.publish
    view = fixtures.WorkspaceAccessTests.view

    def configure(self):
        response = self.client.put('/v2/google/settings', headers=self.headers(self.owner), json={'client_id': CLIENT_ID, 'client_secret': SECRET})
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def connect(self, uid=None, token='reader-access'):
        self.configure()
        headers = {**self.headers(uid or self.viewer), 'Origin': ORIGIN, 'X-Requested-With': 'XMLHttpRequest'}
        with patch.dict(os.environ, {'CORS_ORIGINS': ORIGIN}), patch.object(oauth.httpx, 'post', return_value=httpx.Response(200, json={
            'access_token': token, 'refresh_token': 'refresh-' + token, 'expires_in': 3600, 'scope': oauth.SCOPE,
        })) as request:
            response = self.client.post('/v2/google/connect', headers=headers, json={'code': 'single-use-code', 'client_id': CLIENT_ID})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(request.call_args.kwargs['data']['redirect_uri'], ORIGIN)
            self.assertEqual(request.call_args.kwargs['data']['client_secret'], SECRET)
        return response

    def expire(self, uid=None):
        uid = str(uid or self.viewer)
        saved = self.db.analytics_google_connections.find_one({'_id': uid})
        value = oauth.unseal(saved['credentials'], 'user:' + uid)
        value['expires_at'] = time.time() - 1
        self.db.analytics_google_connections.update_one({'_id': uid}, {'$set': {'credentials': oauth.seal(value, 'user:' + uid)}})

    def test_secret_is_admin_only_encrypted_not_returned_and_preserved_when_blank(self):
        response = self.configure()
        self.assertTrue(response.json()['persistent_available'])
        self.assertNotIn(SECRET, response.text)
        self.assertNotIn(SECRET, str(self.db.analytics_settings.find_one()))
        self.assertEqual(oauth.credentials(self.db), (CLIENT_ID, SECRET))
        self.client.put('/v2/google/settings', headers=self.headers(self.owner), json={'client_id': CLIENT_ID})
        self.assertEqual(oauth.credentials(self.db), (CLIENT_ID, SECRET))
        denied = self.client.put('/v2/google/settings', headers=self.headers(self.viewer), json={'client_id': CLIENT_ID, 'client_secret': 'another-secret'})
        self.assertEqual(denied.status_code, 403)
        # Changing clients invalidates the old secret, including environment fallback from another client.
        self.client.put('/v2/google/settings', headers=self.headers(self.owner), json={'client_id': 'other.apps.googleusercontent.com'})
        self.assertEqual(oauth.credentials(self.db)[1], '')

    def test_validation_does_not_echo_sensitive_input(self):
        response = self.client.put('/v2/google/settings', headers=self.headers(self.owner), json={'client_id': CLIENT_ID, 'client_secret': {'value': SECRET}})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(SECRET, response.text)

    def test_connection_survives_fresh_http_request_and_is_scoped_to_user(self):
        response = self.connect()
        self.assertTrue(response.json()['connected'])
        self.assertNotIn('reader-access', response.text)
        saved = self.db.analytics_google_connections.find_one()
        self.assertNotIn('reader-access', str(saved))
        self.assertNotIn('single-use-code', str(saved))
        self.assertTrue(self.client.get('/v2/google/status', headers=self.headers(self.viewer)).json()['connected'])
        self.assertFalse(self.client.get('/v2/google/status', headers=self.headers(self.other)).json()['connected'])
        self.assertEqual(oauth.access_token(self.db, str(self.viewer)), 'reader-access')
        self.assertEqual(oauth.access_token(self.db, str(self.other)), '')

    def test_connect_checks_origin_custom_header_client_and_logged_in_user_before_exchange(self):
        self.configure()
        body = {'code': 'single-use-code', 'client_id': CLIENT_ID}
        with patch.dict(os.environ, {'CORS_ORIGINS': ORIGIN}), patch.object(oauth, 'token_request') as exchange:
            base = self.headers(self.viewer)
            for headers in [base, {**base, 'Origin': ORIGIN}, {**base, 'Origin': 'https://evil.example', 'X-Requested-With': 'XMLHttpRequest'}]:
                self.assertEqual(self.client.post('/v2/google/connect', json=body, headers=headers).status_code, 403)
            good = {**base, 'Origin': ORIGIN, 'X-Requested-With': 'XMLHttpRequest'}
            self.assertEqual(self.client.post('/v2/google/connect', json={**body, 'client_id': 'wrong'}, headers=good).status_code, 409)
            self.assertEqual(self.client.post('/v2/google/connect', json=body).status_code, 401)
            exchange.assert_not_called()

    def test_expired_token_refreshes_once_for_concurrent_charts(self):
        self.connect()
        self.expire()
        with patch.object(oauth, 'token_request', return_value={'access_token': 'renewed', 'expires_in': 3600}) as request:
            with ThreadPoolExecutor(max_workers=3) as executor:
                result = list(executor.map(lambda _: oauth.access_token(self.db, str(self.viewer)), range(3)))
        self.assertEqual(result, ['renewed'] * 3)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0]['grant_type'], 'refresh_token')
        self.assertNotIn('renewed', str(self.db.analytics_google_connections.find_one()))

    def test_expired_google_permission_needs_reconnect_without_logging_out_app(self):
        self.connect(self.owner, 'owner-access')
        self.connect()
        self.expire()
        with patch.object(oauth.httpx, 'post', return_value=httpx.Response(400, json={'error': 'invalid_grant'})):
            with self.assertRaises(HTTPException) as error:
                oauth.access_token(self.db, str(self.viewer))
        self.assertEqual(error.exception.status_code, 409)
        self.assertIsNone(self.db.analytics_google_connections.find_one({'_id': str(self.viewer)}))
        self.assertEqual(oauth.access_token(self.db, str(self.owner)), 'owner-access')

    def test_transient_google_error_does_not_delete_grant(self):
        self.connect()
        self.expire()
        with patch.object(oauth.httpx, 'post', return_value=httpx.Response(503, json={'error': 'unavailable'})):
            with self.assertRaises(HTTPException) as error:
                oauth.access_token(self.db, str(self.viewer))
        self.assertEqual(error.exception.status_code, 502)
        self.assertIsNotNone(self.db.analytics_google_connections.find_one())

    def test_disconnect_removes_only_callers_grant_even_if_google_is_offline(self):
        self.connect(self.owner, 'owner-access')
        self.connect()
        with patch.object(oauth.httpx, 'post', side_effect=httpx.ConnectError('offline')):
            response = self.client.delete('/v2/google/connection', headers=self.headers(self.viewer))
        self.assertEqual(response.json(), {'connected': False, 'revoked': False})
        self.assertEqual(oauth.access_token(self.db, str(self.viewer)), '')
        self.assertEqual(oauth.access_token(self.db, str(self.owner)), 'owner-access')

    def test_disconnect_during_refresh_is_not_undone(self):
        self.connect()
        self.expire()
        def request(_):
            self.db.analytics_google_connections.delete_one({'_id': str(self.viewer)})
            return {'access_token': 'renewed', 'expires_in': 3600}
        with patch.object(oauth, 'token_request', side_effect=request), self.assertRaises(HTTPException):
            oauth.access_token(self.db, str(self.viewer))
        self.assertEqual(self.db.analytics_google_connections.count_documents({}), 0)

    def test_account_switch_without_refresh_permission_does_not_mix_accounts(self):
        self.connect()
        with patch.object(oauth, 'token_request', return_value={'access_token': 'different-account'}), self.assertRaises(HTTPException):
            oauth.connect(self.db, str(self.viewer), 'new-code', ORIGIN)
        self.assertEqual(oauth.access_token(self.db, str(self.viewer)), 'reader-access')

    def test_deleted_user_or_copied_ciphertext_cannot_use_a_connection(self):
        self.connect()
        saved = self.db.analytics_google_connections.find_one()
        self.db.analytics_google_connections.insert_one({**saved, '_id': str(self.other)})
        with self.assertRaises(HTTPException):
            oauth.access_token(self.db, str(self.other))
        self.db.users.delete_one({'_id': self.viewer})
        with self.assertRaises(HTTPException) as error:
            oauth.access_token(self.db, str(self.viewer))
        self.assertEqual(error.exception.status_code, 401)

    def test_saved_private_chart_uses_viewers_google_account_not_creators(self):
        self.connect(self.owner, 'owner-access')
        self.connect()
        self.publish([self.owner, self.viewer, self.other])
        self.db.analytics_sources.update_one({'_id': 'private-file'}, {'$set': {'kind': 'google', 'spreadsheet_id': 'a' * 30}})
        tokens = []
        def read(source, token, sheet=None):
            tokens.append(token)
            if not token:
                raise HTTPException(409, 'Conectá Google.')
            return {'values': [['x', 'y'], ['a', 10], ['b', 20]]}
        with patch.object(api, 'google_get', side_effect=read):
            response = self.view(suffix='widgets/chart-a/chart')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['datasets'][0]['data'], [10])
            self.assertEqual(self.view(self.other, suffix='widgets/chart-a/chart').status_code, 409)
        self.assertEqual(tokens, ['reader-access', ''])

    def test_private_sheet_import_and_preview_reuse_saved_connection(self):
        self.connect(self.owner, 'owner-access')
        with patch.object(api, 'read_public_rows', side_effect=HTTPException(409, 'Privada')), patch.object(api, 'google_get', return_value={
            'properties': {'title': 'Privada'}, 'sheets': [{'properties': {'title': 'Datos'}}], 'values': [['x', 'y'], ['a', 10]],
        }) as read:
            source = self.client.post('/v2/sources/google', json={'url': 'https://docs.google.com/spreadsheets/d/' + 'a' * 30}, headers=self.headers(self.owner))
            self.assertEqual(source.status_code, 200, source.text)
            sid = source.json()['_id']
            self.assertEqual(self.client.post(f'/v2/sources/{sid}/preview', json={'sheet': 'Datos'}, headers=self.headers(self.owner)).status_code, 200)
            self.assertEqual(self.client.get(f'/v2/sources/{sid}/sheets', headers=self.headers(self.owner)).status_code, 200)
            self.assertTrue(all(call.args[1] == 'owner-access' for call in read.call_args_list))
