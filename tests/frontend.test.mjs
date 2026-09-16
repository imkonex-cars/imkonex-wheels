import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM,VirtualConsole} from 'jsdom';
import {effectiveOffer,cartTotals,filterProducts,parseFilters,serializeFilters,escapeHTML,retailPrice} from '../frontend/domain.js';
const data=JSON.parse(await readFile(new URL('../data/demo-catalog.json',import.meta.url)));
const fixture={id:'stock',kind:'tires',brand:'Test',model:'Stock',width:225,profile:45,diameter:18,offers:[{stock:2,price:100,days:1},{stock:2,price:90,days:4}]};

test('a set cannot combine insufficient stocks from two warehouses',()=>{assert.equal(effectiveOffer(fixture,4),null);assert.equal(effectiveOffer(fixture,2).price,100);assert.equal(effectiveOffer(fixture,-1),null);assert.deepEqual(cartTotals([{id:'stock',quantity:4}],[fixture]).issues,['stock']);});
test('quote uses the warehouse with the complete quantity',()=>{const p={...fixture,offers:[...fixture.offers,{stock:8,price:120,days:5}]};assert.equal(cartTotals([{id:'stock',quantity:4}],[p]).total,480);});
test('prices sort by the selected set offer, with unavailable products last',()=>{const a={...fixture,id:'a',offers:[{stock:2,price:50,days:1},{stock:8,price:300,days:5}]},b={...fixture,id:'b',offers:[{stock:8,price:200,days:1}]},c={...fixture,id:'c',offers:[]};assert.deepEqual(filterProducts([a,b],{inSet:true,sort:'price-up'}).map(p=>p.id),['b','a']);assert.equal(filterProducts([a,b,c],{sort:'price-down'}).at(-1).id,'c');});
test('invalid quantity and unknown product do not contribute to totals',()=>{const t=cartTotals([{id:'stock',quantity:1.5},{id:'missing',quantity:1}],[fixture]);assert.equal(t.total,0);assert.equal(t.issues.length,2);});
test('URL filters round-trip including multiple brands and boolean switches',()=>{const f=parseFilters('kind=tires&brand=Michelin&brand=Hankook&width=225&inSet=1');assert.deepEqual(parseFilters(serializeFilters(f)),f);});
test('markup rounds up and enforces a minimum absolute margin',()=>{assert.equal(retailPrice(10000,18,700),11800);assert.equal(retailPrice(100,1,55),160);assert.throws(()=>retailPrice(0,10,0));});
test('untrusted text is escaped for HTML attributes',()=>{assert.equal(escapeHTML('<img onerror="x">'), '&lt;img onerror=&quot;x&quot;&gt;');});

async function browser(){
 const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const html=(await readFile(new URL('../frontend/index.html',import.meta.url),'utf8')).replace(/<script[^>]*>[\s\S]*?<\/script>/g,'');
 const dom=new JSDOM(html,{url:'https://demo.imkonex.test/',runScripts:'outside-only',virtualConsole:vc});
 const w=dom.window;w.IMKONEX_DATA=data;w.IMKONEX_CONFIG={mode:'demo',apiBase:''};w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
 const domain=(await readFile(new URL('../frontend/domain.js',import.meta.url),'utf8')).replace(/^export /gm,'');
 const app=(await readFile(new URL('../frontend/app.js',import.meta.url),'utf8')).replace(/^import .*;\n/,'');
 w.eval(domain+'\nconst h=escapeHTML;\n'+app);
 return {w,dom,errors,$:s=>w.document.querySelector(s),click:s=>w.document.querySelector(s).click(),route:async path=>{w.location.hash=path;await new Promise(r=>w.setTimeout(r,10));}};
}
test('catalog, favorite, detail, cart, quote and manager work end to end',async()=>{const b=await browser();try{
 assert.equal(b.w.document.querySelectorAll('.product-card').length,12);
 b.click('[data-action="favorite"][data-id="demo-001"]');assert.equal(b.$('#favorites-count').textContent,'1');
 b.click('[data-action="detail"][data-id="demo-001"]');assert.equal(b.$('#dialog').open,true);
 b.click('#dialog [data-action="add"]');assert.equal(b.$('#cart-count').textContent,'4');
 await b.route('cart');assert.match(b.$('.summary-total').textContent,/42\s*720/);
 b.click('[data-action="quote"]');assert.match(b.$('#dialog-title').textContent,/сохранена/);
 await b.route('manager');assert.equal(b.w.document.querySelectorAll('.manager-table tbody tr').length,1);
 assert.deepEqual(b.errors,[]);
 }finally{b.dom.window.close();}});
test('filter URL, category switch and comparison limit are interactive',async()=>{const b=await browser();try{
 await b.route('catalog?kind=tires&width=205&profile=55&diameter=16');assert.equal(b.w.document.querySelectorAll('.product-card').length,3);
 b.click('[data-action="compare"][data-id="demo-001"]');b.click('[data-action="compare"][data-id="demo-002"]');
 await b.route('compare');assert.equal(b.w.document.querySelectorAll('.compare-head').length,2);
 await b.route('catalog?kind=wheels');b.click('[data-action="compare"]');assert.match(b.$('#toast').textContent,/одной категории/);
 b.click('[data-action="add"]');assert.equal(b.$('#cart-count').textContent,'4');assert.deepEqual(b.errors,[]);
 }finally{b.dom.window.close();}});
test('garage uses user-entered size and escapes saved model names',async()=>{const b=await browser();try{
 b.w.localStorage.setItem('imx:garage',JSON.stringify({brand:'<img src=x onerror=alert(1)>',model:'Camry',year:'2022',width:'225',profile:'45',diameter:'18'}));
 await b.route('garage');assert.equal(b.$('[name="brand"]').value,'<img src=x onerror=alert(1)>');assert.equal(b.$('.garage-layout img'),null);
 b.$('#garage-form').dispatchEvent(new b.w.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>b.w.setTimeout(r,10));
 assert.equal(b.$('[data-filter="width"]').value,'225');assert.match(b.$('#toast').textContent,/не подтверждена/);assert.deepEqual(b.errors,[]);
 }finally{b.dom.window.close();}});
