import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from lxml import html
import pytest
from fastapi.testclient import TestClient
from backend.pricing import price_result, sale_price, category
from backend.site_shell import extract
from test_portal import env, login, public

@pytest.mark.parametrize('rule',[{'mode':'supplier'},{'mode':'fixed','price':1},
 {'mode':'discount','percent':99,'roundTo':.01},{'mode':'markup','percent':0,'roundTo':.01},
 {'mode':'profit','amount':-9999,'roundTo':.01}])
def test_every_pricing_path_enforces_100_kopeck_exact(rule):
    cost={'cost':4567.89,'retail':4500,'updated':time.time()}
    result=price_result({'price':9000},rule,cost)
    assert result['price']>=4667.89 and result['floor']==4667.89 and result['adjusted']
    assert sale_price({'price':9000},rule,None) is None
    assert sale_price({'price':9000},rule,{**cost,'updated':time.time()-86401}) is None

def test_recommended_retail_has_no_automatic_rounding_or_invented_rrp():
    cost={'cost':1000,'retail':1255.55,'updated':time.time()}
    assert sale_price({'price':7777},{'mode':'supplier','roundTo':10},cost)==1255.55
    assert sale_price({'price':7777},{'mode':'supplier'},{**cost,'retail':None}) is None
    assert category({'kind':'tires'})=='other'  # Legacy dimensions do not prove passenger fitment.

def test_category_policy_floor_and_warehouse_precedence(env):
    app,c,s,path=env;login(c);pid='4t-tires-R5019'
    c.post('/api/manager/purchase/refresh',json={'ids':[pid]})
    rule={'mode':'discount','percent':90,'minimum':100,'roundTo':.01}
    preview=c.post('/api/manager/pricing/tires/preview',json=rule)
    assert preview.status_code==200 and preview.json()['floorApplied']>=1
    assert c.put('/api/manager/pricing/tires',json=rule).status_code==200
    assert public(c)['products'][0]['offers'][0]['price']==4667.89
    assert c.put('/api/manager/prices/'+pid+'?warehouse=2017',json={'mode':'fixed','price':5200}).status_code==200
    assert public(c)['products'][0]['offers'][0]['price']==5200
    assert c.post('/api/manager/pricing/tires/preview',json=rule).json()['overridden']==1
    assert c.delete('/api/manager/prices/'+pid+'?warehouse=2017').status_code==200
    assert public(c)['products'][0]['offers'][0]['price']==4667.89
    assert c.put('/api/manager/pricing/all',json={**rule,'percent':101}).status_code==422
    assert TestClient(app).get('/api/manager/pricing').status_code==401

def quote(c,lines=None):
    r=c.post('/api/shop/quote',json={'lines':lines or [{'productId':'4t-tires-R5019','quantity':4}]})
    assert r.status_code==200,r.text
    return r.json()
def request_payload(q):
    return {'quoteId':q['quoteId'],'key':'synthetic-idempotency-key-1234','name':'Тестовый клиент','phone':'+79991234567','city':'Челябинск','delivery':'delivery','comment':'Тест','consent':True}

def test_checkout_to_status_retry_privacy_and_manager_transitions(env):
    app,c,s,path=env;q=quote(c);body=request_payload(q)
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:c.post('/api/shop/orders',json=body),range(2)))
    assert all(r.status_code==200 for r in results)
    assert results[0].json()==results[1].json()
    assert len(app.state.store.customer_orders())==1 and s.created==[]
    r=results[0].json();status=c.get('/api/shop/orders/'+r['token'])
    assert status.status_code==200 and status.json()['total']==32000
    assert all(v not in status.text for v in ['79991234567','Тестовый клиент','purchasePrice','4567.89','_key'])
    assert status.headers['cache-control']=='no-store'
    assert c.get('/api/shop/orders/'+r['id']).status_code==404
    assert c.get('/api/manager/customer-orders').status_code==401
    login(c);rows=c.get('/api/manager/customer-orders').json()['items'];o=rows[0]
    assert o['customer']['name']=='Тестовый клиент'
    assert c.put('/api/manager/customer-orders/'+r['id']+'/status',json={'status':'shipped','note':'','updatedAt':o['updatedAt']}).status_code==422
    payload={'status':'confirmed','note':'Комплект подтверждён','updatedAt':o['updatedAt']}
    assert c.put('/api/manager/customer-orders/'+r['id']+'/status',json=payload).status_code==200
    assert c.put('/api/manager/customer-orders/'+r['id']+'/status',json=payload).status_code==409
    assert c.get('/api/shop/orders/'+r['token']).json()['history'][-1]['note']=='Комплект подтверждён'

def test_checkout_rechecks_total_stock_and_cannot_inject_prices(env):
    app,c,s,path=env;q=quote(c);body=request_payload(q)
    s.cost=8100  # retail 8000 now violates the minimum
    assert c.post('/api/shop/orders',json=body).status_code==409
    assert app.state.store.customer_orders()==[]
    q=quote(c);assert q['total']==32800
    s.stock=3
    assert c.post('/api/shop/orders',json=request_payload(q)).status_code==409
    assert c.post('/api/shop/quote',json={'lines':[{'productId':'4t-tires-R5019','quantity':1,'price':1}]}).status_code==422
    s.stock=20
    assert c.post('/api/shop/quote',json={'lines':[{'productId':'4t-tires-R5019','quantity':12},{'productId':'4t-tires-R5019','quantity':12,'warehouseId':2017}]}).status_code==409
    c.headers['Origin']='https://bad.invalid'
    assert c.post('/api/shop/quote',json={'lines':[{'productId':'4t-tires-R5019','quantity':1}]}).status_code==403

def test_shop_settings_are_persistent_and_not_publicly_writable(env):
    app,c,s,path=env;settings=c.get('/api/shop/settings').json()
    assert c.put('/api/manager/shop-settings',json=settings).status_code==401
    login(c);settings['delivery']='Доставку согласуем лично. Проверочный текст условий.'
    assert c.put('/api/manager/shop-settings',json=settings).status_code==200
    assert c.get('/api/shop/settings').json()['delivery']==settings['delivery']

def test_footer_preserves_offices_contacts_and_safe_svg():
    links=''.join('<a href="/cars/">Автомобили</a>' for _ in range(5))
    offices=''.join(f'<article class="imx-pf__office"><address>Офис {i}<br>Улица {i}</address><a href="tel:+7000000{i}">Телефон</a></article>' for i in range(3))
    raw=f'<header id="imxGlobalHeader">{links}<svg viewBox="0 0 24 24"><path d="M0 0" stroke="currentColor" onload="bad()"/></svg></header><footer id="imxPremiumFooter">{links}{offices}</footer>'
    shell=extract(raw);footer=html.fromstring(shell['footer'])
    assert len(footer.xpath('.//article'))==3 and len(footer.xpath('.//address'))==3
    assert 'onload' not in shell['header'] and '<svg' in shell['header']
    assert shell['formatVersion']==2
