import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM,VirtualConsole} from 'jsdom';
import {calculatePrice} from '../frontend/manager/pricing.js';
import {selectionText} from '../frontend/share.js';

test('bidirectional price, percent and rubles use purchase as markup denominator',()=>{
 const a=calculatePrice(5000,'percent',12,4);assert.deepEqual(a,{sale:5600,profit:600,percent:12,margin:10.71,total:22400,profitTotal:2400});
 assert.deepEqual(calculatePrice(5000,'profit',600,4),a);assert.deepEqual(calculatePrice(5000,'price',5600,4),a);
 assert.equal(calculatePrice(4567.89,'percent',10).sale,5024.68);
 assert.equal(calculatePrice(5000,'price',4900).profit,-100);
 assert.equal(calculatePrice(0,'percent',15),null);assert.equal(calculatePrice(5000,'price',NaN),null);
});

test('client text never serializes arbitrary private properties',()=>{
 const text=selectionText({total:1000,lines:[{name:'Товар',description:'Описание',sku:'S1',quantity:2,price:500,subtotal:1000,purchasePrice:348.87,profit:151.13}]},'https://test/s/token');
 assert.match(text,/2 шт./);assert.doesNotMatch(text,/348|151|purchase|profit/);
});

test('manager edits all three price fields and sends only selected product IDs and quantity to share endpoint',async()=>{
 const vc=new VirtualConsole(),errors=[];vc.on('jsdomError',e=>errors.push(e.message));
 const html=(await readFile(new URL('../frontend/manager/index.html',import.meta.url),'utf8')).replace(/<script[^>]*>[\s\S]*?<\/script>/g,'');
 const dom=new JSDOM(html,{url:'https://test/manager/',runScripts:'outside-only',virtualConsole:vc});const w=dom.window;
 const p={id:'4t-tires-TEST',sku:'TEST',brand:'Test',model:'Touring',description:'Synthetic tire',width:205,profile:55,diameter:16,kind:'tires',image:'assets/product-unavailable.svg',priceRule:{mode:'supplier'},offers:[{id:'warehouse-2017',warehouse:'Склад А',warehouseId:2017,stock:12,liveStock:12,price:9000,salePrice:9000,purchasePrice:5000,purchaseStale:false,supplierRetailPrice:9000,profit:4000,markupPercent:80,priceRule:{mode:'supplier'},warehouseInfo:{color:'#9ACD32',shortName:'А',logisticDays:0}}]};
 const requests=[];w.AbortSignal.timeout=()=>undefined;w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
 w.fetch=async(url,options={})=>{requests.push([url,options]);let data={};
  if(url.endsWith('/session'))data={csrf:'test'};
  else if(url.endsWith('/status'))data={products:1,supplierConfigured:true};
  else if(url.endsWith('/catalog-filters'))data={warehouses:[{id:2017,name:'Склад А',color:'#9ACD32'}],facets:{kind:['tires']}};
  else if(url.includes('/products?'))data={items:[p],total:1};
  else if(url.includes('/prices/')){const rule=JSON.parse(options.body);p.offers[0].salePrice=rule.mode==='profit'?5000+rule.amount:rule.price;p.offers[0].profit=p.offers[0].salePrice-5000;p.offers[0].markupPercent=p.offers[0].profit/50;data={ok:true};}
  else if(url==='/api/selections')data={url:'/s/testtoken',total:24800,lines:[{name:'Test Touring',description:'205/55 R16',sku:'TEST',quantity:4,price:6200,subtotal:24800}]};
  else throw Error(url);
  return {ok:true,json:async()=>data};};
 const files=['domain.js','share.js','manager/pricing.js','manager/manager.js'];let code='';
 for(const file of files)code+=(file==='manager/manager.js'?'const h=escapeHTML;\n':'')+(await readFile(new URL('../frontend/'+file,import.meta.url),'utf8')).replace(/^import .*;\n/gm,'').replace(/^export /gm,'')+'\n';
 try{
  await w.eval('(async()=>{'+code+'})()');
  const $=s=>w.document.querySelector(s),tick=()=>new Promise(r=>setTimeout(r,15)),click=async s=>{$(s).click();await tick();};
  const input=(selector,value)=>{const node=$(selector);node.value=String(value);node.dispatchEvent(new w.Event('input',{bubbles:true}));};
  await click('[data-tab="products"]');assert.ok($('.thumb-button img'));assert.match($('.warehouse-pill').getAttribute('style'),/#9ACD32/);
  input('[data-row-quantity]',4);input('[data-price-field="percent"]',12);
  assert.equal($('[data-price-field="price"]').value,'5600');assert.equal($('[data-price-field="profit"]').value,'600');
  input('[data-price-field="profit"]',1000);assert.equal($('[data-price-field="price"]').value,'6000');assert.equal($('[data-price-field="percent"]').value,'20');
  input('[data-price-field="price"]',6200);assert.equal($('[data-price-field="profit"]').value,'1200');assert.equal($('[data-price-field="percent"]').value,'24');
  await click('[data-action="save-offer"]');const save=requests.find(([u])=>u.includes('/prices/'));assert.equal(JSON.parse(save[1].body).price,6200);
  await click('[data-action="share-selection"]');const shared=JSON.parse(requests.find(([u])=>u==='/api/selections')[1].body);
  assert.deepEqual(shared,{lines:[{productId:p.id,warehouseId:2017,quantity:4}]});assert.doesNotMatch($('#shared-text').value,/5\s?000|закупк/);
  assert.deepEqual(errors,[]);
 }finally{w.close();}
});
