"""Read-only account catalog -> public retail snapshot, with atomic publication.

Request names/envelopes are based on the user's successful 2026-09-17 report.
Raw SOAP responses, purchase prices and credentials are never written to disk.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from .export_snapshot import number, text, public_offers

ROOT = Path(__file__).resolve().parent.parent
GROUPS = {
    'tires': ('GetFindTyre', 'TyrePriceRest', 'tyreList', 'TyreContainer'),
    'wheels': ('GetFindDisk', 'DiskPriceRest', 'rimList', 'RimContainer'),
    'tubes': ('GetFindCamera', 'CameraPriceRest', 'cameraList', 'CameraContainer'),
}


class SyncError(RuntimeError):
    """A fixed, public error identifier, never a provider exception message."""


class ProviderError(SyncError):
    pass


class AuthenticationError(SyncError):
    pass


class UnsupportedProduct(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def checked_response(value, operation):
    from .supplier_check import provider_error
    if isinstance(value, dict) and operation + 'Result' in value:
        value = value[operation + 'Result']
    if not isinstance(value, dict):
        raise SyncError('response_shape_changed')
    if provider_error(value):
        error = value.get('error') or {}
        if isinstance(error, dict) and str(error.get('code')) == '30':
            raise AuthenticationError('authentication_failed')
        raise ProviderError('provider_rejected_request')
    return value


def rows_at(value, *path):
    for name in path:
        if value is None:
            return []
        if not isinstance(value, dict) or name not in value:
            raise SyncError('response_array_changed')
        value = value[name]
    if value is None:
        return []
    # Diagnostic {count, firstItems} snippets must never be mistaken for a feed.
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise SyncError('response_array_changed')
    return value


def code_of(row):
    code = row.get('code')
    if not isinstance(code, str) or not code.strip() or len(code) > 100 or any(ord(c) < 32 for c in code):
        raise SyncError('unsupported_article_code')
    return code


def product_id(kind, code):
    safe = code if re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', code) else hashlib.sha256(code.encode()).hexdigest()
    return f'4t-{kind}-{safe}'


def request_arguments(name, login, password, *, page=1, page_size=100, codes=(), warehouse_ids=(), extended=False, warehouse=None):
    args = {'login': login, 'password': password}
    if name in ('GetFindTyre', 'GetFindDisk', 'GetFindCamera'):
        args.update(filter={}, page=page, pageSize=page_size)
        if name == 'GetFindCamera':
            args['filter']['subtype_id_list'] = {'int': [0]}
        if extended and name == 'GetFindTyre':
            args['filter']['quality'] = 0
        if warehouse_ids:
            args['filter']['wrh_list'] = {'int': list(warehouse_ids)}
    elif name == 'GetRest':
        if type(warehouse) is not int or warehouse<=0:raise SyncError('warehouse_required')
        args['filter']={'wrh':warehouse,'page':page}
    elif name == 'GetGoodsPriceRestByCode':
        args['filter']={'code_list':{'string':list(codes)},'searchCodeByOccurence':False,'include_paid_delivery':True}
    elif name == 'GetGoodsInfo':
        if not 1 <= len(codes) <= 100:
            raise SyncError('invalid_detail_batch')
        args['code_list'] = {'string': list(codes)}
    elif name != 'GetWarehouses':
        raise SyncError('operation_not_allowed')
    return args


class LiveSupplier:
    def __init__(self, config):
        from .supplier_check import create_check_client
        from .catalog_check import validate_payload
        self.login = os.getenv('FOURTOCHKI_LOGIN', '').strip()
        self.password = os.getenv('FOURTOCHKI_PASSWORD', '')
        if not self.login or not self.password:
            raise SyncError('credentials_missing')
        self.config, self.calls = config, 0
        self.client = create_check_client()
        self.client.transport.session.headers['User-Agent'] = 'IMKONEX-CATALOG-SYNC/0.6.0'
        self.deadline = time.monotonic() + config['max_duration_seconds']
        self.client.transport.deadline = self.deadline
        self.last_call = 0
        self.operations = self.client.service._binding._operations
        required = ['GetWarehouses', 'GetFindTyre', 'GetFindDisk', 'GetGoodsInfo']
        if config.get('include_tubes'): required.append('GetFindCamera')
        if config.get('include_accessories'):required+=['GetRest','GetGoodsPriceRestByCode']
        for name in required:
            if name not in self.operations:
                raise SyncError('required_operation_missing')
            args = request_arguments(name, '__login__', '__password__',
                                     codes=['__code__'], page_size=config['page_size'], warehouse=1,
                                     warehouse_ids=config['warehouse_ids'], extended=config.get('extended_categories', False))
            validate_payload(self.operations[name].input.body.type, args)

    def __call__(self, name, **options):
        from requests import Timeout, ConnectionError, HTTPError
        from zeep.helpers import serialize_object
        from .supplier import probe
        from .catalog_check import validate_payload
        args = request_arguments(name, self.login, self.password,
                                 page_size=self.config['page_size'],
                                 warehouse_ids=self.config['warehouse_ids'], extended=self.config.get('extended_categories', False), **options)
        validate_payload(self.operations[name].input.body.type, args)
        for attempt in range(3):
            if self.calls >= self.config['max_api_calls'] or time.monotonic() >= self.deadline:
                raise SyncError('api_budget_exceeded')
            delay = max(0, self.config['request_interval_seconds'] - (time.monotonic() - self.last_call))
            if delay:
                time.sleep(delay)
            self.calls += 1
            self.last_call = time.monotonic()
            try:
                return checked_response(serialize_object(probe(self.client, name, args)), name)
            except HTTPError as error:
                if error.response is None or error.response.status_code not in (429, 500, 502, 503, 504):
                    raise SyncError('http_request_failed') from None
            except (Timeout, ConnectionError, TimeoutError):
                pass
            if attempt == 2:
                raise SyncError('supplier_unavailable') from None
            time.sleep(2 ** (attempt + 1))
        raise SyncError('supplier_unavailable')

    def close(self):
        self.client.transport.session.close()


def rub_prices(response):
    rate = response.get('currencyRate')
    try:
        valid = (isinstance(rate, dict) and rate.get('charCode') == 'RUB'
                 and number(rate.get('nominal')) == 1 and number(rate.get('value')) == 1)
    except ValueError:
        valid = False
    if not valid:
        raise SyncError('currency_not_confirmed_rub')


def search_pages(call, kind, config, stats):
    operation, item, _, _ = GROUPS[kind]

    def get(page):
        response = call(operation, page=page)
        rub_prices(response)
        total = response.get('totalPages')
        if type(total) is not int or not 0 <= total <= config['max_pages_per_category']:
            raise SyncError('page_budget_or_shape_changed')
        rows = rows_at(response, 'price_rest_list', item)
        if len(rows) > config['page_size']:
            raise SyncError('page_size_not_honoured')
        return total, rows

    # Distinguish zero-based paging, one-based paging and a page-0 alias.
    # Network/authentication failures are not interpreted as a missing page.
    try:
        zero = get(0)
    except ProviderError:
        zero = None
    try:
        one = get(1)
    except ProviderError:
        if zero is not None and zero[0] == 0 and not zero[1]:
            one = (0, [])
        elif zero is not None and zero[0] == 1 and zero[1]:
            one = (1, [])
        else:
            raise
    total = one[0] if zero is None else zero[0]
    if one[0] != total:
        raise SyncError('page_count_changed_during_scan')
    zero_codes = [code_of(row) for row in zero[1]] if zero else []
    one_codes = [code_of(row) for row in one[1]]
    if zero_codes and zero_codes != one_codes:
        base, first = 0, zero[1]
        if set(zero_codes) & set(one_codes):
            raise SyncError('overlapping_search_pages')
    else:
        base, first = 1, one[1]
    if total == 0:
        if first or zero_codes or one_codes:
            raise SyncError('invalid_empty_page_count')
        stats.update(pages=0, pageBase=base, scanned=0)
        return
    stats.update(pages=total, pageBase=base, scanned=0)
    seen = set()
    for page in range(base, base + total):
        count, rows = (total, first) if page == base else (one if page == 1 else get(page))
        if count != total:
            raise SyncError('page_count_changed_during_scan')
        if not rows:
            raise SyncError('unexpected_empty_search_page')
        codes = [code_of(row) for row in rows]
        if len(codes) != len(set(codes)) or seen.intersection(codes):
            raise SyncError('repeated_or_overlapping_search_page')
        seen.update(codes)
        if len(seen) > config['max_products']:
            raise SyncError('product_budget_exceeded')
        stats['scanned'] += len(rows)
        if page == base or (page - base + 1) % 10 == 0:
            print(f'{kind}: page {page - base + 1}/{total}, articles {len(seen)}', flush=True)
        yield rows


def public_product(kind, detail, search, warehouses, warehouse_ids):
    from .catalog_photos import public_photo_url
    code = code_of(detail)
    if code != code_of(search):
        raise SyncError('article_mapping_mismatch')
    offers, used = [], set()
    for row in rows_at(search, 'whpr', 'wh_price_rest'):
        wid = row.get('wrh')
        if type(wid) is not int or wid in used or wid not in warehouses:
            raise SyncError('warehouse_mapping_changed')
        used.add(wid)
        if warehouse_ids and wid not in warehouse_ids:
            continue
        offers.append({'warehouseId': wid, 'warehouseKnown': True,
                       'warehouseName': warehouses[wid], 'rest': row.get('rest'),
                       'price_rozn': row.get('price_rozn')})
    # Financial/stock mapping errors abort a run rather than hiding the product.
    try:
        public = public_offers({'offers': offers})
    except (ValueError, TypeError):
        raise SyncError('invalid_retail_or_stock') from None
    if not public:
        return None
    try:
        p = {'id': product_id(kind, code), 'sku': code, 'kind': kind,
             'brand': text(detail.get('brand'), 100), 'model': text(detail.get('model')),
             'description': text(detail.get('name'), 1000),
             'image': public_photo_url(detail) or public_photo_url(search) or 'assets/product-unavailable.svg',
             'isDemo': False, 'rank': 100,
             'diameter': number(detail.get('diameter'), minimum=10, maximum=30), 'offers': public}
        if kind == 'tires':
            season = {'s': 'summer', 'w': 'winter'}.get(detail.get('season'), 'unknown')
            if type(detail.get('thorn')) is not bool or detail.get('constr') not in ('R', 'ZR'):
                raise UnsupportedProduct('unsupported_tire_parameters')
            p.update(width=number(detail.get('width'), integer=True, minimum=100, maximum=500),
                     profile=number(detail.get('height'), integer=True, minimum=15, maximum=100),
                     season=season, construction=detail['constr'],
                     loadIndex=text(detail.get('load_index'), 20), speedIndex=text(detail.get('speed_index'), 20),
                     studded=detail['thorn'], xl=True if str(detail.get('tonnage', '')).upper() == 'XL' else None,
                     runflat=None)
        else:
            bolts = number(detail.get('bolts_count'), integer=True, minimum=3, maximum=12)
            spacing = number(detail.get('bolts_spacing'), minimum=50, maximum=300)
            second = number(detail.get('bolt_spacing2') or 0, maximum=300)
            p.update(wheelWidth=number(detail.get('width'), minimum=3, maximum=16),
                     pcd=f'{bolts}×{spacing}' + (f' / {bolts}×{second}' if second else ''),
                     et=number(detail.get('et'), minimum=-100, maximum=200),
                     dia=number(detail.get('dia'), minimum=30, maximum=200),
                     color=text(detail.get('color'), 100),
                     colorDescription=text(detail.get('color_explanation') or detail.get('color')), type=None)
    except (ValueError, TypeError):
        raise UnsupportedProduct('unsupported_product_parameters') from None
    return p


def collect_catalog(call, config):
    started = utc_now()
    response = call('GetWarehouses')
    if response.get('success') is not True:
        raise SyncError('warehouses_not_verified')
    warehouses = {}
    for row in rows_at(response, 'warehouses', 'WarehouseInfo'):
        wid = row.get('id')
        if type(wid) is not int or wid <= 0 or wid in warehouses:
            raise SyncError('warehouse_mapping_changed')
        warehouses[wid] = text(row.get('name'))
    if not warehouses or set(config['warehouse_ids']) - warehouses.keys():
        raise SyncError('configured_warehouse_not_available')
    products, categories, excluded = [], {}, Counter()
    for kind, (_, _, container, item) in GROUPS.items():
        if kind == 'tubes' and not config.get('include_tubes'): continue
        stats = categories[kind] = {'published': 0, 'excluded': 0}
        for rows in search_pages(call, kind, config, stats):
            batch_size = config['detail_batch_size']
            for offset in range(0, len(rows), batch_size):
                batch = rows[offset:offset + batch_size]
                codes = [code_of(row) for row in batch]
                details = rows_at(call('GetGoodsInfo', codes=codes), container, item)
                mapped = {code_of(row): row for row in details}
                if len(mapped) != len(details) or set(mapped) != set(codes):
                    raise SyncError('incomplete_or_duplicate_product_details')
                for row in batch:
                    try:
                        from .catalog_types import extended_product
                        convert = extended_product if config.get('extended_categories') else public_product
                        p = convert(kind, mapped[code_of(row)], row, warehouses, config['warehouse_ids'])
                    except UnsupportedProduct:
                        p = None
                        excluded['unsupportedParameters'] += 1
                    else:
                        if p is None:
                            excluded['noRetailOffer'] += 1
                    if p is None:
                        stats['excluded'] += 1
                    else:
                        products.append(p)
                        stats['published'] += 1
                if sum(c.get('scanned', 0) for c in categories.values()) > config['max_products']:
                    raise SyncError('product_budget_exceeded')
    if config.get('include_accessories'):
        from .catalog_accessories import collect_accessories
        extra,extra_stats,no_retail=collect_accessories(call,config,warehouses,[p['sku'] for p in products])
        products.extend(extra);categories.update(extra_stats);excluded['noRetailOffer']+=no_retail
    if not products or any(categories[k]['published'] == 0 for k in ('tires','wheels')):
        raise SyncError('empty_catalog_or_category')
    finished = utc_now()
    return {'schemaVersion': 5 if config.get('include_accessories') else 4 if config.get('extended_categories') else 3, 'mode': 'snapshot', 'source': '4tochki',
            'updatedAt': finished, 'priceBasis': 'supplier_retail',
            'notice': 'Товары с розничной ценой и наличием по последней успешной выгрузке API. '
                      'Показаны поддержанные размеры и характеристики. Наличие, цена и доставка '
                      'подтверждаются менеджером. Расчёт не является заказом.',
            'sync': {'complete': True, 'scope': 'available_supported_products',
                     'startedAt': started, 'finishedAt': finished,
                     'sourceProducts': sum(c['scanned'] for c in categories.values()),
                     'publishedProducts': len(products), 'excludedProducts': sum(excluded.values()),
                     'categories': categories, 'excludedReasons': dict(excluded)},
            'products': sorted(products, key=lambda p: (p['kind'], p['brand'].casefold(), p['model'].casefold(), p['sku']))}


def check_drop(data, previous, maximum, allow=False):
    if allow or not previous or previous.get('schemaVersion') not in (3,4,5):
        return
    for kind in [*GROUPS,'sensors','consumables','oils']:
        old = sum(p['kind'] == kind for p in previous['products'])
        new = sum(p['kind'] == kind for p in data['products'])
        if old and new < old * (1 - maximum):
            raise SyncError('large_catalog_drop')


def load_config(path):
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    bounds = {'page_size': (5, 100), 'detail_batch_size': (1, 100),
              'max_pages_per_category': (1, 5000), 'max_products': (2, 50000),
              'max_api_calls': (10, 10000), 'max_duration_seconds': (60, 3000),
              'max_public_bytes': (10000, 32000000), 'photos_per_run': (0, 500),
              'photo_cache_bytes': (1000000, 200000000), 'photo_time_seconds': (0, 300)}
    optional = {'extended_categories', 'include_tubes', 'include_accessories'}
    if any(type(data[k]) is not bool for k in optional if k in data):
        raise SyncError('invalid_sync_config')
    if (data.get('include_tubes') or data.get('include_accessories')) and not data.get('extended_categories'):
        raise SyncError('invalid_sync_config')
    if set(data) - optional != set(bounds) | {'request_interval_seconds', 'warehouse_ids', 'max_drop_fraction'}:
        raise SyncError('invalid_sync_config')
    for key, (low, high) in bounds.items():
        if type(data[key]) is not int or not low <= data[key] <= high:
            raise SyncError('invalid_sync_config')
    for key, low, high in [('request_interval_seconds', 0.5, 10), ('max_drop_fraction', 0, 0.9)]:
        if type(data[key]) not in (int, float) or not low <= data[key] <= high:
            raise SyncError('invalid_sync_config')
    if not isinstance(data['warehouse_ids'], list) or any(type(i) is not int or i <= 0 for i in data['warehouse_ids']):
        raise SyncError('invalid_sync_config')
    return data


def validate_public_file(file):
    result = subprocess.run(['node', str(ROOT / 'scripts/validate-public.mjs'), str(file)],
                            capture_output=True, text=True)
    if result.returncode:
        raise SyncError('public_contract_failed')


def publish_snapshot(data, target, previous, config, *, allow_drop=False, dry_run=False, downloader=None):
    from .catalog_photos import cache_photos
    check_drop(data, previous, config['max_drop_fraction'], allow_drop)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.catalog-sync-', dir=ROOT) as temp:
        stage = Path(temp)
        photos = cache_photos(data, previous, ROOT / 'frontend', stage, config, downloader=downloader)
        raw = json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n'
        if len(raw.encode('utf-8')) > config['max_public_bytes']:
            raise SyncError('public_snapshot_too_large')
        for key in ('FOURTOCHKI_LOGIN', 'FOURTOCHKI_PASSWORD'):
            secret = os.getenv(key, '')
            if secret and secret in raw:
                raise SyncError('credential_in_public_data')
        candidate = stage / 'snapshot.json'
        candidate.write_text(raw, encoding='utf-8')
        validate_public_file(candidate)
        for product in data['products']:
            if product['image'].startswith('assets/'):
                relative = product['image']
                if not (stage / relative).is_file() and not (ROOT / 'frontend' / relative).is_file():
                    raise SyncError('local_photo_missing')
        if not dry_run:
            # Extra immutable photos are harmless if a process stops before JSON replacement.
            for file in (stage / 'assets/products').glob('*') if (stage / 'assets/products').exists() else []:
                destination = ROOT / 'frontend/assets/products' / file.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(file, destination)
            # Stage lives on the same filesystem as the repository/data directory.
            os.replace(candidate, target)
        return photos


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/sync.json')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--allow-large-drop', action='store_true')
    args = parser.parse_args()
    report_path = ROOT / 'runtime/sync-summary.json'
    report = {'version': '0.6.0', 'startedAt': utc_now(), 'status': 'failed', 'published': False}
    supplier = None
    try:
        config = load_config(args.config)
        target = ROOT / 'data/supplier-snapshot.json'
        previous = json.loads(target.read_text(encoding='utf-8-sig')) if target.exists() else None
        supplier = LiveSupplier(config)
        data = collect_catalog(supplier, config)
        report['photos'] = publish_snapshot(data, target, previous, config,
                                           allow_drop=args.allow_large_drop, dry_run=args.dry_run)
        report.update(status='verified' if args.dry_run else 'snapshot_written',
                      products=len(data['products']), sync=data['sync'])
        print(f'SYNC_OK {len(data["products"])} products; all search pages read; retail prices only.', flush=True)
    except Exception as error:
        report['error'] = str(error) if isinstance(error, SyncError) else 'sync_failed_' + type(error).__name__
        print('SYNC_FAILED ' + report['error'] + '. Previous public snapshot retained.', flush=True)
    finally:
        if supplier:
            report['apiCalls'] = supplier.calls
            supplier.close()
        report['finishedAt'] = utc_now()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if report['status'] == 'failed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
