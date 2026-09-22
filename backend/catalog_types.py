"""Public extended categories from explicit supplier types, never size guesses."""
from .export_snapshot import number, text, public_offers
from .catalog_photos import public_photo_url

TYPE_CATEGORY = {'car':'passenger','vned':'passenger','cartruck':'truck','truck':'truck',
                 'moto':'moto','quadbike':'moto','specteh':'special','selhoz':'special',
                 'loader':'special','other':'other'}


def optional_number(value, low=0.01, high=3000):
    return None if value in (None,'') else number(value,minimum=low,maximum=high)


def extended_product(kind, detail, search, warehouses, warehouse_ids):
    from .catalog_sync import public_product, product_id, code_of, rows_at, SyncError, UnsupportedProduct
    if kind == 'wheels':
        p=public_product(kind,detail,search,warehouses,warehouse_ids)
        if p is not None:p['vehicleCategory']='wheels'
        return p
    code=code_of(detail)
    if code!=code_of(search):raise SyncError('article_mapping_mismatch')
    offers=[];seen=set()
    for row in rows_at(search,'whpr','wh_price_rest'):
        wid=row.get('wrh')
        if type(wid) is not int or wid in seen or wid not in warehouses:raise SyncError('warehouse_mapping_changed')
        seen.add(wid)
        if warehouse_ids and wid not in warehouse_ids:continue
        offers.append({'warehouseId':wid,'warehouseKnown':True,'warehouseName':warehouses[wid],
                       'rest':row.get('rest'),'price_rozn':row.get('price_rozn')})
    try:public=public_offers({'offers':offers})
    except (ValueError,TypeError):raise SyncError('invalid_retail_or_stock') from None
    if not public:return None
    try:
        name=text(detail.get('name') or search.get('name'),1000)
        p={'id':product_id(kind,code),'sku':code,'kind':kind,
           'brand':text(detail.get('brand') or search.get('marka') or 'Марка не указана',100),
           'model':text(detail.get('model') or search.get('model') or name),
           'description':name,'sizeLabel':name,
           'image':public_photo_url(detail) or public_photo_url(search) or 'assets/product-unavailable.svg',
           'isDemo':False,'rank':100,'diameter':optional_number(detail.get('diameter'),high=100),'offers':public}
        if kind=='tubes':
            if detail.get('sub_type_id')!=0:raise UnsupportedProduct('not_a_tube')
            p.update(vehicleCategory='tubes',subtype=0)
        else:
            raw_type=search.get('type') or detail.get('type') or 'other'
            tyre_type=raw_type if raw_type in TYPE_CATEGORY else 'other'
            p.update(vehicleCategory=TYPE_CATEGORY[tyre_type],tyreType=tyre_type,
                     width=optional_number(detail.get('width')),profile=optional_number(detail.get('height'),high=200),
                     season={'s':'summer','w':'winter','u':'allseason'}.get(detail.get('season'),'unknown'),
                     construction=text(detail.get('constr') or '',10) if detail.get('constr') else None,
                     loadIndex=str(detail.get('load_index') or '')[:20],speedIndex=str(detail.get('speed_index') or '')[:20],
                     studded=detail.get('thorn') if type(detail.get('thorn')) is bool else None,
                     xl=True if str(detail.get('tonnage','')).upper()=='XL' else None,
                     runflat=detail.get('runflat') if type(detail.get('runflat')) is bool else None)
        return p
    except (ValueError,TypeError):
        raise UnsupportedProduct('unsupported_product_parameters') from None
