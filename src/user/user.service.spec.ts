import { Types } from 'mongoose';
import { UserService } from './user.service';

const generatedId = new Types.ObjectId();
const sectionId = new Types.ObjectId();
const hiddenId = new Types.ObjectId();
const legacyId = new Types.ObjectId();

function fixture() {
  const account = { access: [
    { dashboard: generatedId, sections: [sectionId, hiddenId] },
    { dashboard: legacyId, sections: [] },
  ] };
  const generated = { _id: generatedId, keyname: 'ingresos', name: 'Ingresos', icon: 'chart', show: true,
    generatedWorkspaceId: 'saved', sections: [
      { _id: sectionId, keyname: 'graficos', name: 'Gráficos', show: true },
      { _id: hiddenId, keyname: 'oculta', show: false },
    ] };
  const dashboards = [generated, { _id: legacyId, keyname: 'economia', name: 'Economía', show: true, sections: [] }];
  const userModel = { findById: () => ({ exec: async () => account }) };
  const dashboardModel = { findOne: ({ _id, show }: any) => ({ populate: () => ({
    exec: async () => dashboards.find(d => d._id.equals(_id) && d.show === show),
  }) }) };
  return { account, generated, service: new UserService(userModel as any, dashboardModel as any) };
}

test('generated dashboards use the same current user access as the existing menu', async () => {
  const { service } = fixture();
  const menu = await service.getMyDashboards('user');
  expect(menu.map(d => d.keyname)).toEqual(['ingresos', 'economia']);
  expect(menu[0].sections).toEqual(['graficos']);
});

test('revoking the generated section removes its dashboard from the menu', async () => {
  const { service, account } = fixture();
  await service.getMyDashboards('user');
  account.access[0].sections = [];
  const menu = await service.getMyDashboards('user');
  expect(menu.map(d => d.keyname)).toEqual(['economia']);
});

test('disabling the generated dashboard or its only accessible section hides it', async () => {
  const { service, generated } = fixture();
  generated.show = false;
  expect((await service.getMyDashboards('user')).map(d => d.keyname)).toEqual(['economia']);
  generated.show = true;
  generated.sections[0].show = false;
  expect((await service.getMyDashboards('user')).map(d => d.keyname)).toEqual(['economia']);
});

test('deleting a user also removes their stored Google authorization', async () => {
  const id = new Types.ObjectId();
  const removeGrant = jest.fn().mockResolvedValue({ deletedCount: 1 });
  const collection = jest.fn().mockReturnValue({ deleteOne: removeGrant });
  const users = { findOneAndDelete: jest.fn().mockResolvedValue({ _id: id }), db: { collection } };
  const service = new UserService(users as any, {} as any);
  await service.deleteUser('Ana', 'ana@example.test');
  expect(collection).toHaveBeenCalledWith('analytics_google_connections');
  expect(removeGrant).toHaveBeenCalledWith({ _id: id.toString() });
});
