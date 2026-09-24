const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const source=fs.readFileSync(path.join(__dirname,'../frontend/shared/contact-widget.js'),'utf8');
function page(suffix='',body=''){
  const dom=new JSDOM('<!doctype html><body>'+body+'</body>',{url:'https://wheels.imkonex.com/'+suffix,runScripts:'outside-only'});
  dom.window.eval(source);dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
  const d=dom.window.document;d.querySelector('link')?.dispatchEvent(new dom.window.Event('load'));
  return {dom,w:dom.window,d,widget:d.getElementById('imxMessenger'),toggle:d.getElementById('imxMessengerToggle'),links:d.getElementById('imxMessengerLinks')};
}
test('verified fallback contact paths work without shell API; duplicate script does not duplicate widget',()=>{
 const p=page();assert.equal(p.widget.hidden,false);assert.equal(p.links.children.length,4);
 assert.equal(p.links.querySelector('[data-contact-channel="telegram"]').href,'https://t.me/IMKONEX_GROUP');
 p.w.eval(source);assert.equal(p.d.querySelectorAll('#imxMessenger').length,1);p.dom.window.close();
});
test('toggle, Escape with focus restoration, outside click close',()=>{
 const p=page();p.toggle.click();assert.equal(p.links.hidden,false);
 p.d.dispatchEvent(new p.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(p.links.hidden,true);assert.equal(p.d.activeElement,p.toggle);
 p.toggle.click();p.d.body.click();assert.equal(p.links.hidden,true);p.dom.window.close();
});
test('embedded Tilda and existing main-site widget do not create duplicates',()=>{
 for(const p of [page('?embed=tilda'),page('','<div class="t898"></div>')]){assert.equal(p.widget,null);p.dom.window.close();}
});
test('contact configuration rejects script and lookalike hosts',()=>{
 const p=page();p.d.dispatchEvent(new p.w.CustomEvent('imkonex:contacts',{detail:[{channel:'telegram',url:'javascript:alert(1)'},{channel:'max',url:'https://max.ru.attacker.example/path'}]}));
 assert.equal(p.links.querySelector('[data-contact-channel="telegram"]').href,'https://t.me/IMKONEX_GROUP');
 p.d.dispatchEvent(new p.w.CustomEvent('imkonex:contacts',{detail:[{channel:'telegram',url:'https://t.me/updated_support'}]}));
 assert.equal(p.links.querySelector('[data-contact-channel="telegram"]').href,'https://t.me/updated_support');
 assert.equal(p.links.querySelector('[data-contact-channel="phone"]').hidden,true);p.dom.window.close();
});
test('widget hides and collapses during checkout dialog and mobile filter overlay',async()=>{
 const p=page();p.toggle.click();const modal=p.d.createElement('dialog');modal.open=true;p.d.body.append(modal);
 await new Promise(r=>p.w.setTimeout(r,0));assert.equal(p.widget.hidden,true);assert.equal(p.links.hidden,true);
 modal.remove();await new Promise(r=>p.w.setTimeout(r,0));assert.equal(p.widget.hidden,false);
 p.d.body.classList.add('filters-open');await new Promise(r=>p.w.setTimeout(r,0));assert.equal(p.widget.hidden,true);p.dom.window.close();
});
