"""Accessories via GetRest + GetGoodsInfo + GetGoodsPriceRestByCode.

Contracts are from the user's successful WSDL report of 2026-09-17.
No unverified GetFind* names or numeric category IDs are used.
"""
from .catalog_sync import rows_at, code_of, SyncError, ProviderError, product_id
from .catalog_photos import public_photo_url
from .export_snapshot import public_offers, text

CONTAINERS={'pressureSensorList':('PressureSensorContainer','sensors'),
            'oilList':('OilContainer','oils'),'fastenerList':('FastenerContainer','consumables'),
            'sparePartList':('SparePartContainer','consumables'),'cameraList':('CameraContainer','consumables')}
ATTRIBUTES={'sae':'Вязкость SAE','acea':'ACEA','api':'API','composition':'Состав','apply':'Применение',
            'fuel_type':'Тип топлива','type':'Тип','use':'Назначение','volume':'Объём, л',
            'sub_type':'Тип расходника','sub_type_name':'Тип расходника'}

def rest_codes(call,wid,config):
    def get(page):
        response=call('GetRest',warehouse=wid,page=page)
        if response.get('success') is not True:raise SyncError('rest_not_verified')
        return rows_at(response,'restItems','restItem')
    try:zero=get(0)
    except ProviderError:zero=None
    one=get(1)
    base=0 if zero and [code_of(r) for r in zero]!=[code_of(r) for r in one] else 1
    seen=set()
    for page in range(base,base+config['max_pages_per_category']):
        rows=zero if page==0 else one if page==1 else get(page)
        if not rows:return
        codes=[code_of(r) for r in rows]
        if len(set(codes))!=len(codes) or seen.intersection(codes):raise SyncError('rest_paging_repeated')
        seen.update(codes)
        if len(seen)>config['max_products']:raise SyncError('rest_budget_exceeded')
        for row in rows:
            if type(row.get('rest')) is not int or row['rest']<0:raise SyncError('rest_shape_changed')
            if row['rest']>0:yield code_of(row)
    raise SyncError('rest_page_budget_exceeded')

def accessory_product(kind,detail,prices,warehouses,warehouse_ids):
    code=code_of(detail);offers=[]
    for row in rows_at(prices,'whpr','wh_price_rest'):
        wid=row.get('wrh')
        if type(wid) is not int or wid not in warehouses:raise SyncError('warehouse_mapping_changed')
        if warehouse_ids and wid not in warehouse_ids:continue
        offers.append({'warehouseId':wid,'warehouseKnown':True,'warehouseName':warehouses[wid],
                       'rest':row.get('rest'),'price_rozn':row.get('price_rozn')})
    try:public=public_offers({'offers':offers})
    except (ValueError,TypeError):raise SyncError('invalid_retail_or_stock') from None
    if not public:return None
    name=text(detail.get('name'),1000)
    attributes=[{'label':label,'value':str(detail[key])[:300]} for key,label in ATTRIBUTES.items()
                if detail.get(key) not in (None,'') and not isinstance(detail[key],(list,dict))]
    return {'id':product_id(kind,code),'sku':code,'kind':kind,'vehicleCategory':kind,
            'brand':text(detail.get('brand') or 'Марка не указана',100),'model':text(detail.get('model') or name),
            'description':name,'sizeLabel':detail.get('sae') or detail.get('sub_type_name') or '',
            'image':public_photo_url(detail) or 'assets/product-unavailable.svg','isDemo':False,
            'rank':100,'diameter':None,'offers':public,'attributes':attributes}

def collect_accessories(call,config,warehouses,known_codes):
    # Scan all enabled warehouses: GetRest does not require guessed category values.
    codes=set()
    for wid in config['warehouse_ids'] or warehouses:
        codes.update(rest_codes(call,wid,config))
        if len(codes)>config['max_products']:raise SyncError('product_budget_exceeded')
    codes=sorted(codes-set(known_codes));products=[];excluded=0
    stats={k:{'scanned':0,'published':0,'excluded':0,'pages':0,'pageBase':1} for k in ['sensors','consumables','oils']}
    for offset in range(0,len(codes),config['detail_batch_size']):
        batch=codes[offset:offset+config['detail_batch_size']];info=call('GetGoodsInfo',codes=batch);mapped={}
        for container,(item,kind) in CONTAINERS.items():
            if not info.get(container):continue
            for detail in rows_at(info,container,item):
                code=code_of(detail)
                if code not in batch or code in mapped:raise SyncError('accessory_mapping_changed')
                if container=='cameraList' and detail.get('sub_type_id')==0:continue
                mapped[code]=(kind,detail)
        if not mapped:continue
        rows=rows_at(call('GetGoodsPriceRestByCode',codes=list(mapped)),'price_rest_list','price_rest')
        prices={code_of(r):r for r in rows}
        if len(prices)!=len(rows) or set(prices)-set(mapped):raise SyncError('accessory_price_mapping_changed')
        for code,(kind,detail) in mapped.items():
            stat=stats[kind];stat['scanned']+=1
            p=accessory_product(kind,detail,prices.get(code,{'whpr':{'wh_price_rest':[]}}),warehouses,config['warehouse_ids'])
            if p:products.append(p);stat['published']+=1
            else:stat['excluded']+=1;excluded+=1
    return products,stats,excluded
