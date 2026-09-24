import base64
from io import BytesIO
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from backend.portal_store import Store
from backend.supplier_notifications import NS, SOAP11, SOAP12, parse_signal, wsdl, register_notification_routes


def message(number='A-123', soap=SOAP11):
    return f'<s:Envelope xmlns:s="{soap}"><s:Body><OrderChanged xmlns="{NS}"><OrderNumber>{number}</OrderNumber></OrderChanged></s:Body></s:Envelope>'.encode()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('SUPPLIER_NOTIFICATIONS_ENABLED', 'true')
    monkeypatch.setenv('FOURTOCHKI_ALITES_NOTIFY_USER', 'notify-test')
    monkeypatch.setenv('FOURTOCHKI_ALITES_NOTIFY_PASSWORD', 'synthetic-notification-secret-384932')
    monkeypatch.setenv('PUBLIC_ORIGIN', 'https://shop.example.test')
    app = FastAPI(); store = Store(tmp_path/'db.sqlite3')
    def manager(request: Request):
        if request.headers.get('x-test-manager') != 'yes': raise HTTPException(401)
    register_notification_routes(app, store, authenticated=manager, throttle=lambda *args: None)
    client = TestClient(app)
    client.headers['Authorization'] = 'Basic '+base64.b64encode(b'notify-test:synthetic-notification-secret-384932').decode()
    return client, store


def test_signal_is_durable_account_scoped_and_repeatable(env):
    client, store = env
    for soap in (SOAP11, SOAP12):
        r = client.post('/api/provider-notifications/alites', content=message(soap=soap))
        assert r.status_code == 200 and '>true<' in r.text
    with store.connect() as con:
        row = con.execute('SELECT * FROM supplier_notifications').fetchone()
        assert row['revision'] == 2 and row['order_number'] == 'A-123' and row['status'] == 'pending'
        assert row['profile'] == 'alites'
    assert client.post('/api/provider-notifications/inveks', content=message()).status_code == 503
    assert client.get('/api/manager/provider-notifications').status_code == 401
    result = client.get('/api/manager/provider-notifications', headers={'x-test-manager':'yes'}).json()
    assert result['processorEnabled'] is False
    assert len(result['items']) == 1


def test_receiver_disabled_or_bad_auth_never_persists(env, monkeypatch):
    client, store = env
    assert client.post('/api/provider-notifications/alites',content=message(),headers={'Authorization':'Basic bad!'}).status_code == 401
    monkeypatch.setenv('SUPPLIER_NOTIFICATIONS_ENABLED','false')
    assert client.post('/api/provider-notifications/alites',content=message()).status_code == 503
    with store.connect() as con: assert con.execute('SELECT count(*) FROM supplier_notifications').fetchone()[0] == 0


@pytest.mark.parametrize('data',[message('12345678901'),message(''),message('<evil/>'),b'x'*16385,
    b'<!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'+message('&xxe;'),
    message().replace(b'OrderChanged>',b'WrongMethod>'),message().replace(b'<s:Body>',b'<s:Header/><s:Body>')])
def test_reject_bad_signals(data):
    with pytest.raises((ValueError,)): parse_signal(data)


def test_wsdl_describes_invocable_single_order_operation(env):
    from zeep import Client
    client, _ = env
    r = client.get('/api/provider-notifications/alites?wsdl')
    assert r.status_code == 200
    parsed = Client(BytesIO(r.content))
    operation = parsed.service._binding._operations['OrderChanged']
    assert 'OrderNumber' in str(operation.input.signature())
    assert parsed.service._binding_options['address'] == 'https://shop.example.test/api/provider-notifications/alites'


def test_no_ack_when_durable_write_fails(env, monkeypatch):
    from contextlib import contextmanager
    client, store = env
    @contextmanager
    def broken(): raise RuntimeError('synthetic disk error'); yield
    monkeypatch.setattr(store, 'connect', broken)
    with pytest.raises(RuntimeError): client.post('/api/provider-notifications/alites',content=message())
