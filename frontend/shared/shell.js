// Shared loader for both catalogs. Main-site scripts never execute here.
(() => {
  const own=document.currentScript; if(!own)return; const base=new URL(own.dataset.origin||'/',location.href).origin;
  const embedded=window.self!==window.top||new URLSearchParams(location.search).get('embed')==='tilda';
  if(embedded)return;
  async function mount(){
    let data;
    try{const r=await fetch(base+'/api/site-shell',{credentials:'omit',signal:AbortSignal.timeout(7000)});if(!r.ok)throw Error();data=await r.json();}
    catch{try{const r=await fetch(base+'/shared/fallback.json',{credentials:'omit'});if(!r.ok)return;data=await r.json();}catch{return;}}
    if(!data.header||!data.footer)return;
    for(const kind of ['header','footer']){
      const old=document.querySelector(kind==='header'?'#imxGlobalHeader, .site-header > .topbar':'#imxPremiumFooter, footer.site-footer');
      const host=document.createElement('imkonex-'+kind),shadow=host.attachShadow({mode:'open'});
      const sheet=document.createElement('link');sheet.rel='stylesheet';sheet.href=base+'/shared/shell.css';
      const template=document.createElement('template');template.innerHTML=data[kind];
      for(const img of template.content.querySelectorAll('img[src^="/shared/"]'))img.src=base+img.getAttribute('src');
      shadow.append(sheet,template.content);
      // Wait for CSS to avoid replacing a working header with unstyled navigation.
      const ready=new Promise(resolve=>{sheet.onload=()=>resolve(true);sheet.onerror=()=>resolve(false);setTimeout(()=>resolve(false),8000);});
      host.hidden=true;if(kind==='header')document.body.prepend(host);else document.body.append(host);
      if(!await ready){host.remove();continue;}
      host.hidden=false;if(old){old.hidden=true;old.classList.add('imkonex-old-shell');}
      const header=shadow.querySelector('header'),burger=shadow.querySelector('#imxGlobalBurger');
      const close=()=>{header?.classList.remove('is-menu-open');burger?.setAttribute('aria-expanded','false');document.body.style.overflow='';};
      burger?.addEventListener('click',()=>{const open=header.classList.toggle('is-menu-open');burger.setAttribute('aria-expanded',String(open));document.body.style.overflow=open?'hidden':'';});
      shadow.querySelectorAll('.imx-gh__mobile-trigger').forEach(b=>b.addEventListener('click',()=>b.setAttribute('aria-expanded',String(b.closest('.imx-gh__mobile-section').classList.toggle('is-open')))));
      shadow.querySelectorAll('#imxGlobalMobile a').forEach(a=>a.addEventListener('click',close));
      document.addEventListener('keydown',e=>{if(e.key==='Escape')close();});
      window.addEventListener('resize',()=>{if(window.innerWidth>1030)close();},{passive:true});
      const scroll=()=>header?.classList.toggle('is-scrolled',window.scrollY>20);scroll();window.addEventListener('scroll',scroll,{passive:true});
      const year=shadow.querySelector('#imxFooterYear');if(year)year.textContent=new Date().getFullYear();
      shadow.querySelector('#imxFooterUp')?.addEventListener('click',()=>window.scrollTo({top:0,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'}));
    }
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount,{once:true});else mount();
})();
