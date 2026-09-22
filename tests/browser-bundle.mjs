// Execute the actual local ES module graph inside JSDOM. No production stubs.
import {readFile} from 'node:fs/promises';
export async function browserBundle(entry,base=new URL('../frontend/',import.meta.url)){
 const modules=new Map();
 async function collect(url){
  const id=url.href;if(modules.has(id))return;modules.set(id,'');
  let source=await readFile(url,'utf8');const imports=[...source.matchAll(/^import\s+\{([^}]+)\}\s+from\s+['"]([^'"]+)['"];\s*$/gm)];
  for(const m of imports){const dep=new URL(m[2],url);await collect(dep);source=source.replace(m[0],`const {${m[1].replace(/\s+as\s+/g,':')}}=__load(${JSON.stringify(dep.href)});`);}
  const names=[...source.matchAll(/^export\s+(?:async\s+)?(?:function|const|let|class)\s+(\w+)/gm)].map(m=>m[1]);
  source=source.replace(/^export\s+/gm,'');modules.set(id,source+'\nreturn {'+names.join(',')+'};');
 }
 const url=new URL(entry,base);await collect(url);
 return `(function(){const factories={${[...modules].map(([id,src])=>JSON.stringify(id)+(id.endsWith('/manager/manager.js')?':async function(__load){':':function(__load){')+'\n'+src+'\n}').join(',')}};const cache={};function __load(id){return cache[id]??(cache[id]=factories[id](__load));}return __load(${JSON.stringify(url.href)});})()`;
}
