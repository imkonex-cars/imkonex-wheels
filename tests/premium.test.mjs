import {browserBundle} from './browser-bundle.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM,VirtualConsole} from 'jsdom';
const sample=JSON.parse(await readFile(new URL('./fixtures/public-sample.json',import.meta.url),'utf8'));
async function open({manager=false,data=sample,fetch}={}){
 const dir=manager?'manager/':'';
 const html=(await readFile(new URL('../frontend/'+dir+'index.html',import.meta.url),'utf8')).replace(/<script[^>]*>[\s\S]*?<\/script>/g,'');
 const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const dom=new JSDOM(html,{url:'https://catalog.test/'+dir,runScripts:'outside-only',virtualConsole:vc}),w=dom.window;
 w.IMKONEX_DATA=data;w.IMKONEX_CONFIG={portal:true};w.fetch=fetch;w.AbortSignal.timeout=()=>undefined;
 w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};
 w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
 w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');this.dispatchEvent(new w.Event('close'));};
 await w.eval(await browserBundle(manager?'manager/manager.js':'app.js'));
 const $=s=>w.document.querySelector(s),tick=()=>new Promise(r=>w.setTimeout(r,20));
 return {dom,w,errors,$,tick,click:async s=>{$(s).click();await tick();},change:async(s,v)=>{$(s).value=v;$(s).dispatchEvent(new w.Event('change',{bubbles:true}));await tick();},submit:async s=>{$(s).dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();}};
}
const ok=data=>({ok:true,status:200,json:async()=>data});

test('vehicle sequence cascades and selects only API-confirmed products',async()=>{
 const calls=[];const b=await open({fetch:async url=>{calls.push(url);const stage=new URL(url,'https://catalog.test').pathname.split('/').at(-1);return ok({makes:{items:['Toyota','BMW']},models:{items:['Camry']},years:{items:[{begin:2017,end:2021}]},modifications:{items:['2.5 AT']},products:{productIds:['4t-tires-2398000']}}[stage]);}});
 try{
  await b.click('[data-action="mode"][data-value="car"]');
  assert.equal(b.$('[data-vehicle="model"]').disabled,true);
  await b.change('[data-vehicle="make"]','Toyota');await b.change('[data-vehicle="model"]','Camry');
  await b.change('[data-vehicle="years"]','2017:2021');await b.change('[data-vehicle="modification"]','2.5 AT');
  await b.click('[data-action="vehicle-search"]');
  assert.equal(b.w.document.querySelectorAll('.product-card').length,1);
  assert.ok(b.$('.product-card [data-id="4t-tires-2398000"]'));
  assert.match(calls.at(-1),/make=Toyota&model=Camry&begin=2017&end=2021&modification=2.5\+AT/);
  await b.change('[data-vehicle="make"]','BMW');assert.equal(b.$('[data-vehicle="years"]').value,'');
  assert.equal(b.$('[data-action="vehicle-search"]').disabled,true);
  assert.equal(b.w.document.querySelectorAll('.product-card').length,3);
  assert.deepEqual(b.errors,[]);
 }finally{b.dom.window.close();}
});

test('vehicle API failure keeps a visible recoverable error',async()=>{
 const b=await open({fetch:async()=>{throw new Error('offline');}});try{
  await b.click('[data-action="mode"][data-value="car"]');assert.match(b.$('.vehicle-hint').textContent,/Не удалось загрузить/);
  assert.equal(b.$('[data-action="vehicle-search"]').disabled,true);assert.equal(b.w.document.querySelectorAll('.product-card').length,3);
 }finally{b.dom.window.close();}
});

test('sections remain visible and filter only explicit supplier categories',async()=>{
 const data=structuredClone(sample);data.products[0].vehicleCategory='truck';data.products[1].vehicleCategory='moto';
 const b=await open({data,fetch:async()=>ok({})});try{
  assert.ok(b.$('[data-category="truck"]'));assert.ok(b.$('[data-category="moto"]'));assert.ok(b.$('[data-category="special"]'));
  await b.click('[data-category="truck"]');assert.equal(b.w.document.querySelectorAll('.product-card').length,1);
  assert.ok(b.$('.product-card [data-id="4t-tires-R5019"]'));
 }finally{b.dom.window.close();}
});

test('manager calculates no client prices and confirms exact server preview',async()=>{
 const requests=[];let created=0;const p=structuredClone(sample.products[0]);p.offers=[{id:'warehouse-2017',warehouseId:2017,warehouse:'Test warehouse',stock:20,liveStock:20,price:8000,salePrice:8000,purchasePrice:6000,purchaseCheckedAt:1,profit:2000}];p.priceRule={mode:'supplier'};
 const order={id:'IMX-SYNTHETIC',confirmation:'test-confirmation',test:true,createdAt:1,expiresAt:9999999999,purchaseTotal:24000,saleTotal:32000,lines:[{productId:p.id,sku:p.sku,name:'Test tire',warehouse:'Test warehouse',warehouseId:2017,quantity:4,purchasePrice:6000,salePrice:8000}]};
 const b=await open({manager:true,fetch:async(url,options)=>{requests.push([url,options]);
  if(url.endsWith('/session'))return ok({csrf:'synthetic-csrf'});
  if(url.endsWith('/status'))return ok({version:'0.5.0',products:1,updatedAt:'2026-09-22',supplierConfigured:true,ordersEnabled:true,rules:0,orders:0});
  if(url.endsWith('/catalog-filters'))return ok({warehouses:[],facets:{kind:['tires']}});
  if(url.includes('/products?'))return ok({items:[p],total:1,page:1});
  if(url.endsWith('/preview'))return ok({...order,status:'draft'});
  if(url.endsWith('/submit')){created++;return ok({...order,status:'submitted',providerId:123,providerNumber:'TEST'});}
  if(url.endsWith('/orders'))return ok({items:created?[{...order,status:'submitted',providerId:123}]:[]});
  throw new Error('Unexpected endpoint '+url);
 }});
 try{
  await b.click('[data-tab="products"]');assert.match(b.$('#manager-app').textContent,/6\s*000/);
  const qty=b.$('[data-row-quantity]');qty.value='4';qty.dispatchEvent(new b.w.Event('input',{bubbles:true}));await b.click('[data-action="selection-order"]');await b.submit('#preview-order-form');
  assert.match(b.$('#manager-dialog-content').textContent,/24\s*000/);
  const payload=JSON.parse(requests.find(([url])=>url.endsWith('/preview'))[1].body);
  assert.deepEqual(payload.lines,[{productId:p.id,warehouseId:2017,quantity:4}]);assert.equal(payload.test,true);
  assert.equal(created,0);await b.click('[data-action="submit-order"]');assert.equal(created,1);
  assert.match(b.$('#manager-dialog-content').textContent,/Создан у поставщика/);
  assert.equal(b.w.localStorage.length,0);assert.deepEqual(b.errors,[]);
 }finally{b.dom.window.close();}
});

test('customer card loads real characteristics, labels missing fields and escapes API text',async()=>{
 const b=await open({fetch:async url=>{assert.match(url,/\/api\/products\/.*\/details/);return ok({attributes:[{label:'Шумность',value:'72 дБ'},{label:'Сцепление',value:'B'},{label:'Комфорт',value:'<img src=x onerror=alert(1)>'}]});}});
 try{
  await b.click('[data-action="detail"]');
  const text=b.$('#technical-characteristics').textContent;
  assert.match(text,/Шумность72 дБ/);assert.match(text,/СцеплениеB/);
  assert.match(text,/Индекс износостойкостиНет данных/);
  assert.equal(b.$('#technical-characteristics img'),null);
  assert.equal(b.$('#technical-status').textContent,'');
  assert.deepEqual(b.errors,[]);
 }finally{b.dom.window.close();}
});
