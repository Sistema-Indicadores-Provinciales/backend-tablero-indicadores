export function corsOrigins(env: NodeJS.ProcessEnv = process.env): string[] {
  const configured = (env.CORS_ORIGINS || '').split(',').map(origin => origin.trim()).filter(Boolean);
  if (env.NODE_ENV === 'production') return configured;
  // Vite advances to the next port when another local instance is running.
  const local = ['localhost', '127.0.0.1', '[::1]'].flatMap(host =>
    [4173, 5173, 5174, 5175, 5176, 5177, 5178, 5179].map(port => `http://${host}:${port}`),
  );
  return [...new Set([...configured, ...local])];
}
