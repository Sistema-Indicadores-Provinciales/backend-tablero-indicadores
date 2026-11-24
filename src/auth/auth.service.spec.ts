import * as bcrypt from 'bcryptjs';
import { model } from 'mongoose';
import { AuthService } from './auth.service';
import { UserService } from '../user/user.service';
import { UserSchema } from '../user/user.schema';

const Account = model('UserWithoutEmailTest', UserSchema);
afterEach(() => jest.restoreAllMocks());

test('schema accepts multiple users without email and retains username uniqueness', async () => {
  for (const username of ['Ana', 'Bruno']) {
    const account = new Account({ username, password: 'hash', profileType: 'INVITADO', email: 'ignored@example.test' });
    await expect(account.validate()).resolves.toBeUndefined();
    expect(account.toObject()).not.toHaveProperty('email');
  }
  expect(UserSchema.indexes()).toContainEqual([{ username: 1 }, expect.objectContaining({ unique: true })]);
  expect(UserSchema.indexes().some(([fields]) => 'email' in fields)).toBe(false);
});

test('both creation paths save hashed passwords and no email', async () => {
  const saved: any[] = [];
  jest.spyOn(Account.prototype, 'save').mockImplementation(async function () { saved.push(this.toObject()); return this; });
  await new AuthService(Account as any, {} as any).register('Ana', 'test-password', 'ADMIN');
  await new UserService(Account as any, {} as any).createNewUser('Bruno', 'test-password', 'INVITADO');
  expect(saved.map(account => account.username)).toEqual(['Ana', 'Bruno']);
  for (const account of saved) {
    expect(account).not.toHaveProperty('email');
    expect(await bcrypt.compare('test-password', account.password)).toBe(true);
  }
});

test('existing and new users still authenticate by username and password only', async () => {
  const account = new Account({ username: 'Ana', password: bcrypt.hashSync('test-password', 4), profileType: 'ADMIN', access: [] });
  const users = { findOne: jest.fn().mockResolvedValue(account) };
  const service = new AuthService(users as any, {} as any);
  expect(await service.validateUser('Ana', 'test-password')).toMatchObject({ username: 'Ana', profileType: 'ADMIN', access: [] });
  expect(users.findOne).toHaveBeenCalledWith({ username: 'Ana' });
  await expect(service.validateUser('Ana', 'wrong')).rejects.toThrow('Usuario o contraseña no válidos');
});
