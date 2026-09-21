"""Exercise Zeep's real WSDL/XSD parser through our restricted transport."""
import requests
import pytest
from zeep.exceptions import DTDForbidden, ExternalReferenceForbidden
from zeep import Settings, Client

from backend.supplier import HOST, WSDL, SupplierTransport, create_client
from backend.supplier_check import create_check_client


SCHEMA_URL = f'https://{HOST}/WCF/ClientService.svc?xsd=xsd0'
SCHEMA = b'''<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema"
  targetNamespace="urn:imkonex:check" elementFormDefault="qualified">
  <xsd:element name="GetWarehouses"><xsd:complexType><xsd:sequence>
    <xsd:element name="login" type="xsd:string"/>
    <xsd:element name="password" type="xsd:string"/>
  </xsd:sequence></xsd:complexType></xsd:element>
  <xsd:element name="GetWarehousesResponse" type="xsd:string"/>
</xsd:schema>'''


def wsdl(schema_url):
    return f'''<wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
      xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
      xmlns:xsd="http://www.w3.org/2001/XMLSchema"
      xmlns:tns="urn:imkonex:check" targetNamespace="urn:imkonex:check">
      <wsdl:types><xsd:schema><xsd:import namespace="urn:imkonex:check"
        schemaLocation="{schema_url}"/></xsd:schema></wsdl:types>
      <wsdl:message name="Request"><wsdl:part name="parameters" element="tns:GetWarehouses"/></wsdl:message>
      <wsdl:message name="Response"><wsdl:part name="parameters" element="tns:GetWarehousesResponse"/></wsdl:message>
      <wsdl:portType name="ClientService"><wsdl:operation name="GetWarehouses">
        <wsdl:input message="tns:Request"/><wsdl:output message="tns:Response"/>
      </wsdl:operation></wsdl:portType>
      <wsdl:binding name="Binding" type="tns:ClientService">
        <soap:binding style="document" transport="http://schemas.xmlsoap.org/soap/http"/>
        <wsdl:operation name="GetWarehouses"><soap:operation soapAction="urn:GetWarehouses"/>
          <wsdl:input><soap:body use="literal"/></wsdl:input>
          <wsdl:output><soap:body use="literal"/></wsdl:output>
        </wsdl:operation>
      </wsdl:binding>
      <wsdl:service name="ClientService"><wsdl:port name="Port" binding="tns:Binding">
        <soap:address location="http://{HOST}/WCF/ClientService.svc"/>
      </wsdl:port></wsdl:service>
    </wsdl:definitions>'''.encode()


def serve_documents(monkeypatch, initial):
    monkeypatch.setattr('backend.supplier.InMemoryCache', lambda **kwargs: None)
    requested = []
    documents = {WSDL: initial, SCHEMA_URL: SCHEMA}
    def send(session, request, **kwargs):
        requested.append(request.url)
        assert request.method == 'GET'
        assert request.url in documents, 'Unexpected network destination'
        assert request.url.startswith('https://')
        assert session.verify is True
        assert kwargs.get('verify') is not False
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response.request = request
        response.headers['Content-Type'] = 'text/xml; charset=utf-8'
        response._content = documents[request.url]
        response._content_consumed = True
        return response
    monkeypatch.setattr(requests.Session, 'send', send)
    return requested


def test_reproduces_original_failure_before_loading_import(monkeypatch):
    requested = serve_documents(monkeypatch, wsdl(SCHEMA_URL))
    with pytest.raises(ExternalReferenceForbidden):
        Client(WSDL, transport=SupplierTransport(), settings=Settings(
            forbid_dtd=True, forbid_entities=True, forbid_external=True))
    assert requested == [WSDL]


@pytest.mark.parametrize('factory', [create_client, create_check_client])
def test_loads_supplier_schema_with_https_and_builds_typed_request(monkeypatch, factory):
    requested = serve_documents(monkeypatch, wsdl(SCHEMA_URL.replace('https:', 'http:')))
    client = factory()
    message = client.create_message(client.service, 'GetWarehouses', login='test-login', password='test-password')
    assert message.find('.//{urn:imkonex:check}login').text == 'test-login'
    assert requested == [WSDL, SCHEMA_URL]


@pytest.mark.parametrize('factory', [create_client, create_check_client])
@pytest.mark.parametrize('schema_url', ['https://unrelated.invalid/schema.xsd', 'file:///private/local.xsd'])
def test_blocks_unrelated_domains_and_local_files(monkeypatch, factory, schema_url):
    requested = serve_documents(monkeypatch, wsdl(schema_url))
    with pytest.raises(ValueError, match='Unexpected supplier endpoint'):
        factory()
    assert requested == [WSDL]


@pytest.mark.parametrize('factory', [create_client, create_check_client])
def test_dtd_and_entity_protection_remains_enabled(monkeypatch, factory):
    initial = b'<!DOCTYPE definitions [<!ENTITY forbidden SYSTEM "https://unrelated.invalid/entity">]>' + wsdl(SCHEMA_URL)
    requested = serve_documents(monkeypatch, initial)
    with pytest.raises(DTDForbidden):
        factory()
    assert requested == [WSDL]
