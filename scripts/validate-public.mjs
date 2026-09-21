import {readFile} from 'node:fs/promises';
import {validateSnapshot} from './public-catalog.mjs';
try {
  const raw=await readFile(process.argv[2]);
  if(raw.length>32000000)throw new Error('Snapshot exceeds 32 MB');
  const data=validateSnapshot(JSON.parse(raw));
  console.log(`PUBLIC_OK ${data.products.length} products; schema ${data.schemaVersion}`);
} catch {
  console.error('PUBLIC_INVALID: unexpected fields, values, size or incomplete metadata');
  process.exitCode=1;
}
