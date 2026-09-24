import {browserBundle} from './browser-bundle.mjs';
import {pathToFileURL} from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,writeFile,cp,mkdir,mkdtemp,rm,readdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {JSDOM,VirtualConsole} from 'jsdom';

const source=process.env.IMKONEX_WORKSPACE_DIR || fileURLToPath(new URL('../',import.meta.url));
const run=root=>spawnSync(process.execPath,[path.join(root,'BUILD_RENDER.mjs')],{
  cwd:tmpdir(),encoding:'utf8',env:{...process.env,CATALOG_MODE:'demo',PUBLIC_API_BASE:'https://old-api.invalid'},
});
async function copyWorkspace(){
  const root=await mkdtemp(path.join(tmpdir(),'imkonex-workspace-'));
  for(const name of ['BUILD_RENDER.mjs','package.json','frontend','data','scripts']){
    await cp(path.join(source,name),path.join(root,name),{recursive:true});
  }
  await cp(new URL('./fixtures/public-sample.json',import.meta.url),path.join(root,'data/supplier-snapshot.json'));
  return root;
}

test('complete source entry builds snapshot from any working directory and ignores stale root files',async()=>{
  const root=await copyWorkspace();try{
    await writeFile(path.join(root,'index.html'),'OLD ROOT DEMO PAGE');
    await mkdir(path.join(root,'dist'));await writeFile(path.join(root,'dist','old-file.txt'),'old');
    const result=run(root);assert.equal(result.status,0,result.stdout+result.stderr);
    const {version}=JSON.parse(await readFile(path.join(root,'package.json'),'utf8'));
    assert.ok(result.stdout.includes(`BUILD_OK IMKONEX-WHEELS-${version}-WORKSPACE-1 | snapshot | 6 products`));
    const info=JSON.parse(await readFile(path.join(root,'dist/build-info.json'),'utf8'));
    assert.equal(info.mode,'snapshot');assert.equal(info.products,6);assert.equal(info.tyres,3);assert.equal(info.wheels,3);
    assert.deepEqual(info.photos,{local:6,remote:0,unavailable:0});
    const snapshot=JSON.parse(await readFile(path.join(root,'data/supplier-snapshot.json'),'utf8'));
    for(const product of snapshot.products){
      assert.match(product.image,/^assets\/products\//);
      assert.deepEqual(await readFile(path.join(root,'dist',product.image)),await readFile(path.join(root,'frontend',product.image)));
    }
    const files=await readdir(path.join(root,'dist'));
    assert.ok(!files.includes('old-file.txt')&&!files.includes('backend')&&!files.includes('SOURCE_CODE.zip'));
    assert.doesNotMatch(await readFile(path.join(root,'dist/index.html'),'utf8'),/OLD ROOT DEMO PAGE/);
    assert.match(await readFile(path.join(root,'dist/config.js'),'utf8'),/"apiBase":""/);
  }finally{await rm(root,{recursive:true,force:true});}
});

test('missing workspace files and private catalog fields fail before replacing previous dist',async()=>{
  const root=await copyWorkspace();try{
    const first=run(root);assert.equal(first.status,0,first.stdout+first.stderr);
    const previous=await readFile(path.join(root,'dist/build-info.json'));
    const domain=await readFile(path.join(root,'frontend/domain.js'));
    await rm(path.join(root,'frontend/domain.js'));
    const missing=run(root);assert.equal(missing.status,1);assert.match(missing.stderr,/Missing: frontend\/domain.js/);
    assert.deepEqual(await readFile(path.join(root,'dist/build-info.json')),previous);
    await writeFile(path.join(root,'frontend/domain.js'),domain);
    const file=path.join(root,'data/supplier-snapshot.json');
    const data=JSON.parse(await readFile(file,'utf8'));data.products[0].offers[0].cost=123;
    await writeFile(file,JSON.stringify(data));
    const privateData=run(root);assert.equal(privateData.status,1);assert.match(privateData.stderr,/BUILD_FAILED/);
    assert.deepEqual(await readFile(path.join(root,'dist/build-info.json')),previous);
  }finally{await rm(root,{recursive:true,force:true});}
});

test('published source workspace loads six real products and calculates a complete rim set',async()=>{
  const root=await copyWorkspace();let dom;
  try{
    const result=run(root);assert.equal(result.status,0,result.stdout+result.stderr);
    const dist=path.join(root,'dist'),errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
    dom=new JSDOM(await readFile(path.join(dist,'index.html'),'utf8'),{url:'https://imkonex.test/',runScripts:'outside-only',virtualConsole:vc});
    const w=dom.window;w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};
    w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
    w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
    for(const script of w.document.querySelectorAll('script[src]')){
      let code=await readFile(path.join(dist,script.getAttribute('src')),'utf8');
      if(script.type==='module')code=await browserBundle(script.getAttribute('src'),pathToFileURL(dist+'/'));
      w.eval(code);
    }
    const $=s=>w.document.querySelector(s);
    assert.equal(w.IMKONEX_DATA.products.length,6);assert.equal(w.IMKONEX_CONFIG.mode,'snapshot');
    assert.equal($('#catalog-notice'),null);assert.match($('.hero h1').textContent,/Ваш маршрут/);
    w.location.hash='catalog?kind=wheels&inSet=1';await new Promise(r=>w.setTimeout(r,10));
    assert.equal(w.document.querySelectorAll('.product-card').length,1);assert.match($('.stock-row').textContent,/В наличии/);
    $('[data-action="add"][data-id="4t-wheels-WHS121894"]').click();
    w.location.hash='cart';await new Promise(r=>w.setTimeout(r,10));
    assert.match($('.summary-total').textContent,/30\s*640/);assert.deepEqual(errors,[]);
  }finally{dom?.window.close();await rm(root,{recursive:true,force:true});}
});
