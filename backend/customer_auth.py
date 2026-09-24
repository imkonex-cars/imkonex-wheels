"""Phone OTP customer authentication; no demo codes or vendor credentials.
See docs/CUSTOMER_SMS_AUTH.md for integration and our custom SMS bridge contract.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from typing import Literal, Protocol
from urllib.parse import urlsplit
import requests
from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

COOKIE = 'imkonex_customer'
PREAUTH_COOKIE = 'imkonex_customer_login'
OTP_TTL, SESSION_TTL, PREAUTH_TTL = 300, 30 * 86400, 1200
CONSENT_VERSION = '2026-09-24'

class SmsSender(Protocol):
    def send(self, phone: str, message: str, idempotency_key: str) -> None:
        """Return only when accepted; raise on rejection or uncertain outcome."""

class SmsDeliveryError(Exception):
    pass

class CustomHttpsSmsBridge:
    """Our explicit bridge contract, NOT a direct adapter for an SMS vendor."""
    def __init__(self, url, token):
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query or not token:
            raise ValueError('Invalid custom SMS bridge configuration')
        self.url, self.token = url, token

    def send(self, phone, message, idempotency_key):
        try:
            result = requests.post(self.url, json={'phone': phone, 'message': message},
                headers={'Authorization': 'Bearer ' + self.token, 'Idempotency-Key': idempotency_key},
                timeout=(5, 15), allow_redirects=False)
            if result.status_code not in (200, 202) or len(result.content) > 16384 or result.json().get('accepted') is not True:
                raise SmsDeliveryError()
        except (requests.RequestException, ValueError, AttributeError):
            raise SmsDeliveryError() from None


def normalize_phone(value):
    if not isinstance(value, str) or not re.fullmatch(r'[+0-9 ()-]{10,32}', value):
        raise HTTPException(422, 'invalid_phone')
    number = re.sub(r'[^0-9]', '', value)
    if len(number) == 10:
        number = '7' + number
    elif len(number) == 11 and number[0] == '8':
        number = '7' + number[1:]
    if not re.fullmatch(r'79[0-9]{9}', number):
        raise HTTPException(422, 'invalid_phone')
    return '+' + number

@dataclass
class AuthResult:
    customer: dict
    token: str
    csrf: str

class CustomerAuth:
    def __init__(self, path, *, secret=None, sender=None, clock=time.time):
        self.path, self.clock = path, clock
        self.secret = (secret if secret is not None else os.getenv('CUSTOMER_AUTH_SECRET', '')).encode()
        self.sender = sender
        provider = os.getenv('SMS_PROVIDER', '')
        if self.sender is None and provider == 'smsru' and os.getenv('SMS_RU_API_ID'):
            from .sms_ru import SmsRuSender
            self.sender = SmsRuSender(os.environ['SMS_RU_API_ID'], os.getenv('SMS_RU_SENDER', ''))
        elif self.sender is None and provider in ('', 'custom') and os.getenv('CUSTOMER_SMS_BRIDGE_URL'):
            try:
                self.sender = CustomHttpsSmsBridge(os.environ['CUSTOMER_SMS_BRIDGE_URL'], os.getenv('CUSTOMER_SMS_BRIDGE_TOKEN', ''))
            except ValueError:
                pass
        self.enabled = len(self.secret) >= 32 and self.sender is not None
        with self.connect() as con:
            con.executescript('''
              CREATE TABLE IF NOT EXISTS customer_login_nonces(token TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS customer_auth_rates(scope TEXT NOT NULL, subject TEXT NOT NULL, created REAL NOT NULL);
              CREATE INDEX IF NOT EXISTS customer_auth_rate_idx ON customer_auth_rates(scope,subject,created);
              CREATE TABLE IF NOT EXISTS customer_auth_codes(id TEXT PRIMARY KEY, phone TEXT NOT NULL, digest TEXT NOT NULL,
                nonce TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL);
              CREATE INDEX IF NOT EXISTS customer_auth_phone_idx ON customer_auth_codes(phone,created);
              CREATE TABLE IF NOT EXISTS customer_accounts(id TEXT PRIMARY KEY, phone TEXT UNIQUE NOT NULL, name TEXT NOT NULL DEFAULT '',
                created REAL NOT NULL, updated REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS customer_auth_sessions(token TEXT PRIMARY KEY, customer_id TEXT NOT NULL,
                csrf TEXT NOT NULL, expires REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS customer_privacy_consents(challenge_id TEXT PRIMARY KEY,
                customer_id TEXT, phone_digest TEXT NOT NULL, purpose TEXT NOT NULL, version TEXT NOT NULL,
                accepted_at REAL NOT NULL, verified_at REAL);
              CREATE INDEX IF NOT EXISTS customer_privacy_consent_customer_idx ON customer_privacy_consents(customer_id);
            ''')

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        try:
            con.execute('BEGIN IMMEDIATE')
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def digest(self, purpose, text):
        return hmac.new(self.secret, (purpose + ':' + text).encode(), hashlib.sha256).hexdigest()

    def available(self):
        if not self.enabled:
            raise HTTPException(503, 'sms_not_configured')

    def _rate(self, con, scope, subject, limit, seconds):
        now = self.clock()
        hashed = self.digest('rate', subject)
        times = con.execute('SELECT created FROM customer_auth_rates WHERE scope=? AND subject=? AND created>? ORDER BY created',
                            (scope, hashed, now-seconds)).fetchall()
        if len(times) >= limit:
            raise HTTPException(429, 'too_many_requests', headers={'Retry-After': str(max(1, int(times[0]['created']+seconds-now)+1))})
        con.execute('INSERT INTO customer_auth_rates VALUES (?,?,?)', (scope, hashed, now))

    def bootstrap(self, ip):
        self.available()
        token, csrf, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), self.clock()
        with self.connect() as con:
            con.execute('DELETE FROM customer_auth_rates WHERE created < ?', (now-86400,))
            con.execute('DELETE FROM customer_login_nonces WHERE expires <= ?', (now,))
            con.execute('DELETE FROM customer_auth_codes WHERE expires < ?', (now-86400,))
            con.execute('DELETE FROM customer_privacy_consents WHERE customer_id IS NULL AND accepted_at < ?', (now-86400,))
            con.execute('DELETE FROM customer_auth_sessions WHERE expires <= ?', (now,))
            self._rate(con, 'bootstrap', ip, 60, 3600)
            con.execute('INSERT INTO customer_login_nonces VALUES (?,?,?)', (self.digest('nonce', token), csrf, now+PREAUTH_TTL))
        return token, csrf

    def preauth(self, token, csrf):
        self.available()
        with self.connect() as con:
            row = con.execute('SELECT csrf FROM customer_login_nonces WHERE token=? AND expires>?',
                              (self.digest('nonce', token), self.clock())).fetchone()
        if not row or not hmac.compare_digest(row['csrf'].encode(), csrf.encode()):
            raise HTTPException(403, 'csrf_rejected')
        return self.digest('nonce', token)

    def request_code(self, phone, ip, nonce, *, consent=False, consent_version=''):
        self.available()
        # A submitted checkbox and its exact document version are required before SMS processing.
        if consent is not True or consent_version != CONSENT_VERSION:
            raise HTTPException(422, 'consent_required')
        phone, now = normalize_phone(phone), self.clock()
        code, challenge = f'{secrets.randbelow(1000000):06d}', secrets.token_urlsafe(24)
        with self.connect() as con:
            self._rate(con, 'send_global_hour', 'all', 100, 3600)
            self._rate(con, 'send_global_day', 'all', 500, 86400)
            self._rate(con, 'send_phone_minute', phone, 1, 60)
            self._rate(con, 'send_phone_hour', phone, 5, 3600)
            self._rate(con, 'send_phone_day', phone, 10, 86400)
            self._rate(con, 'send_ip_hour', ip, 20, 3600)
            con.execute("UPDATE customer_auth_codes SET state='superseded' WHERE phone=? AND state IN ('pending','sent')", (phone,))
            con.execute('INSERT INTO customer_auth_codes(id,phone,digest,nonce,state,created,expires) VALUES (?,?,?,?,?,?,?)',
                        (challenge, phone, self.digest('otp', challenge+':'+code), nonce, 'pending', now, now+OTP_TTL))
            con.execute('INSERT INTO customer_privacy_consents(challenge_id,phone_digest,purpose,version,accepted_at) VALUES (?,?,?,?,?)',
                        (challenge, self.digest('consent-phone', phone), 'account_sms_login', consent_version, now))
        try:
            self.sender.send(phone, f'IMKONEX CARS: код входа {code}. Действует 5 минут. Никому не сообщайте код.', challenge)
        except Exception:
            with self.connect() as con:
                con.execute("UPDATE customer_auth_codes SET state='failed' WHERE id=? AND state='pending'", (challenge,))
            raise HTTPException(503, 'sms_delivery_unavailable') from None
        with self.connect() as con:
            changed = con.execute("UPDATE customer_auth_codes SET state='sent' WHERE id=? AND state='pending'", (challenge,)).rowcount
        if not changed:
            raise HTTPException(409, 'code_superseded')
        return {'challengeId': challenge, 'expiresIn': OTP_TTL, 'retryAfter': 60}

    def verify(self, challenge, code, ip, nonce):
        self.available()
        now, failure, result = self.clock(), None, None
        with self.connect() as con:
            self._rate(con, 'verify_ip_hour', ip, 60, 3600)
            row = con.execute('SELECT * FROM customer_auth_codes WHERE id=?', (challenge,)).fetchone()
            consent = con.execute('SELECT version FROM customer_privacy_consents WHERE challenge_id=?', (challenge,)).fetchone()
            if not row or not consent or consent['version'] != CONSENT_VERSION or row['nonce'] != nonce or row['state'] != 'sent' or row['expires'] <= now or row['attempts'] >= 5:
                failure = 'invalid_or_expired_code'
            else:
                correct = hmac.compare_digest(row['digest'], self.digest('otp', challenge+':'+code))
                con.execute('UPDATE customer_auth_codes SET attempts=attempts+1 WHERE id=?', (challenge,))
                if not correct:
                    failure = 'invalid_or_expired_code'
                    if row['attempts'] >= 4:
                        con.execute("UPDATE customer_auth_codes SET state='locked' WHERE id=?", (challenge,))
                else:
                    con.execute("UPDATE customer_auth_codes SET state='used' WHERE id=?", (challenge,))
                    con.execute('INSERT INTO customer_accounts(id,phone,created,updated) VALUES (?,?,?,?) ON CONFLICT(phone) DO NOTHING',
                                (secrets.token_urlsafe(18), row['phone'], now, now))
                    customer = dict(con.execute('SELECT id,phone,name FROM customer_accounts WHERE phone=?', (row['phone'],)).fetchone())
                    con.execute('UPDATE customer_privacy_consents SET customer_id=?,verified_at=? WHERE challenge_id=?',
                                (customer['id'], now, challenge))
                    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                    con.execute('INSERT INTO customer_auth_sessions VALUES (?,?,?,?)',
                                (self.digest('session', token), customer['id'], csrf, now+SESSION_TTL))
                    result = AuthResult(customer, token, csrf)
        # Failures MUST commit counters before raising, including invalid challenge IDs.
        if failure:
            raise HTTPException(400, failure)
        return result

    def session(self, token):
        if not self.enabled or not token:
            raise HTTPException(401, 'customer_login_required')
        with self.connect() as con:
            row = con.execute('SELECT a.id,a.phone,a.name,s.csrf FROM customer_auth_sessions s JOIN customer_accounts a ON a.id=s.customer_id '
                              'WHERE s.token=? AND s.expires>?', (self.digest('session', token), self.clock())).fetchone()
        if not row:
            raise HTTPException(401, 'customer_login_required')
        return dict(row)

    def logout(self, token):
        with self.connect() as con:
            con.execute('DELETE FROM customer_auth_sessions WHERE token=?', (self.digest('session', token),))

    def profile(self, customer_id, name):
        with self.connect() as con:
            con.execute('UPDATE customer_accounts SET name=?,updated=? WHERE id=?', (name.strip(), self.clock(), customer_id))

    def orders(self, customer_id):
        with self.connect() as con:
            rows = con.execute('SELECT payload FROM customer_orders WHERE json_extract(payload,\'$.customerAccountId\')=? ORDER BY created DESC LIMIT 100', (customer_id,)).fetchall()
        # Explicit projection prevents leaking supplier identifiers, costs, tokens or client details.
        result = []
        for row in rows:
            order = json.loads(row['payload'])
            result.append({**{k: order.get(k) for k in ('id','status','createdAt','updatedAt','total')},
                'lines': [{k: line.get(k) for k in ('name','sku','quantity','subtotal')} for line in order.get('lines', [])]})
        return result

class PhoneInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    phone: str = Field(min_length=10, max_length=32)
    consent: Literal[True]
    consentVersion: Literal['2026-09-24']

    @field_validator('consent', mode='before')
    @classmethod
    def affirmative_consent(cls, value):
        if value is not True:
            raise ValueError('consent_required')
        return value

class CodeInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    challengeId: str = Field(min_length=20, max_length=64)
    code: str = Field(pattern=r'^[0-9]{6}$')

class ProfileInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(max_length=100)


def register_customer_routes(app, store, *, origin, secure_cookie=True, sender=None, secret=None, clock=time.time):
    """origin(request) must reject mutations not coming from PUBLIC_ORIGIN."""
    service = CustomerAuth(store.path, secret=secret, sender=sender, clock=clock)
    app.state.customer_auth = service

    def private(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Robots-Tag'] = 'noindex, nofollow'

    def ip(request):
        # Never trust client-supplied forwarded headers. Configure uvicorn trusted proxies.
        return request.client.host if request.client else 'unknown'

    def login_guard(request):
        origin(request)
        return service.preauth(request.cookies.get(PREAUTH_COOKIE, ''), request.headers.get('x-csrf-token', ''))

    def current(request, required=False):
        try:
            session = service.session(request.cookies.get(COOKIE, ''))
        except HTTPException:
            if required:
                raise
            return None
        if request.method not in ('GET', 'HEAD'):
            origin(request)
            if not hmac.compare_digest(session['csrf'].encode(), request.headers.get('x-csrf-token', '').encode()):
                raise HTTPException(403, 'csrf_rejected')
        return session
    service.current = current

    @app.get('/api/customer/auth/bootstrap')
    def bootstrap(request: Request, response: Response):
        private(response)
        if not service.enabled:
            return {'available': False}
        token, csrf = service.bootstrap(ip(request))
        response.set_cookie(PREAUTH_COOKIE, token, max_age=PREAUTH_TTL, httponly=True, secure=secure_cookie, samesite='strict', path='/api/customer')
        return {'available': True, 'csrf': csrf}

    @app.post('/api/customer/auth/request')
    def request_code(body: PhoneInput, request: Request, response: Response):
        private(response)
        return service.request_code(body.phone, ip(request), login_guard(request),
                                    consent=body.consent, consent_version=body.consentVersion)

    @app.post('/api/customer/auth/verify')
    def verify(body: CodeInput, request: Request, response: Response):
        private(response)
        result = service.verify(body.challengeId, body.code, ip(request), login_guard(request))
        if request.cookies.get(COOKIE):
            service.logout(request.cookies[COOKIE])
        response.set_cookie(COOKIE, result.token, max_age=SESSION_TTL, httponly=True, secure=secure_cookie, samesite='strict', path='/api')
        response.delete_cookie(PREAUTH_COOKIE, path='/api/customer', secure=secure_cookie, httponly=True, samesite='strict')
        return {'customer': result.customer, 'csrf': result.csrf}

    @app.get('/api/customer/me')
    @app.get('/api/customer/session')
    def me(request: Request, response: Response):
        private(response)
        session = current(request, required=True)
        return {'customer': {k: session[k] for k in ('id', 'phone', 'name')}, 'csrf': session['csrf']}

    @app.patch('/api/customer/me')
    def profile(body: ProfileInput, request: Request, response: Response):
        private(response)
        service.profile(current(request, required=True)['id'], body.name)
        return {'saved': True}

    @app.get('/api/customer/orders')
    def orders(request: Request, response: Response):
        private(response)
        return {'orders': service.orders(current(request, required=True)['id'])}

    @app.post('/api/customer/auth/logout')
    def logout(request: Request, response: Response):
        private(response)
        current(request, required=True)
        service.logout(request.cookies.get(COOKIE, ''))
        response.delete_cookie(COOKIE, path='/api', secure=secure_cookie, httponly=True, samesite='strict')
        return {'loggedOut': True}

    return service
