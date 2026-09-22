"""4tochki SOAP inspection and bounded read-only probe.

Field mappings are deliberately supplied from an inspected WSDL, not guessed.
No supplier ordering method can be called through this adapter.
"""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit
import requests
from zeep import Client,Settings
from zeep.cache import InMemoryCache
from zeep.transports import Transport

HOST='api-b2b.4tochki.ru'
WSDL=f'https://{HOST}/WCF/ClientService.svc?wsdl'
READ_OPERATIONS=frozenset({'GetFindTyre','GetFindDisk','GetFindCamera','GetGoodsPriceRestByCode','GetGoodsInfo','GetGoodsByCar','GetMarkaAvto','GetModelAvto','GetYearAvto','GetModificationAvto','GetWarehouses','GetDeliveryPeriod','GetPrice','GetRest'})

def secure_url(url):
    parsed=urlsplit(url)
    if parsed.scheme not in ('http','https') or parsed.hostname!=HOST or parsed.username or parsed.password or parsed.port not in (None,80,443):
        raise ValueError('Unexpected supplier endpoint')
    return urlunsplit(('https',HOST,parsed.path,parsed.query,''))

class SupplierTransport(Transport):
    def load(self,url):
        return super().load(secure_url(url))
    def post_xml(self,address,envelope,headers):
        return super().post_xml(secure_url(address),envelope,headers)
    def _load_remote_data(self,url):
        # Disallow redirects that could send a request to another host.
        response=self.session.get(secure_url(url),timeout=self.load_timeout,allow_redirects=False)
        if response.is_redirect: raise ValueError('Supplier WSDL redirect requires review')
        response.raise_for_status()
        if len(response.content)>10_000_000: raise ValueError('Oversized WSDL document')
        return response.content
    def post(self,address,message,headers):
        response=self.session.post(secure_url(address),data=message,headers=headers,timeout=self.operation_timeout,allow_redirects=False)
        if response.is_redirect: raise ValueError('Supplier redirect requires review')
        return response

def create_client():
    session=requests.Session()
    session.verify=True
    transport=SupplierTransport(session=session,cache=InMemoryCache(timeout=3600),timeout=20,operation_timeout=25)
    # WSDL imports require external schema loading. SupplierTransport restricts
    # every imported URL to the supplier host over verified HTTPS.
    return Client(WSDL,transport=transport,settings=Settings(strict=True,xml_huge_tree=False,forbid_dtd=True,forbid_entities=True,forbid_external=False))

def resolve_tokens(value):
    if isinstance(value,dict): return {k:resolve_tokens(v) for k,v in value.items()}
    if isinstance(value,list): return [resolve_tokens(v) for v in value]
    if value in ('$LOGIN','$PASSWORD'):
        key='FOURTOCHKI_LOGIN' if value=='$LOGIN' else 'FOURTOCHKI_PASSWORD'
        if not os.getenv(key): raise ValueError(f'{key} is not configured')
        return os.environ[key]
    return value

def probe(client,operation,arguments):
    if operation not in READ_OPERATIONS:
        raise ValueError('Operation is not on the read-only allowlist')
    return getattr(client.service,operation)(**resolve_tokens(arguments))

def main():
    p=argparse.ArgumentParser(description='Read-only 4tochki diagnostics; no ordering support')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('inspect')
    test=sub.add_parser('probe');test.add_argument('operation',choices=sorted(READ_OPERATIONS));test.add_argument('arguments',type=Path)
    args=p.parse_args()
    try:
        client=create_client()
        if args.command=='inspect':
            for service in client.wsdl.services.values():
                for port in service.ports.values():
                    print(f'{service.name} / {port.name}')
                    for name,operation in sorted(port.binding._operations.items()):
                        if name in READ_OPERATIONS:
                            print(f'{name}({operation.input.signature()}) -> {operation.output.signature()}')
        else:
            result=probe(client,args.operation,json.loads(args.arguments.read_text()))
            # Neither raw responses nor supplier exceptions are printed: they can contain secrets.
            print(json.dumps({'operation':args.operation,'transportSucceeded':True,'responseType':type(result).__name__,'businessSuccess':'not evaluated; inspect and implement the provider response mapping'},ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'status':'failed','errorType':type(error).__name__,'message':'Check WSDL availability, credentials and method schema. Sensitive details omitted.'}))
        raise SystemExit(1) from None

if __name__=='__main__': main()
