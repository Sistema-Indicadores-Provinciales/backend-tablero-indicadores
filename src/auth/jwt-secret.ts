export function jwtSecret(): string {
  const secret = process.env.JWT_SECRET;
  if (!secret || (process.env.NODE_ENV === 'production' && secret.length < 32)) {
    throw new Error('Configurá JWT_SECRET (mínimo 32 caracteres en producción).');
  }
  return secret;
}
