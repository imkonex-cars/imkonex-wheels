// The static build accepts only an explicit public contract, never a raw API report.
const keys = (value, allowed) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).every(k => allowed.includes(k));
const finite = (value, min, max) => typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max;
const word = value => typeof value === 'string' && value.length > 0 && value.length <= 3000;
export function validateSnapshot(data) {
  const accessories=data?.schemaVersion===5;
  const extended=[4,5].includes(data?.schemaVersion);
  const bulk=[3,4,5].includes(data?.schemaVersion);
  const top = ['schemaVersion','mode','source','updatedAt','priceBasis','notice','products',...(bulk?['sync']:[])];
  if (!keys(data,top) || ![2,3,4,5].includes(data.schemaVersion) || data.mode !== 'snapshot' || data.source !== '4tochki'
    || data.priceBasis !== 'supplier_retail' || !word(data.notice) || !word(data.updatedAt)
    || !Number.isFinite(Date.parse(data.updatedAt)) || !Array.isArray(data.products)
    || data.products.length < 2 || data.products.length > (bulk?50000:6)) throw new Error('Invalid public snapshot');
  if(bulk){
    const s=data.sync, integer=n=>Number.isInteger(n)&&finite(n,0,50000);
    if(!keys(s,['complete','scope','startedAt','finishedAt','sourceProducts','publishedProducts','excludedProducts','categories','excludedReasons'])
      ||s.complete!==true||s.scope!=='available_supported_products'||s.finishedAt!==data.updatedAt
      ||!word(s.startedAt)||!Number.isFinite(Date.parse(s.startedAt))||Date.parse(s.startedAt)>Date.parse(s.finishedAt)
      ||![s.sourceProducts,s.publishedProducts,s.excludedProducts].every(integer)
      ||s.publishedProducts!==data.products.length||s.sourceProducts!==s.publishedProducts+s.excludedProducts
      ||!keys(s.categories,accessories?['tires','wheels','tubes','sensors','consumables','oils']:extended?['tires','wheels','tubes']:['tires','wheels'])||!keys(s.excludedReasons,['noRetailOffer','unsupportedParameters'])
      ||!Object.values(s.excludedReasons).every(integer)
      ||Object.values(s.excludedReasons).reduce((a,b)=>a+b,0)!==s.excludedProducts)throw new Error('Invalid catalog sync metadata');
    let scanned=0,published=0,excluded=0;
    for(const kind of Object.keys(s.categories)){
      if(!s.categories.tires||!s.categories.wheels)throw new Error('Missing category totals');
      const c=s.categories[kind];
      if(!keys(c,['published','excluded','pages','pageBase','scanned'])||![c.published,c.excluded,c.scanned].every(integer)
        ||!Number.isInteger(c.pages)||!finite(c.pages,0,5000)||![0,1].includes(c.pageBase)
        ||c.scanned!==c.published+c.excluded||c.published!==data.products.filter(p=>p.kind===kind).length)
        throw new Error('Invalid catalog category totals');
      scanned+=c.scanned;published+=c.published;excluded+=c.excluded;
    }
    if(scanned!==s.sourceProducts||published!==s.publishedProducts||excluded!==s.excludedProducts)throw new Error('Invalid catalog totals');
  }
  const ids = new Set(), skus = new Set();
  const common = ['id','sku','kind','brand','model','description','image','isDemo','rank','diameter','offers',...(extended?['vehicleCategory','sizeLabel']:[]),...(extended?['attributes']:[])];
  const tire = ['width','profile','season','construction','loadIndex','speedIndex','studded','xl','runflat',...(extended?['tyreType']:[])];
  const rim = ['wheelWidth','pcd','et','dia','color','colorDescription','type'];
  for (const p of data.products) {
    if (!keys(p,[...common,...(p.kind === 'tires' ? tire : p.kind==='tubes'?['subtype']:rim)]) || !(accessories?['tires','wheels','tubes','sensors','consumables','oils']:extended?['tires','wheels','tubes']:['tires','wheels']).includes(p.kind)
      || !(extended?/^4t-(tires|wheels|tubes|sensors|consumables|oils)-[a-zA-Z0-9_-]+$/:/^4t-(tires|wheels)-[a-zA-Z0-9_-]+$/).test(p.id) || ids.has(p.id) || skus.has(`${p.kind}:${p.sku}`)
      || p.isDemo !== false || ![p.sku,p.brand,p.model,p.description,p.image].every(word)
      || !(extended?(p.diameter===null||finite(p.diameter,0.01,100)):finite(p.diameter,10,30)) || !finite(p.rank,0,100)) throw new Error('Invalid public product');
    ids.add(p.id); skus.add(`${p.kind}:${p.sku}`);
    const localPhoto = /^assets\/products\/[a-zA-Z0-9][a-zA-Z0-9_-]*\.(png|jpg|jpeg|webp)$/.test(p.image);
    if (p.image !== 'assets/product-unavailable.svg' && !localPhoto) {
      const url = new URL(p.image);
      if (url.protocol !== 'https:' || !['api-b2b.pwrs.ru','www.4tochki.ru'].includes(url.hostname)
        || url.username || url.password || url.port || url.search || url.hash
        || !/^\/[a-zA-Z0-9_./-]+\.(png|jpg|jpeg|webp)$/.test(url.pathname)) throw new Error('Invalid public image');
    }
    if(extended){
      const categories={car:'passenger',vned:'passenger',cartruck:'truck',truck:'truck',moto:'moto',quadbike:'moto',specteh:'special',selhoz:'special',loader:'special',other:'other'};
      if(p.kind==='tires'&&categories[p.tyreType]!==p.vehicleCategory||p.kind==='wheels'&&p.vehicleCategory!=='wheels'||p.kind==='tubes'&&(p.vehicleCategory!=='tubes'||p.subtype!==0))throw new Error('Invalid vehicle category');
      if(p.sizeLabel!==undefined&&!(accessories&&p.sizeLabel==='')&&!word(p.sizeLabel))throw new Error('Invalid size label');
    }
    if(p.attributes!==undefined&&(!Array.isArray(p.attributes)||p.attributes.length>30||!p.attributes.every(a=>keys(a,['label','value'])&&word(a.label)&&word(a.value))))throw new Error('Invalid public attributes');
    if(['sensors','consumables','oils'].includes(p.kind)){
      if(!accessories||p.vehicleCategory!==p.kind||!Array.isArray(p.attributes)||p.attributes.length>30||!p.attributes.every(a=>keys(a,['label','value'])&&word(a.label)&&word(a.value)))throw new Error('Invalid accessory');
    }
    if (p.kind === 'tires' && extended) {
      const optional=(v,hi)=>v===null||finite(v,0.01,hi);
      if(!optional(p.width,3000)||!optional(p.profile,200)||!['summer','winter','allseason','unknown'].includes(p.season)
        ||!(p.construction===null||typeof p.construction==='string'&&p.construction.length<=10)
        ||![p.loadIndex,p.speedIndex].every(v=>typeof v==='string'&&v.length<=20)
        ||![p.studded,p.xl,p.runflat].every(v=>[null,true,false].includes(v)))throw new Error('Invalid extended tire');
    } else if (p.kind === 'tires') {
      if (!finite(p.width,100,500) || !finite(p.profile,15,100) || !(bulk?['summer','winter','unknown']:['summer','winter']).includes(p.season)
        || !['R','ZR'].includes(p.construction) || ![p.loadIndex,p.speedIndex].every(word)
        || typeof p.studded !== 'boolean' || ![null,true,false].includes(p.xl)
        || ![null,true,false].includes(p.runflat)) throw new Error('Invalid tire parameters');
    } else if (p.kind === 'wheels' && (!finite(p.wheelWidth,3,16) || !finite(p.et,-100,200) || !finite(p.dia,30,200)
      || ![p.pcd,p.color,p.colorDescription].every(word) || p.type !== null)) throw new Error('Invalid rim parameters');
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
