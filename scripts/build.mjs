import {cp,mkdir,mkdtemp,rename,rm,readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {validateSnapshot} from './public-catalog.mjs';
const root=fileURLToPath(new URL('../',import.meta.url));
const {version}=JSON.parse(await readFile(path.join(root,'package.json'),'utf8'));
const mode=process.env.CATALOG_MODE || 'snapshot';
if(!['demo','snapshot'].includes(mode))throw new Error('CATALOG_MODE must be demo or snapshot');
const raw=await readFile(path.join(root,mode==='snapshot'?'data/supplier-snapshot.json':'data/demo-catalog.json'));
if(raw.length>32000000)throw new Error('Catalog exceeds the static pilot limit of 32 MB');
const data=JSON.parse(raw);
if(mode==='snapshot')validateSnapshot(data);
if(!Array.isArray(data.products)||!data.products.length) throw new Error('Catalog is empty');
for(const p of data.products){
 if(!p.id||!['tires','wheels'].includes(p.kind))throw new Error(`Invalid product ${p.id}`);
 if(p.image.startsWith('assets/'))await readFile(path.join(root,'frontend',p.image));
 else if(mode!=='snapshot')throw new Error('Demo images must be local');
}
const api=process.env.PUBLIC_API_BASE||'';
const photos={
 local:data.products.filter(p=>p.image.startsWith('assets/products/')).length,
 remote:data.products.filter(p=>p.image.startsWith('https://')).length,
 unavailable:data.products.filter(p=>p.image==='assets/product-unavailable.svg').length,
};
if(api&&!/^https:\/\/[a-zA-Z0-9.-]+(?::\d+)?(?:\/[a-zA-Z0-9/_-]*)?$/.test(api))throw new Error('PUBLIC_API_BASE must be an HTTPS API URL');
const catalogScript=`window.IMKONEX_DATA = ${JSON.stringify(data)};\n`;
await mkdir(path.join(root,'frontend/data'),{recursive:true});
await writeFile(path.join(root,'frontend/data/catalog.js'),catalogScript);
const stage=await mkdtemp(path.join(root,'.imkonex-source-build-'));
try{
 await cp(path.join(root,'frontend'),stage,{recursive:true});
 await writeFile(path.join(stage,'config.js'),`window.IMKONEX_CONFIG = Object.freeze(${JSON.stringify({mode,apiBase:api,version})});\n`);
 await writeFile(path.join(stage,'robots.txt'),'User-agent: *\nDisallow: /\n');
 await writeFile(path.join(stage,'build-info.json'),JSON.stringify({
  release:`IMKONEX-WHEELS-${version}-WORKSPACE-1`,version,mode,products:data.products.length,
  tyres:data.products.filter(p=>p.kind==='tires').length,wheels:data.products.filter(p=>p.kind==='wheels').length,
  dataUpdatedAt:data.updatedAt,builtAt:new Date().toISOString(),photos,
  dataScope:data.schemaVersion===3?'account_catalog':'verified_sample',
  sync:data.sync||null,
 },null,2)+'\n');
 await rm(path.join(root,'dist'),{recursive:true,force:true});
 await rename(stage,path.join(root,'dist'));
}finally{await rm(stage,{recursive:true,force:true});}
console.log(`Built dist: ${data.products.length} products. Mode: ${mode}. No client credentials.`);
if(mode==='snapshot')console.log(`Photos: ${photos.local} local, ${photos.remote} remote, ${photos.unavailable} unavailable.`);
