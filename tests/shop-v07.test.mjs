import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM,VirtualConsole} from 'jsdom';
import {browserBundle} from './browser-bundle.mjs';
import {filterProducts,parseFilters,serializeFilters} from '../frontend/domain.js';
import {fieldsFor,pairKey} from '../frontend/search-model.js';
import {calculatePrice} from '../frontend/manager/pricing.js';
const tire=(id,width,profile,model='Touring')=>({id,sku:id,brand:'Test',model,kind:'tires',vehicleCategory:'passenger',width,profile,diameter:18,season:'summer',construction:'R',loadIndex:'100',speedIndex:'W',description:'Synthetic tire',image:'assets/product-unavailable.svg',offers:[{id:'warehouse-1',warehouse:'А',price:7000,stock:4,days:null}]});
const items=[tire('front',225,45),tire('rear',255,40),tire('wrong',255,50),{...tire('truck',315,70),vehicleCategory:'truck',attributes:[{label:'Ось установки',value:'Ведущая'}]}];
async function open(entry='app.js',fetch=async()=>({ok:true,json:async()=>({})})){
 const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const html=(await readFile(new URL('../frontend/index.html',import.meta.url),'utf8')).replace(/<script[^>]*>[\s\S]*?<\/script>/g,'');
 const dom=new JSDOM(html,{url:'https://shop.test/',runScripts:'outside-only',virtualConsole:vc});const w=dom.window;
 w.IMKONEX_DATA={products:structuredClone(items)};w.IMKONEX_CONFIG={portal:true};w.fetch=fetch;w.AbortSignal.timeout=()=>undefined;w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
 const module=w.eval(await browserBundle(entry));const tick=()=>new Promise(r=>setTimeout(r,15));return {dom,w,module,errors,tick,$:s=>w.document.querySelector(s)};
}

test('staggered dimensions match each axle independently and persist in URL',()=>{
 const f=parseFilters('kind=tires&category=passenger&staggered=1&width=225&profile=45&diameter=18&rearWidth=255&rearProfile=40&rearDiameter=18');
 assert.deepEqual(filterProducts(items,f).map(p=>p.id),['front','rear']);assert.deepEqual(parseFilters(serializeFilters(f)),f);
 assert.deepEqual(filterProducts(items,{kind:'tires',category:'truck',axle:'Ведущая'}).map(p=>p.id),['truck']);
 assert.ok(fieldsFor('tires','truck').some(([key])=>key==='axle'));assert.ok(fieldsFor('oils','').some(([key])=>key==='sae'));
 assert.notEqual(pairKey(items[0]),pairKey({...items[0],model:'Other'}));
});

test('price and income edits clamp to purchase plus 100',()=>{
 for(const [field,v] of [['price',1],['percent',0],['profit',-500]]){const r=calculatePrice(4567.89,field,v,4);assert.equal(r.sale,4667.89);assert.equal(r.profit,100);assert.equal(r.profitTotal,400);assert.equal(r.floorApplied,true);}
});

test('customer can select both axles and add exactly two plus two',async()=>{
 const b=await open();try{
  b.w.location.hash='catalog?kind=tires&category=passenger&staggered=1&width=225&profile=45&diameter=18&rearWidth=255&rearProfile=40&rearDiameter=18';await b.tick();
  b.$('[data-action="choose-axle"][data-id="front"]').click();b.$('[data-action="choose-axle"][data-id="rear"]').click();
  assert.equal(b.$('[data-action="add-axles"]').disabled,false);b.$('[data-action="add-axles"]').click();await b.tick();
  assert.equal(b.w.document.querySelectorAll('.cart-item').length,2);assert.equal(b.$('#cart-count').textContent,'4');assert.deepEqual(b.errors,[]);
 }finally{b.w.close();}
});

test('checkout displays server total, creates one request, and exposes status link',async()=>{
 let requests=0,success=0;const calls=[];
 const b=await open('shop.js',async(url,opts)=>{calls.push([url,opts]);if(url==='/api/customer/session')return {ok:false,status:401,json:async()=>({detail:'customer_login_required'})};if(url.endsWith('/quote'))return {ok:true,json:async()=>({quoteId:'q'.repeat(32),total:28000,lines:[{name:'Test tire',sku:'front',quantity:4,price:7000,subtotal:28000}]})};requests++;return {ok:true,json:async()=>({id:'IMX-TEST',token:'t'.repeat(43),status:'new'})};});
 try{
  await b.module.startCheckout([{productId:'front',quantity:4}],()=>success++);
  const form=b.$('#checkout-form');for(const [key,v] of Object.entries({name:'Иван',phone:'+79999999999',city:'Москва'}))form.elements[key].value=v;form.elements.consent.checked=true;
  form.dispatchEvent(new b.w.Event('submit',{bubbles:true,cancelable:true}));form.dispatchEvent(new b.w.Event('submit',{bubbles:true,cancelable:true}));await b.tick();
  assert.equal(requests,1);assert.equal(success,1);assert.ok(b.$('a[href$="/order/#'+('t'.repeat(43))+'"]'));assert.match(b.$('#dialog-content').textContent,/Спасибо/);
  const payload=JSON.parse(calls.find(([url])=>url==='/api/shop/orders')[1].body);assert.equal(payload.consent,true);assert.equal(payload.price,undefined);assert.equal(payload.total,undefined);assert.deepEqual(b.errors,[]);
 }finally{b.w.close();}
});

test('manager category pricing requires preview then applies the exact inspected rule',async()=>{
 const html=(await readFile(new URL('../frontend/manager/index.html',import.meta.url),'utf8')).replace(/<script[^>]*>[\s\S]*?<\/script>/g,'');
 const dom=new JSDOM(html,{url:'https://shop.test/manager/',runScripts:'outside-only'}),w=dom.window;w.AbortSignal.timeout=()=>undefined;
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};const writes=[];
 w.fetch=async(url,opts={})=>{let data={};if(url.endsWith('/session'))data={csrf:'synthetic'};else if(url.endsWith('/status'))data={products:6};else if(url.endsWith('/pricing'))data={groups:[{category:'oils',label:'Масла',products:2,checkedOffers:3,supplierDiscount:20,rule:null}],sync:{status:'ready'}};else if(url.endsWith('/preview')){writes.push([url,JSON.parse(opts.body)]);data={affected:3,floorApplied:1,unknown:0,overridden:0,examples:[{sku:'OIL',warehouse:'A',purchase:1000,retail:1200,sale:1100,gain:100,floorApplied:true}]};}else if(opts.method==='PUT'){writes.push([url,JSON.parse(opts.body)]);data={ok:true};}else throw Error(url);return {ok:true,json:async()=>data};};
 try{await w.eval(await browserBundle('manager/manager.js'));const $=s=>w.document.querySelector(s),tick=()=>new Promise(r=>setTimeout(r,15));$('[data-tab="pricing"]').click();await tick();$('[data-admin-action="edit-policy"]').click();await tick();const form=$('#category-policy-form');form.elements.mode.value='discount';form.elements.percent.value='25';form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();assert.equal(writes.length,1);assert.match($('#policy-preview').textContent,/защищены минимумом/);$('[data-admin-action="apply-policy"]').click();await tick();assert.equal(writes.length,2);assert.deepEqual(writes[1][1],writes[0][1]);assert.equal(writes[1][1].minimum,100);
 }finally{w.close();}
});
