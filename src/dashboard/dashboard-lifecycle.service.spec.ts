import { Types } from 'mongoose';
import { DashboardLifecycleService } from './dashboard-lifecycle.service';

const did = new Types.ObjectId(), sid = new Types.ObjectId();
function fixture() {
  const collections: Record<string, any> = {};
  for (const name of ['dashboards', 'sections', 'users', 'analytics_workspaces']) {
    collections[name] = { find: jest.fn(() => ({ toArray: async () => [] })), findOne: jest.fn(async () => null), updateOne: jest.fn(async () => ({})), updateMany: jest.fn(async () => ({})) };
  }
  const connection = { db: { collection: jest.fn((name: string) => collections[name]) } };
  return { service: new DashboardLifecycleService(connection as any), collections, connection };
}

test('deleting a generated dashboard retires its configuration and access, preserving source files', async () => {
  const { service, collections: c, connection } = fixture();
  c.dashboards.findOne.mockResolvedValue({ _id: did, generatedWorkspaceId: 'saved', sections: [sid] });
  await service.deleteDashboard(did.toString());
  expect(c.dashboards.updateOne.mock.calls[0][1].$set).toMatchObject({ show: false, deletedAt: expect.any(Date) });
  expect(c.users.updateMany).toHaveBeenCalledWith({}, { $pull: { access: { dashboard: did } } });
  expect(c.analytics_workspaces.updateOne).toHaveBeenCalledWith({ _id: 'saved' }, { $set: { menu_initialized: true, deletedAt: expect.any(Date) } });
  expect(connection.db.collection.mock.calls.flat()).not.toContain('analytics_sources');
});

test('a generated section used in another dashboard remains available there', async () => {
  const { service, collections: c } = fixture();
  c.dashboards.findOne.mockResolvedValueOnce({ _id: did, generatedWorkspaceId: 'saved' }).mockResolvedValueOnce({ _id: new Types.ObjectId(), sections: [sid] });
  c.sections.find.mockReturnValue({ toArray: async () => [{ _id: sid }] });
  await service.deleteDashboard(did.toString());
  expect(c.analytics_workspaces.updateOne).not.toHaveBeenCalled();
  expect(c.sections.updateMany).not.toHaveBeenCalled();
});

test('deleting a regular dashboard retires the configurations of all its unshared sections', async () => {
  const { service, collections: c } = fixture();
  c.dashboards.findOne.mockResolvedValueOnce({ _id: did, sections: [sid, new Types.ObjectId()] });
  c.sections.find.mockReturnValueOnce({ toArray: async () => [{ _id: sid, workspaceId: 'first' }, { _id: new Types.ObjectId(), workspaceId: 'second' }] });
  await service.deleteDashboard(did.toString());
  expect(c.analytics_workspaces.updateOne.mock.calls.map(call => call[0]._id)).toEqual(['first', 'second']);
});

test('reconciliation removes dangling references but preserves empty containers and hidden sections', async () => {
  const { service, collections: c } = fixture();
  const orphan = { _id: did, sections: [sid] };
  c.dashboards.find.mockReturnValue({ toArray: async () => [orphan, { _id: new Types.ObjectId(), sections: [] }, { _id: new Types.ObjectId(), sections: [new Types.ObjectId()] }] });
  c.sections.findOne.mockResolvedValueOnce(null).mockResolvedValueOnce({ show: false });
  const remove = jest.spyOn(service, 'deleteDashboard').mockResolvedValue({ message: 'ok' });
  expect(await service.reconcileGenerated()).toEqual({ removed: [did.toString()] });
  expect(remove).toHaveBeenCalledTimes(1);
});

test('deleting a section cleans references only after reconciling affected generated boards', async () => {
  const { service, collections: c } = fixture();
  c.sections.findOne.mockResolvedValue({ _id: sid, workspaceId: 'saved' });
  const reconcile = jest.spyOn(service, 'reconcileGenerated').mockResolvedValue({ removed: [did.toString()] });
  const result = await service.deleteSection(sid.toString());
  expect(result.removedDashboards).toEqual([did.toString()]);
  expect(c.sections.updateOne.mock.calls[0][1].$set.show).toBe(false);
  // Positional updates must exclude users whose legacy document has no access array.
  expect(c.users.updateMany).toHaveBeenCalledWith({ 'access.sections': sid }, { $pull: { 'access.$[].sections': sid } });
  expect(reconcile.mock.invocationCallOrder[0]).toBeLessThan(c.dashboards.updateMany.mock.invocationCallOrder[0]);
  expect(c.dashboards.updateMany).toHaveBeenCalledWith({}, { $pull: { sections: sid } });
});

test('retrying a deleted dashboard completes cleanup without reviving it', async () => {
  const { service, collections: c } = fixture();
  const when = new Date('2026-01-01');
  c.dashboards.findOne.mockResolvedValue({ _id: did, generatedWorkspaceId: 'saved', deletedAt: when });
  await service.deleteDashboard(did.toString());
  expect(c.analytics_workspaces.updateOne.mock.calls[0][1].$set.deletedAt).toBe(when);
});

test('invalid and unknown ids do not mutate the database', async () => {
  const { service, collections: c } = fixture();
  await expect(service.deleteDashboard('invalid')).rejects.toThrow('Identificador inválido');
  await expect(service.deleteDashboard(did.toString())).rejects.toThrow('El tablero no existe');
  expect(c.dashboards.updateOne).not.toHaveBeenCalled();
});
