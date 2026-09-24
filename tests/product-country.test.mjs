import {browserBundle} from './browser-bundle.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM,VirtualConsole} from 'jsdom';

const sample=JSON.parse(await readFile(new URL('./fixtures/public-sample.json',import.meta.url),'utf8'));
async function open({attributes,product={}}={}){
 const html=(await readFile(new URL('../frontend/index.html',import.meta.url),'utf8')).replace(/<script[^>]*>[\s\S]*?<\/script>/g,'');
 const dom=new JSDOM(html,{url:'https://catalog.test/',runScripts:'outside-only',virtualConsole:new VirtualConsole()});
 const w=dom.window,data=structuredClone(sample);Object.assign(data.products[0],product);
 w.IMKONEX_DATA=data;w.IMKONEX_CONFIG={portal:attributes!==undefined};
 w.fetch=async()=>({ok:true,json:async()=>({attributes})});w.AbortSignal.timeout=()=>undefined;
 w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};
 w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
 w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
 await w.eval(await browserBundle('app.js'));
 w.document.querySelector('[data-action="detail"][data-id="'+data.products[0].id+'"]').click();
 await new Promise(resolve=>w.setTimeout(resolve,20));
 const row=[...w.document.querySelectorAll('#technical-characteristics dl > div')].find(el=>el.querySelector('dt')?.textContent==='Страна изготовления');
 return {dom,w,row};
}

test('country remains explicitly unknown instead of using a brand or guessed API alias',async()=>{
 const b=await open({product:{brand:'Example Russian brand',country:'Россия',brandCountry:'Италия'}});
 try{assert.equal(b.row.querySelector('dd').textContent,'Уточняется перед подтверждением заказа');assert.equal(b.row.querySelector('dd').className,'missing-attribute');}
 finally{b.dom.window.close();}
});

test('explicit per-product country from details replaces the missing-data notice',async()=>{
 const b=await open({attributes:[{label:'Страна изготовления',value:'Сербия'}]});
 try{assert.equal(b.row.querySelector('dd').textContent,'Сербия');assert.equal(b.row.querySelector('dd').className,'');}
 finally{b.dom.window.close();}
});

test('a blank detail value never pretends to identify a country',async()=>{
 const b=await open({attributes:[{label:'Страна изготовления',value:'  '}]});
 try{assert.equal(b.row.querySelector('dd').textContent,'Уточняется перед подтверждением заказа');}
 finally{b.dom.window.close();}
});

test('country values are rendered as text, never executable markup',async()=>{
 const b=await open({product:{attributes:[{label:'Страна изготовления',value:'<img src=x onerror=alert(1)>'}]}});
 try{assert.equal(b.row.querySelector('dd').textContent,'<img src=x onerror=alert(1)>');assert.equal(b.row.querySelector('img'),null);}
 finally{b.dom.window.close();}
});
