// Run after deploying the backend without User.email. No Google connection is modified.
const fs = require('node:fs');
const path = require('node:path');
const { MongoClient, BSON } = require('mongoose').mongo;

async function removeUserEmail(database, { apply = false, backupPath } = {}) {
  const users = database.collection('users');
  const indexes = await users.listIndexes().toArray();
  const emailIndexes = indexes.filter(index => Object.hasOwn(index.key, 'email'));
  if (emailIndexes.some(index => Object.keys(index.key).length !== 1)) {
    throw new Error('Hay un índice compuesto con email. Revisalo antes de modificarlo.');
  }
  const records = await users.find({ email: { $exists: true } }, { projection: { email: 1 } }).toArray();
  const summary = { database: database.databaseName, usersWithEmail: records.length,
    indexes: emailIndexes.map(index => index.name), applied: false };
  if (!apply || (!records.length && !emailIndexes.length)) return summary;
  if (!backupPath || !path.isAbsolute(backupPath)) throw new Error('Indicá un respaldo privado con ruta absoluta.');
  // Exclusive creation prevents overwriting the original backup on a retry.
  fs.writeFileSync(backupPath, BSON.EJSON.stringify({ database: database.databaseName,
    createdAt: new Date(), users: records, indexes: emailIndexes }, null, 2), { flag: 'wx', mode: 0o600 });
  // MongoDB keeps old indexes after a Mongoose field is removed. Drop the unique
  // email index before unsetting values, otherwise multiple missing values collide.
  for (const index of emailIndexes) await users.dropIndex(index.name);
  const result = await users.updateMany({ email: { $exists: true } }, { $unset: { email: '' } });
  if (await users.countDocuments({ email: { $exists: true } })) throw new Error('Todavía hay usuarios con email.');
  if ((await users.listIndexes().toArray()).some(index => Object.hasOwn(index.key, 'email'))) {
    throw new Error('Todavía hay un índice de email.');
  }
  return { ...summary, applied: true, modified: result.modifiedCount };
}

async function main() {
  require('dotenv').config({ quiet: true });
  const args = process.argv.slice(2);
  const value = flag => args[args.indexOf(flag) + 1];
  const expectedDatabase = args.includes('--database') ? value('--database') : '';
  if (!expectedDatabase || expectedDatabase.startsWith('--') || !process.env.MONGODB_URL) {
    throw new Error('Se requieren MONGODB_URL y --database NOMBRE. Sin --apply solo se inspecciona.');
  }
  const client = new MongoClient(process.env.MONGODB_URL, { serverSelectionTimeoutMS: 10000 });
  try {
    await client.connect();
    const database = client.db();
    if (database.databaseName !== expectedDatabase) throw new Error('La base no coincide con --database.');
    console.log(JSON.stringify(await removeUserEmail(database, { apply: args.includes('--apply'),
      backupPath: args.includes('--backup') ? value('--backup') : undefined })));
  } finally {
    await client.close();
  }
}

module.exports = { removeUserEmail };
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
