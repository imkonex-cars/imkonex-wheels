"""Private portal contracts; fake supplier only, no network or real orders."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import json
import time
import pytest
from fastapi.testclient import TestClient
from backend.portal import create_app, sale_price
from backend.portal_supplier import PortalSupplier, SupplierFailure, pack

ROOT=Path(__file__).resolve().parent.parent
PASSWORD='Synthetic-only-password-56391'

class Supplier:
    configured=True
    def __init__(self):
        self.cost=4567.89; self.stock=20; self.created=[]; self.failure=None; self.fitment_calls=[]
    def purchase(self,codes):
        return [{'sku':c,'warehouseId':2017,'purchasePrice':self.cost,'supplierRetailPrice':8000,'stock':self.stock} for c in codes]
    def fitment(self,stage,p):
        self.fitment_calls.append((stage,p))
        return ['R5019','UNKNOWN'] if stage=='products' else ['Toyota']
    def create_order(self,draft):
        self.created.append(deepcopy(draft))
        if self.failure: raise SupplierFailure(self.failure)
        return {'providerId':123456,'providerNumber':'TEST-ONLY'}
    def order_info(self,id):
        return {'statusKey':'2','statusName':'Подтверждён','paymentPercent':'0'}

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.delenv('PUBLIC_ORIGIN',raising=False);monkeypatch.delenv('RENDER_EXTERNAL_URL',raising=False)
    monkeypatch.setenv('SUPPLIER_ORDERS_ENABLED','true');monkeypatch.setenv('MANAGER_LOGIN','manager')
    data=json.loads((ROOT/'tests/fixtures/public-sample.json').read_text())
    for p in data['products']:p['offers']=[{'id':'warehouse-2017','warehouse':'Synthetic warehouse','stock':20,'price':8000,'days':None}]
    path=tmp_path/'public.json';path.write_text(json.dumps(data));supplier=Supplier()
    app=create_app(db_path=tmp_path/'private.sqlite3',catalog_path=path,supplier=supplier,password=PASSWORD,local=True)
    client=TestClient(app);client.headers['Origin']='http://testserver'
    return app,client,supplier,path

def login(c):
    r=c.post('/api/manager/login',json={'login':'manager','password':PASSWORD});assert r.status_code==200,r.text
    c.headers['X-CSRF-Token']=r.json()['csrf'];return r

def preview(c,**extra):
    payload={'lines':[{'productId':'4t-tires-R5019','warehouseId':2017,'quantity':4}],**extra}
    r=c.post('/api/manager/orders/preview',json=payload);assert r.status_code==200,r.text
    return r.json()

def submit(c,d):return c.post('/api/manager/orders/'+d['id']+'/submit',json={'confirmation':d['confirmation']})

def public(c):return json.loads(c.get('/data/catalog.js').text.split(' = ',1)[1].rstrip(';'))

def test_private_endpoints_require_session_and_do_not_leak(env):
    app,c,s,path=env
    for endpoint in ['session','status','products','orders']:
        r=c.get('/api/manager/'+endpoint);assert r.status_code==401;assert r.headers['cache-control']=='no-store'
    for endpoint in ['/runtime/manager.sqlite3','/backend/portal.py','/.env']:
        assert c.get(endpoint).status_code==404
    text=c.get('/data/catalog.js').text
    assert 'purchasePrice' not in text and PASSWORD not in text

def test_login_csrf_origin_size_and_validation(env):
    app,c,s,path=env
    c.headers['Origin']='https://evil.invalid';assert c.post('/api/manager/login',json={'login':'manager','password':PASSWORD}).status_code==403
    c.headers['Origin']='http://testserver';r=login(c)
    assert 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=strict' in r.headers['set-cookie']
    c.headers['X-CSRF-Token']='bad';assert c.post('/api/manager/logout').status_code==403
    c.headers['X-CSRF-Token']=r.json()['csrf'];assert c.post('/api/manager/logout').status_code==200
    assert c.get('/api/manager/session').status_code==401
    r=c.post('/api/manager/login',json={'login':'manager','password':PASSWORD,'secret':'NEVER-ECHO'})
    assert r.status_code==422 and 'NEVER-ECHO' not in r.text
    assert c.post('/api/manager/login',content=b'x'*65537).status_code==413
    r=c.post('/api/manager/login',json={'login':'менеджер','password':'incorrect'})
    assert r.status_code==401

def test_password_required_secure_cookies_and_password_rotation(env):
    app,c,s,path=env;login(c)
    second=create_app(db_path=app.state.store.path,catalog_path=path,supplier=s,password=PASSWORD)
    after=TestClient(second,base_url='https://testserver');after.headers['Origin']='https://testserver';r=login(after)
    assert 'Secure' in r.headers['set-cookie']
    create_app(db_path=app.state.store.path,catalog_path=path,supplier=s,password='changed-secure-password')
    assert c.get('/api/manager/session').status_code==401
    blocked=TestClient(create_app(db_path=app.state.store.path,catalog_path=path,supplier=s,password=''))
    blocked.headers['Origin']='http://testserver'
    assert blocked.post('/api/manager/login',json={'login':'manager','password':'any'}).status_code==503

def test_purchase_prices_private_and_fixed_rule_survives_restart(env):
    app,c,s,path=env;login(c)
    r=c.post('/api/manager/purchase/refresh',json={'ids':['4t-tires-R5019']});assert r.status_code==200
    assert r.json()['items'][0]['offers'][0]['purchasePrice']==s.cost
    assert '4567.89' not in c.get('/data/catalog.js').text
    r=c.put('/api/manager/prices/4t-tires-R5019',json={'mode':'fixed','price':9123});assert r.status_code==200
    assert public(c)['products'][0]['offers'][0]['price']==9123
    after=TestClient(create_app(db_path=app.state.store.path,catalog_path=path,supplier=s,password=PASSWORD,local=True))
    assert public(after)['products'][0]['offers'][0]['price']==9123
    assert after.get('/api/manager/products').status_code==401

def test_markup_is_per_warehouse_rounded_up_and_expires(env):
    app,c,s,path=env;login(c)
    assert c.put('/api/manager/prices/4t-tires-R5019',json={'mode':'markup','percent':10,'minimum':700}).status_code==200
    assert public(c)['products'][0]['offers'][0]['price']==5270
    offer={'price':8000};rule={'mode':'markup','percent':10,'minimum':700}
    assert sale_price(offer,rule,{'cost':10000,'updated':time.time()})==11000
    assert sale_price(offer,rule,{'cost':10000,'updated':time.time()-90000}) is None
    assert sale_price(offer,rule,None) is None

def test_fitment_uses_confirmed_ids_only_and_caches(env):
    app,c,s,path=env
    assert c.get('/api/fitment/products').status_code==422
    params={'make':'Toyota','model':'Camry','begin':2017,'end':2021,'modification':'2.5'}
    for _ in range(2):
        r=c.get('/api/fitment/products',params=params);assert r.status_code==200
        assert r.json()['productIds']==['4t-tires-R5019']
    assert len(s.fitment_calls)==1

def test_preview_recalculates_from_server_and_cannot_inject_price(env):
    app,c,s,path=env;login(c);d=preview(c)
    assert d['test'] is True and d['purchaseTotal']==18271.56 and d['saleTotal']==32000
    assert s.created==[]
    r=c.post('/api/manager/orders/preview',json={'lines':[{'productId':'4t-tires-R5019','warehouseId':2017,'quantity':4,'purchasePrice':1}]})
    assert r.status_code==422
    s.stock=3
    r=c.post('/api/manager/orders/preview',json={'lines':[{'productId':'4t-tires-R5019','warehouseId':2017,'quantity':4}]})
    assert r.status_code==409

def test_submit_once_and_status_refresh(env):
    app,c,s,path=env;login(c);d=preview(c,test=False)
    assert submit(c,d).status_code==200;assert submit(c,d).status_code==200
    assert len(s.created)==1 and s.created[0]['test'] is False
    r=c.post('/api/manager/orders/'+d['id']+'/refresh');assert r.status_code==200
    assert r.json()['history'][0]['name']=='Подтверждён'
    assert len(s.created)==1

@pytest.mark.parametrize('failure',['order_result_unknown','supplier_unavailable'])
def test_unknown_submission_is_durable_and_never_retried(env,failure):
    app,c,s,path=env;login(c);d=preview(c);s.failure=failure
    assert submit(c,d).status_code==503
    assert app.state.store.order(d['id'])['status']=='unknown'
    assert submit(c,d).status_code==409 and len(s.created)==1
    r=c.post('/api/manager/orders/'+d['id']+'/reconcile',json={'providerId':123456})
    assert r.status_code==200 and r.json()['manuallyLinked'] is True and len(s.created)==1

@pytest.mark.parametrize('change',['cost','stock','disabled','expired'])
def test_submit_rechecks_price_stock_expiration_and_enablement(env,monkeypatch,change):
    app,c,s,path=env;login(c);d=preview(c)
    if change=='cost':s.cost+=1
    if change=='stock':s.stock=1
    if change=='disabled':monkeypatch.setenv('SUPPLIER_ORDERS_ENABLED','false')
    if change=='expired':
        d['expiresAt']=time.time()-1;app.state.store.update_order(d['id'],'draft',d)
    assert submit(c,d).status_code in (409,503)
    assert s.created==[]

def test_parallel_claims_have_exactly_one_winner(env):
    app,c,s,path=env;login(c);d=preview(c)
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(lambda _:app.state.store.claim_order(d['id']),range(16)))
    assert sum(results)==1

def test_soap_order_payload_contains_retail_not_purchase_and_no_skip():
    adapter=PortalSupplier();calls=[]
    def call(name,args,**options):
        calls.append((name,args,options));return {'success':True,'orderID':12}
    adapter.call=call
    adapter.create_order({'id':'IMX-TEST','test':True,'lines':[{'sku':'T1','quantity':4,'warehouseId':2017,'salePrice':5000,'purchasePrice':4000}]})
    name,args,options=calls[0];order=args['order']
    assert name=='CreateOrder' and options=={'write':True}
    assert order['product_list'][0]['priceIn']==5000
    assert order['skip_error_61'] is False and order['base_order']['orderNumber']=='IMX-TEST'
    assert '4000' not in str(order)

def test_actual_wsdl_array_wrapping_and_case_are_respected():
    from zeep import xsd
    array=xsd.ComplexType(xsd.Sequence([xsd.Element('string',xsd.String(),max_occurs='unbounded')]))
    schema=xsd.ComplexType(xsd.Sequence([xsd.Element('orderID',xsd.Int()),xsd.Element('codes',array)]))
    assert pack(schema,{'orderId':12,'codes':['A','B']})=={'orderID':12,'codes':{'string':['A','B']}}
    with pytest.raises(SupplierFailure):pack(schema,{'invented':1})

def test_accepted_order_id_is_retained_even_with_provider_warning():
    adapter=PortalSupplier()
    adapter.call=lambda *args,**kwargs:{'success':False,'orderID':123,'error_product_list':{'OrderProduct':[{'code':'T1'}]}}
    result=adapter.create_order({'id':'IMX-TEST','test':True,'lines':[{'sku':'T1','quantity':1,'warehouseId':2017,'salePrice':5000}]})
    assert result['providerId']==123 and result['providerReviewRequired'] is True

def test_background_markup_refresh_updates_private_costs_only(env):
    import threading
    from backend.portal_jobs import refresh_markups
    app,c,s,path=env;login(c)
    c.put('/api/manager/prices/4t-tires-R5019',json={'mode':'markup','percent':10,'minimum':0})
    s.cost=7000
    with app.state.store.connect() as con:con.execute('UPDATE purchase SET updated=?',(time.time()-21601,))
    class Stop:
        def is_set(self):return False
        def wait(self,timeout):return False
    refreshed=[]
    products={p['id']:p for p in json.loads(path.read_text())['products']}
    refresh_markups(app.state.store,s,products,Stop(),lambda:refreshed.append(True))
    assert app.state.store.costs()[('R5019',2017)]['cost']==7000
    assert refreshed==[True]
    assert {k[0] for k in app.state.store.costs()}=={p['sku'] for p in products.values()}

def test_vehicle_requests_match_previously_observed_supplier_wsdl():
    from zeep import xsd
    from lxml import etree
    schema=json.loads((ROOT/'tests/fixtures/fourtochki_inputs_20260917.json').read_text())
    def type_(d):
        if not d.get('fields'):return xsd.Schema().get_type(d['type'])
        return xsd.ComplexType(xsd.Sequence([xsd.Element(f['name'],type_(f),min_occurs=f['minOccurs'],max_occurs=f['maxOccurs'] if f['maxOccurs']=='unbounded' else int(f['maxOccurs']),nillable=f['nillable']) for f in d['fields']]))
    adapter=PortalSupplier();observed=[]
    def call(name,arguments=None,**opts):
        values={'login':'synthetic-login','password':'synthetic-password',**(arguments or {})}
        operation=type_(schema[name]);element=xsd.Element(name,operation)
        xml=etree.Element('Envelope');element.render(xml,element(**pack(operation,values)))
        observed.append((name,xml))
        return {'marka_list':{'string':['Toyota']},'model_list':{'string':['Camry']},'yearAvto_list':{'yearAvto':[{'year_begin':2017,'year_end':2021}]},'modification_list':{'string':['2.5 AT']},'price_rest_list':{'goods_price_rest':[{'code':'T1'}]}}
    adapter.call=call
    parameters={'make':'Toyota','model':'Camry','begin':2017,'end':2021,'modification':'2.5 AT'}
    for stage in ('makes','models','years','modifications','products'):adapter.fitment(stage,parameters)
    assert [v[0] for v in observed]==['GetMarkaAvto','GetModelAvto','GetYearAvto','GetModificationAvto','GetGoodsByCar']
    assert observed[-1][1].find('.//podbor_type/int').text=='1'
    assert [e.text for e in observed[-1][1].findall('.//filter/type/string')]==['tire','disk']
