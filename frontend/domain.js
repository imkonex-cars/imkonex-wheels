export const money = value => new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0}).format(Number(value) || 0) + ' ₽';
export const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const normalize = value => String(value ?? '').toLowerCase().replaceAll('ё','е').replace(/[\s/хx×.,-]+/g, '');
export function retailPrice(base, percent, minimum) {
  const cost=Number(base), markup=Number(percent), floor=Number(minimum);
  if (![cost,markup,floor].every(Number.isFinite) || cost<=0 || markup<0 || floor<0) throw new Error('Некорректная цена');
  return Math.ceil((cost + Math.max(cost*markup/100, floor))/10)*10;
}
export function effectiveOffer(product, quantity=1) {
  if (!Number.isInteger(quantity) || quantity<1 || quantity>20) return null;
  const offers = product.offers.filter(o => o.stock >= quantity && o.price > 0);
  if (!offers.length) return null;
  return [...offers].sort((a,b) => (a.days-b.days) || (a.price-b.price))[0];
}
export function formatSize(p) { return p.kind === 'tires' ? `${p.width}/${p.profile} R${p.diameter}` : `${p.wheelWidth}J × ${p.diameter} · ${p.pcd}`; }
export function filterProducts(products, filters, {favorites=[], compare=[]}={}) {
  const q=normalize(filters.q);
  return products.filter(p => {
    if (filters.kind && p.kind!==filters.kind) return false;
    if (q && !normalize([p.brand,p.model,formatSize(p),p.sku,p.pcd].join(' ')).includes(q)) return false;
    for (const key of ['width','profile','diameter','season','pcd','wheelWidth','et','dia','type']) {
      if (filters[key] && String(p[key])!==String(filters[key])) return false;
    }
    if (filters.brands?.length && !filters.brands.includes(p.brand)) return false;
    if (filters.studded && !p.studded) return false;
    if (filters.runflat && !p.runflat) return false;
    if (filters.xl && !p.xl) return false;
    if (filters.onlyFavorites && !favorites.includes(p.id)) return false;
    if (filters.onlyCompare && !compare.includes(p.id)) return false;
    const offer = effectiveOffer(p, filters.inSet ? 4 : 1);
    if (filters.inSet && !offer) return false;
    if (filters.fast && (!offer || offer.days>3)) return false;
    const price=offer?.price ?? Infinity;
    if (filters.min && price<Number(filters.min)) return false;
    if (filters.max && price>Number(filters.max)) return false;
    return true;
  }).sort((a,b)=>{
    const quantity=filters.inSet?4:1, ao=effectiveOffer(a,quantity), bo=effectiveOffer(b,quantity);
    const ap=ao?.price ?? Infinity, bp=bo?.price ?? Infinity;
    if(filters.sort==='price-up')return ap-bp;
    if(filters.sort==='price-down')return !ao?1:!bo?-1:bp-ap;
    if(filters.sort==='delivery')return (ao?.days??999)-(bo?.days??999);
    return (b.rank??0)-(a.rank??0);
  });
}
export function facetOptions(products, filters, key) {
  const copy={...filters,[key]:'',q:'',page:1};
  if (key==='brands') copy.brands=[];
  return [...new Set(filterProducts(products,copy).map(p=>p[key==='brands'?'brand':key]).filter(v=>v!==undefined&&v!==null))].sort((a,b)=>String(a).localeCompare(String(b),'ru',{numeric:true}));
}
export function cartTotals(lines,products) {
  let total=0;let count=0;const issues=[];
  const items=lines.map(line=>{
    const p=products.find(p=>p.id===line.id); const quantity=Number(line.quantity);
    if(!p || !Number.isInteger(quantity) || quantity<1 || quantity>20) {issues.push(line.id); return null;}
    const offer=effectiveOffer(p,quantity);
    if(!offer){issues.push(line.id);return {...line,product:p,unavailable:true};}
    total+=offer.price*quantity;count+=quantity;
    return {...line,product:p,offer,subtotal:offer.price*quantity};
  }).filter(Boolean);
  return {total,count,items,issues};
}
export function fitmentMessage() {return 'Применимость не подтверждена. Нужна проверка параметров автомобиля.';}
export function parseFilters(search='') {
 const p=new URLSearchParams(search); const f={kind:p.get('kind')==='wheels'?'wheels':'tires',sort:p.get('sort')||'recommended',brands:p.getAll('brand')};
 for(const k of ['q','width','profile','diameter','season','pcd','wheelWidth','et','dia','min','max','type']) f[k]=p.get(k)||'';
 for(const k of ['inSet','fast','studded','runflat','xl'])f[k]=p.get(k)==='1';
 return f;
}
export function serializeFilters(f) {
 const p=new URLSearchParams();
 for(const [k,v] of Object.entries(f)) {
   if(k==='brands')v.forEach(x=>p.append('brand',x));
   else if(v && !['page','onlyFavorites','onlyCompare'].includes(k))p.set(k,typeof v==='boolean'?'1':v);
 }
 return p.toString();
}
