import {mkdir,writeFile,readFile,copyFile} from 'node:fs/promises';
const models=[
 {kind:'tires',brand:'Michelin',model:'Primacy 4',season:'summer',image:'michelin-primacy-4.webp',base:10680,description:'Модель летней шины. На фотографии может быть показан иллюстративный диск: он не входит в комплект шины.'},
 {kind:'tires',brand:'Hankook',model:'Winter i*cept evo3 W330',season:'winter',image:'hankook-w330-card.jpg',base:9720,description:'Модель зимней нешипованной шины. Характеристики конкретного артикула необходимо сверить с поставщиком.'},
 {kind:'tires',brand:'Triangle',model:'SporteX TH201',season:'summer',image:'triangle-sportex-th201-card.png',base:6480,description:'Модель летней шины. Диск на фотографии иллюстративный и не входит в комплект шины.'},
 {kind:'wheels',brand:'Enkei',model:'Racing RPF1',image:'enkei-rpf1-silver.png',base:24800,color:'Silver'},
 {kind:'wheels',brand:'Enkei',model:'PerformanceLine PF01',image:'enkei-pf01-sparkle-silver.png',base:22400,color:'Sparkle Silver'},
 {kind:'wheels',brand:'Enkei',model:'PerformanceLine PF05',image:'enkei-pf05-dark-silver.png',base:23900,color:'Dark Silver'}
];
const sizes=[[205,55,16],[215,55,17],[225,45,17],[225,50,17],[225,45,18],[235,45,18],[235,55,18],[245,45,19],[255,40,19],[255,45,20],[275,40,20],[285,35,21]];
const wheelSizes=[[7,17,'5×114.3',45,73.1],[7.5,18,'5×112',35,73.1],[8,18,'5×114.3',40,73.1],[8,19,'5×112',35,73.1],[8.5,19,'5×120',40,72.6],[9,20,'5×112',35,73.1],[7,17,'5×100',48,73.1],[8,18,'5×120',35,72.6],[8.5,18,'5×114.3',38,73.1],[9,19,'5×114.3',35,73.1],[9,20,'5×120',40,72.6],[9.5,20,'5×112',40,73.1]];
const products=[];
for(let s=0;s<12;s++) for(let m=0;m<models.length;m++){
 const model=models[m],n=products.length+1;
 const p={...model,id:`demo-${String(n).padStart(3,'0')}`,sku:`DEMO-${String(n).padStart(5,'0')}`,isDemo:true,image:`assets/products/${model.image}`,rank:1000-s*10-m};
 delete p.base;
 if(p.kind==='tires')Object.assign(p,{width:sizes[s][0],profile:sizes[s][1],diameter:sizes[s][2],loadIndex:s<3?'91':'103',speedIndex:p.season==='winter'?'V':'Y',studded:false,runflat:false,xl:s>4});
 else Object.assign(p,{wheelWidth:wheelSizes[s][0],diameter:wheelSizes[s][1],pcd:wheelSizes[s][2],et:wheelSizes[s][3],dia:wheelSizes[s][4],type:'alloy',description:'Фотография модели диска. Размеры, вылет и крепёж в этой демонстрации условные; они не подтверждают применимость к автомобилю.'});
 const price=model.base+s*(p.kind==='tires'?710:1150);
 p.offers=[{id:`o-${n}-a`,warehouse:'Демо-склад · Челябинск',stock:s===3?2:4+(n%3)*4,days:1+(n%2),price},{id:`o-${n}-b`,warehouse:'Демо-склад · Екатеринбург',stock:8+(n%4)*4,days:4+(n%3),price:price-180}];
 if(s===9)p.offers=[{id:`o-${n}-a`,warehouse:'Демо-склад · Челябинск',stock:2,days:2,price},{id:`o-${n}-b`,warehouse:'Демо-склад · Екатеринбург',stock:2,days:5,price:price-180}];
 if(s===11&&m===2)p.offers=[];
 products.push(p);
}
await mkdir('data',{recursive:true});
await writeFile('data/demo-catalog.json',JSON.stringify({schemaVersion:1,mode:'demo',updatedAt:'2026-09-14T00:00:00Z',notice:'Все артикулы, варианты размеров, характеристики, цены, остатки и сроки — синтетические примеры. Фото иллюстрируют модели. Применимость не подтверждена.',products},null,2)+'\n');
console.log(`Generated ${products.length} synthetic products.`);
