// Generated release entry point. It does not load the old scripts/build.mjs.
import {readFile,writeFile,mkdir,mkdtemp,rename,rm} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {Script} from 'node:vm';

const RELEASE=__RELEASE_METADATA__;
const EXPECTED_HTML_SHA256=__EXPECTED_HTML_SHA256__;
const root=path.dirname(fileURLToPath(import.meta.url));
const digest=text=>createHash('sha256').update(text).digest('hex');
const normalize=text=>text.replace(/^\uFEFF/,'').replace(/\r\n?/g,'\n');
const ensure=(condition,message)=>{if(!condition)throw new Error(message);};

__PUBLIC_VALIDATOR__

async function inspect(){
  const original=normalize(await readFile(path.join(root,'index.html'),'utf8'));
  ensure(digest(original)===EXPECTED_HTML_SHA256,
    'index.html and BUILD_RENDER.mjs are from different releases. Upload ALL files from the full archive into the repository root.');
  const scripts=[...original.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)];
  ensure(scripts.length===3 && scripts.every(m=>!(/\bsrc\s*=/i.test(m[1]))),
    'The complete index.html must contain its three application scripts.');
  scripts.forEach((m,i)=>new Script(m[2],{filename:`inline-${i}.js`}));
  const dataMatch=scripts[1][2].match(/^\s*window\.IMKONEX_DATA\s*=\s*([\s\S]*);\s*$/);
  ensure(dataMatch,'Public catalog is missing from index.html.');
  const data=validateSnapshot(JSON.parse(dataMatch[1]));
  const configMatch=scripts[0][2].match(/window\.IMKONEX_CONFIG\s*=\s*Object\.freeze\((\{[^\n]*\})\);/);
  ensure(configMatch,'Release configuration is missing from index.html.');
  const config=JSON.parse(configMatch[1]);
  ensure(config.version===RELEASE.version && config.mode==='snapshot',
    'Unexpected configuration version or catalog mode. Use the matching full release.');
  ensure(data.products.length===RELEASE.products && data.mode==='snapshot',
    'This release must contain the verified six-product sample.');
  ensure(original.includes(`data-imkonex-release="${RELEASE.id}"`),'Release label is missing.');
  return {original,scripts,data};
}

async function build(){
  console.log(RELEASE.id);
  const {original,scripts,data}=await inspect();
  if(process.argv.includes('--check')){
    console.log(`CHECK_OK ${RELEASE.id} | snapshot | ${data.products.length} products`);
    return;
  }
  const stage=await mkdtemp(path.join(root,'.imkonex-build-'));
  try{
    await mkdir(path.join(stage,'assets'));
    let html=original;
    const assets=[];
    for(const [i,match] of scripts.entries()){
      const filename=`assets/${['config','catalog','app'][i]}.${digest(match[2]).slice(0,16)}.js`;
      await writeFile(path.join(stage,filename),match[2]);
      html=html.replace(match[0],()=>`<script${match[1]} src="./${filename}"></script>`);
      assets.push(filename);
    }
    const styles=[...html.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)];
    ensure(styles.length===1,'Expected one complete stylesheet.');
    for(const match of styles){
      const filename=`assets/styles.${digest(match[1]).slice(0,16)}.css`;
      await writeFile(path.join(stage,filename),match[1]);
      html=html.replace(match[0],()=>`<link rel="stylesheet" href="./${filename}">`);
      assets.push(filename);
    }
    // Only compiled public assets are published, even when old repository files remain.
    await writeFile(path.join(stage,'index.html'),html);
    await writeFile(path.join(stage,'robots.txt'),'User-agent: *\nDisallow: /\n');
    await writeFile(path.join(stage,'build-info.json'),JSON.stringify({
      release:RELEASE.id,version:RELEASE.version,mode:data.mode,products:data.products.length,
      tyres:data.products.filter(p=>p.kind==='tires').length,
      wheels:data.products.filter(p=>p.kind==='wheels').length,
      dataUpdatedAt:data.updatedAt,builtAt:new Date().toISOString(),sourceSha256:EXPECTED_HTML_SHA256,assets,
    },null,2)+'\n');
    await rm(path.join(root,'dist'),{recursive:true,force:true});
    await rename(stage,path.join(root,'dist'));
    console.log(`Built dist: ${data.products.length} products. Mode: snapshot. No client credentials.`);
    console.log(`BUILD_OK ${RELEASE.id} | snapshot | ${data.products.length} products`);
  }finally{await rm(stage,{recursive:true,force:true});}
}

build().catch(error=>{
  console.error(`BUILD_FAILED ${RELEASE.id}: ${error.message}`);
  console.error('Render: Build Command = node BUILD_RENDER.mjs; Publish Directory = dist; Root Directory = repository root.');
  process.exitCode=1;
});
