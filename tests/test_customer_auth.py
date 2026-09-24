"""Meaningful auth boundaries; all SMS is an injected test-only transport."""
from concurrent.futures import ThreadPoolExecutor
import json
import re
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from backend.customer_auth import CustomerAuth, CustomHttpsSmsBridge, normalize_phone, register_customer_routes, COOKIE, PREAUTH_COOKIE, OTP_TTL, SESSION_TTL, CONSENT_VERSION
from backend.portal_store import Store

class Sender:
    def __init__(self): self.sent=[]
    def send(self,phone,message,key): self.sent.append((phone,message,key))
    @property
    def code(self): return re.search(r'код входа (\d{6})', self.sent[-1][1]).group(1)

@pytest.fixture
def setup(tmp_path, monkeypatch):
    for key in ('CUSTOMER_AUTH_SECRET','CUSTOMER_SMS_BRIDGE_URL','CUSTOMER_SMS_BRIDGE_TOKEN','SMS_PROVIDER','SMS_RU_API_ID'):
        monkeypatch.delenv(key, raising=False)
    now=[10000.0];sender=Sender();store=Store(tmp_path/'private.sqlite3')
    service=CustomerAuth(store.path,secret='k'*40,sender=sender,clock=lambda:now[0])
    return SimpleNamespace(store=store,service=service,sender=sender,now=now)

def pending(setup,phone='+79991234567',ip='127.0.0.1'):
    token,csrf=setup.service.bootstrap(ip)
    nonce=setup.service.preauth(token,csrf)
    challenge=setup.service.request_code(phone,ip,nonce,consent=True,consent_version=CONSENT_VERSION)['challengeId']
    return challenge,setup.sender.code,nonce

def rejects(status, function, *args, **kwargs):
    with pytest.raises(HTTPException) as error: function(*args, **kwargs)
    assert error.value.status_code==status
    return error.value

def test_real_phone_normalization_rejects_non_mobile_and_international():
    assert normalize_phone('8 (999) 123-45-67') == normalize_phone('+7 999 123 45 67') == normalize_phone('9991234567') == '+79991234567'
    for number in ('+18005551234','+7 351 1234567','79991234567<script>','999123456','+7４９９1234567'):
        rejects(422,normalize_phone,number)

def test_no_configuration_has_no_demo_path(setup):
    disabled=CustomerAuth(setup.store.path)
    assert disabled.enabled is False
    rejects(503,disabled.bootstrap,'ip')
    rejects(503,disabled.request_code,'+79991234567','ip','nonce')
    rejects(401,disabled.session,'anything')

def test_otp_and_session_secrets_not_stored_raw_and_code_single_use(setup):
    challenge,code,nonce=pending(setup)
    with setup.service.connect() as con:
        row=con.execute('SELECT * FROM customer_auth_codes').fetchone()
        assert row['digest']!=code and len(row['digest'])==64
    result=setup.service.verify(challenge,code,'ip',nonce)
    assert result.customer['phone']=='+79991234567'
    assert setup.service.session(result.token)['id']==result.customer['id']
    with setup.service.connect() as con:
        row=con.execute('SELECT token FROM customer_auth_sessions').fetchone()
        assert row['token']!=result.token
    rejects(400,setup.service.verify,challenge,code,'ip',nonce)

def test_expiry_at_exact_boundary(setup):
    challenge,code,nonce=pending(setup)
    setup.now[0]+=OTP_TTL
    rejects(400,setup.service.verify,challenge,code,'ip',nonce)

def test_five_failures_commit_then_even_correct_code_is_locked(setup):
    challenge,code,nonce=pending(setup)
    wrong=f'{(int(code)+1)%1000000:06d}'
    for _ in range(5): rejects(400,setup.service.verify,challenge,wrong,'ip',nonce)
    rejects(400,setup.service.verify,challenge,code,'another-ip',nonce)
    with setup.service.connect() as con:
        row=con.execute('SELECT attempts,state FROM customer_auth_codes').fetchone()
        assert (row['attempts'],row['state'])==(5,'locked')

def test_parallel_verification_consumes_once(setup):
    challenge,code,nonce=pending(setup)
    def verify(_):
        try:return setup.service.verify(challenge,code,'ip',nonce).customer['id']
        except HTTPException:return None
    with ThreadPoolExecutor(max_workers=6) as pool: results=list(pool.map(verify,range(6)))
    assert sum(x is not None for x in results)==1
    with setup.service.connect() as con:
        assert con.execute('SELECT count(*) FROM customer_auth_sessions').fetchone()[0]==1

def test_rate_limits_persist_across_instances_and_phone_format(setup):
    challenge,code,nonce=pending(setup)
    new=CustomerAuth(setup.store.path,secret='k'*40,sender=setup.sender,clock=lambda:setup.now[0])
    error=rejects(429,new.request_code,'8 999 123-45-67','different-ip',nonce,consent=True,consent_version=CONSENT_VERSION)
    assert int(error.headers['Retry-After'])>0
    setup.now[0]+=61
    replacement=new.request_code('+79991234567','ip',nonce,consent=True,consent_version=CONSENT_VERSION)['challengeId']
    rejects(400,new.verify,challenge,code,'ip',nonce)
    assert replacement != challenge

def test_send_failure_fails_closed_and_counts_rate(setup):
    class Failing:
        def send(self,*args):raise RuntimeError('secret upstream response')
    setup.service.sender=Failing()
    token,csrf=setup.service.bootstrap('ip');nonce=setup.service.preauth(token,csrf)
    error=rejects(503,setup.service.request_code,'+79991234567','ip',nonce,consent=True,consent_version=CONSENT_VERSION)
    assert error.detail=='sms_delivery_unavailable'
    with setup.service.connect() as con:
        assert con.execute('SELECT state FROM customer_auth_codes').fetchone()[0]=='failed'
    rejects(429,setup.service.request_code,'+79991234567','ip',nonce,consent=True,consent_version=CONSENT_VERSION)

def test_session_expiry_logout_key_rotation(setup):
    challenge,code,nonce=pending(setup);result=setup.service.verify(challenge,code,'ip',nonce)
    setup.service.logout(result.token);rejects(401,setup.service.session,result.token)
    setup.now[0]+=61
    challenge,code,nonce=pending(setup);result=setup.service.verify(challenge,code,'ip',nonce)
    other=CustomerAuth(setup.store.path,secret='z'*40,sender=setup.sender)
    rejects(401,other.session,result.token)
    setup.now[0]+=SESSION_TTL
    rejects(401,setup.service.session,result.token)

def test_pre_auth_bound_to_browser(setup):
    challenge,code,nonce=pending(setup)
    rejects(400,setup.service.verify,challenge,code,'ip','wrong nonce')
    assert setup.service.verify(challenge,code,'ip',nonce).customer
    token,csrf=setup.service.bootstrap('ip')
    rejects(403,setup.service.preauth,token,'несовпадающий')
    setup.now[0]+=1200
    rejects(403,setup.service.preauth,token,csrf)

def test_unknown_challenge_attempts_commit_ip_budget(setup):
    for _ in range(60):rejects(400,setup.service.verify,'unknown','000000','ip','nonce')
    rejects(429,setup.service.verify,'unknown','000000','ip','nonce')


def web(setup, enabled=True):
    app=FastAPI()
    def origin(request):
        if request.headers.get('origin')!='https://shop.test': raise HTTPException(403,'origin_rejected')
    auth=register_customer_routes(app,setup.store,origin=origin,secret='k'*40 if enabled else '',sender=setup.sender if enabled else None,clock=lambda:setup.now[0])
    return TestClient(app,base_url='https://shop.test'),auth

def login_web(client,setup):
    boot=client.get('/api/customer/auth/bootstrap').json()
    headers={'Origin':'https://shop.test','X-CSRF-Token':boot['csrf']}
    issued=client.post('/api/customer/auth/request',json={'phone':'+79991234567','consent':True,'consentVersion':CONSENT_VERSION},headers=headers).json()
    result=client.post('/api/customer/auth/verify',json={'challengeId':issued['challengeId'],'code':setup.sender.code},headers=headers)
    assert result.status_code==200
    return result

def test_http_origin_csrf_and_secure_cookie_attributes(setup):
    client,auth=web(setup)
    boot=client.get('/api/customer/auth/bootstrap');assert boot.headers['cache-control']=='no-store'
    cookie=boot.headers['set-cookie'];assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=strict' in cookie
    data={'phone':'+79991234567','consent':True,'consentVersion':CONSENT_VERSION}
    assert client.post('/api/customer/auth/request',json=data).status_code==403
    assert client.post('/api/customer/auth/request',json=data,headers={'Origin':'https://evil.test','X-CSRF-Token':boot.json()['csrf']}).status_code==403
    assert client.post('/api/customer/auth/request',json=data,headers={'Origin':'https://shop.test'}).status_code==403
    assert setup.sender.sent==[]
    result=login_web(client,setup)
    assert 'Path=/api;' in result.headers['set-cookie']
    assert client.get('/api/customer/me').json()['customer']['phone']=='+79991234567'
    assert client.patch('/api/customer/me',json={'name':'Андрей'}).status_code==403
    headers={'Origin':'https://shop.test','X-CSRF-Token':result.json()['csrf']}
    assert client.patch('/api/customer/me',json={'name':'Андрей'},headers=headers).status_code==200
    assert client.get('/api/customer/session').json()['customer']['name']=='Андрей'
    assert client.post('/api/customer/auth/logout',headers=headers).status_code==200
    assert client.get('/api/customer/me').status_code==401

def test_disabled_http_returns_no_fake_challenge(setup):
    client,_=web(setup,enabled=False)
    assert client.get('/api/customer/auth/bootstrap').json()=={'available':False}
    result=client.post('/api/customer/auth/request',json={'phone':'+79991234567','consent':True,'consentVersion':CONSENT_VERSION},headers={'Origin':'https://shop.test'})
    assert result.status_code==503 and result.json()['detail']=='sms_not_configured'

def test_owned_orders_explicit_account_id_and_no_private_fields(setup):
    client,_=web(setup);result=login_web(client,setup);owner=result.json()['customer']['id']
    for i,account_id in enumerate((owner,'another-account',None)):
        payload={'id':f'O{i}','status':'received','createdAt':1,'updatedAt':2,'total':1000,'customerAccountId':account_id,
                 '_key':'private','_token':'secret','purchasePrice':400,'lines':[{'name':'Шина','sku':'ABC','quantity':1,'subtotal':1000,'supplierCost':400}]}
        with setup.store.connect() as con:
            con.execute('INSERT INTO customer_orders VALUES (?,?,?,?,?,?)',(str(i),str(i),str(i),str(i),json.dumps(payload),i))
    response=client.get('/api/customer/orders');assert response.status_code==200
    assert [r['id'] for r in response.json()['orders']]==['O0']
    assert not any(x in response.text for x in ('secret','private','supplierCost','purchasePrice'))

def test_global_budget_prevents_rotating_phone_ip_sms_abuse(setup):
    # Persisted rate budget at capacity: no provider call should occur.
    with setup.service.connect() as con:
        con.executemany('INSERT INTO customer_auth_rates VALUES (?,?,?)', [('send_global_hour',setup.service.digest('rate','all'),setup.now[0])]*100)
    rejects(429,setup.service.request_code,'+79991234567','new-ip','nonce',consent=True,consent_version=CONSENT_VERSION)
    assert setup.sender.sent==[]

def test_custom_bridge_rejects_insecure_or_credential_urls():
    for url in ('http://sms.test','https://user:pass@sms.test','https://sms.test?api=secret','https://sms.test#foo'):
        with pytest.raises(ValueError):CustomHttpsSmsBridge(url,'token')


@pytest.mark.parametrize('extra', [{}, {'consent': False, 'consentVersion': CONSENT_VERSION},
    {'consent': 1, 'consentVersion': CONSENT_VERSION}, {'consent': True},
    {'consent': True, 'consentVersion': 'shop-0.7.0'}])
def test_http_requires_explicit_current_consent_before_processing_phone(setup, extra):
    client, auth = web(setup)
    boot = client.get('/api/customer/auth/bootstrap').json()
    headers = {'Origin': 'https://shop.test', 'X-CSRF-Token': boot['csrf']}
    result = client.post('/api/customer/auth/request', json={'phone': '+79991234567', **extra}, headers=headers)
    assert result.status_code == 422
    assert setup.sender.sent == []
    with auth.connect() as con:
        assert con.execute('SELECT count(*) FROM customer_auth_codes').fetchone()[0] == 0
        assert con.execute('SELECT count(*) FROM customer_privacy_consents').fetchone()[0] == 0


def test_consent_service_guard_and_verified_account_journal(setup):
    rejects(422, setup.service.request_code, '+79991234567', 'ip', 'nonce')
    assert setup.sender.sent == []
    challenge, code, nonce = pending(setup)
    with setup.service.connect() as con:
        consent = dict(con.execute('SELECT * FROM customer_privacy_consents').fetchone())
    assert consent['version'] == CONSENT_VERSION
    assert consent['purpose'] == 'account_sms_login'
    assert consent['accepted_at'] == setup.now[0] and consent['verified_at'] is None
    assert consent['customer_id'] is None
    assert len(consent['phone_digest']) == 64 and '+79991234567' not in str(consent)
    setup.now[0] += 3
    result = setup.service.verify(challenge, code, 'ip', nonce)
    with setup.service.connect() as con:
        consent = dict(con.execute('SELECT * FROM customer_privacy_consents').fetchone())
    assert consent['customer_id'] == result.customer['id']
    assert consent['verified_at'] == setup.now[0]
    assert consent['accepted_at'] == setup.now[0] - 3


def test_old_pending_challenge_without_consent_cannot_create_account(setup):
    challenge, code, nonce = pending(setup)
    with setup.service.connect() as con:
        con.execute('DELETE FROM customer_privacy_consents WHERE challenge_id=?', (challenge,))
    rejects(400, setup.service.verify, challenge, code, 'ip', nonce)
    with setup.service.connect() as con:
        assert con.execute('SELECT count(*) FROM customer_accounts').fetchone()[0] == 0


def test_unverified_consent_cleanup_does_not_remove_account_history(setup):
    challenge, code, nonce = pending(setup)
    setup.service.verify(challenge, code, 'ip', nonce)
    pending(setup, phone='+79991234568')
    setup.now[0] += 86401
    setup.service.bootstrap('ip')
    with setup.service.connect() as con:
        rows = con.execute('SELECT customer_id FROM customer_privacy_consents').fetchall()
    assert len(rows) == 1 and rows[0]['customer_id'] is not None
