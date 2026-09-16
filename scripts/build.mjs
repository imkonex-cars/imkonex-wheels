import {cp,mkdir,rm,readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
const root=process.cwd();
const data=JSON.parse(await readFile(path.join(root,'data/demo-catalog.json'),'utf8'));
if(!Array.isArray(data.products)||!data.products.length) throw new Error('Demo catalog is empty');
for(const p of data.products){
 if(!p.id||!['tires','wheels'].includes(p.kind)||!p.image.startsWith('assets/'))throw new Error(`Invalid product ${p.id}`);
 await readFile(path.join(root,'frontend',p.image));
}
await mkdir('frontend/data',{recursive:true});
await writeFile('frontend/data/catalog.js',`window.IMKONEX_DATA = ${JSON.stringify(data)};\n`);
await rm('dist',{recursive:true,force:true});await mkdir('dist',{recursive:true});
await cp('frontend','dist',{recursive:true});
const api=process.env.PUBLIC_API_BASE||'';
if(api&&!/^https:\/\/[a-zA-Z0-9.-]+(?::\d+)?(?:\/[a-zA-Z0-9/_-]*)?$/.test(api))throw new Error('PUBLIC_API_BASE must be an HTTPS API URL');
await writeFile('dist/config.js',`window.IMKONEX_CONFIG = Object.freeze(${JSON.stringify({mode:'demo',apiBase:api,version:'0.1.0'})});\n`);
await writeFile('dist/robots.txt','User-agent: *\nDisallow: /\n');
console.log(`Built dist: ${data.products.length} demo products. Mode: demo. No client credentials.`);
