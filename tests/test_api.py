import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from backend.app import app
from backend.models import Catalog, Product
from backend.supplier import secure_url,probe,resolve_tokens

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('DATA_MODE','demo')
    monkeypatch.delenv('ADMIN_API_TOKEN',raising=False)
    return TestClient(app)

def test_catalog_filter_and_pagination(client):
    r=client.get('/api/products',params={'kind':'tires','width':225,'profile':45,'diameter':18})
    assert r.status_code==200
    assert r.json()['total']==3
    assert {p['brand'] for p in r.json()['items']}=={'Michelin','Hankook','Triangle'}
    assert client.get('/api/products',params={'limit':101}).status_code==422

def test_quote_reprices_on_server_and_returns_no_order(client):
    result=client.post('/api/quote',json={'lines':[{'id':'demo-001','quantity':4}]}).json()
    assert result['total']==42720
    assert result['mode']=='demo'
    assert result['orderCreated'] is False
    assert result['fitmentConfirmed'] is False
    assert result['deliveryIncluded'] is False

def test_stock_does_not_sum_warehouses(client):
    r=client.post('/api/quote',json={'lines':[{'id':'demo-055','quantity':4}]})
    assert r.status_code==409
    assert r.json()['detail']['code']=='INSUFFICIENT_STOCK'
    r=client.post('/api/quote',json={'lines':[{'id':'demo-019','quantity':4}]})
    assert r.status_code==200
    assert 'Екатеринбург' in r.json()['items'][0]['offer']['warehouse']

@pytest.mark.parametrize('lines',[[{'id':'demo-001','quantity':-1}],[{'id':'demo-001','quantity':1.5}],[{'id':'demo-001','quantity':True}],[{'id':'demo-001','quantity':1,'price':1}],[{'id':'demo-001','quantity':12},{'id':'demo-001','quantity':12}]])
def test_rejects_invalid_or_price_tampered_quote(client,lines):
    assert client.post('/api/quote',json={'lines':lines}).status_code==422

def test_admin_closed_by_default_and_token_required(client,monkeypatch):
    assert client.get('/api/admin/status').status_code==503
    monkeypatch.setenv('ADMIN_API_TOKEN','x'*40)
    assert client.get('/api/admin/status').status_code==401
    assert client.get('/api/admin/status',headers={'Authorization':'Bearer '+'x'*40}).status_code==200

def test_no_live_order_creation(client):
    assert client.post('/api/orders',json={}).status_code==501
    assert client.get('/health').json()['supplierOrdersEnabled'] is False

def test_public_models_reject_private_supplier_fields():
    raw=json.loads(Path('data/demo-catalog.json').read_text())
    Catalog.model_validate(raw)
    raw['products'][0]['purchasePrice']=50
    with pytest.raises(ValidationError): Catalog.model_validate(raw)

def test_invalid_snapshot_rejected_before_import():
    raw=json.loads(Path('data/demo-catalog.json').read_text())
    raw['products'].append(raw['products'][0])
    with pytest.raises(ValidationError): Catalog.model_validate(raw)

def test_upgrades_legacy_wsdl_import_and_blocks_external_host():
    assert secure_url('http://api-b2b.4tochki.ru/WCF/ClientService.svc?xsd=xsd0').startswith('https://')
    for url in ['https://evil.test/','file:///etc/passwd','https://api-b2b.4tochki.ru@evil.test/','https://api-b2b.4tochki.ru:9999/']:
        with pytest.raises(ValueError): secure_url(url)

def test_supplier_order_methods_cannot_be_probed():
    with pytest.raises(ValueError): probe(None,'CreateOrder',{})

def test_secrets_resolved_only_on_server(monkeypatch):
    monkeypatch.setenv('FOURTOCHKI_LOGIN','test-login')
    monkeypatch.setenv('FOURTOCHKI_PASSWORD','not-a-real-secret')
    assert resolve_tokens({'nested':{'login':'$LOGIN','pass':'$PASSWORD'}})=={'nested':{'login':'test-login','pass':'not-a-real-secret'}}
