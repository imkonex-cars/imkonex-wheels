import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM,VirtualConsole} from 'jsdom';
import {validateSnapshot} from '../scripts/public-catalog.mjs';
import {effectiveOffer,filterProducts,cartTotals} from '../frontend/domain.js';

const data=JSON.parse(await readFile(new URL('./fixtures/public-sample.json',import.meta.url)));
const product=sku=>data.products.find(p=>p.sku===sku);
test('public sample preserves six verified retail prices and no assumed delivery days',()=>{
 assert.equal(validateSnapshot(data),data);
 assert.deepEqual(data.products.map(p=>[p.sku,p.offers[0].price]),[['R5019',14890],['2398000',33050],['T429599',10130],['WHS118147',7650],['WHS121894',7660],['WHS158820',6452]]);
 assert.ok(data.products.every(p=>p.offers.every(o=>o.days===null)));
 assert.equal(filterProducts(data.products,{fast:true}).length,0);
 assert.equal(product('2398000').xl,true);assert.equal(product('T429599').xl,true);
 assert.deepEqual(filterProducts(data.products,{q:'235/50 R17'}).map(p=>p.sku),['R5019']);
});
test('a real set selects Domodedovo or Ufa and never sums warehouses',()=>{
 assert.equal(effectiveOffer(product('2398000'),4).warehouse,'Домодедово (Кучино)');
 const venti=product('WHS121894');assert.equal(effectiveOffer(venti,4).warehouse,'Уфа 2');
 assert.equal(cartTotals([{id:venti.id,quantity:4}],data.products).total,30640);
 assert.equal(effectiveOffer({...venti,offers:venti.offers.map(o=>({...o,stock:1}))},4),null);
 assert.equal(effectiveOffer(product('R5019'),4),null);
});
test('static contract rejects accidentally included private fields or foreign photo URLs',()=>{
 for(const mutate of [d=>d.calls=[],d=>d.products[0].password='secret',d=>d.products[0].offers[0].cost=98765,d=>d.products[0].image='https://example.org/p.png']){
  const copy=structuredClone(data);mutate(copy);assert.throws(()=>validateSnapshot(copy));
 }
});
test('local photo paths cannot escape the product assets directory',()=>{
 for(const image of ['assets/products/../private.png','assets/products/%2e%2e/private.png','/assets/products/R5019.png','assets/products/R5019.png?token=secret','assets/products/R5019.svg','assets/products/nested/R5019.png','assets/products\\R5019.png']){
  const copy=structuredClone(data);copy.products[0].image=image;assert.throws(()=>validateSnapshot(copy),image);
 }
});

async function browser(url='https://catalog.imkonex.test/'){
 const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const html=(await readFile(new URL('../frontend/index.html',import.meta.url),'utf8')).replace(/<script[^>]*>[\s\S]*?<\/script>/g,'');
 const dom=new JSDOM(html,{url,runScripts:'outside-only',virtualConsole:vc}),w=dom.window;
 w.IMKONEX_DATA=data;w.IMKONEX_CONFIG={mode:'snapshot',apiBase:''};w.scrollTo=()=>{};
 w.HTMLElement.prototype.scrollIntoView=()=>{};
 w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
 w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
 if(!url.startsWith('file:'))w.localStorage.setItem('imx:requests',JSON.stringify([{id:'OLD-DEMO',total:1}]));
 const domain=(await readFile(new URL('../frontend/domain.js',import.meta.url),'utf8')).replace(/^export /gm,'');
 const app=(await readFile(new URL('../frontend/app.js',import.meta.url),'utf8')).replace(/^import .*;\n/,'');
 w.eval(domain+'\nconst h=escapeHTML;\n'+app);
 return {w,dom,errors,$:s=>w.document.querySelector(s),click:s=>w.document.querySelector(s).click(),route:async path=>{w.location.hash=path;await new Promise(r=>w.setTimeout(r,10));}};
}
test('real tire selection, set calculation and saved quote retain price provenance',async()=>{
 const b=await browser();try{
  assert.equal(b.w.document.querySelectorAll('.product-card').length,3);
  assert.match(b.$('#catalog-notice').textContent,/Реальная выборка/);
  assert.match(b.$('.data-summary-date').textContent,/17.09.2026/);
  assert.equal(b.$('[data-filter="fast"]'),null);
  b.click('[data-action="detail"][data-id="4t-tires-2398000"]');
  assert.match(b.$('#dialog-content').textContent,/Домодедово/);
  assert.match(b.$('#dialog-content').textContent,/Срок уточняется/);
  assert.doesNotMatch(b.$('#dialog-content').textContent,/0–1 дн\./);
  b.click('#dialog [data-action="add"]');
  await b.route('cart');assert.match(b.$('.summary-total').textContent,/132\s*200/);
  assert.match(b.$('.cart-info').textContent,/Домодедово/);
  b.click('[data-action="quote"]');
  const quote=JSON.parse(b.w.localStorage.getItem('imx:snapshot:requests'))[0];
  assert.equal(quote.priceBasis,'supplier_retail');assert.equal(quote.mode,'snapshot');
  assert.equal(quote.lines[0].sku,'2398000');assert.equal(quote.sourceUpdatedAt,data.updatedAt);
  await b.route('manager');assert.equal(b.w.document.querySelectorAll('.manager-table tbody tr').length,1);
  assert.doesNotMatch(b.$('#main').textContent,/OLD-DEMO|36 шин/);assert.deepEqual(b.errors,[]);
 }finally{b.dom.window.close();}
});
test('rim filters, Ufa stock limit, comparison and image fallback work',async()=>{
 const b=await browser();try{
  await b.route('catalog?kind=wheels&inSet=1');
  assert.equal(b.w.document.querySelectorAll('.product-card').length,1);
  assert.match(b.$('.warehouse-label').textContent,/Уфа 2/);
  b.click('[data-action="compare"]');await b.route('compare');
  assert.match(b.$('.comparison-table').textContent,/Уточняется/);
  b.click('[data-action="add"]');await b.route('cart');
  assert.match(b.$('.summary-total').textContent,/30\s*640/);
  b.click('[data-action="quantity"][data-delta="1"]');assert.equal(b.$('.quantity-control span').textContent,'4');
  await b.route('catalog?kind=wheels&q=WHS158820');
  assert.equal(b.w.document.querySelectorAll('.product-card').length,1);
  assert.equal(b.$('.product-tag').textContent.trim(),'Колёсный диск');
  const img=b.$('.product-visual img');img.dispatchEvent(new b.w.Event('error'));
  assert.match(img.src,/product-unavailable\.svg$/);assert.match(img.alt,/недоступно/);
  assert.deepEqual(b.errors,[]);
 }finally{b.dom.window.close();}
});
test('local file preview switches category and keeps calculations when persistent storage is unavailable',async()=>{
 const b=await browser('file:///C:/Users/Admin/Downloads/catalog.html');try{
  b.click('[data-action="category"][data-kind="wheels"]');
  await new Promise(r=>b.w.setTimeout(r,10));
  assert.equal(b.w.document.querySelectorAll('.product-card').length,3);
  b.click('[data-action="add"][data-id="4t-wheels-WHS121894"]');
  await b.route('cart');b.click('[data-action="quote"]');await b.route('manager');
  assert.equal(b.w.document.querySelectorAll('.manager-table tbody tr').length,1);
  assert.match(b.$('.manager-table').textContent,/30\s*640/);assert.deepEqual(b.errors,[]);
 }finally{b.dom.window.close();}
});
