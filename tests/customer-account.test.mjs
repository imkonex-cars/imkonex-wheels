import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM,VirtualConsole} from 'jsdom';
const tick=()=>new Promise(r=>setTimeout(r,15));
async function page(handler){
 const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const html=(await readFile(new URL('../frontend/account/index.html',import.meta.url),'utf8')).replace(/<script[^>]*>[\s\S]*?<\/script>/g,'');
 const dom=new JSDOM(html,{url:'https://shop.test/account/',runScripts:'outside-only',virtualConsole:vc});
 dom.window.fetch=async(url,options={})=>{const result=handler(url,options);return {ok:result.status<400,status:result.status,headers:new Map(),json:async()=>result.body};};
 dom.window.eval(await readFile(new URL('../frontend/account/account.js',import.meta.url),'utf8'));await tick();
 return {w:dom.window,$:s=>dom.window.document.querySelector(s),errors};
}
test('unconfigured SMS never displays a fake success or login form',async()=>{
 const p=await page(url=>url.endsWith('/me')?{status:401,body:{detail:'customer_login_required'}}:{status:200,body:{available:false}});
 try{assert.match(p.$('#account-content').textContent,/Вход по СМС пока недоступен/);assert.equal(p.$('#phone-form'),null);assert.deepEqual(p.errors,[]);}finally{p.w.close();}
});
test('phone to code to account uses each stage CSRF and renders owned order text safely',async()=>{
 const calls=[];const p=await page((url,opts)=>{calls.push([url,opts]);
  if(url.endsWith('/me'))return {status:401,body:{detail:'customer_login_required'}};
  if(url.endsWith('/bootstrap'))return {status:200,body:{available:true,csrf:'preauth'}};
  if(url.endsWith('/request'))return {status:200,body:{challengeId:'synthetic-challenge',retryAfter:60}};
  if(url.endsWith('/verify'))return {status:200,body:{csrf:'authenticated',customer:{id:'u1',phone:'+79991112233',name:'<img src=x>'}}};
  if(url.endsWith('/orders'))return {status:200,body:{orders:[{id:'OWN-1',status:'new',createdAt:0,total:8000,lines:[{name:'<b>safe</b>',sku:'TEST',quantity:1,subtotal:8000}]}]}};
  throw Error(url);
 });
 try{const phone=p.$('#phone-form');phone.elements.phone.value='+79991112233';phone.querySelector('[type=checkbox]').checked=true;phone.dispatchEvent(new p.w.Event('submit',{bubbles:true,cancelable:true}));await tick();
  assert.ok(p.$('#code-form'));assert.equal(p.$('#resend').disabled,true);const code=p.$('#code-form');code.elements.code.value='123456';code.dispatchEvent(new p.w.Event('submit',{bubbles:true,cancelable:true}));await tick();
  assert.ok(p.$('#profile-form'));assert.match(p.$('#orders').textContent,/OWN-1/);assert.equal(p.$('#orders b')?.textContent.includes('safe'),false);assert.equal(p.$('#profile-form img'),null);
  assert.equal(calls.find(([u])=>u.endsWith('/request'))[1].headers['X-CSRF-Token'],'preauth');assert.equal(calls.find(([u])=>u.endsWith('/orders'))[1].headers['X-CSRF-Token'],'authenticated');assert.deepEqual(p.errors,[]);
  assert.deepEqual(JSON.parse(calls.find(([u])=>u.endsWith('/request'))[1].body),{phone:'+79991112233',consent:true,consentVersion:'2026-09-24'});
 }finally{p.w.close();}
});
test('SMS login requires its unchecked separate consent and links to local documents',async()=>{
 const calls=[];const p=await page((url,opts)=>{calls.push([url,opts]);
  if(url.endsWith('/me'))return {status:401,body:{detail:'customer_login_required'}};
  if(url.endsWith('/bootstrap'))return {status:200,body:{available:true,csrf:'preauth'}};
  throw Error('Unconsented request must never be sent');
 });
 try{const form=p.$('#phone-form'),consent=form.elements.consent;assert.equal(consent.checked,false);
  assert.ok(p.$('a[href="/info/consent/#account"]'));assert.ok(p.$('a[href="/info/privacy/"]'));
  form.elements.phone.value='+79991112233';form.dispatchEvent(new p.w.Event('submit',{bubbles:true,cancelable:true}));await tick();
  assert.equal(calls.filter(([url])=>url.endsWith('/request')).length,0);assert.match(form.querySelector('.error').textContent,/согласие/);
  assert.equal(p.$('#code-form'),null);assert.deepEqual(p.errors,[]);
 }finally{p.w.close();}
});
