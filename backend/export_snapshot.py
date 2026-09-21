"""Export a public, dated sample from the private 1.3 diagnostic report.

No network calls, credentials, cost-price fallback, markup or delivery estimates.
The result is a frontend snapshot (schema 2), not a live backend catalog.
"""
import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


def number(value, *, integer=False, minimum=0, maximum=100000000):
    if value is None or isinstance(value, bool):
        raise ValueError('Missing or invalid numeric field')
    try:
        n = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Invalid numeric field') from None
    if not n.is_finite() or not minimum <= n <= maximum:
        raise ValueError('Numeric field out of range')
    if integer and n != n.to_integral_value():
        raise ValueError('Expected integer field')
    return int(n) if n == n.to_integral_value() else float(n)


def text(value, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError('Missing or invalid text field')
    return value.strip()


def photo_url(value):
    if value is None:
        return 'assets/product-unavailable.svg'
    value = text(value, 2000)
    p = urlsplit(value)
    if (p.scheme != 'https' or p.hostname not in {'api-b2b.pwrs.ru', 'www.4tochki.ru'}
            or p.port not in (None, 443) or p.username or p.password or p.query or p.fragment
            or not re.fullmatch(r'/[a-zA-Z0-9_./%-]+\.(?:png|jpg|jpeg|webp)', p.path)
            or '..' in p.path or '%' in p.path):
        raise ValueError('Unsupported product photo URL')
    return value


def detail_rows(report, container, item):
    result = {}
    for call in report.get('calls', []):
        if call.get('operation') != 'GetGoodsInfo' or call.get('status') != 'response_received':
            continue
        group = (call.get('response', {}).get(container) or {}).get(item) or []
        if isinstance(group, dict):
            # 1.3 includes all three selected details in firstItems.
            group = group.get('firstItems', [])
        for row in group:
            code = text(row.get('code'), 100)
            if code in result and result[code] != row:
                raise ValueError('Conflicting product details')
            result[code] = row
    return result


def public_offers(product):
    offers, ids = [], set()
    for offer in product.get('offers', []):
        warehouse_id = number(offer.get('warehouseId'), integer=True, minimum=1)
        if warehouse_id in ids:
            raise ValueError('Duplicate warehouse offer')
        ids.add(warehouse_id)
        stock = number(offer.get('rest'), integer=True, maximum=1000000)
        retail = offer.get('price_rozn')
        if retail is None or retail == '':
            continue  # Never substitute the private purchase price.
        price = number(retail)
        if not stock or not price:
            continue
        if Decimal(str(retail)).quantize(Decimal('.01')) != Decimal(str(retail)):
            raise ValueError('Retail price has unsupported precision')
        if offer.get('warehouseKnown') is not True:
            raise ValueError('Warehouse name has not been resolved')
        offers.append({
            'id': f'warehouse-{warehouse_id}',
            'warehouse': text(offer.get('warehouseName')),
            'stock': stock, 'price': price,
            'days': None,  # logistDays is not a promise of delivery to the buyer.
        })
    return offers


def convert_report(report):
    if report.get('checkVersion') != '1.3' or report.get('status') != 'sample_verified':
        raise ValueError('A successful catalog check version 1.3 is required')
    updated = text(report.get('finishedAt') or report.get('checkedAt'), 100)
    parsed_time = datetime.fromisoformat(updated.replace('Z', '+00:00'))
    if parsed_time.tzinfo is None:
        raise ValueError('Report date must include the time zone')
    products, product_ids = [], set()
    for category, kind, container, item in (
        ('tyres', 'tires', 'tyreList', 'TyreContainer'),
        ('wheels', 'wheels', 'rimList', 'RimContainer'),
    ):
        part = report.get('categories', {}).get(category, {})
        if part.get('verified') is not True:
            raise ValueError('Both product categories must be verified')
        rate = part.get('currencyRate') or {}
        if (rate.get('charCode') != 'RUB' or number(rate.get('nominal')) != 1
                or number(rate.get('value')) != 1):
            raise ValueError('Only confirmed RUB prices without conversion are supported')
        rows = part.get('products', [])
        if not 1 <= len(rows) <= 3:
            raise ValueError('Expected one to three verified products per category')
        details = detail_rows(report, container, item)
        for source in rows:
            code = text(source.get('code'), 100)
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', code):
                raise ValueError('Unsupported supplier article format')
            detail = details.get(code)
            if not detail:
                raise ValueError('Matching product detail is missing')
            params = source.get('parameters') or {}
            p = {
                'id': f'4t-{kind}-{code}', 'sku': code, 'kind': kind,
                'brand': text(source.get('brand'), 100),
                'model': text(source.get('model')),
                'description': text(source.get('name'), 1000),
                'image': photo_url(source.get('photoUrl')),
                'isDemo': False, 'rank': 100 - len(products),
                'diameter': number(params.get('diameter'), minimum=10, maximum=30),
                'offers': public_offers(source),
            }
            if p['id'] in product_ids:
                raise ValueError('Duplicate product article')
            product_ids.add(p['id'])
            if kind == 'tires':
                season = {'s': 'summer', 'w': 'winter'}.get(params.get('season'))
                if season is None or type(params.get('thorn')) is not bool:
                    raise ValueError('Unsupported season or stud field')
                construction = detail.get('constr')
                if construction not in ('R', 'ZR'):
                    raise ValueError('Unknown tire construction')
                p.update({
                    'width': number(params.get('width'), integer=True, minimum=100, maximum=500),
                    'profile': number(params.get('height'), integer=True, minimum=15, maximum=100),
                    'season': season, 'construction': construction,
                    'loadIndex': text(params.get('load_index'), 20),
                    'speedIndex': text(params.get('speed_index'), 20),
                    'studded': params['thorn'],
                    # strengthening=false does not negate the explicit XL tonnage.
                    'xl': True if str(detail.get('tonnage', '')).upper() == 'XL' else None,
                    'runflat': None,
                })
            else:
                bolts = number(params.get('bolts_count'), integer=True, minimum=3, maximum=12)
                spacing = number(params.get('bolts_spacing'), minimum=50, maximum=300)
                second = number(params.get('bolt_spacing2', 0), maximum=300)
                p.update({
                    'wheelWidth': number(params.get('width'), minimum=3, maximum=16),
                    'pcd': f'{bolts}×{spacing}' + (f' / {bolts}×{second}' if second else ''),
                    'et': number(params.get('et'), minimum=-100, maximum=200),
                    'dia': number(params.get('dia'), minimum=30, maximum=200),
                    'color': text(params.get('color'), 100),
                    'colorDescription': text(detail.get('color_explanation') or params.get('color')),
                    'type': None,  # Numeric supplier codes need a verified dictionary.
                })
            products.append(p)
    return {
        'schemaVersion': 2, 'mode': 'snapshot', 'source': '4tochki',
        'updatedAt': updated, 'priceBasis': 'supplier_retail',
        'notice': 'Выборка реальных товаров из отчёта API. Цены — розничные цены поставщика '
                  'на дату проверки. Автообновление и оформление заказа не подключены.',
        'products': products,
    }


def load_report(path):
    if path.stat().st_size > 5000000:
        raise ValueError('Report exceeds 5 MB')
    raw = path.read_text(encoding='utf-8-sig').strip()
    # Accept a JSON file or one fenced JSON block copied from PowerShell.
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[1]
        if not raw.rstrip().endswith('```'):
            raise ValueError('Unclosed JSON block')
        raw = raw.rstrip()[:-3].strip()
    return json.loads(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--output', type=Path, default=Path('data/supplier-snapshot.json'))
    args = parser.parse_args()
    try:
        result = convert_report(load_report(args.report))
        # Write only the deliberately selected public fields, never the raw report.
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temp = args.output.with_suffix('.tmp')
        temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temp.replace(args.output)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.exit(1, f'Export failed ({type(exc).__name__}). Check the private report locally.\n')
    print(f'Public sample: {len(result["products"])} products. Purchase prices excluded. No network calls.')


if __name__ == '__main__':
    main()
