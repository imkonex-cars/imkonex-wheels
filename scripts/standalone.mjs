import {bundleBrowser} from './browser-bundle.mjs';
import {pathToFileURL} from 'node:url';
// Run after npm run build. Local product photos are embedded in the preview.
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
const types={'.svg':'image/svg+xml','.webp':'image/webp','.png':'image/png','.jpg':'image/jpeg'};
const uri=async file=>`data:${types[path.extname(file)]};base64,${(await readFile(file)).toString('base64')}`;
const raw=await readFile('dist/data/catalog.js','utf8');
const data=JSON.parse(raw.slice(raw.indexOf('=')+1).trim().replace(/;$/,'')),map={};
for(const file of new Set(['assets/product-unavailable.svg',...data.products.map(p=>p.image).filter(p=>p.startsWith('assets/'))]))map[file]=await uri('dist/'+file);
let html=await readFile('dist/index.html','utf8');
const css=await readFile('dist/styles.css','utf8');
html=html.replace('<link rel="stylesheet" href="./styles.css">',()=>`<style>${css}</style>`);
for(const file of ['assets/imkonex-cars.svg','assets/favicon.svg'])html=html.replaceAll('./'+file,await uri('dist/'+file));
const safe=value=>JSON.stringify(value).replaceAll('<','\\u003c');
const config=await readFile('dist/config.js','utf8');
html=html.replace('<script src="./config.js"></script>',()=>`<script>${config}\nwindow.IMKONEX_IMAGE_MAP=${safe(map)};</script>`);
html=html.replace('<script src="./data/catalog.js"></script>',()=>`<script>window.IMKONEX_DATA=${safe(data)};</script>`);
const code=await bundleBrowser('app.js',pathToFileURL(path.resolve('dist')+'/'));
html=html.replace('<script type="module" src="./app.js"></script>',()=>`<script>${code.replaceAll('</script','<\\/script')}</script>`);
for(const file of ['premium.css','shop.css','shared/bridge.css']){
 const content=await readFile('dist/'+file,'utf8');
 html=html.replace(`<link rel="stylesheet" href="./${file}">`,()=>`<style>${content}</style>`);
}
html=html.replace(/<script[^>]*src="\.\/shared\/shell.js"[^>]*><\/script>/g,'');
for(const file of ['assets/products/R5019.png','assets/products/WHS121894.png']){
 try{const image=await uri('dist/'+file);html=html.replaceAll('./'+file,image);}catch{}
}
await mkdir('artifacts',{recursive:true});
const file=data.mode==='snapshot'?'IMKONEX_WHEELS_REAL_DATA.html':'IMKONEX_WHEELS_PREVIEW.html';
await writeFile('artifacts/'+file,html);
console.log(`Standalone preview generated: ${file}. ${data.products.some(p=>p.image.startsWith('https://'))?'External photos need an internet connection.':'Local assets embedded.'}`);
