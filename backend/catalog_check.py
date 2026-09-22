"""Bounded, read-only catalogue sample using the observed 2026-09-17 WSDL.

Produces private JSON/HTML diagnostics, never a public feed or a supplier order.
The existing, verified 1.2 single-article checker is intentionally unchanged.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import getpass
from html import escape
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
import webbrowser

from zeep import xsd
from zeep.helpers import serialize_object

from .supplier import WSDL, probe
from .supplier_check import (
    CheckTransport, MappingRequired, create_check_client, describe_operation,
    failure, provider_error, redact_literals, scrub,
)

VERSION = '1.3'
PAGE_SIZE = 5
SAMPLE_SIZE = 3
MAX_CALLS = 9
OPERATIONS = ('GetWarehouses', 'GetFindTyre', 'GetFindDisk',
              'GetGoodsInfo', 'GetGoodsPriceRestByCode')
CATEGORIES = {
    'tyres': ('Шины', 'GetFindTyre', 'TyrePriceRest', 'tyreList', 'TyreContainer'),
    'wheels': ('Диски', 'GetFindDisk', 'DiskPriceRest', 'rimList', 'RimContainer'),
}


class AuthenticationFailed(RuntimeError):
    pass


class ResponseMappingRequired(ValueError):
    pass


def validate_payload(type_, payload, path=''):
    """Check every supplied field against the live schema before sending it."""
    fields = dict(getattr(type_, 'elements', ()))
    if not isinstance(payload, dict) or not fields:
        raise MappingRequired('Expected request structure: ' + path)
    if set(payload) - set(fields):
        raise MappingRequired('Request field is absent from the schema: ' + path)
    for name, element in fields.items():
        if name not in payload:
            if not element.is_optional:
                raise MappingRequired('New required request field: ' + path + name)
            continue
        value = payload[name]
        many = element.accepts_multiple
        if many != isinstance(value, list):
            raise MappingRequired('Request cardinality changed: ' + path + name)
        for item in value if many else [value]:
            if getattr(element.type, 'elements', None):
                validate_payload(element.type, item, path + name + '.')
            elif isinstance(element.type, xsd.Boolean):
                if type(item) is not bool:
                    raise MappingRequired('Boolean field changed: ' + path + name)
            elif isinstance(element.type, xsd.UnsignedByte):
                if type(item) is not int or not 0 <= item <= 255:
                    raise MappingRequired('Unsigned byte field changed: ' + path + name)
            elif isinstance(element.type, xsd.Int):
                if type(item) is not int:
                    raise MappingRequired('Integer field changed: ' + path + name)
            elif isinstance(element.type, xsd.String):
                if not isinstance(item, str):
                    raise MappingRequired('String field changed: ' + path + name)
            else:
                raise MappingRequired('Unsupported request field type: ' + path + name)


def arguments(operation, login, password, *, page=1, codes=()):
    """Exact names and array envelopes from the user's complete WSDL report."""
    name = operation.name
    result = {'login': login, 'password': password}
    if name in ('GetFindTyre', 'GetFindDisk'):
        result.update(filter={}, page=page, pageSize=PAGE_SIZE)
    elif name in ('GetGoodsInfo', 'GetGoodsPriceRestByCode'):
        if not 1 <= len(codes) <= SAMPLE_SIZE:
            raise ValueError('Expected 1–3 supplier codes')
        if any(not isinstance(c, str) or not c or len(c) > 100 or
               any(ord(ch) < 32 for ch in c) for c in codes):
            raise ValueError('Invalid supplier code')
        code_list = {'string': list(codes)}
        if name == 'GetGoodsInfo':
            result['code_list'] = code_list
        else:
            result['filter'] = {'code_list': code_list, 'searchCodeByOccurence': False}
    elif name != 'GetWarehouses':
        raise MappingRequired('Operation outside catalogue diagnostic scope')
    validate_payload(operation.input.body.type, result)
    return result


def array_at(value, *path):
    """Interpret SOAP arrays, never a truncated diagnostic excerpt as live data."""
    for name in path:
        if value is None:
            return []
        if not isinstance(value, dict) or name not in value:
            raise ResponseMappingRequired('Expected response container: ' + '.'.join(path))
        value = value[name]
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(v, dict) for v in value):
        raise ResponseMappingRequired('Expected list of response objects: ' + '.'.join(path))
    return value


def positive_number(value):
    if value is None or isinstance(value, bool):
        return False
    try:
        number = Decimal(str(value))
        return number.is_finite() and number > 0
    except (InvalidOperation, ValueError):
        return False


def safe_url(value):
    if not isinstance(value, str):
        return None
    try:
        u = urlsplit(value)
        if (u.scheme == 'https' and u.hostname in {'www.4tochki.ru', 'api-b2b.pwrs.ru'}
                and not u.username and not u.password and u.port in (None, 443)):
            return value
    except ValueError:
        pass
    return None


def select_codes(rows):
    codes = []
    for row in rows:
        code = row.get('code')
        if isinstance(code, str) and code and code not in codes:
            codes.append(code)
            if len(codes) == SAMPLE_SIZE:
                break
    return codes


def normalise_products(category, codes, info, prices, warehouses):
    """Join exact supplier codes and warehouse IDs. Never sum warehouse stock."""
    _, _, _, container, item = CATEGORIES[category]
    details = array_at(info, container, item)
    stock_rows = array_at(prices, 'price_rest_list', 'price_rest')
    wanted = set(codes)
    detail_by_code, offers_by_code = {}, {}
    for detail in details:
        code = detail.get('code')
        if code in wanted:
            if code in detail_by_code:
                raise ResponseMappingRequired('Duplicate product details')
            detail_by_code[code] = detail
    for row in stock_rows:
        code = row.get('code')
        if code in wanted:
            if code in offers_by_code:
                raise ResponseMappingRequired('Duplicate product stock envelope')
            offers_by_code[code] = array_at(row, 'whpr', 'wh_price_rest')
    result = []
    for code in codes:
        detail = detail_by_code.get(code)
        if not detail:
            continue
        parameters = ('width', 'height', 'diameter', 'load_index', 'speed_index',
                      'season', 'thorn', 'camera', 'strengthening') if category == 'tyres' else (
                      'width', 'diameter', 'bolts_count', 'bolts_spacing',
                      'bolt_spacing2', 'et', 'dia', 'color', 'type', 'rim_vid_name')
        product = {'code': code, 'brand': detail.get('brand'), 'model': detail.get('model'),
                   'name': detail.get('name'),
                   'parameters': {p: detail[p] for p in parameters if detail.get(p) is not None},
                   'photoUrl': next((u for p in ('img_big_my', 'img_big', 'img_big_pish', 'img_small')
                                     if (u := safe_url(detail.get(p)))), None), 'offers': []}
        seen_warehouses = set()
        for row in offers_by_code.get(code, []):
            warehouse_id = row.get('wrh')
            if type(warehouse_id) is not int or warehouse_id in seen_warehouses:
                raise ResponseMappingRequired('Invalid or duplicate warehouse offer')
            seen_warehouses.add(warehouse_id)
            warehouse = warehouses.get(warehouse_id, {})
            product['offers'].append({
                'warehouseId': warehouse_id, 'warehouseName': warehouse.get('name'),
                'warehouseKnown': bool(warehouse),
                'price': row.get('price'), 'price_rozn': row.get('price_rozn'),
                'rest': row.get('rest'),
            })
        product['hasAvailableRetailOffer'] = any(
            positive_number(o['rest']) and positive_number(o['price_rozn']) for o in product['offers'])
        result.append(product)
    return result


def render_html(report):
    """Escape all supplier text; the local review page has no active scripts."""
    def e(value):
        return escape('—' if value is None else str(value), quote=True)
    labels = {'sample_verified': 'Выборка шин и дисков проверена',
              'partial': 'Проверка завершена частично', 'authentication_failed': 'Ошибка авторизации',
              'mapping_required': 'Требуется настройка схемы', 'check_failed': 'Проверка не завершена',
              'cancelled': 'Проверка прервана', 'credentials_missing': 'Не введены данные доступа'}
    sections = []
    for category, (title, *_) in CATEGORIES.items():
        data = report['categories'].get(category, {})
        cards = []
        for product in data.get('products', []):
            params = ' · '.join(f'{e(k)}: {e(v)}' for k, v in product['parameters'].items())
            rows = ''.join('<tr><td>'+e(o['warehouseName'] or f"ID {o['warehouseId']}")+ '</td><td>' +
                           e(o['price'])+'</td><td>'+e(o['price_rozn'])+'</td><td>'+e(o['rest'])+'</td></tr>'
                           for o in product['offers'])
            photo = safe_url(product.get('photoUrl'))
            photo_link = f'<a href="{e(photo)}" target="_blank" rel="noopener noreferrer">Фото поставщика ↗</a>' if photo else ''
            cards.append(f'<article><p class="code">Артикул {e(product["code"])}</p>'
                         f'<h3>{e(product.get("brand"))} {e(product.get("model"))}</h3>'
                         f'<p>{e(product.get("name"))}</p><p class="params">{params}</p>{photo_link}'
                         '<div class="table"><table><thead><tr><th>Склад</th><th>price</th>'
                         '<th>price_rozn</th><th>rest</th></tr></thead><tbody>'+rows+'</tbody></table></div></article>')
        sections.append(f'<section><h2>{e(title)}</h2><p>{e(data.get("message", "Нет результата"))}</p>' + ''.join(cards) + '</section>')
    calls = ''.join(f'<tr><td>{e(c["operation"])}</td><td>{e(c.get("status"))}</td>'
                    f'<td>{e(c.get("providerError", {}).get("comment") or c.get("error", {}).get("message") or c.get("error", {}).get("type"))}</td></tr>' for c in report['calls'])
    error_notice = ('<p>' + e(report['error'].get('message') or report['error'].get('type')) + '</p>') if report.get('error') else ''
    return '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>IMKONEX CARS — проверка каталога</title><style>
*{box-sizing:border-box}body{margin:0;background:#eef2f3;color:#172d36;font:16px/1.6 system-ui,sans-serif}
header{background:#102f39;color:#fff;padding:36px max(24px,calc((100vw - 1050px)/2))}header p{color:#a7d6b3}
main{max-width:1100px;padding:24px;margin:auto}h1{font-size:30px;line-height:1.2}h2{font-size:24px}h3{margin:6px 0}
article,section.notice{background:#fff;padding:24px;margin:18px 0;border-top:3px solid #488e68}p,h3,td{overflow-wrap:anywhere}
.code,.params{font-size:13px;color:#52676f}.table{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:10px;text-align:left;border-bottom:1px solid #dce4e7}th{color:#52676f}a{color:#1a6845}footer{padding:30px 0;color:#52676f}
</style><header><p>IMKONEX CARS · ПРОВЕРКА API 1.3</p><h1>''' + e(labels.get(report['status'], report['status'])) + '''</h1><p>''' + e(report['checkedAt']) + ''' · UTC</p></header><main>
<section class="notice"><strong>Локальный снимок ответов API для проверки подключения.</strong>
<p>Выбрано до трёх артикулов каждой категории. Полный ассортимент не выгружался; сайт на Render не обновлялся.</p>
<p>Поля price, price_rozn и rest приведены как в API. Продажная цена IMKONEX и значение предельных остатков ещё не настроены. Остатки разных складов не суммируются.</p></section>
''' + error_notice + ''.join(sections) + '<section><h2>Выполненные запросы</h2><div class="table"><table><tr><th>Метод</th><th>Результат</th><th>Комментарий API</th></tr>' + calls + '''</table></div></section>
<footer><a href="IMKONEX_4TOCHKI_CATALOG_REPORT.json" download>Скачать JSON-отчёт</a><br>
Для продолжения подключения передайте этот файл в переписку.</footer></main></html>'''


def run_catalog_check(report_path, html_path, client_factory=create_check_client):
    report = {'checkVersion': VERSION, 'checkedAt': datetime.now(timezone.utc).isoformat(),
              'endpoint': WSDL, 'scope': 'bounded_catalog_sample', 'status': 'started',
              'credentialsSent': False, 'catalogIntegrationVerified': False, 'siteUpdated': False,
              'limits': {'pageSize': PAGE_SIZE, 'codesPerCategory': SAMPLE_SIZE, 'maxCalls': MAX_CALLS},
              'calls': [], 'operations': {}, 'warehouses': [], 'categories': {}}
    login = password = ''
    client = None
    try:
        client = client_factory()
        operations = client.service._binding._operations
        for name in OPERATIONS:
            if name not in operations:
                raise MappingRequired('Required read operation unavailable')
            report['operations'][name] = describe_operation(operations[name])
            # Validate all request shapes before asking for the password.
            arguments(operations[name], '__login__', '__password__', codes=['__code__'])
        login = (os.getenv('FOURTOCHKI_LOGIN') or input('Логин API «4точки»: ')).strip()
        password = os.getenv('FOURTOCHKI_PASSWORD') or getpass.getpass('Пароль API (ввод не отображается): ')
        if not login or not password:
            report['status'] = 'credentials_missing'
            return report
        secrets = (login, password)
        if isinstance(client.transport, CheckTransport):
            client.transport.deadline = time.monotonic() + 180
            client.transport.session.headers['User-Agent'] = 'IMKONEX-4TOCHKI-CATALOG-CHECK/' + VERSION

        def call(name, **options):
            if len(report['calls']) >= MAX_CALLS:
                raise RuntimeError('Diagnostic call budget exceeded')
            entry = {'operation': name, 'request': dict(options)}
            report['calls'].append(entry)
            args = arguments(operations[name], login, password, **options)
            print(f'Запрос {name}' + (f' · страница {options["page"]}' if 'page' in options else '') + '…', flush=True)
            report['credentialsSent'] = True
            try:
                response = serialize_object(probe(client, name, args))
                if isinstance(response, dict) and name + 'Result' in response:
                    response = response[name + 'Result']
                if not isinstance(response, dict):
                    raise ResponseMappingRequired('Expected response object')
                entry['response'] = scrub(response, secrets)
                if provider_error(response):
                    entry['status'] = 'provider_error'
                    error = response.get('error') or {}
                    entry['providerError'] = scrub(error, secrets) if isinstance(error, dict) else {}
                    code = entry['providerError'].get('code')
                    comment = entry['providerError'].get('comment')
                    print(f'Ответ поставщика: код {code}; {comment}', flush=True)
                    if str(code) == '30':
                        raise AuthenticationFailed()
                    return None
                entry['status'] = 'response_received'
                return response
            except AuthenticationFailed:
                raise
            except Exception as error:
                entry.update(status='request_failed', error=failure(error))
                return None

        warehouse_response = call('GetWarehouses')
        if warehouse_response is None or warehouse_response.get('success') is not True:
            report['status'] = 'check_failed'
            return report
        warehouse_rows = array_at(warehouse_response, 'warehouses', 'WarehouseInfo')
        warehouse_map = {}
        for row in warehouse_rows:
            if type(row.get('id')) is not int or row['id'] in warehouse_map:
                raise ResponseMappingRequired('Invalid or duplicate warehouse ID')
            # Whitelist metadata instead of copying unexpected personal fields.
            warehouse_map[row['id']] = {k: row.get(k) for k in (
                'id', 'key', 'name', 'shortName', 'logisticDays', 'haveDelivery', 'havePickup', 'isPaidDelivery')}
        report['warehouses'] = list(warehouse_map.values())
        print(f'Авторизация успешна. Складов: {len(warehouse_map)}.', flush=True)
        for category, (title, search, search_item, _, _) in CATEGORIES.items():
            data = {'products': [], 'verified': False, 'message': 'Запрос не завершён', 'selectedCodes': []}
            report['categories'][category] = data
            try:
                # One bounded page. An empty page 1 may mean zero-based numbering;
                # inspect page 0 once, record it explicitly, never infer completeness.
                response = call(search, page=1)
                if response is None:
                    data['message'] = 'Поиск не вернул успешный ответ; см. журнал запросов.'
                    continue
                rows = array_at(response, 'price_rest_list', search_item)
                data['reportedTotalPages'] = response.get('totalPages')
                data['searchPage'] = 1
                if not rows:
                    response = call(search, page=0)
                    data['searchPage'] = 0
                    if response is None:
                        data['message'] = 'Дополнительная проверка страницы 0 не дала результата.'
                        continue
                    rows = array_at(response, 'price_rest_list', search_item)
                    data['reportedTotalPages'] = response.get('totalPages')
                data['rowsReceived'] = len(rows)
                rate = response.get('currencyRate')
                data['currencyRate'] = ({k: rate.get(k) for k in ('charCode', 'nominal', 'numCode', 'value')}
                                        if isinstance(rate, dict) else None)
                if len(rows) > PAGE_SIZE:
                    data['message'] = 'Поставщик вернул больше товаров, чем задано pageSize; требуется проверка ограничений.'
                    continue
                codes = select_codes(rows)
                data['selectedCodes'] = codes
                if not codes:
                    data['message'] = 'Поиск вернул пустую выборку. Доступ к этой категории пока не подтверждён.'
                    continue
                info = call('GetGoodsInfo', codes=codes)
                if info is None:
                    data['message'] = 'Не удалось получить характеристики выбранных товаров.'
                    continue
                prices = call('GetGoodsPriceRestByCode', codes=codes)
                if prices is None:
                    data['message'] = 'Не удалось получить отдельный ответ с ценами и остатками.'
                    continue
                data['products'] = normalise_products(category, codes, info, prices, warehouse_map)
                data['missingDetails'] = [c for c in codes if c not in {p['code'] for p in data['products']}]
                data['verified'] = (not data['missingDetails'] and bool(data['products']) and
                                    all(p['offers'] for p in data['products']))
                data['availableProducts'] = sum(p['hasAvailableRetailOffer'] for p in data['products'])
                data['message'] = (f'Характеристики и предложения получены: {len(data["products"])}. '
                                   f'С положительным остатком и price_rozn: {data["availableProducts"]}.')
                if not data['verified']:
                    data['message'] += ' Некоторые выбранные товары не имеют характеристик или складских предложений.'
            except AuthenticationFailed:
                raise
            except (MappingRequired, ResponseMappingRequired) as error:
                data['message'] = 'Формат запроса или ответа требует настройки; подробности сохранены в JSON.'
                data['error'] = failure(error)
            print(f'{title}: {data["message"]}', flush=True)
        report['status'] = ('sample_verified' if len(report['categories']) == 2 and
                            all(c['verified'] for c in report['categories'].values()) else 'partial')
        return report
    except AuthenticationFailed:
        report['status'] = 'authentication_failed'
        return report
    except (MappingRequired, ResponseMappingRequired) as error:
        report.update(status='mapping_required', error=failure(error))
        return report
    except (KeyboardInterrupt, EOFError):
        report['status'] = 'cancelled'
        return report
    except Exception as error:
        report.update(status='check_failed', error=failure(error))
        return report
    finally:
        report['finishedAt'] = datetime.now(timezone.utc).isoformat()
        # Raw replies are scrubbed above; normalised data are from a field whitelist.
        # Literal redaction also covers unexpected supplier strings in every section.
        cleaned = redact_literals(report, (login, password))
        report_path, html_path = Path(report_path), Path(html_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
        html_path.write_text(render_html(cleaned), encoding='utf-8')
        for path in (report_path, html_path):
            try:
                path.chmod(0o600)
            except OSError:
                pass
        if client is not None and isinstance(client.transport, CheckTransport):
            client.transport.session.close()
        print(f'\nРезультат: {report["status"]}', flush=True)
        print(f'Отчёт для отправки: {report_path.resolve()}', flush=True)
        print(f'Открыть в браузере: {html_path.resolve()}', flush=True)


def main():
    parser = argparse.ArgumentParser(description='Проверка небольшой выборки шин и дисков — только чтение')
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--open-result', action='store_true', help='Открыть локальный HTML-отчёт после проверки')
    args = parser.parse_args()
    output = args.output_dir or Path('results') / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    print('IMKONEX CARS · Проверка каталога «4точки» · версия ' + VERSION, flush=True)
    print('Автоматически проверяются до трёх шин и трёх дисков. Заказы не создаются.', flush=True)
    result = run_catalog_check(output / 'IMKONEX_4TOCHKI_CATALOG_REPORT.json', output / 'RESULT.html')
    if args.open_result:
        try:
            webbrowser.open((output / 'RESULT.html').resolve().as_uri())
        except Exception:
            print('Откройте RESULT.html вручную по напечатанному выше пути.', flush=True)
    return 0 if result['status'] == 'sample_verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
