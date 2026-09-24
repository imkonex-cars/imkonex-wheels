import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM,VirtualConsole} from 'jsdom';
import {browserBundle} from './browser-bundle.mjs';
import {filterProducts,parseFilters,serializeFilters} from '../frontend/domain.js';
import {setSeason} from '../frontend/search-model.js';

const tire=(id,studded,brand='Nokian',season='winter')=>({id,sku:id,brand,model:'Test '+id,kind:'tires',vehicleCategory:'passenger',width:205,profile:55,diameter:16,construction:'R',season,studded,image:'assets/product-unavailable.svg',offers:[{stock:4,price:7000,days:2}]});
const items=[tire('studs',true),tire('friction',false),tire('unknown',null),tire('bad-false','false'),tire('bad-true','true'),tire('summer',false,'Michelin','summer')];

test('winter subtypes use explicit supplier booleans, never null or truthy strings',()=>{
 assert.deepEqual(filterProducts(items,{kind:'tires',season:'winter',winterType:'studded'}).map(p=>p.id),['studs']);
 assert.deepEqual(filterProducts(items,{kind:'tires',season:'winter',winterType:'friction'}).map(p=>p.id),['friction']);
 assert.equal(filterProducts(items,{kind:'tires',season:'winter',winterType:''}).length,5);
});

test('shared filters preserve subtype and multiple manufacturers, legacy studded links migrate',()=>{
 const f=parseFilters('kind=tires&season=winter&winterType=friction&brand=Nokian&brand=Michelin&width=205&staggered=1&rearWidth=225');
 assert.deepEqual(parseFilters(serializeFilters(f)),f);
 const legacy=parseFilters('kind=tires&studded=1&brand=Nokian');
 assert.equal(legacy.season,'winter');assert.equal(legacy.winterType,'studded');
 assert.deepEqual(filterProducts(items,legacy).map(p=>p.id),['studs']);
 assert.doesNotMatch(serializeFilters(legacy),/(?:^|&)studded=/);
});

test('changing season or category clears hidden winter restrictions',()=>{
 const f=parseFilters('season=winter&winterType=studded');
 for(const season of ['summer','allseason','']){const next=setSeason(f,season);assert.equal(next.winterType,'');assert.equal(next.studded,false);assert.equal(next.season,season);}
 assert.deepEqual(filterProducts(items,setSeason(f,'summer')).map(p=>p.id),['summer']);
 assert.equal(parseFilters('kind=wheels&season=winter&winterType=friction').winterType,'');
 assert.equal(parseFilters('season=summer&studded=1').winterType,'');
 assert.equal(parseFilters('season=winter&winterType=unsupported').winterType,'');
});

async function browser(hash='catalog'){
 const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const html=(await readFile(new URL('../frontend/index.html',import.meta.url),'utf8')).replace(/<script[^>]*>[\s\S]*?<\/script>/g,'');
 const dom=new JSDOM(html,{url:'https://shop.test/#'+hash,runScripts:'outside-only',virtualConsole:vc}),w=dom.window;
 w.IMKONEX_DATA={products:structuredClone(items)};w.IMKONEX_CONFIG={portal:false};w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
 await w.eval(await browserBundle('app.js'));
 return {w,errors,$:s=>w.document.querySelector(s),all:s=>[...w.document.querySelectorAll(s)]};
}

test('winter type appears only when winter is chosen, updates results and can be cleared',async()=>{
 const b=await browser();try{
  assert.equal(b.$('.winter-type-control'),null);
  b.$('[data-action="season"][data-value="winter"]').click();
  assert.ok(b.$('.seasonal-finder .winter-type-control'));
  b.$('[data-action="winter-type"][data-value="friction"]').click();
  assert.equal(b.all('.product-card').length,1);assert.match(b.w.location.hash,/winterType=friction/);
  assert.match(b.$('.active-filters').textContent,/Без шипов/);
  b.$('[data-action="remove-filter"][data-key="season"]').click();
  assert.equal(b.$('.winter-type-control'),null);assert.equal(b.all('.product-card').length,6);
  assert.doesNotMatch(b.w.location.hash,/winterType/);assert.deepEqual(b.errors,[]);
 }finally{b.w.close();}
});

test('manufacturer text and drawer state persist through selection; Escape restores filter trigger',async()=>{
 const b=await browser();try{
  b.$('[data-action="open-filters"]').click();assert.equal(b.$('[data-action="open-filters"]').getAttribute('aria-expanded'),'true');
  const input=b.$('#brand-search');input.value=' NO-kian ';input.dispatchEvent(new b.w.Event('input',{bubbles:true}));
  assert.equal(b.all('[data-brand-row]:not([hidden])').length,1);
  const checkbox=b.$('[data-brand="Nokian"]');checkbox.focus();checkbox.checked=true;checkbox.dispatchEvent(new b.w.Event('change',{bubbles:true}));
  assert.equal(b.$('#brand-search').value,' NO-kian ');assert.ok(b.$('#filters.open'));assert.equal(b.w.document.activeElement.dataset.brand,'Nokian');
  b.$('#brand-search').value='missing';b.$('#brand-search').dispatchEvent(new b.w.Event('input',{bubbles:true}));assert.equal(b.$('.brand-search-empty').hidden,false);
  b.w.document.dispatchEvent(new b.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  assert.equal(b.$('#filters.open'),null);assert.equal(b.w.document.activeElement.dataset.action,'open-filters');assert.deepEqual(b.errors,[]);
 }finally{b.w.close();}
});

test('mobile drawer stays open and keeps keyboard focus when winter subtype changes',async()=>{
 const b=await browser('catalog?season=winter');try{
  b.$('[data-action="open-filters"]').click();const choice=b.$('#filters [data-action="winter-type"][data-value="studded"]');choice.focus();choice.click();
  assert.ok(b.$('#filters.open'));assert.ok(b.$('#filters').contains(b.w.document.activeElement));assert.equal(b.all('.product-card').length,1);
  b.$('#filters [data-action="season"][data-value="summer"]').click();assert.equal(b.$('.winter-type-control'),null);assert.equal(b.all('.product-card').length,1);assert.match(b.$('.product-title').textContent,/summer/);
 }finally{b.w.close();}
});

test('public disclaimer and customer account link remain outside the replaced shared footer',async()=>{
 const b=await browser();try{
  assert.equal(b.$('.account-link').getAttribute('href'),'/account/');assert.ok(b.$('.catalog-legal-note'));assert.equal(b.$('footer .catalog-legal-note'),null);
  assert.match(b.$('.catalog-legal-note').textContent,/Справочные материалы/);assert.match(b.$('.catalog-legal-note').textContent,/437/);assert.match(b.$('.catalog-legal-note').textContent,/действующим законодательством/);
 }finally{b.w.close();}
});
