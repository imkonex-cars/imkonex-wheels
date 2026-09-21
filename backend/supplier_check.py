"""Read-only, local 4tochki diagnostics. Never imports or publishes a feed."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import getpass
import json
import os
from pathlib import Path
import re
import time

import requests
from zeep import Client, Settings, xsd
from zeep.helpers import serialize_object

from .supplier import READ_OPERATIONS, WSDL, SupplierTransport, probe, secure_url


class MappingRequired(ValueError):
    pass


class CheckTransport(SupplierTransport):
    """Bound the complete check, each connection, and each response."""
    def __init__(self):
        super().__init__(timeout=15, operation_timeout=25)
        self.deadline = time.monotonic() + 180
        self.documents = 0
        self.session.headers['User-Agent'] = 'IMKONEX-4TOCHKI-CHECK/1.2'

    def request(self, method, url, **kwargs):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Check time budget exceeded')
        with self.session.request(method, secure_url(url), stream=True,
                                  timeout=(min(5, remaining), min(25, remaining)),
                                  allow_redirects=False, **kwargs) as response:
            if response.is_redirect:
                raise ValueError('Supplier redirect requires review')
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 8_000_000:
                    raise ValueError('Supplier response exceeds the diagnostic limit')
                if time.monotonic() > self.deadline:
                    raise TimeoutError('Check time budget exceeded')
                chunks.append(chunk)
            response._content = b''.join(chunks)
            response._content_consumed = True
            return response

    def _load_remote_data(self, url):
        self.documents += 1
        if self.documents > 160:
            raise ValueError('Too many schema documents')
        if self.documents == 1 or self.documents % 10 == 0:
            print(f'Загрузка схем API: документ {self.documents}…', flush=True)
        response = self.request('GET', url)
        response.raise_for_status()
        return response.content

    def post(self, address, message, headers):
        return self.request('POST', address, data=message, headers=headers)


def create_check_client():
    return Client(WSDL, transport=CheckTransport(), settings=Settings(
        strict=True, xml_huge_tree=False, forbid_dtd=True,
        forbid_entities=True, forbid_external=False))


def key(value):
    return re.sub(r'[^a-zа-я0-9]', '', str(value).lower())


def describe_type(type_, depth=0, ancestors=()):
    """Public type information, not credentials or a request envelope."""
    name = str(getattr(type_, 'qname', None) or type(type_).__name__)
    result = {'type': name}
    if id(type_) in ancestors or depth >= 9:
        result['referenceOnly'] = True
        return result
    fields = list(getattr(type_, 'elements', []))
    if fields:
        result['fields'] = [{
            'name': field_name, 'minOccurs': element.min_occurs,
            'maxOccurs': str(element.max_occurs), 'nillable': element.nillable,
            **describe_type(element.type, depth + 1, (*ancestors, id(type_))),
        } for field_name, element in fields[:100]]
    return result


def describe_operation(operation):
    return {
        'inputSignature': operation.input.signature(),
        'outputSignature': operation.output.signature(),
        'input': describe_type(operation.input.body.type),
        'output': describe_type(operation.output.body.type),
    }


LOGIN_KEYS = {'login', 'username', 'userlogin'}
PASSWORD_KEYS = {'password', 'pass', 'userpassword'}
CODE_KEYS = {'code', 'codes', 'goodscode', 'goodscodes', 'productcode',
             'productcodes', 'article', 'articles', 'sku', 'codelist', 'codeslist'}


def code_argument(element, sku):
    fields = list(getattr(element.type, 'elements', []))
    if fields:
        if len(fields) != 1:
            raise MappingRequired('Unsupported product code structure')
        child_name, child = fields[0]
        if not isinstance(child.type, xsd.String):
            raise MappingRequired('Product code is not a string')
        return {child_name: [sku] if child.accepts_multiple else sku}
    if not isinstance(element.type, xsd.String):
        raise MappingRequired('Product code is not a string')
    return [sku] if element.accepts_multiple else sku


def build_arguments(operation, login, password, sku=None):
    """Use observed field names/types; stop on unknown required parameters."""
    used = set()

    def build(type_, path='', depth=0):
        if depth > 6:
            raise MappingRequired('Request nesting requires review')
        result = {}
        for name, element in getattr(type_, 'elements', []):
            label = key(name)
            if label in LOGIN_KEYS | PASSWORD_KEYS:
                if not isinstance(element.type, xsd.String) or element.accepts_multiple:
                    raise MappingRequired('Credential field type requires review')
                role = 'login' if label in LOGIN_KEYS else 'password'
                result[name] = login if role == 'login' else password
                used.add(role)
            elif sku and label in CODE_KEYS:
                result[name] = code_argument(element, sku)
                used.add('sku')
            elif getattr(element.type, 'elements', None) and not element.accepts_multiple:
                before = set(used)
                try:
                    nested = build(element.type, path + name + '.', depth + 1)
                except MappingRequired:
                    used.clear()
                    used.update(before)
                    if not element.is_optional:
                        raise
                    nested = {}
                if nested:
                    result[name] = nested
                elif not element.is_optional:
                    raise MappingRequired('Required structure: ' + path + name)
            elif not element.is_optional:
                raise MappingRequired('Required field: ' + path + name)
        return result

    args = build(operation.input.body.type)
    required = {'login', 'password'} | ({'sku'} if sku else set())
    if not required.issubset(used):
        raise MappingRequired('Credentials or product code could not be mapped from the schema')
    return args


def scrub(value, secrets=(), depth=0):
    if depth > 12:
        return '[nested content omitted]'
    if isinstance(value, dict):
        result = {}
        for name, child in list(value.items())[:120]:
            normalized = key(name)
            if normalized in LOGIN_KEYS | PASSWORD_KEYS or any(part in normalized for part in
                    ('password', 'пароль', 'secret', 'token', 'apikey', 'credential', 'email', 'phone')):
                result[name] = '[redacted]'
            else:
                result[name] = scrub(child, secrets, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return {'count': len(value), 'firstItems': [scrub(v, secrets, depth + 1) for v in value[:3]]}
    if isinstance(value, (datetime, Decimal)):
        value = str(value)
    if isinstance(value, str):
        for secret in sorted((s for s in secrets if s), key=len, reverse=True):
            value = value.replace(secret, '[redacted]')
        return value[:2000]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return type(value).__name__


def provider_error(value):
    """Empty error envelopes are normal; explicit failure still stops requests."""
    def has_detail(detail):
        if isinstance(detail, dict):
            return any(has_detail(child) for child in detail.values())
        if isinstance(detail, (list, tuple)):
            return any(has_detail(child) for child in detail)
        if isinstance(detail, str):
            return detail.strip() not in ('', '0')
        return detail not in (None, False, 0)

    if isinstance(value, dict):
        for name, child in value.items():
            label = key(name)
            if label == 'success' and child is False:
                return True
            if label in {'error', 'errors', 'errormessage', 'errorcode', 'fault', 'faultstring'} and has_detail(child):
                return True
            if provider_error(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(provider_error(v) for v in value)
    return False


def redact_literals(value, secrets):
    """Keep valid JSON even when a credential happens to be 'true' or '123'."""
    if isinstance(value, dict):
        return {redact_literals(name, secrets): redact_literals(child, secrets)
                for name, child in value.items()}
    if isinstance(value, list):
        return [redact_literals(child, secrets) for child in value]
    if isinstance(value, str):
        for secret in sorted((s for s in secrets if s), key=len, reverse=True):
            value = value.replace(secret, '[redacted]')
    return value


def failure(error):
    if isinstance(error, (requests.Timeout, TimeoutError)):
        return {'type': 'timeout', 'message': 'Сервер API не ответил вовремя.'}
    if isinstance(error, requests.exceptions.SSLError):
        return {'type': 'tls', 'message': 'Не удалось проверить защищённое соединение.'}
    if isinstance(error, requests.ConnectionError):
        return {'type': 'connection', 'message': 'Не удалось соединиться с сервером API.'}
    if isinstance(error, requests.HTTPError):
        return {'type': 'http', 'status': error.response.status_code if error.response is not None else None}
    return {'type': type(error).__name__, 'message': 'Ответ или схема требует проверки; чувствительные детали скрыты.'}


def run_check(report_path, no_auth=False, sample_sku=None, client_factory=create_check_client):
    report = {'checkVersion': '1.2', 'checkedAt': datetime.now(timezone.utc).isoformat(),
              'endpoint': WSDL, 'status': 'started', 'credentialsSent': False,
              'catalogIntegrationVerified': False, 'calls': [], 'operations': {}}
    login = password = ''
    try:
        client = client_factory()
        operations = client.service._binding._operations
        for name in sorted(READ_OPERATIONS & operations.keys()):
            report['operations'][name] = describe_operation(operations[name])
        report['status'] = 'schema_loaded'
        if no_auth:
            return report
        if 'GetWarehouses' not in operations:
            report['status'] = 'mapping_required'
            print('Метод складов отличается. Схемы сохранены для подключения.', flush=True)
            return report
        # Check the complete authentication shape before requesting any secrets.
        try:
            build_arguments(operations['GetWarehouses'], '__login__', '__password__')
        except MappingRequired:
            report['status'] = 'mapping_required'
            print('Схема авторизации требует настройки. Пароль пока не нужен.', flush=True)
            return report
        login = os.getenv('FOURTOCHKI_LOGIN') or input('Логин API «4точки»: ').strip()
        password = os.getenv('FOURTOCHKI_PASSWORD') or getpass.getpass('Пароль API (ввод не отображается): ')
        if not login or not password:
            report['status'] = 'credentials_missing'
            return report
        if sample_sku is None:
            sample_sku = input('Артикул поставщика для проверки товара (Enter — только склады): ').strip()
        if len(sample_sku) > 100 or any(ord(c) < 32 for c in sample_sku):
            report['status'] = 'invalid_product_code'
            return report
        # Time spent typing credentials does not consume the network time budget.
        if isinstance(client.transport, CheckTransport):
            client.transport.deadline = time.monotonic() + 90
        requested = [('GetWarehouses', None)]
        if sample_sku:
            requested += [('GetGoodsInfo', sample_sku), ('GetGoodsPriceRestByCode', sample_sku)]
        for name, sku in requested:
            entry = {'operation': name}
            report['calls'].append(entry)
            if name not in operations:
                entry['status'] = 'method_unavailable'
                continue
            try:
                arguments = build_arguments(operations[name], login, password, sku)
            except MappingRequired:
                entry['status'] = 'mapping_required'
                continue
            print(f'Запрос {name}…', flush=True)
            report['credentialsSent'] = True
            try:
                response = serialize_object(probe(client, name, arguments))
            except Exception as error:
                entry.update(status='request_failed', error=failure(error))
                report['status'] = 'request_failed'
                break
            entry.update(status='response_received', response=scrub(response, (login, password)),
                         businessSuccess='requires_response_review')
            if provider_error(response):
                entry['status'] = 'provider_error'
                report['status'] = 'provider_error'
                break
            report['status'] = 'responses_received'
        return report
    except (KeyboardInterrupt, EOFError):
        report['status'] = 'cancelled'
        return report
    except Exception as error:
        report.update(status='check_failed', error=failure(error))
        return report
    finally:
        # Redact literal secrets once more, including unexpected provider field names.
        rendered = json.dumps(redact_literals(report, (login, password)),
                              ensure_ascii=False, indent=2, default=str)
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + '\n', encoding='utf-8')
        try:
            path.chmod(0o600)
        except OSError:
            pass
        print(f'Отчёт: {path.resolve()}', flush=True)
        print(f'Результат: {report["status"]}. Подключение витрины этим тестом не подтверждается.', flush=True)


def main():
    parser = argparse.ArgumentParser(description='Проверка API «4точки» — только чтение')
    parser.add_argument('--report', default='IMKONEX_4TOCHKI_REPORT.json')
    parser.add_argument('--no-auth', action='store_true', help='Только схема API, без ввода пароля')
    parser.add_argument('--sample-sku', help='Артикул именно поставщика, не артикул демоверсии')
    args = parser.parse_args()
    print('IMKONEX CARS · Проверка API «4точки»\nСначала проверяется соединение и схема API.', flush=True)
    report = run_check(args.report, args.no_auth, args.sample_sku)
    return 1 if report['status'] in {'check_failed', 'request_failed', 'provider_error'} else 0


if __name__ == '__main__':
    raise SystemExit(main())
