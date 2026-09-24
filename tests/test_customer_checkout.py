import json
import re
from pathlib import Path
from fastapi.testclient import TestClient
from backend.portal import create_app
from test_portal import Supplier, PASSWORD
from test_shop_v07 import quote, request_payload


class Sender:
    def send(self, phone, message, key): self.code=re.search(r'\b[0-9]{6}\b',message)[0]


def test_verified_checkout_belongs_only_to_current_account(tmp_path,monkeypatch):
    monkeypatch.delenv('PUBLIC_ORIGIN',raising=False)
    monkeypatch.delenv('RENDER_EXTERNAL_URL',raising=False)
    data=json.loads((Path(__file__).parent/'fixtures/public-sample.json').read_text())
    for p in data['products']: p['offers']=[{'id':'warehouse-2017','warehouse':'Test','stock':20,'price':8000,'days':None}]
    path=tmp_path/'catalog.json';path.write_text(json.dumps(data))
    sender=Sender()
    app=create_app(db_path=tmp_path/'db.sqlite3',catalog_path=path,supplier=Supplier(),password=PASSWORD,
                   local=True,customer_sender=sender,customer_secret='synthetic-auth-key-for-tests-32-bytes')
    client=TestClient(app);client.headers['Origin']='http://testserver'
    csrf=client.get('/api/customer/auth/bootstrap').json()['csrf'];client.headers['X-CSRF-Token']=csrf
    r=client.post('/api/customer/auth/request',json={'phone':'+79991234567','consent':True,'consentVersion':'2026-09-24'});assert r.status_code==200,r.text
    login=client.post('/api/customer/auth/verify',json={'challengeId':r.json()['challengeId'],'code':sender.code})
    assert login.status_code==200,login.text
    client.headers['X-CSRF-Token']=login.json()['csrf']
    payload=request_payload(quote(client))
    wrong=client.post('/api/shop/orders',json={**payload,'phone':'+79990001122'})
    assert wrong.status_code==422 and wrong.json()['detail']=='checkout_phone_mismatch'
    rejected=client.post('/api/shop/orders',json=payload,headers={'X-CSRF-Token':'wrong'})
    assert rejected.status_code==403
    created=client.post('/api/shop/orders',json=payload);assert created.status_code==200,created.text
    mine=client.get('/api/customer/orders');assert mine.status_code==200 and mine.json()['orders'][0]['id']==created.json()['id']
    assert 'customerAccountId' not in mine.text and '_token' not in mine.text
    assert app.state.store.customer_orders()[0]['customerAccountId']==login.json()['customer']['id']
    assert TestClient(app).get('/api/customer/orders').status_code==401
    assert app.state.customer_auth.orders('unrelated-owner')==[]
    public=client.get('/api/shop/orders/'+created.json()['token'])
    assert 'customerAccountId' not in public.text and login.json()['customer']['id'] not in public.text
    assert client.post('/api/shop/orders',json=payload).json()==created.json()
