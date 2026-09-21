// The static build accepts only an explicit public contract, never a raw API report.
const keys = (value, allowed) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).every(k => allowed.includes(k));
const finite = (value, min, max) => typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max;
const word = value => typeof value === 'string' && value.length > 0 && value.length <= 3000;
export function validateSnapshot(data) {
  const top = ['schemaVersion','mode','source','updatedAt','priceBasis','notice','products'];
  if (!keys(data,top) || data.schemaVersion !== 2 || data.mode !== 'snapshot' || data.source !== '4tochki'
    || data.priceBasis !== 'supplier_retail' || !word(data.notice) || !word(data.updatedAt)
    || !Number.isFinite(Date.parse(data.updatedAt)) || !Array.isArray(data.products)
    || data.products.length < 2 || data.products.length > 6) throw new Error('Invalid public snapshot');
  const ids = new Set(), skus = new Set();
  const common = ['id','sku','kind','brand','model','description','image','isDemo','rank','diameter','offers'];
  const tire = ['width','profile','season','construction','loadIndex','speedIndex','studded','xl','runflat'];
  const rim = ['wheelWidth','pcd','et','dia','color','colorDescription','type'];
  for (const p of data.products) {
    if (!keys(p,[...common,...(p.kind === 'tires' ? tire : rim)]) || !['tires','wheels'].includes(p.kind)
      || !/^4t-(tires|wheels)-[a-zA-Z0-9_-]+$/.test(p.id) || ids.has(p.id) || skus.has(`${p.kind}:${p.sku}`)
      || p.isDemo !== false || ![p.sku,p.brand,p.model,p.description,p.image].every(word)
      || !finite(p.diameter,10,30) || !finite(p.rank,0,100)) throw new Error('Invalid public product');
    ids.add(p.id); skus.add(`${p.kind}:${p.sku}`);
    if (p.image !== 'assets/product-unavailable.svg') {
      const url = new URL(p.image);
      if (url.protocol !== 'https:' || !['api-b2b.pwrs.ru','www.4tochki.ru'].includes(url.hostname)
        || url.username || url.password || url.port || url.search || url.hash
        || !/^\/[a-zA-Z0-9_./-]+\.(png|jpg|jpeg|webp)$/.test(url.pathname)) throw new Error('Invalid public image');
    }
    if (p.kind === 'tires') {
      if (!finite(p.width,100,500) || !finite(p.profile,15,100) || !['summer','winter'].includes(p.season)
        || !['R','ZR'].includes(p.construction) || ![p.loadIndex,p.speedIndex].every(word)
        || typeof p.studded !== 'boolean' || ![null,true,false].includes(p.xl)
        || ![null,true,false].includes(p.runflat)) throw new Error('Invalid tire parameters');
    } else if (!finite(p.wheelWidth,3,16) || !finite(p.et,-100,200) || !finite(p.dia,30,200)
      || ![p.pcd,p.color,p.colorDescription].every(word) || p.type !== null) throw new Error('Invalid rim parameters');
    if (!Array.isArray(p.offers) || p.offers.length > 500) throw new Error('Invalid public offers');
    const warehouses = new Set();
    for (const o of p.offers) {
      if (!keys(o,['id','warehouse','stock','price','days']) || !word(o.id) || warehouses.has(o.id)
        || !word(o.warehouse) || !Number.isInteger(o.stock) || !finite(o.stock,1,1000000)
        || !finite(o.price,0.01,100000000) || o.days !== null) throw new Error('Invalid public offer');
      warehouses.add(o.id);
    }
  }
  if (!data.products.some(p=>p.kind==='tires') || !data.products.some(p=>p.kind==='wheels')) throw new Error('Both categories required');
  return data;
}
