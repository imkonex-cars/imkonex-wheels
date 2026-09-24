"""Durable inbound SOAP signals, not payment or shipment authority.

The provider documents an OrderNumber -> boolean callback selected by its WSDL.
HTTP Basic and our OrderChanged contract are deployment choices to validate with
the provider. No external calls or order mutations are performed by this receiver.
"""
import base64
import hmac
import os
import re
import time
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
from lxml import etree

NS = 'urn:imkonex:fourtochki:notifications:v1'
SOAP11 = 'http://schemas.xmlsoap.org/soap/envelope/'
SOAP12 = 'http://www.w3.org/2003/05/soap-envelope'
PROFILES = {'alites': 'ALITES', 'inveks': 'INVEKS'}


def parse_signal(raw):
    if not raw or len(raw) > 16384 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('invalid_soap')
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True,
                             huge_tree=False, recover=False)
    try:
        root = etree.fromstring(raw, parser)
    except etree.XMLSyntaxError:
        raise ValueError('invalid_soap') from None
    envelope = etree.QName(root)
    if envelope.localname != 'Envelope' or envelope.namespace not in {SOAP11, SOAP12}:
        raise ValueError('invalid_soap')
    # Detect DTDs regardless of source encoding (including UTF-16).
    if root.getroottree().docinfo.doctype:
        raise ValueError('invalid_soap')
    children = list(root)
    bodies = root.findall(f'{{{envelope.namespace}}}Body')
    if len(bodies) != 1 or len(children) != 1 or len(bodies[0]) != 1:
        raise ValueError('invalid_soap')
    operation = bodies[0][0]
    if operation.tag != f'{{{NS}}}OrderChanged' or len(operation) != 1:
        raise ValueError('invalid_soap')
    number = operation[0]
    if number.tag != f'{{{NS}}}OrderNumber' or len(number):
        raise ValueError('invalid_soap')
    value = number.text or ''
    if not re.fullmatch(r'[A-Za-zА-Яа-яЁё0-9/_-]{1,10}', value):
        raise ValueError('invalid_order_number')
    return envelope.namespace, value


def wsdl(endpoint):
    return f'''<?xml version="1.0" encoding="utf-8"?>
<wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/" xmlns:tns="{NS}" targetNamespace="{NS}" name="ImkonexNotifications">
<wsdl:types><xsd:schema targetNamespace="{NS}" elementFormDefault="qualified">
<xsd:element name="OrderChanged"><xsd:complexType><xsd:sequence><xsd:element name="OrderNumber"><xsd:simpleType><xsd:restriction base="xsd:string"><xsd:minLength value="1"/><xsd:maxLength value="10"/></xsd:restriction></xsd:simpleType></xsd:element></xsd:sequence></xsd:complexType></xsd:element>
<xsd:element name="OrderChangedResponse"><xsd:complexType><xsd:sequence><xsd:element name="OrderChangedResult" type="xsd:boolean"/></xsd:sequence></xsd:complexType></xsd:element>
</xsd:schema></wsdl:types>
<wsdl:message name="OrderChangedInput"><wsdl:part name="parameters" element="tns:OrderChanged"/></wsdl:message><wsdl:message name="OrderChangedOutput"><wsdl:part name="parameters" element="tns:OrderChangedResponse"/></wsdl:message>
<wsdl:portType name="NotificationPortType"><wsdl:operation name="OrderChanged"><wsdl:input message="tns:OrderChangedInput"/><wsdl:output message="tns:OrderChangedOutput"/></wsdl:operation></wsdl:portType>
<wsdl:binding name="NotificationBinding" type="tns:NotificationPortType"><soap:binding transport="http://schemas.xmlsoap.org/soap/http" style="document"/><wsdl:operation name="OrderChanged"><soap:operation soapAction="{NS}/OrderChanged"/><wsdl:input><soap:body use="literal"/></wsdl:input><wsdl:output><soap:body use="literal"/></wsdl:output></wsdl:operation></wsdl:binding>
<wsdl:service name="ImkonexNotificationService"><wsdl:port name="NotificationPort" binding="tns:NotificationBinding"><soap:address location="{escape(endpoint, {'"': '&quot;'})}"/></wsdl:port></wsdl:service></wsdl:definitions>'''


def register_notification_routes(app, store, *, authenticated, throttle):
    with store.connect() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS supplier_notifications(
            profile TEXT NOT NULL, order_number TEXT NOT NULL, revision INTEGER NOT NULL,
            first_seen REAL NOT NULL, last_seen REAL NOT NULL, status TEXT NOT NULL,
            PRIMARY KEY(profile,order_number))''')

    def authorize(profile, request):
        prefix = PROFILES.get(profile)
        if not prefix or os.getenv('SUPPLIER_NOTIFICATIONS_ENABLED') != 'true':
            raise HTTPException(503, 'notifications_not_configured')
        username = os.getenv(f'FOURTOCHKI_{prefix}_NOTIFY_USER', '')
        password = os.getenv(f'FOURTOCHKI_{prefix}_NOTIFY_PASSWORD', '')
        if not username or len(password) < 24:
            raise HTTPException(503, 'notifications_not_configured')
        throttle(request, 'notification_auth', 120)
        try:
            scheme, encoded = request.headers.get('authorization', '').split(' ', 1)
            if scheme.lower() != 'basic': raise ValueError()
            supplied = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            supplied = b''
        expected = (username + ':' + password).encode('utf-8')
        if not hmac.compare_digest(expected, supplied):
            raise HTTPException(401, 'notification_auth_required', headers={'WWW-Authenticate': 'Basic realm="IMKONEX"'})

    @app.get('/api/provider-notifications/{profile}')
    @app.get('/api/provider-notifications/{profile}/wsdl')
    def schema(profile: str, request: Request):
        authorize(profile, request)
        origin = os.getenv('PUBLIC_ORIGIN', '').rstrip('/')
        parsed = urlsplit(origin)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise HTTPException(503, 'notification_origin_not_configured')
        return Response(wsdl(origin + '/api/provider-notifications/' + profile), media_type='text/xml')

    @app.post('/api/provider-notifications/{profile}')
    async def receive(profile: str, request: Request):
        authorize(profile, request)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 16384: raise HTTPException(413, 'notification_too_large')
        try:
            soap, number = parse_signal(bytes(raw))
        except (ValueError, etree.XMLSyntaxError):
            raise HTTPException(400, 'invalid_notification') from None
        # A later signal for the same order is new work. Do not permanently
        # deduplicate on OrderNumber: all status updates use the same number.
        now = time.time()
        with store.connect() as con:
            con.execute('''INSERT INTO supplier_notifications VALUES(?,?,1,?,?,'pending')
                ON CONFLICT(profile,order_number) DO UPDATE SET
                revision=revision+1,last_seen=excluded.last_seen,status='pending' ''',
                (profile, number, now, now))
        payload = f'<s:Envelope xmlns:s="{soap}"><s:Body><OrderChangedResponse xmlns="{NS}"><OrderChangedResult>true</OrderChangedResult></OrderChangedResponse></s:Body></s:Envelope>'
        return Response(payload, media_type='application/soap+xml' if soap == SOAP12 else 'text/xml')

    @app.get('/api/manager/provider-notifications')
    def pending(session=Depends(authenticated)):
        with store.connect() as con:
            rows = con.execute('SELECT * FROM supplier_notifications ORDER BY last_seen DESC LIMIT 100').fetchall()
        return {'items': [dict(r) for r in rows], 'processorEnabled': False,
                'note': 'Сигналы сохранены. Автоматическая сверка и изменение заказов ещё не подключены.'}
