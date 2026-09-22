import {browserBundle} from './browser-bundle.mjs';
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
 await w.eval(await browserBundle('app.js'));
 return {w,dom,errors,$:s=>w.document.querySelector(s),click:s=>w.document.querySelector(s).click(),route:async path=>{w.location.hash=path;await new Promise(r=>w.setTimeout(r,10));}};
}

test('real snapshot supports exact selection, stock limits and private-free storefront',async()=>{
 const b=await browser();try{
  assert.equal(b.w.document.querySelectorAll('.product-card').length,3);
  assert.equal(b.$('#catalog-notice'),null);assert.equal(b.$('.data-summary'),null);
  assert.doesNotMatch(b.$('#main').textContent,/4точки|выгрузка|6 товаров|проверка каталога/i);
  b.click('[data-action="detail"][data-id="4t-tires-2398000"]');b.click('#dialog [data-action="add"]');
  await b.route('cart');assert.match(b.$('.summary-total').textContent,/132\s*200/);assert.match(b.$('.warehouse-label').textContent,/Домодедово/);
  b.click('[data-action="clear-cart"]');await b.route('catalog?kind=wheels&inSet=1');
  assert.equal(b.w.document.querySelectorAll('.product-card').length,1);
  b.click('[data-action="add"]');await b.route('cart');assert.match(b.$('.summary-total').textContent,/30\s*640/);
  b.click('[data-action="quantity"][data-delta="1"]');assert.equal(b.$('.quantity-control span').textContent,'4');
  assert.deepEqual(b.errors,[]);
 }finally{b.dom.window.close();}
});
test('local file preview keeps selection with unavailable persistent storage',async()=>{
 const b=await browser('file:///C:/Users/Admin/Downloads/catalog.html');try{
  b.click('[data-action="segment"][data-kind="wheels"]');assert.equal(b.w.document.querySelectorAll('.product-card').length,3);
  b.click('[data-action="add"][data-id="4t-wheels-WHS121894"]');await b.route('cart');
  assert.match(b.$('.summary-total').textContent,/30\s*640/);assert.deepEqual(b.errors,[]);
 }finally{b.dom.window.close();}
});
