import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import jwt
import mongomock
from bson import ObjectId
from fastapi.testclient import TestClient
from app import generator as api
from app import workspace_access as access
from main import app


class WorkspaceAccessTests(unittest.TestCase):
    def setUp(self):
        self.db = mongomock.MongoClient().test
        self.owner, self.viewer, self.other = [ObjectId() for _ in range(3)]
        for uid, name, role in [(self.owner, 'Creador', 'ADMIN'), (self.viewer, 'Lector', 'INVITADO'), (self.other, 'Otro', 'INVITADO')]:
            self.db.users.insert_one({'_id': uid, 'username': name, 'profileType': role, 'access': [], 'password': 'must-not-return'})
        self.workspace = {'_id': 'workspace-a', 'owner': str(self.owner), 'name': 'Economía semanal', 'source_id': 'private-file', 'widgets': [{
            'id': 'chart-a', 'title': 'Total', 'width': 6, 'library': 'Plotly',
            'config': {'sheet': 'Datos', 'chart_type': 'bar', 'aggregation': 'sum', 'x_col': 'x', 'y_col': 'y', 'filters': {'x': ['a']}},
        }]}
        self.db.analytics_workspaces.insert_one(self.workspace)
        self.db.analytics_sources.insert_one({'_id': 'private-file', 'owner': str(self.owner), 'name': 'Privado', 'kind': 'upload', 'path': 'private.csv'})
        self.client = TestClient(app, raise_server_exceptions=False)
        self.env = patch.dict(os.environ, {'JWT_SECRET': 'workspace-test-secret-at-least-32-characters'})
        self.env.start()
        self.mock_db = patch.object(api, 'db', return_value=self.db)
        self.mock_db.start()

    def tearDown(self):
        self.mock_db.stop()
        self.env.stop()

    def headers(self, uid):
        token = jwt.encode({'sub': str(uid), 'exp': time.time() + 60, 'tokenUse': 'access', 'profileType': 'ADMIN'}, os.environ['JWT_SECRET'], algorithm='HS256')
        return {'Authorization': 'Bearer ' + token}

    def publish(self, recipients=None, uid=None):
        body = {'icon': 'material-symbols:bar-chart-rounded'}
        if recipients is not None:
            body['recipientIds'] = [str(u) for u in recipients]
        return self.client.put('/v2/workspaces/workspace-a/publication', headers=self.headers(uid or self.owner), json=body)

    def view(self, uid=None, suffix='view'):
        return self.client.get('/v2/workspaces/workspace-a/' + suffix, headers=self.headers(uid or self.viewer))

    def test_restore_recovers_only_own_workspaces_once(self):
        self.db.analytics_workspaces.insert_one({**self.workspace, '_id': 'other-workspace', 'owner': str(self.other)})
        first = self.client.post('/v2/workspaces/restore-menu', headers=self.headers(self.owner))
        second = self.client.post('/v2/workspaces/restore-menu', headers=self.headers(self.owner))
        self.assertEqual(first.json(), {'restored': 1})
        self.assertEqual(second.json(), {'restored': 0})
        self.assertEqual(self.db.dashboards.count_documents({}), 1)
        self.assertEqual(self.db.sections.count_documents({}), 1)
        self.assertEqual(self.view(self.owner).status_code, 200)
        self.assertEqual(self.view().status_code, 404)

    def test_publication_uses_existing_user_access_and_retries_do_not_duplicate(self):
        first = self.publish([self.owner, self.viewer])
        second = self.publish([self.owner, self.viewer, self.viewer])
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.json()['path'], first.json()['path'])
        self.assertEqual(self.db.dashboards.count_documents({}), 1)
        self.assertEqual(len(self.db.users.find_one({'_id': self.viewer})['access']), 1)
        self.assertTrue(self.view().json()['can_edit'] is False)
        self.assertEqual(self.view(self.other).status_code, 404)
        self.assertNotIn('password', str(first.json()))

    def test_viewer_only_gets_saved_chart_and_cannot_read_source_or_edit(self):
        self.assertEqual(self.publish([self.owner, self.viewer]).status_code, 200)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private.csv'
            path.write_text('x,y\na,0\nb,99\n')
            with patch.object(api, 'local_path', return_value=path):
                response = self.view(suffix='widgets/chart-a/chart?chart_type=table&filters={}')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['datasets'][0]['data'], [0])
        self.assertEqual(response.json()['filtered_rows'], 1)
        self.assertEqual(self.view(suffix='widgets/unknown/chart').status_code, 404)
        self.assertEqual(self.client.get('/v2/sources/private-file/sheets', headers=self.headers(self.viewer)).status_code, 404)
        self.assertEqual(self.client.get('/v2/workspaces/workspace-a', headers=self.headers(self.viewer)).status_code, 404)
        self.assertEqual(self.publish([], self.viewer).status_code, 404)

    def test_revoke_from_existing_user_access_takes_effect_with_same_token(self):
        self.publish([self.owner, self.viewer])
        self.db.users.update_one({'_id': self.viewer}, {'$set': {'access': []}})
        self.assertEqual(self.view().status_code, 404)
        self.assertEqual(self.view(suffix='widgets/chart-a/chart').status_code, 404)

    def test_hidden_dashboard_section_or_detached_section_denies_view(self):
        self.publish([self.owner, self.viewer])
        for collection, change, reset in [
            (self.db.dashboards, {'show': False}, {'show': True}),
            (self.db.sections, {'show': False}, {'show': True}),
            (self.db.dashboards, {'sections': []}, {'sections': [self.db.sections.find_one()['_id']]}),
        ]:
            with self.subTest(change=change):
                collection.update_one({}, {'$set': change})
                self.assertEqual(self.view().status_code, 404)
                self.assertEqual(self.publish().status_code, 200)
                self.assertEqual(self.view().status_code, 404)
                collection.update_one({}, {'$set': reset})

    def test_nonadmin_cannot_share_even_with_stale_admin_claim(self):
        self.db.users.update_one({'_id': self.owner}, {'$set': {'profileType': 'INVITADO'}})
        self.assertEqual(self.publish([self.viewer]).status_code, 403)
        self.assertEqual(self.db.dashboards.count_documents({}), 0)
        own = self.publish()
        self.assertEqual(own.status_code, 200, own.text)
        self.assertFalse(own.json()['canShare'])
        self.assertEqual(own.json()['users'], [])
        self.assertEqual(self.view(self.owner).status_code, 200)
        self.assertEqual(self.view().status_code, 404)

    def test_sharing_changes_preserve_other_dashboards_and_sections(self):
        self.publish([self.owner, self.viewer])
        dashboard = self.db.dashboards.find_one()
        extra_dashboard, extra_section = ObjectId(), ObjectId()
        self.db.users.update_one({'_id': self.viewer, 'access': {'$elemMatch': {'dashboard': dashboard['_id']}}}, {'$addToSet': {'access.$.sections': extra_section}})
        unrelated = {'dashboard': extra_dashboard, 'sections': [ObjectId()]}
        self.db.users.update_one({'_id': self.viewer}, {'$push': {'access': unrelated}})
        response = self.publish([self.owner, self.other])
        self.assertEqual(response.status_code, 200, response.text)
        entries = self.db.users.find_one({'_id': self.viewer})['access']
        self.assertIn(unrelated, entries)
        self.assertEqual(next(a for a in entries if a['dashboard'] == dashboard['_id'])['sections'], [extra_section])
        self.assertEqual(self.view().status_code, 404)
        self.assertEqual(self.view(self.other).status_code, 200)

    def test_invalid_recipient_does_not_create_or_change_access(self):
        self.assertEqual(self.publish([ObjectId()]).status_code, 422)
        self.assertEqual(self.db.dashboards.count_documents({}), 0)

    def test_deleted_section_is_not_recreated_by_saving_or_restoring(self):
        self.publish([self.owner, self.viewer])
        self.db.sections.delete_many({})
        self.assertEqual(self.publish().status_code, 409)
        self.client.post('/v2/workspaces/restore-menu', headers=self.headers(self.owner))
        self.assertEqual(self.db.sections.count_documents({}), 0)
        self.assertEqual(self.view().status_code, 404)

    def test_deleted_configuration_is_absent_from_library_and_cannot_be_restored_or_edited(self):
        self.publish([self.owner, self.viewer])
        self.db.analytics_workspaces.update_one({'_id': 'workspace-a'}, {'$set': {'deletedAt': 'deleted'}})
        self.assertEqual(self.client.get('/v2/workspaces', headers=self.headers(self.owner)).json(), [])
        self.assertEqual(self.client.post('/v2/workspaces/restore-menu', headers=self.headers(self.owner)).json(), {'restored': 0})
        self.assertEqual(self.client.get('/v2/workspaces/workspace-a', headers=self.headers(self.owner)).status_code, 404)
        body = {key: self.workspace[key] for key in ['name', 'source_id', 'widgets']}
        self.assertEqual(self.client.put('/v2/workspaces/workspace-a', json=body, headers=self.headers(self.owner)).status_code, 404)
        self.assertEqual(self.publish().status_code, 404)
        self.assertEqual(self.view().status_code, 404)
        self.assertIsNotNone(self.db.analytics_sources.find_one({'_id': 'private-file'}))

    def test_admin_can_configure_public_oauth_id_and_guest_cannot_change_it(self):
        client_id = '123-test.apps.googleusercontent.com'
        response = self.client.put('/v2/google/settings', headers=self.headers(self.owner), json={'client_id': client_id})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get('/v2/google/status', headers=self.headers(self.viewer)).json()['client_id'], client_id)
        denied = self.client.put('/v2/google/settings', headers=self.headers(self.viewer), json={'client_id': '456-test.apps.googleusercontent.com'})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.db.analytics_settings.find_one()['client_id'], client_id)

    def test_google_configuration_rejects_unknown_fields_and_invalid_client_ids(self):
        for body in [{'client_id': 'secret'}, {'client_id': '123.apps.googleusercontent.com', 'access_token': 'not-allowed'}]:
            self.assertEqual(self.client.put('/v2/google/settings', headers=self.headers(self.owner), json=body).status_code, 422)
        self.assertEqual(self.db.analytics_settings.count_documents({}), 0)

    def test_restore_does_not_regrant_access_removed_by_admin(self):
        self.publish([self.viewer])
        self.assertEqual(self.view(self.owner).status_code, 404)
        self.client.post('/v2/workspaces/restore-menu', headers=self.headers(self.owner))
        self.assertEqual(self.view(self.owner).status_code, 404)

    def test_interrupted_creation_can_be_resumed(self):
        with patch.object(access, 'grant_section', side_effect=RuntimeError('simulated unavailable database')):
            self.assertEqual(self.publish().status_code, 500)
        retry = self.publish()
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(self.db.dashboards.count_documents({}), 1)
        self.assertEqual(self.view(self.owner).status_code, 200)

    def test_saving_without_permission_changes_preserves_sharing_and_renamed_route(self):
        self.publish([self.owner, self.viewer])
        self.db.dashboards.update_one({}, {'$set': {'keyname': 'nombre-editado-desde-tableros'}})
        self.db.users.update_one({'_id': self.owner}, {'$set': {'profileType': 'INVITADO'}})
        response = self.publish()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['path'].startswith('/nombre-editado-desde-tableros/'))
        self.assertEqual(self.view().status_code, 200)


if __name__ == '__main__':
    unittest.main()
