// Category-specific facets use explicit supplier fields, never guessed fitment.
export const sections=[
 ['tires','','Все шины','wheel'],['tires','passenger','Легковые','car'],['tires','truck','Грузовые','truck'],
 ['tires','moto','Мото','moto'],['tires','special','Спецтехника','special'],['tubes','','Камеры','tube'],
 ['wheels','','Диски','wheel'],['sensors','','Датчики','sensor'],['consumables','','Расходники','tools'],['oils','','Масла','oil']
];
export const attributeLabels={axle:'Ось установки',camera:'Камерность',protector:'Тип протектора',layers:'Количество слоёв протектора',
 sae:'Вязкость SAE',acea:'ACEA',api:'API',composition:'Состав',fuel:'Тип топлива',apply:'Применение',volume:'Объём, л',subtype:'Тип',construction:'Конструкция'};
export function valueFor(p,key){
 if(key==='category')return p.vehicleCategory|| (p.kind==='tires'?'other':p.kind);
 if(p[key]!=null)return p[key];
 const label=attributeLabels[key];return label?(p.attributes||[]).find(a=>a.label===label)?.value:undefined;
}
export function fieldsFor(kind,cat){
 if(kind==='wheels')return [['diameter','Диаметр'],['wheelWidth','Ширина'],['pcd','PCD'],['et','Вылет ET'],['dia','DIA']];
 if(kind==='tubes')return [['diameter','Диаметр'],['subtype','Тип камеры']];
 if(kind==='oils')return [['sae','Вязкость SAE'],['volume','Объём, л'],['composition','Состав'],['apply','Применение'],['acea','ACEA'],['api','API']];
 if(kind==='consumables')return [['subtype','Тип расходника'],['diameter','Диаметр']];
 if(kind==='sensors')return [];
 const size=[['width','Ширина'],['profile','Профиль'],['diameter','Диаметр']];
 if(cat==='truck')return [...size,['tyreType','Назначение'],['axle','Ось установки'],['loadIndex','Индекс нагрузки'],['camera','Камерность']];
 if(cat==='moto')return [...size,['tyreType','Мото / квадроцикл'],['axle','Передняя / задняя'],['construction','Конструкция'],['camera','Камерность'],['speedIndex','Индекс скорости']];
 if(cat==='special')return [...size,['tyreType','Тип техники'],['protector','Протектор'],['layers','Слойность'],['camera','Камерность'],['loadIndex','Индекс нагрузки']];
 return size;
}
export const typeLabels={car:'Легковые',vned:'Внедорожные',cartruck:'Лёгкие грузовики',truck:'Грузовые',moto:'Мотоциклы',quadbike:'Квадроциклы',specteh:'Спецтехника',selhoz:'Сельхозтехника',loader:'Погрузчики',other:'Прочие'};
export const advancedKeys=['tyreType','axle','camera','protector','layers','loadIndex','speedIndex','construction','sae','acea','api','composition','fuel','apply','volume','subtype'];
export const winterTypes=[['','Все'],['studded','Шипы'],['friction','Без шипов']];
// Old shared links with studded=1 remain usable; never leave a hidden winter
// restriction active after switching to summer, all-season or another category.
export function normalizeWinterFilters(filters){
 const next={...filters};
 let type=['studded','friction'].includes(next.winterType)?next.winterType:next.studded===true?'studded':'';
 if(type&&!next.season&&(!next.kind||next.kind==='tires'))next.season='winter';
 if(next.kind&&next.kind!=='tires'||next.season!=='winter')type='';
 next.winterType=type;next.studded=false;
 return next;
}
export function setSeason(filters,season){
 return normalizeWinterFilters({...filters,season,winterType:season==='winter'?filters.winterType:'',studded:false});
}
export function matchesAxle(p,f,axle){
 const prefix=axle==='rear'?'rear':'';
 return ['width','profile','diameter'].every(k=>!f[prefix?prefix+k[0].toUpperCase()+k.slice(1):k]||String(p[k])===String(f[prefix?prefix+k[0].toUpperCase()+k.slice(1):k]));
}
export function pairKey(p){return [p.brand,p.model,p.season,p.studded,p.runflat,p.xl].map(v=>String(v??'')).join('\u0001');}
