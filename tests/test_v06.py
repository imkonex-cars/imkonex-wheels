import json
import sqlite3
import time
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from backend.portal import create_app, sale_price
from backend.portal_store import Store
from backend.site_shell import extract, SiteShell
from backend.catalog_accessories import collect_accessories, rest_codes
from backend.catalog_sync import SyncError, request_arguments, collect_catalog, load_config, validate_public_file
from test_catalog_sync import FakeSupplier, config

ROOT=Path(__file__).resolve().parent.parent

@pytest.fixture
def context(tmp_path,monkeypatch):
    monkeypatch.delenv('PUBLIC_ORIGIN',raising=False)
    monkeypatch.delenv('RENDER_EXTERNAL_URL',raising=False)
    monkeypatch.setenv('MANAGER_LOGIN','manager')
    class Supplier:
        configured=True
        def purchase(self,codes):
            return [{'sku':c,'warehouseId':wid,'purchasePrice':cost,'supplierRetailPrice':9000,'stock':stock}
                    for c in codes for wid,cost,stock in [(2017,5000,3),(2099,6000,12)]]
        def warehouses(self):return [{'id':2017,'name':'Склад А','shortName':'А','color':'#9ACD32','haveDelivery':True,'havePickup':True,'isPaidDelivery':False,'logisticDays':0}]
    data=json.loads((ROOT/'tests/fixtures/public-sample.json').read_text())
    for p in data['products']:
        p['offers']=[{'id':f'warehouse-{wid}','warehouse':f'Склад {wid}','price':9000,'stock':stock,'days':None} for wid,stock in [(2017,3),(2099,12)]]
    path=tmp_path/'public.json';path.write_text(json.dumps(data))
    app=create_app(db_path=tmp_path/'db.sqlite3',catalog_path=path,supplier=Supplier(),password='synthetic-only-password',local=True)
    c=TestClient(app);c.headers['Origin']='http://testserver'
    login=c.post('/api/manager/login',json={'login':'manager','password':'synthetic-only-password'})
    c.headers['X-CSRF-Token']=login.json()['csrf']
    return app,c,data

def test_existing_database_migrates_without_losing_prices_orders(tmp_path):
    file=tmp_path/'old.sqlite3'
    with sqlite3.connect(file) as con:
        con.execute('CREATE TABLE purchase(sku TEXT,warehouse INTEGER,cost REAL,stock INTEGER,updated REAL,PRIMARY KEY(sku,warehouse))')
        con.execute('INSERT INTO purchase VALUES (?,?,?,?,?)',('X',2017,3500,4,time.time()))
    store=Store(file);store.set_rule('product',{'mode':'fixed','price':4444})
    store.create_order({'id':'TEST','lines':[]})
    again=Store(file)
    assert again.costs()[('X',2017)]['cost']==3500
    assert again.rules()['product']['price']==4444
    assert again.order('TEST')['status']=='draft'

def test_warehouse_prices_filter_and_public_quote_never_leak_procurement(context):
    app,c,data=context;pid=data['products'][0]['id']
    assert c.post('/api/manager/purchase/refresh',json={'ids':[pid]}).status_code==200
    assert c.put('/api/manager/prices/'+pid+'?warehouse=2099',json={'mode':'profit','amount':1234.56,'roundTo':.01}).status_code==200
    rows=c.get('/api/manager/products',params={'q':data['products'][0]['sku'],'warehouses':'2099','min_stock':4}).json()['items']
    o=next(p for p in rows if p['id']==pid)['offers'][0]
    assert o['purchasePrice']==6000 and o['salePrice']==7234.56 and o['profit']==1234.56
    assert o['supplierRetailPrice']==9000 and o['warehouseId']==2099
    assert c.get('/api/manager/products?warehouses=2017&min_stock=4').json()['total']==0
    assert c.get('/api/manager/products?warehouses=none').json()['total']==0
    result=c.post('/api/selections',json={'lines':[{'productId':pid,'warehouseId':2099,'quantity':4}]}).json()
    assert result['total']==28938.24
    token=result['url'].split('/')[-1];anonymous=TestClient(app)
    public=anonymous.get('/api/selections/'+token)
    assert public.status_code==200
    assert all(k not in public.text for k in ['purchasePrice','warehouseId','profit','6000','priceRule','supplierRetailPrice'])
    assert anonymous.get('/api/manager/catalog-filters').status_code==401
    assert anonymous.get('/api/manager/products').status_code==401
    assert c.post('/api/selections',json={'lines':[{'productId':pid,'quantity':4,'price':1}]}).status_code==422
    assert c.post('/api/selections',json={'lines':[{'productId':pid,'warehouseId':2017,'quantity':4}]}).status_code==409
    with app.state.store.connect() as con:con.execute('UPDATE selections SET expires=0')
    assert anonymous.get('/api/selections/'+token).status_code==404

def test_rounding_and_formula_modes():
    cost={'cost':4567.89,'updated':time.time()}
    assert sale_price({'price':9000},{'mode':'markup','percent':10,'minimum':0,'roundTo':.01},cost)==5024.68
    assert sale_price({'price':9000},{'mode':'profit','amount':700,'roundTo':.01},cost)==5267.89
    assert sale_price({'price':9000},{'mode':'profit','amount':-5000,'roundTo':.01},cost)==4667.89
    assert sale_price({'price':9000},{'mode':'markup','percent':10,'minimum':700},cost)==5270

def test_product_characteristics_are_public_whitelisted_and_cached(context):
    from backend.product_attributes import product_attributes
    from backend.portal_supplier import PortalSupplier
    detail={'code':'TEST','noise':'72 дБ','grip':'B','comfort':'4','wear_index':0,
            'price':1337.42,'password':'NEVER-PUBLISH','thorn':False}
    supplier=PortalSupplier();supplier.call=lambda *args,**kwargs:{'tyreList':{'TyreContainer':[detail]}}
    attributes=supplier.details('TEST','tires')
    assert {'label':'Шумность','value':'72 дБ'} in attributes
    assert {'label':'Шипы','value':'Нет'} in attributes
    assert 'NEVER-PUBLISH' not in json.dumps(attributes) and '1337.42' not in json.dumps(attributes)
    assert not any(a['label']=='Индекс износостойкости' for a in attributes)
    assert product_attributes('tires',{'noise':'2'})==[{'label':'Шумность','value':'2'}]
    app,c,data=context;pid=data['products'][0]['id'];calls=[]
    app.state.supplier.details=lambda code,kind:(calls.append((code,kind)) or attributes)
    anonymous=TestClient(app)
    for _ in range(2):
        response=anonymous.get('/api/products/'+pid+'/details')
        assert response.status_code==200 and response.json()=={'id':pid,'attributes':attributes}
    assert len(calls)==1
    assert anonymous.get('/api/products/does-not-exist/details').status_code==404

def test_menu_sanitization_and_last_good_cache(tmp_path):
    links=''.join('<a href="/new-section/" onclick="bad()">Новый раздел</a>' for _ in range(5))
    raw=f'<header id="imxGlobalHeader">{links}<script>secret()</script><a href="javascript:bad()">bad</a><img src="https://evil.invalid/track" onerror="bad()"></header><footer id="imxPremiumFooter">{links}</footer>'
    value=extract(raw)
    assert 'https://www.imkonex.com/new-section/' in value['header']
    assert all(text not in value['header'] for text in ['onclick','onerror','javascript:','<script','evil.invalid'])
    fallback=tmp_path/'fallback.json';fallback.write_text(json.dumps(value))
    shell=SiteShell(tmp_path/'cache.json',fallback)
    with patch('backend.site_shell.requests.get',side_effect=TimeoutError):shell.refresh()
    assert shell.value==value and shell.last_error

def test_shared_menu_cors_is_public_only(context):
    app,c,data=context
    for endpoint in ['/api/site-shell','/shared/shell.css']:
        assert c.get(endpoint,headers={'Origin':'https://catalog.imkonex.com'}).headers['access-control-allow-origin']=='https://catalog.imkonex.com'
        assert 'access-control-allow-origin' not in c.get(endpoint,headers={'Origin':'https://evil.invalid'}).headers
    assert 'access-control-allow-origin' not in c.get('/api/manager/products',headers={'Origin':'https://catalog.imkonex.com'}).headers

def accessory_supplier(name,**options):
    if name=='GetRest':return {'success':True,'restItems':{'restItem':[{'code':code,'rest':6} for code in ['SENSOR1','OIL1','FAST1']] if options['page']<2 else []}}
    if name=='GetGoodsInfo':
        if not set(options['codes']) & {'SENSOR1','OIL1','FAST1'}:return NotImplemented
        codes=options['codes'];d={'pressureSensorList':{'PressureSensorContainer':[{'code':'SENSOR1','name':'TPMS test','brand':'Test'}] if 'SENSOR1' in codes else []},
           'oilList':{'OilContainer':[{'code':'OIL1','name':'Oil test','brand':'Test','sae':'5W-30','volume':4}] if 'OIL1' in codes else []},
           'fastenerList':{'FastenerContainer':[{'code':'FAST1','name':'Bolt test','brand':'Test','sub_type':'Болт'}] if 'FAST1' in codes else []}}
        return d
    if name=='GetGoodsPriceRestByCode':return {'price_rest_list':{'price_rest':[{'code':code,'whpr':{'wh_price_rest':[{'wrh':2017,'rest':6,'price':'1245.67','price_rozn':'1999.50'}]}} for code in options['codes']]}}
    return NotImplemented

def test_new_categories_import_via_verified_contract_and_fail_closed(tmp_path):
    cfg=config();cfg.update(extended_categories=True,include_accessories=True)
    result=collect_catalog(FakeSupplier(override=lambda n,o:accessory_supplier(n,**o)),cfg)
    assert {p['kind'] for p in result['products']}=={'tires','wheels','sensors','oils','consumables'}
    assert result['schemaVersion']==5
    assert '1245.67' not in json.dumps(result)
    assert next(p for p in result['products'] if p['kind']=='oils')['attributes'][0]=={'label':'Вязкость SAE','value':'5W-30'}
    file=tmp_path/'public.json';file.write_text(json.dumps(result));validate_public_file(file)
    with pytest.raises(SyncError):list(rest_codes(lambda *a,**k:{'success':True,'restItems':{'restItem':[{'code':'REPEAT','rest':5}]}},2017,cfg))
    args=request_arguments('GetRest','private','password',warehouse=2017,page=3)
    assert args['filter']=={'wrh':2017,'page':3}
