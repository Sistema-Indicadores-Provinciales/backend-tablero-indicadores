import { ExecutionContext } from '@nestjs/common';
import { JwtService } from '@nestjs/jwt';
import { JwtAuthGuard } from './auth.guard';
import { AdminGuard } from './admin.guard';

const jwt = new JwtService();
const original = process.env.JWT_SECRET;
const secret = 'isolated-test-secret-with-at-least-32-characters';
const context = (token?: string) => ({ switchToHttp: () => ({ getRequest: () => ({ headers: token ? { authorization: `Bearer ${token}` } : {} }) }) }) as unknown as ExecutionContext;
beforeAll(() => { process.env.JWT_SECRET = secret; });
afterAll(() => { if (original === undefined) delete process.env.JWT_SECRET; else process.env.JWT_SECRET = original; });
test('rejects a missing token', async () => {
  await expect(new JwtAuthGuard(jwt).canActivate(context())).rejects.toThrow();
});
test('accepts an access token', async () => {
  const token = jwt.sign({ sub: 'test', tokenUse: 'access' }, { secret, expiresIn: '1h' });
  await expect(new JwtAuthGuard(jwt).canActivate(context(token))).resolves.toBe(true);
});
test('rejects a refresh token on API routes', async () => {
  const token = jwt.sign({ sub: 'test', tokenUse: 'refresh' }, { secret, expiresIn: '1h' });
  await expect(new JwtAuthGuard(jwt).canActivate(context(token))).rejects.toThrow();
});
test('rejects an expired token', async () => {
  const token = jwt.sign({ sub: 'test', tokenUse: 'access' }, { secret, expiresIn: '-1s' });
  await expect(new JwtAuthGuard(jwt).canActivate(context(token))).rejects.toThrow();
});
test('admin guard rejects an invited user', async () => {
  const token = jwt.sign({ sub: 'test', tokenUse: 'access', profileType: 'INVITADO' }, { secret, expiresIn: '1h' });
  const request = { headers: { authorization: `Bearer ${token}` } };
  const ctx = { switchToHttp: () => ({ getRequest: () => request }) } as unknown as ExecutionContext;
  await expect(new AdminGuard(jwt).canActivate(ctx)).rejects.toThrow();
});
test('admin guard accepts administrator', async () => {
  const token = jwt.sign({ sub: 'test', tokenUse: 'access', profileType: 'ADMIN' }, { secret, expiresIn: '1h' });
  const request = { headers: { authorization: `Bearer ${token}` } };
  const ctx = { switchToHttp: () => ({ getRequest: () => request }) } as unknown as ExecutionContext;
  await expect(new AdminGuard(jwt).canActivate(ctx)).resolves.toBe(true);
});
