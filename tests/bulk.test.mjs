import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM,VirtualConsole} from 'jsdom';
import {validateSnapshot} from '../scripts/public-catalog.mjs';
import {paginationPages,brandCounts,filterProducts} from '../frontend/domain.js';

const sample=JSON.parse(await readFile(new URL('./fixtures/public-sample.json',import.meta.url),'utf8'));
function catalog(count){
 const products=Array.from({length:count},(_,i)=>{
  const p=structuredClone(sample.products[i%6]);
  p.sku=`BULK${i}`;p.id=`4t-${p.kind}-${p.sku}`;p.brand=`Brand ${i%120}`;return p;
 });
 const categories=Object.fromEntries(['tires','wheels'].map(kind=>{
  const n=products.filter(p=>p.kind===kind).length;
  return [kind,{published:n,excluded:0,pages:Math.ceil(n/100),pageBase:1,scanned:n}];
 }));
 return {...sample,schemaVersion:3,products,sync:{complete:true,scope:'available_supported_products',
  startedAt:sample.updatedAt,finishedAt:sample.updatedAt,sourceProducts:count,publishedProducts:count,
  excludedProducts:0,categories,excludedReasons:{}}};
}

test('bulk contract accepts complete large imports but rejects partial or inconsistent totals',()=>{
 const data=catalog(24000);assert.equal(validateSnapshot(data),data);
 for(const mutate of [d=>d.sync.complete=false,d=>d.sync.sourceProducts++,d=>d.sync.categories.tires.published--,
  d=>d.sync.rawResponse={},d=>d.products[0].offers[0].cost=1]){
  const changed=structuredClone(data);mutate(changed);assert.throws(()=>validateSnapshot(changed));
 }
 const missing=structuredClone(data);delete missing.sync;assert.throws(()=>validateSnapshot(missing));
});

test('paging stays bounded and brand totals respect filters without sorting every brand',()=>{
 assert.deepEqual(paginationPages(500,1000),[1,null,498,499,500,501,502,null,1000]);
 assert.deepEqual(paginationPages(1,1),[1]);
 assert.ok(paginationPages(999,1000).length<=9);
 const data=catalog(180),filters={kind:'wheels',inSet:true};
 const counts=brandCounts(data.products,filters);
 assert.equal([...counts.values()].reduce((a,b)=>a+b,0),filterProducts(data.products,filters).length);
});

test('24,000 products render 12 cards, show freshness and reach last page and exact SKU',async()=>{
 const data=catalog(24000),errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const html=(await readFile(new URL('../frontend/index.html',import.meta.url),'utf8')).replace(/<script[^>]*>[\s\S]*?<\/script>/g,'');
 const dom=new JSDOM(html,{url:'https://imkonex.test/',runScripts:'outside-only',virtualConsole:vc}),w=dom.window;
 try{
  w.IMKONEX_DATA=data;w.IMKONEX_CONFIG={mode:'snapshot',apiBase:''};w.scrollTo=()=>{};
  w.HTMLElement.prototype.scrollIntoView=()=>{};
  w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
  w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
  const domain=(await readFile(new URL('../frontend/domain.js',import.meta.url),'utf8')).replace(/^export /gm,'');
  const app=(await readFile(new URL('../frontend/app.js',import.meta.url),'utf8')).replace(/^import .*;\n/,'');
  const start=performance.now();w.eval(domain+'\nconst h=escapeHTML;\n'+app);
  const $=s=>w.document.querySelector(s);
  assert.equal(w.document.querySelectorAll('.product-card').length,12);
  assert.ok(w.document.querySelectorAll('.pagination button').length<=7);
  assert.match($('#catalog-notice').textContent,/Каталог поставщика.*24000/);
  assert.match($('.data-summary').textContent,/Последняя успешная выгрузка/);
  assert.doesNotMatch($('.data-summary').textContent,/ещё не подключено|выборка из отчёта/);
  $('[data-action="page"][data-page="1000"]').click();
  assert.match($('.pagination').textContent,/11989–12000 из 12000/);
  w.location.hash='catalog?kind=wheels&q=BULK23999';await new Promise(r=>w.setTimeout(r,10));
  assert.equal(w.document.querySelectorAll('.product-card').length,1);
  assert.match($('.product-code').textContent,/BULK23999/);
  assert.deepEqual(errors,[]);
  console.log(`Large-catalog DOM check: ${Math.round(performance.now()-start)} ms for initial render, last page and exact search.`);
 }finally{dom.window.close();}
});
