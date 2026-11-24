import { UserController } from './user.controller';

test('creation ignores a legacy email and only sends the remaining user fields', async () => {
  const service = { createNewUser: jest.fn().mockResolvedValue({ username: 'Ana' }) };
  await new UserController(service as any).createNewUser({ username: 'Ana', password: 'test-password',
    profileType: 'INVITADO', email: 'legacy@example.test' });
  expect(service.createNewUser).toHaveBeenCalledWith('Ana', 'test-password', 'INVITADO');
});

test('deletion uses the unique username, with or without legacy email', async () => {
  const service = { deleteUser: jest.fn().mockResolvedValue({ message: 'ok' }) };
  const controller = new UserController(service as any);
  await controller.deleteUser({ username: 'Ana' });
  await controller.deleteUser({ username: 'Ana', email: 'outdated@example.test' });
  expect(service.deleteUser.mock.calls).toEqual([['Ana'], ['Ana']]);
});

test('user listing hides legacy email and credentials while retaining access', async () => {
  const account = { _id: 'id', username: 'Ana', profileType: 'ADMIN', access: [{ dashboard: 'health', sections: ['one'] }],
    email: 'legacy@example.test', password: 'hash', refreshToken: 'secret' };
  const service = { getAllUsers: jest.fn().mockResolvedValue([{ toObject: () => account }, account]) };
  const result = await new UserController(service as any).getAllUsers();
  expect(result).toEqual([0, 1].map(() => ({ _id: 'id', username: 'Ana', profileType: 'ADMIN', access: account.access })));
});
