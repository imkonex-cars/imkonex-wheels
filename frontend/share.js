import {withLoading} from './loading.js';
import {money} from './domain.js';
export function selectionText(selection,url){
  return ['IMKONEX CARS · Ваш подбор','',...selection.lines.map((l,i)=>`${i+1}. ${l.name}\n${l.description}\nАртикул: ${l.sku}\n${l.quantity} шт. × ${money(l.price)} = ${money(l.subtotal)}`),
    '',`Итого: ${money(selection.total)}`,'Цена и наличие подтверждаются при оформлении. Доставка рассчитывается отдельно.',url||''].join('\n');
}
export async function copyText(text){
  try{await navigator.clipboard.writeText(text);return;}catch{}
  const el=document.createElement('textarea');el.value=text;el.setAttribute('readonly','');el.style.position='fixed';el.style.opacity='0';document.body.append(el);el.select();
  const ok=document.execCommand('copy');el.remove();if(!ok)throw new Error('Выделите и скопируйте текст вручную.');
}
export async function createSelection(lines){
  const r=await withLoading(()=>fetch('/api/selections',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lines}),signal:AbortSignal.timeout(90000)}));
  if(!r.ok)throw new Error(r.status===409?'Наличие изменилось. Обновите каталог и количество.':'Не удалось сохранить подборку. Повторите позже.');
  return r.json();
}
