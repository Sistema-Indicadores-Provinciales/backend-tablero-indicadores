import { corsOrigins } from './cors-origins';

test('local Vite fallback ports work even when env only lists 5173', () => {
  const origins = corsOrigins({ NODE_ENV: 'development', CORS_ORIGINS: 'http://localhost:5173' });
  expect(origins).toContain('http://localhost:5174');
  expect(origins).toContain('http://127.0.0.1:5174');
  expect(origins).not.toContain('*');
  expect(origins).not.toContain('https://untrusted.example');
});

test('production only permits explicitly configured origins', () => {
  expect(corsOrigins({ NODE_ENV: 'production', CORS_ORIGINS: ' https://indicadores.example, ' })).toEqual(['https://indicadores.example']);
  expect(corsOrigins({ NODE_ENV: 'production' })).toEqual([]);
});
