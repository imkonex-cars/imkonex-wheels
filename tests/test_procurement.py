"""Company preview isolation: all supplier data is synthetic; no network."""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from backend.portal_store import Store
from backend.portal_supplier import PortalSupplier, SupplierFailure, safe_verification_qr
from backend.procurement import register_procurement_routes, load_account


class Supplier:
    calls = []
    names = {'a': (11, 'ООО «АЛИТЕС»'), 'i': (22, 'ИНВЭКС ООО')}
    def __init__(self, *, login, password):
        self.login = login
        self.calls.append(('credentials', login, password))
    def customers(self):
        id_, name = self.names[self.login]
        return [{'id': id_, 'name': name, 'isLegal': True}]
    def purchase(self, codes):
        self.calls.append(('purchase', self.login, codes))
        return [{'sku': code, 'warehouseId': 77, 'purchasePrice': 1000 if self.login == 'a' else 1200,
                 'supplierRetailPrice': 1250, 'stock': 10} for code in codes]
    def addresses(self):
        return [{'id': 1, 'customerId': 11, 'addressName': 'A'},
                {'id': 2, 'customerId': 22, 'addressName': 'I'},
                {'id': 3, 'customerId': None, 'addressName': 'Unassigned'}]
    def create_order(self, *_):
        raise AssertionError('No supplier write may be called by procurement previews')


@pytest.fixture
def env(tmp_path):
    Supplier.calls = []
    store = Store(tmp_path / 'private.sqlite3')
    app = FastAPI()
    def authenticated(request: Request):
        if request.headers.get('x-test-auth') != 'yes':
            raise HTTPException(401, 'login_required')
        if request.method != 'GET' and request.headers.get('x-test-csrf') != 'yes':
            raise HTTPException(403, 'csrf_rejected')
        return {}
    settings = {'FOURTOCHKI_ALITES_LOGIN': 'a', 'FOURTOCHKI_ALITES_PASSWORD': 'a-secret',
                'FOURTOCHKI_ALITES_CUSTOMER_ID': '11', 'FOURTOCHKI_INVEKS_LOGIN': 'i',
                'FOURTOCHKI_INVEKS_PASSWORD': 'i-secret', 'FOURTOCHKI_INVEKS_CUSTOMER_ID': '22',
                'SUPPLIER_ORDERS_ENABLED': 'true'}
    products = {'p': {'id': 'p', 'sku': 'sku-1', 'brand': 'Synthetic', 'model': 'Tyre', 'kind': 'tires',
                      'offers': [{'id': 'warehouse-77', 'warehouse': 'Synthetic warehouse', 'stock': 10, 'price': 1250}]}}
    register_procurement_routes(app, store, authenticated, lambda *_: None, products,
                                supplier_factory=Supplier, environ=settings)
    client = TestClient(app)
    client.headers.update({'x-test-auth': 'yes', 'x-test-csrf': 'yes'})
    return SimpleNamespace(store=store, client=client, settings=settings)


def payload(company='alites'):
    return {'companyId': company, 'lines': [{'productId': 'p', 'warehouseId': 77, 'quantity': 4}]}


def preview(env, company='alites'):
    response = env.client.post('/api/manager/procurement/preview', json=payload(company))
    assert response.status_code == 200, response.text
    return response.json()


def test_requires_existing_manager_auth_and_csrf(env):
    env.client.headers.pop('x-test-auth')
    assert env.client.get('/api/manager/procurement/companies').status_code == 401
    env.client.headers['x-test-auth'] = 'yes'
    env.client.headers.pop('x-test-csrf')
    assert env.client.post('/api/manager/procurement/preview', json=payload()).status_code == 403


def test_no_contract_or_global_credentials_inference(env):
    env.settings.clear()
    env.settings.update(FOURTOCHKI_LOGIN='global', FOURTOCHKI_PASSWORD='never-use-this')
    data = env.client.get('/api/manager/procurement/companies').json()
    assert all(not company['configured'] and company['customerId'] is None for company in data['items'])
    assert env.client.post('/api/manager/procurement/preview', json=payload()).status_code == 409
    assert Supplier.calls == []
    env.settings['FOURTOCHKI_ALITES_CUSTOMER_ID'] = '66063-contract'
    assert load_account('alites', env.settings).customer_id is None


def test_separate_company_prices_and_no_global_cost_mutation(env):
    env.store.save_costs(['sku-1'], [{'sku': 'sku-1', 'warehouseId': 77, 'purchasePrice': 900,
                                   'supplierRetailPrice': 1250, 'stock': 6}])
    old_costs = env.store.costs()
    first, second = preview(env, 'alites'), preview(env, 'inveks')
    assert first['purchaseTotal'] == 4000 and second['purchaseTotal'] == 4800
    assert first['customerId'] == 11 and second['customerId'] == 22
    assert first['lines'][0]['salePrice'] == 1250
    assert second['lines'][0]['salePrice'] == 1300  # Mandatory purchase + 100 floor.
    assert second['lines'][0]['profitPerUnit'] == 100
    assert env.store.costs() == old_costs
    assert env.store.order_list() == []  # Legacy workflow cannot read/submit these drafts.
    for draft in (first, second):
        assert '_credentialFingerprint' not in draft
        assert not draft['canSubmit'] and not draft['contractVerified']
        assert 'secret' not in json.dumps(draft)
        assert draft['priceScope'] == 'api_account'


def test_fingerprint_is_pinned_private_and_detects_config_rotation(env):
    draft = preview(env)
    with env.store.connect() as con:
        data = json.loads(con.execute('SELECT payload FROM procurement_drafts').fetchone()['payload'])
    assert len(data['_credentialFingerprint']) == 64
    assert 'a-secret' not in json.dumps(data)
    url = '/api/manager/procurement/drafts/' + draft['id']
    assert env.client.get(url).json()['accountChanged'] is False
    env.settings['FOURTOCHKI_ALITES_PASSWORD'] = 'changed'
    assert env.client.get(url).json()['accountChanged'] is True
    assert env.client.get(url).json()['customerId'] == 11


def test_submission_blocked_even_global_orders_enabled(env):
    draft = preview(env)
    response = env.client.post('/api/manager/procurement/drafts/' + draft['id'] + '/submit')
    assert response.status_code == 409
    assert response.json()['detail'] == 'procurement_submit_not_available'


def test_id_must_belong_to_intended_company(env):
    env.settings['FOURTOCHKI_ALITES_CUSTOMER_ID'] = '22'
    assert env.client.post('/api/manager/procurement/preview', json=payload()).json()['detail'] == 'procurement_customer_not_available'
    env.settings.update(FOURTOCHKI_ALITES_LOGIN='i', FOURTOCHKI_ALITES_PASSWORD='i-secret')
    assert env.client.post('/api/manager/procurement/preview', json=payload()).json()['detail'] == 'procurement_customer_identity_mismatch'


def test_address_book_filters_explicit_customer_only(env):
    alites = env.client.post('/api/manager/procurement/companies/alites/addresses').json()
    inveks = env.client.post('/api/manager/procurement/companies/inveks/addresses').json()
    assert [a['id'] for a in alites['items']] == [1]
    assert [a['id'] for a in inveks['items']] == [2]
    assert alites['scheduleType'] == 'receiving_hours'


def test_duplicate_unknown_products_and_invalid_quantity_fail_before_api(env):
    value = payload(); value['lines'] *= 2
    assert env.client.post('/api/manager/procurement/preview', json=value).status_code == 422
    value = payload(); value['lines'][0]['productId'] = 'missing'
    assert env.client.post('/api/manager/procurement/preview', json=value).status_code == 404
    value = payload(); value['lines'][0]['quantity'] = True
    assert env.client.post('/api/manager/procurement/preview', json=value).status_code == 422
    assert Supplier.calls == []


def test_insufficient_stock_and_shared_pricing_policy(env):
    value = payload(); value['lines'][0]['quantity'] = 11
    assert env.client.post('/api/manager/procurement/preview', json=value).status_code == 409
    env.store.set_setting('category_pricing', {'all': {'mode': 'markup', 'percent': 50, 'roundTo': .01}})
    assert preview(env)['lines'][0]['salePrice'] == 1500


def test_explicit_supplier_does_not_fall_back(monkeypatch):
    monkeypatch.setenv('FOURTOCHKI_LOGIN', 'global')
    monkeypatch.setenv('FOURTOCHKI_PASSWORD', 'global-secret')
    assert PortalSupplier().configured
    assert not PortalSupplier(login='', password='').configured
    assert PortalSupplier(login='profile', password='profile-secret')._credentials() == ('profile', 'profile-secret')
    with pytest.raises(ValueError):
        PortalSupplier(login='incomplete')


@pytest.mark.parametrize('url', ['http://b2b.4tochki.ru/qr', 'https://evil.invalid/qr',
    'https://b2b.4tochki.ru.evil.invalid/qr', 'https://b2b.4tochki.ru@evil.invalid/qr',
    'https://user@b2b.4tochki.ru/qr', '//b2b.4tochki.ru/qr', 'data:image/png;base64,secret',
    'https://b2b.4tochki.ru:8443/qr', 'https://b2b.4tochki.ru/qr\n', None])
def test_qr_reference_rejects_unknown_origin_and_control_characters(url):
    assert safe_verification_qr(url) == ''


def test_private_fulfillment_does_not_infer_retail_payment():
    api = PortalSupplier(login='profile', password='secret')
    api.call = lambda *args: {'verificationCode': 'PRIVATE-RECEIPT-CODE',
        'verificationQR': 'https://b2b.4tochki.ru/qr/private', 'paymentPercent': 100,
        'pickupWarehouseAddress': 'Synthetic pickup', 'statusName': 'Ready'}
    private = api.order_fulfillment(55)
    assert private['verificationCode'] == 'PRIVATE-RECEIPT-CODE'
    assert private['verificationQR'] == 'https://b2b.4tochki.ru/qr/private'
    assert 'paymentPercent' not in private and 'paid' not in private
    assert 'verificationCode' not in api.order_info(55)
    with pytest.raises(SupplierFailure):
        api.order_fulfillment(True)


def test_documented_customer_and_address_containers():
    api = PortalSupplier(login='profile', password='secret')
    api.call = lambda *args: {'Items': {'GetOrderCustomerListResultItem': [
        {'ID': 11, 'name': 'ООО АЛИТЕС', 'isLegal': True}]}}
    assert api.customers() == [{'id': 11, 'name': 'ООО АЛИТЕС', 'isLegal': True}]
    api.call = lambda *args: {'getAddressListItems': {'GetAddressListItem': [{
        'addressId': 41, 'customerId': 11, 'addressName': 'Synthetic', 'schedule': {
            'Day': [{'dayOfWeek': 'Monday', 'hourFrom': 9, 'hourTo': 18, 'enabled': True}]}}]}}
    addresses = api.addresses()
    assert addresses[0]['customerId'] == 11
    assert addresses[0]['schedule'][0] == {'dayOfWeek': 'Monday', 'hourFrom': '9', 'hourTo': '18', 'enabled': True}
