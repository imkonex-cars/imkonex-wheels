import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir,cp,mkdtemp,rm,readdir} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {JSDOM,VirtualConsole} from 'jsdom';

// Run after packaging. IMKONEX_RELEASE_DIR can point to a freshly extracted ZIP.
const release=process.env.IMKONEX_RELEASE_DIR || fileURLToPath(new URL('../artifacts/github-full/',import.meta.url));
const run=(cwd,...args)=>spawnSync(process.execPath,['BUILD_RENDER.mjs',...args],{
  cwd,encoding:'utf8',env:{...process.env,CATALOG_MODE:'demo'},
});

async function browser(standalone=false){
  const entry=path.join(release,standalone?'index.html':'dist/index.html');
  const html=await readFile(entry,'utf8');
  const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(html,{url:standalone?'file:///C:/Users/Admin/Downloads/index.html':'https://catalog.imkonex.test/',runScripts:'outside-only',virtualConsole:vc});
  const w=dom.window;
  w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};
  w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
  w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
  for(const script of w.document.querySelectorAll('script')){
    const code=script.hasAttribute('src')?await readFile(path.join(path.dirname(entry),script.getAttribute('src')),'utf8'):script.textContent;
    w.eval(code);
  }
  return {dom,w,errors,$:s=>w.document.querySelector(s),click:s=>w.document.querySelector(s).click(),route:async hash=>{w.location.hash=hash;await new Promise(r=>w.setTimeout(r,10));}};
}

test('flat release publishes six snapshot products even with stale demo files and mode',async()=>{
  const temp=await mkdtemp(path.join(tmpdir(),'imkonex-full-'));
  try{
    await cp(release,temp,{recursive:true});
    await mkdir(path.join(temp,'scripts'),{recursive:true});
    await writeFile(path.join(temp,'scripts/build.mjs'),'throw new Error("Old demo builder must not run");');
    await mkdir(path.join(temp,'dist'),{recursive:true});
    await writeFile(path.join(temp,'dist','old-private-sentinel.txt'),'MUST_NOT_BE_PUBLISHED');
    const result=run(temp);assert.equal(result.status,0,result.stdout+result.stderr);
    assert.match(result.stdout,/BUILD_OK IMKONEX-WHEELS-0\.3\.0-FULL-1 \| snapshot \| 6 products/);
    const info=JSON.parse(await readFile(path.join(temp,'dist/build-info.json')));
    assert.equal(info.mode,'snapshot');assert.equal(info.products,6);assert.equal(info.tyres,3);assert.equal(info.wheels,3);
    assert.deepEqual((await readdir(path.join(temp,'dist'))).sort(),['assets','build-info.json','index.html','robots.txt']);
    const html=await readFile(path.join(temp,'dist/index.html'),'utf8');
    const dom=new JSDOM(html);
    try{
      assert.equal(dom.window.document.querySelectorAll('script:not([src]),style,[onload],[onclick],[onerror]').length,0);
      const urls=[...dom.window.document.querySelectorAll('script[src],link[rel="stylesheet"]')].map(el=>el.getAttribute('src')||el.getAttribute('href'));
      assert.equal(urls.length,4);
      for(const url of urls){assert.match(url,/^\.\/assets\/[a-z]+\.[0-9a-f]{16}\.(js|css)$/);assert.ok((await readFile(path.join(temp,'dist',url))).length>0);}
    }finally{dom.window.close();}
  }finally{await rm(temp,{recursive:true,force:true});}
});

test('release tolerates Windows line endings and fails mismatched HTML without replacing dist',async()=>{
  const temp=await mkdtemp(path.join(tmpdir(),'imkonex-pair-'));
  try{
    await cp(release,temp,{recursive:true});
    const entry=path.join(temp,'index.html'),html=await readFile(entry,'utf8');
    await writeFile(entry,'\uFEFF'+html.replace(/\r\n?/g,'\n').replace(/\n/g,'\r\n'));
    const valid=run(temp);assert.equal(valid.status,0,valid.stdout+valid.stderr);
    const previous=await readFile(path.join(temp,'dist/index.html'));
    await writeFile(entry,html+'\n<!-- stale or incomplete upload -->');
    const invalid=run(temp);assert.equal(invalid.status,1);
    assert.match(invalid.stderr,/from different releases/);
    assert.deepEqual(await readFile(path.join(temp,'dist/index.html')),previous);
  }finally{await rm(temp,{recursive:true,force:true});}
});

test('compiled app calculates real tire and rim sets with warehouse stock limits',async()=>{
  const b=await browser();try{
    assert.equal(b.w.IMKONEX_CONFIG.version,'0.3.0');assert.equal(b.w.IMKONEX_DATA.mode,'snapshot');
    assert.equal(b.w.IMKONEX_DATA.products.length,6);assert.equal(b.w.document.querySelectorAll('.product-card').length,3);
    assert.match(b.$('#catalog-notice').textContent,/Реальная выборка.*6 товаров/);
    assert.match(b.$('#release-label').textContent,/0\.3\.0/);
    b.click('[data-action="detail"][data-id="4t-tires-2398000"]');b.click('#dialog [data-action="add"]');
    await b.route('cart');assert.match(b.$('.summary-total').textContent,/132\s*200/);assert.match(b.$('.cart-info').textContent,/Домодедово/);
    b.click('[data-action="quote"]');
    const quote=JSON.parse(b.w.localStorage.getItem('imx:snapshot:requests'))[0];
    assert.equal(quote.total,132200);assert.equal(quote.priceBasis,'supplier_retail');assert.equal(quote.lines[0].sku,'2398000');
    assert.deepEqual(b.errors,[]);
  }finally{b.dom.window.close();}
  const rim=await browser();try{
    await rim.route('catalog?kind=wheels&inSet=1');assert.equal(rim.w.document.querySelectorAll('.product-card').length,1);
    assert.match(rim.$('.warehouse-label').textContent,/Уфа 2/);
    rim.click('[data-action="add"][data-id="4t-wheels-WHS121894"]');await rim.route('cart');
    assert.match(rim.$('.summary-total').textContent,/30\s*640/);
    rim.click('[data-action="quantity"][data-delta="1"]');assert.equal(rim.$('.quantity-control span').textContent,'4');
    assert.deepEqual(rim.errors,[]);
  }finally{rim.dom.window.close();}
});

test('standalone index opens from a file URL and switches categories without persistent storage',async()=>{
  const b=await browser(true);try{
    b.click('[data-action="category"][data-kind="wheels"]');await new Promise(r=>b.w.setTimeout(r,10));
    assert.equal(b.w.document.querySelectorAll('.product-card').length,3);
    b.click('[data-action="add"][data-id="4t-wheels-WHS121894"]');await b.route('cart');
    b.click('[data-action="quote"]');await b.route('manager');
    assert.equal(b.w.document.querySelectorAll('.manager-table tbody tr').length,1);
    assert.match(b.$('.manager-table').textContent,/30\s*640/);assert.deepEqual(b.errors,[]);
  }finally{b.dom.window.close();}
});
