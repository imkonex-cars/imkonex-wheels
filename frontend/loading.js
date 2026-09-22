export const wheelSpinner=(label='Загрузка')=>`<span class="wheel-loading" role="status"><span class="spinning-wheel" aria-hidden="true"><i></i></span><span>${label}</span></span>`;
let pending=0;
export function busyStart(){let el=document.querySelector('#network-progress');if(!el){el=document.createElement('div');el.id='network-progress';el.innerHTML=wheelSpinner('Загружаем');document.body.append(el);}pending++;el.hidden=false;return()=>{pending=Math.max(0,pending-1);el.hidden=!pending;};}
export async function withLoading(fn){const done=busyStart();try{return await fn();}finally{done();}}
