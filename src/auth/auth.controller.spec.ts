import { AuthController } from './auth.controller';

test('admin registration no longer forwards an email from old clients', async () => {
  const service = { register: jest.fn().mockResolvedValue({ username: 'Ana' }) };
  const controller = new AuthController(service as any, {} as any);
  await controller.register({ username: 'Ana', password: 'test-password', profileType: 'ADMIN', email: 'old@example.test' });
  expect(service.register).toHaveBeenCalledWith('Ana', 'test-password', 'ADMIN');
});
