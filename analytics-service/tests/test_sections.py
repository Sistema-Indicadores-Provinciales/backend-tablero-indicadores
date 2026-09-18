import unittest
from uuid import uuid4
from bson import ObjectId
import test_workspace_access as fixtures


class SectionFlowTests(unittest.TestCase):
    setUp = fixtures.WorkspaceAccessTests.setUp
    tearDown = fixtures.WorkspaceAccessTests.tearDown
    headers = fixtures.WorkspaceAccessTests.headers

    def catalog(self):
        did, sid, other = ObjectId(), ObjectId(), ObjectId()
        self.db.sections.insert_many([{'_id': sid, 'keyname': 'equipos', 'name': 'Equipos', 'show': True},
                                      {'_id': other, 'keyname': 'otra', 'name': 'Otra', 'show': True}])
        self.db.dashboards.insert_one({'_id': did, 'name': 'Equipamiento médico', 'keyname': 'equipamiento', 'show': True, 'icon': 'original', 'sections': [sid, other]})
        for uid in [self.owner, self.viewer]:
            self.db.users.update_one({'_id': uid}, {'$set': {'access': [{'dashboard': did, 'sections': [sid, other]}]}})
        return {'dashboardId': str(did), 'sectionId': str(sid)}, other

    def create(self, did, body, uid=None):
        return self.client.post(f'/v2/dashboards/{did}/sections', headers=self.headers(uid or self.owner), json=body)

    def save(self, target, uid=None):
        return self.client.post('/v2/workspaces', headers=self.headers(uid or self.owner), json={
            **{key: self.workspace[key] for key in ['name', 'source_id', 'widgets']}, 'destination': target})

    def publish(self, wid, recipients=None):
        return self.client.put(f'/v2/workspaces/{wid}/publication', headers=self.headers(self.owner),
                               json={'icon': 'new-icon', **({'recipientIds': recipients} if recipients is not None else {})})

    def test_create_empty_section_and_choose_readers_preserves_siblings_and_retries(self):
        target, sibling = self.catalog()
        body = {'name': 'Nuevos equipos', 'recipientIds': [str(self.owner), str(self.other)], 'requestId': str(uuid4())}
        first = self.create(target['dashboardId'], body)
        again = self.create(target['dashboardId'], body)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), again.json())
        sid = ObjectId(first.json()['sectionId'])
        self.assertEqual(len(self.db.dashboards.find_one()['sections']), 3)
        self.assertEqual(self.db.sections.count_documents({}), 3)
        self.assertNotIn('workspaceId', self.db.sections.find_one({'_id': sid}))
        self.assertIn(sid, self.db.users.find_one({'_id': self.other})['access'][0]['sections'])
        self.assertNotIn(sid, self.db.users.find_one({'_id': self.viewer})['access'][0]['sections'])
        self.assertIn(sibling, self.db.users.find_one({'_id': self.viewer})['access'][0]['sections'])

    def test_guest_and_invalid_recipients_cannot_create_sections(self):
        target, _ = self.catalog()
        body = {'name': 'Nueva', 'recipientIds': [str(self.owner)], 'requestId': str(uuid4())}
        self.assertEqual(self.create(target['dashboardId'], body, self.viewer).status_code, 403)
        self.assertEqual(self.create(target['dashboardId'], {**body, 'recipientIds': [str(ObjectId())]}).status_code, 422)
        self.assertEqual(self.create(target['dashboardId'], {**body, 'name': '  '}).status_code, 422)
        self.assertEqual(self.db.sections.count_documents({}), 2)

    def test_saving_into_existing_section_preserves_parent_and_existing_access(self):
        target, sibling = self.catalog()
        saved = self.save(target)
        self.assertEqual(saved.status_code, 200, saved.text)
        wid = saved.json()['_id']
        published = self.publish(wid)
        self.assertEqual(published.status_code, 200, published.text)
        self.assertEqual(published.json()['path'], '/equipamiento/equipos')
        self.assertEqual(published.json()['destination']['sectionName'], 'Equipos')
        dashboard = self.db.dashboards.find_one()
        self.assertEqual(dashboard['name'], 'Equipamiento médico')
        self.assertEqual(dashboard['icon'], 'original')
        self.assertNotIn('generatedWorkspaceId', dashboard)
        self.assertEqual(dashboard['sections'], [ObjectId(target['sectionId']), sibling])
        self.assertEqual(self.db.dashboards.count_documents({}), 1)
        self.assertEqual(self.db.sections.count_documents({}), 2)
        self.assertEqual(self.client.get(f'/v2/workspaces/{wid}/view', headers=self.headers(self.viewer)).status_code, 200)
        self.assertEqual(self.client.get(f'/v2/workspaces/{wid}', headers=self.headers(self.viewer)).status_code, 404)

    def test_section_sharing_never_grants_or_revokes_other_sections(self):
        target, sibling = self.catalog()
        wid = self.save(target).json()['_id']
        self.assertEqual(self.publish(wid, [str(self.owner), str(self.other)]).status_code, 200)
        self.assertEqual(self.db.users.find_one({'_id': self.viewer})['access'][0]['sections'], [sibling])
        self.assertEqual(self.db.users.find_one({'_id': self.other})['access'][0]['sections'], [ObjectId(target['sectionId'])])
        self.assertEqual(self.client.get(f'/v2/workspaces/{wid}/view', headers=self.headers(self.viewer)).status_code, 404)
        self.assertEqual(self.client.get(f'/v2/workspaces/{wid}/view', headers=self.headers(self.other)).status_code, 200)

    def test_append_chart_updates_same_section_without_replacing_existing_widgets(self):
        target, _ = self.catalog()
        wid = self.save(target).json()['_id']
        self.publish(wid)
        body = {key: self.workspace[key] for key in ['name', 'source_id', 'widgets']}
        body['widgets'] = [*body['widgets'], {**body['widgets'][0], 'id': 'chart-b', 'title': 'Segundo'}]
        updated = self.client.put(f'/v2/workspaces/{wid}', headers=self.headers(self.owner), json=body)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()['destination'], target)
        self.assertEqual(len(self.client.get(f'/v2/workspaces/{wid}/view', headers=self.headers(self.viewer)).json()['widgets']), 2)
        self.assertEqual(self.publish(wid).json()['path'], '/equipamiento/equipos')

    def test_occupied_foreign_deleted_and_dedicated_sections_cannot_be_overwritten(self):
        target, _ = self.catalog()
        wid = self.save(target).json()['_id']
        self.publish(wid)
        self.assertEqual(self.save(target).status_code, 409)
        self.assertEqual(self.save({**target, 'sectionId': str(ObjectId())}).status_code, 404)
        self.db.sections.update_one({'_id': ObjectId(target['sectionId'])}, {'$unset': {'workspaceId': ''}, '$set': {'keyname': 'coparticipacion---cfi-y-totales'}})
        self.assertEqual(self.save(target).status_code, 409)
        self.db.sections.update_one({'_id': ObjectId(target['sectionId'])}, {'$set': {'deletedAt': 'deleted'}})
        self.assertEqual(self.save(target).status_code, 404)

    def test_concurrent_publications_do_not_replace_winner_or_create_new_dashboards_on_restore(self):
        target, _ = self.catalog()
        first = self.save(target).json()['_id']
        second = self.save(target).json()['_id']
        self.assertEqual(self.publish(first).status_code, 200)
        self.assertEqual(self.publish(second).status_code, 409)
        self.db.analytics_workspaces.update_one({'_id': self.workspace['_id']}, {'$set': {'menu_initialized': True}})
        restored = self.client.post('/v2/workspaces/restore-menu', headers=self.headers(self.owner))
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()['skipped'], 1)
        self.assertEqual(self.db.dashboards.count_documents({}), 1)
        self.assertEqual(self.db.sections.find_one({'_id': ObjectId(target['sectionId'])})['workspaceId'], first)

    def test_destinations_options_only_offer_empty_sections_and_enforce_admin_role(self):
        target, _ = self.catalog()
        wid = self.save(target).json()['_id']
        self.publish(wid)
        options = self.client.get('/v2/publication-options', headers=self.headers(self.owner)).json()
        self.assertEqual(len(options['destinations']), 1)
        self.assertNotEqual(options['destinations'][0]['sectionId'], target['sectionId'])
        path = f"/v2/dashboards/{target['dashboardId']}/sections/{target['sectionId']}/options"
        self.assertEqual(self.client.get(path, headers=self.headers(self.viewer)).status_code, 403)
        self.assertNotIn('password', str(options))
        self.db.users.update_one({'_id': self.owner}, {'$set': {'profileType': 'INVITADO'}})
        self.assertEqual(self.save(target).status_code, 403)

    def test_saved_configuration_cannot_be_moved_to_another_section(self):
        target, sibling = self.catalog()
        wid = self.save(target).json()['_id']
        self.publish(wid)
        body = {**{key: self.workspace[key] for key in ['name', 'source_id', 'widgets']}, 'destination': {**target, 'sectionId': str(sibling)}}
        self.assertEqual(self.client.put(f'/v2/workspaces/{wid}', headers=self.headers(self.owner), json=body).status_code, 409)
        self.assertEqual(self.db.analytics_workspaces.find_one({'_id': wid})['destination'], target)


if __name__ == '__main__':
    unittest.main()
