import {valueFor, advancedKeys, matchesAxle, normalizeWinterFilters} from './search-model.js';
const rubFormatter = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2});
export const money = value => rubFormatter.format(Number(value) || 0) + ' ₽';
export const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const normalize = value => String(value ?? '').toLowerCase().replaceAll('ё','е').replace(/[\s/хx×.,-]+/g, '');
export function retailPrice(base, percent, minimum) {
  const cost=Number(base), markup=Number(percent), floor=Number(minimum);
  if (![cost,markup,floor].every(Number.isFinite) || cost<=0 || markup<0 || floor<0) throw new Error('Некорректная цена');
  return Math.ceil((cost + Math.max(cost*markup/100, floor))/10)*10;
}
export function effectiveOffer(product, quantity=1) {
  if (!Number.isInteger(quantity) || quantity<1 || quantity>20) return null;
  let best=null;
  for(const offer of product.offers){
    if(offer.stock<quantity||offer.price<=0)continue;
    if(!best||(offer.days??Infinity)<(best.days??Infinity)
      ||((offer.days??Infinity)===(best.days??Infinity)&&offer.price<best.price))best=offer;
  }
  return best;
}
export function formatSize(p) { if(['sensors','consumables','oils'].includes(p.kind))return p.sizeLabel||p.description; return p.kind==='tubes'||p.width==null&&p.kind==='tires'||p.profile==null&&p.kind==='tires'||p.kind==='tires'&&(p.diameter==null||!p.construction) ? (p.sizeLabel||p.description) : p.kind === 'tires' ? `${p.width}/${p.profile} ${p.construction||'R'}${p.diameter}` : `${p.wheelWidth}${p.isDemo===false?'':'J'} × ${p.diameter} · ${p.pcd}`; }
export function filterProducts(products, filters, {favorites=[], compare=[], sort=true}={}) {
  filters=normalizeWinterFilters(filters);
  const q=normalize(filters.q);
  const found=products.filter(p => {
    if (filters.kind && p.kind!==filters.kind) return false;
    if(filters.category&&valueFor(p,'category')!==filters.category)return false;
    if(filters.vehicleIds&&!filters.vehicleIds.has(p.id))return false;
    const commonSize=p.kind==='tires'?`${p.width}/${p.profile} R${p.diameter}`:'';
    if (q && !normalize([p.brand,p.model,formatSize(p),commonSize,p.sku,p.pcd].join(' ')).includes(q)) return false;
    if(filters.staggered && p.kind==='tires' && !matchesAxle(p,filters,'front') && !matchesAxle(p,filters,'rear'))return false;
    for (const key of ['width','profile','diameter','season','pcd','wheelWidth','et','dia','type',...advancedKeys]) {
      if(filters.staggered&&['width','profile','diameter'].includes(key))continue;
      if (filters[key] && String(valueFor(p,key))!==String(filters[key])) return false;
    }
    if (filters.brands?.length && !filters.brands.includes(p.brand)) return false;
    if (filters.winterType==='studded' && p.studded!==true) return false;
    if (filters.winterType==='friction' && p.studded!==false) return false;
    if (filters.runflat && !p.runflat) return false;
    if (filters.xl && !p.xl) return false;
    if (filters.onlyFavorites && !favorites.includes(p.id)) return false;
    if (filters.onlyCompare && !compare.includes(p.id)) return false;
    const requested=filters.staggered?2:Number(filters.minStock)|| (filters.inSet?4:1);
    const offer = effectiveOffer(p, requested);
    if((filters.staggered||Number(filters.minStock)>0)&&!offer)return false;
    if (filters.inSet && !offer) return false;
    if (filters.fast && (!offer || offer.days==null || offer.days>3)) return false;
    const price=offer?.price ?? Infinity;
    if (filters.min && price<Number(filters.min)) return false;
    if (filters.max && price>Number(filters.max)) return false;
    return true;
  });
  if(!sort)return found;
  return found.sort((a,b)=>{
    const quantity=filters.staggered?2:Number(filters.minStock)||(filters.inSet?4:1), ao=effectiveOffer(a,quantity), bo=effectiveOffer(b,quantity);
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
  return [...new Set(filterProducts(products,copy,{sort:false}).map(p=>valueFor(p,key==='brands'?'brand':key)).filter(v=>v!==undefined&&v!==null))].sort((a,b)=>String(a).localeCompare(String(b),'ru',{numeric:true}));
}
export function brandCounts(products, filters){
  const counts=new Map();
  for(const p of filterProducts(products,{...filters,brands:[]},{sort:false}))counts.set(p.brand,(counts.get(p.brand)||0)+1);
  return counts;
}
export function paginationPages(current,total){
  const pages=new Set([1,total]);
  for(let p=Math.max(1,current-2);p<=Math.min(total,current+2);p++)pages.add(p);
  const result=[];
  for(const p of [...pages].sort((a,b)=>a-b)){
    if(result.length&&p-result[result.length-1]>1)result.push(null);
    result.push(p);
  }
  return result;
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
 const p=new URLSearchParams(search); const f={kind:['wheels','tubes','sensors','consumables','oils'].includes(p.get('kind'))?p.get('kind'):'tires',sort:p.get('sort')||'recommended',brands:p.getAll('brand')};
 for(const k of ['q','category','width','profile','diameter','season','pcd','wheelWidth','et','dia','min','max','type','rearWidth','rearProfile','rearDiameter','minStock',...advancedKeys]) f[k]=p.get(k)||'';
 for(const k of ['inSet','fast','studded','runflat','xl','staggered','sameModel'])f[k]=p.get(k)==='1';
 f.winterType=p.get('winterType')||'';
 return normalizeWinterFilters(f);
}
export function serializeFilters(f) {
 f=normalizeWinterFilters(f);
 const p=new URLSearchParams();
 for(const [k,v] of Object.entries(f)) {
   if(k==='brands')v.forEach(x=>p.append('brand',x));
   else if(v && !['page','onlyFavorites','onlyCompare','vehicleIds'].includes(k))p.set(k,typeof v==='boolean'?'1':v);
 }
 return p.toString();
}
