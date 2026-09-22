// Local source-module bundling for the downloadable HTML preview.
import {readFile} from 'node:fs/promises';
export async function bundleBrowser(entry,base){
 const modules=new Map(),key=url=>url.href.slice(base.href.length);
 async function collect(url){
  if(!url.href.startsWith(base.href))throw Error('Module outside preview root');
  const id=key(url);if(modules.has(id))return;modules.set(id,'');
  let source=await readFile(url,'utf8');const imports=[...source.matchAll(/^import\s+\{([^}]+)\}\s+from\s+['"]([^'"]+)['"];\s*$/gm)];
  for(const m of imports){const dep=new URL(m[2],url);await collect(dep);source=source.replace(m[0],`const {${m[1].replace(/\s+as\s+/g,':')}}=__load(${JSON.stringify(key(dep))});`);}
  if(/^import\s/m.test(source))throw Error('Unsupported preview import');
  const names=[...source.matchAll(/^export\s+(?:async\s+)?(?:function|const|let|class)\s+(\w+)/gm)].map(m=>m[1]);
  source=source.replace(/^export\s+/gm,'');modules.set(id,source+'\nreturn {'+names.join(',')+'};');
 }
 const url=new URL(entry,base);await collect(url);
 return `(function(){const factories={${[...modules].map(([id,src])=>JSON.stringify(id)+':function(__load){\n'+src+'\n}').join(',')}};const cache={};function __load(id){return cache[id]??(cache[id]=factories[id](__load));}return __load(${JSON.stringify(key(url))});})()`;
}
