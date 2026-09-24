import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,stat} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {JSDOM} from 'jsdom';
const root=fileURLToPath(new URL('../frontend/',import.meta.url));
const pages=['contacts','delivery','payment','returns','privacy','consent','security','terms'];

test('buyer information is usable without scripts and all local document links resolve',async()=>{
 for(const name of pages){
  const doc=new JSDOM(await readFile(path.join(root,'info',name,'index.html'),'utf8')).window.document;
  assert.ok(doc.querySelector('h1')?.textContent.trim(),name);
  assert.ok(doc.querySelector('main')?.textContent.length>300,name);
  for(const element of doc.querySelectorAll('a[href],link[href],img[src]')){
   const raw=element.getAttribute('href')||element.getAttribute('src');
   if(/^(https?:|mailto:|tel:|#|data:)/.test(raw))continue;
   const url=new URL(raw,'https://store.test/info/'+name+'/');
   let file=path.join(root,decodeURIComponent(url.pathname));
   if(url.pathname.endsWith('/'))file=path.join(file,'index.html');
   assert.ok((await stat(file)).isFile(),`${name}: ${raw}`);
   if(url.hash && url.pathname.startsWith('/info/')){
    const target=new JSDOM(await readFile(file,'utf8')).window.document;
    assert.ok(target.getElementById(url.hash.slice(1)),`${name}: ${raw} anchor`);
   }
  }
 }
});

test('seller contacts survive replacement of the Tilda footer on each buyer surface',async()=>{
 for(const rel of ['index.html','order/index.html','selection/index.html','account/index.html']){
  const doc=new JSDOM(await readFile(path.join(root,rel),'utf8')).window.document;
  doc.querySelector('footer.site-footer')?.remove();
  const block=doc.querySelector('.merchant-info');assert.ok(block,rel);
  assert.match(block.textContent,/7447231956/);assert.match(block.textContent,/Ленина, 89/);
  assert.match(block.textContent,/Энтузиастов/);assert.ok(block.querySelector('a[href="/info/returns/"]'));
  assert.ok(block.querySelector('a[href="https://www.tbank.ru/"] img'));
 }
});

test('local consent version and links are sent with checkout; no preselected consent',async()=>{
 const source=await readFile(path.join(root,'shop.js'),'utf8');
 assert.match(source,/consentVersion:'2026-09-24'/);
 assert.match(source,/href="\/info\/consent\/#request"/);
 assert.doesNotMatch(source,/polzovatelskoe-soglashenie/);
 assert.match(source,/!form\.reportValidity\(\)/);
 assert.doesNotMatch(source,/<input[^>]*name="consent"[^>]*checked/);
});

test('bank logo is a local inert vector asset',async()=>{
 const source=await readFile(path.join(root,'assets/payments/tbank.svg'),'utf8');
 assert.match(source,/<svg/);assert.doesNotMatch(source,/<script|<image|<foreignObject|\bonload=/i);
});
