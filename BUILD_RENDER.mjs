// Entry point for the complete source workspace, including Render Static Sites.
import {readFile,access} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.dirname(fileURLToPath(import.meta.url));
const required=[
  'package.json','scripts/build.mjs','scripts/public-catalog.mjs',
  'data/supplier-snapshot.json','frontend/index.html','frontend/app.js',
  'frontend/domain.js','frontend/styles.css','frontend/config.js',
  'frontend/assets/favicon.svg','frontend/assets/imkonex-cars.svg',
  'frontend/assets/product-unavailable.svg',
];

try{
  const missing=[];
  for(const name of required){
    try{await access(path.join(root,name));}catch{missing.push(name);}
  }
  if(missing.length)throw new Error(`Incomplete workspace. Upload the full archive contents to the repository root. Missing: ${missing.join(', ')}`);
  const {version}=JSON.parse(await readFile(path.join(root,'package.json'),'utf8'));
  const release=`IMKONEX-WHEELS-${version}-WORKSPACE-1`;
  console.log(release);
  // The ready catalog uses the checked public sample, including on old services
  // that still have CATALOG_MODE=demo or an obsolete PUBLIC_API_BASE value.
  process.env.CATALOG_MODE='snapshot';
  process.env.PUBLIC_API_BASE='';
  process.chdir(root);
  await import('./scripts/build.mjs');
  const info=JSON.parse(await readFile(path.join(root,'dist/build-info.json'),'utf8'));
  console.log(`BUILD_OK ${release} | ${info.mode} | ${info.products} products`);
}catch(error){
  console.error(`BUILD_FAILED: ${error.message}`);
  console.error('Render: Build Command = node BUILD_RENDER.mjs; Publish Directory = dist; Root Directory = empty.');
  process.exitCode=1;
}
