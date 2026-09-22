"""Offline integration tests. All supplier responses here are synthetic."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from backend import catalog_sync as sync
from backend.catalog_photos import public_photo_url, cache_photos, image_extension

ROOT = Path(__file__).resolve().parent.parent


def config():
    result = sync.load_config(ROOT / 'config/sync.json')
    result.update(extended_categories=False, include_tubes=False, include_accessories=False, page_size=5, detail_batch_size=3, photos_per_run=0, photo_time_seconds=1)
    return result


def detail(code):
    tire = code.startswith('T')
    return {'code': code, 'brand': 'Synthetic brand', 'model': 'Synthetic model',
            'name': 'Synthetic product ' + code, 'width': '225' if tire else '6.5',
            'height': '45', 'diameter': '17', 'constr': 'R', 'season': 'w', 'thorn': False,
            'load_index': '95', 'speed_index': 'V', 'tonnage': 'XL', 'bolts_count': 5,
            'bolts_spacing': '114.3', 'bolt_spacing2': '0', 'et': '45', 'dia': '67.1',
            'color': 'Silver', 'color_explanation': 'Серебристый',
            'img_big_my': 'https://api-b2b.pwrs.ru/private-account/pictures/big.png',
            'img_big_pish': 'http://www.4tochki.ru/pictures/tyres/Test/Model/big/0.png'}


class FakeSupplier:
    def __init__(self, base='alias', count=7, override=None):
        self.base, self.count, self.override, self.calls = base, count, override, 0
        self.visited = []

    def __call__(self, name, **options):
        self.calls += 1
        self.visited.append((name, deepcopy(options)))
        if self.override:
            custom = self.override(name, options)
            if custom is not NotImplemented:
                return custom
        if name == 'GetWarehouses':
            return {'success': True, 'warehouses': {'WarehouseInfo': [
                {'id': 2017, 'name': 'Челябинск 2'}, {'id': 2099, 'name': 'Test warehouse'}]}}
        if name in ('GetFindTyre', 'GetFindDisk'):
            page = options['page']
            if self.base == 'one' and page == 0:
                raise sync.ProviderError('provider_rejected_request')
            offset = page if self.base == 'zero' else max(0, page - 1)
            total = (self.count + 4) // 5
            if offset >= total and self.base == 'zero_error':
                raise sync.ProviderError('provider_rejected_request')
            prefix = 'T' if name == 'GetFindTyre' else 'W'
            item = 'TyrePriceRest' if prefix == 'T' else 'DiskPriceRest'
            rows = [{'code': prefix + str(i).zfill(5), 'whpr': {'wh_price_rest': [
                {'wrh': 2017, 'rest': 4, 'price': '9876543.21', 'price_rozn': '8000.50'},
                {'wrh': 2099, 'rest': 2, 'price': '9876543.21', 'price_rozn': '7900'},
            ]}} for i in range(offset * 5, min((offset + 1) * 5, self.count))]
            return {'totalPages': total, 'currencyRate': {'charCode': 'RUB', 'nominal': 1, 'value': '1.0000'},
                    'price_rest_list': {item: rows}}
        if name == 'GetGoodsInfo':
            codes = options['codes']
            container, item = ('tyreList', 'TyreContainer') if codes[0].startswith('T') else ('rimList', 'RimContainer')
            return {container: {item: [detail(code) for code in reversed(codes)]}}
        raise AssertionError('Unexpected operation')

    def close(self):
        pass


class CatalogSyncTests(unittest.TestCase):
    def test_one_based_alias_and_zero_based_pages_read_every_article(self):
        for base in ('one', 'alias', 'zero'):
            with self.subTest(base=base):
                supplier = FakeSupplier(base)
                result = sync.collect_catalog(supplier, config())
                self.assertEqual(len(result['products']), 14)
                self.assertEqual({p['sku'] for p in result['products']},
                                 {prefix + str(i).zfill(5) for prefix in ('T', 'W') for i in range(7)})
                self.assertEqual(result['sync']['categories']['tires']['pageBase'], 0 if base == 'zero' else 1)
                self.assertEqual(result['sync']['sourceProducts'], 14)
                self.assertNotIn('9876543.21', json.dumps(result))
                self.assertNotIn('private-account', json.dumps(result))
                self.assertEqual(result['products'][0]['offers'][0]['price'], 8000.5)
                self.assertEqual([o['stock'] for o in result['products'][0]['offers']], [4, 2])
                self.assertIsNone(result['products'][0]['offers'][0]['days'])

    def test_empty_error_is_success_and_authentication_stops(self):
        self.assertEqual(sync.checked_response({'success': True, 'error': {'code': None, 'comment': None}}, 'GetWarehouses')['success'], True)
        with self.assertRaises(sync.AuthenticationError):
            sync.checked_response({'success': False, 'error': {'code': 30, 'comment': 'test'}}, 'GetWarehouses')
        with self.assertRaises(sync.ProviderError):
            sync.checked_response({'error': {'code': 77, 'comment': 'test'}}, 'GetFindTyre')

    def test_auth_on_page_zero_is_not_ignored(self):
        def fail(name, options):
            if options.get('page') == 0:
                raise sync.AuthenticationError('authentication_failed')
            return NotImplemented
        with self.assertRaises(sync.AuthenticationError):
            sync.collect_catalog(FakeSupplier(override=fail), config())

    def test_repeated_empty_or_changed_pages_are_never_published(self):
        for mode in ('repeat', 'empty', 'pagecount', 'currency', 'truncated'):
            base = FakeSupplier('one')
            def change(name, options):
                if name == 'GetFindTyre' and options.get('page') == 2:
                    result = base(name, page=1 if mode == 'repeat' else 2)
                    if mode == 'empty': result['price_rest_list']['TyrePriceRest'] = []
                    if mode == 'pagecount': result['totalPages'] = 3
                    if mode == 'currency': result['currencyRate']['charCode'] = 'USD'
                    if mode == 'truncated': result['price_rest_list']['TyrePriceRest'] = {'count': 5, 'firstItems': []}
                    return result
                return NotImplemented
            with self.subTest(mode=mode), self.assertRaises(sync.SyncError):
                sync.collect_catalog(FakeSupplier('one', override=change), config())

    def test_missing_or_duplicate_details_abort(self):
        for duplicate in (False, True):
            def change(name, options):
                if name == 'GetGoodsInfo':
                    result = FakeSupplier()(name, **options)
                    group = next(iter(result.values()))
                    values = next(iter(group.values()))
                    if duplicate: values.append(deepcopy(values[0]))
                    else: values.pop()
                    return result
                return NotImplemented
            with self.subTest(duplicate=duplicate), self.assertRaises(sync.SyncError):
                sync.collect_catalog(FakeSupplier(override=change), config())

    def test_zero_retail_does_not_use_purchase_price_and_skips_are_counted(self):
        def change(name, options):
            if name == 'GetFindTyre':
                result = FakeSupplier()(name, **options)
                for row in result['price_rest_list']['TyrePriceRest']:
                    if row['code'] == 'T00000':
                        for offer in row['whpr']['wh_price_rest']: offer['price_rozn'] = None
                return result
            return NotImplemented
        result = sync.collect_catalog(FakeSupplier(override=change), config())
        self.assertEqual(len(result['products']), 13)
        self.assertEqual(result['sync']['excludedReasons'], {'noRetailOffer': 1})
        self.assertNotIn('T00000', {p['sku'] for p in result['products']})

    def test_unknown_parameters_are_visible_in_exclusion_counts(self):
        def change(name, options):
            if name == 'GetGoodsInfo':
                result = FakeSupplier()(name, **options)
                for group in result.values():
                    for rows in group.values():
                        for row in rows:
                            if row['code'] == 'T00001': row['diameter'] = '99'
                return result
            return NotImplemented
        result = sync.collect_catalog(FakeSupplier(override=change), config())
        self.assertEqual(result['sync']['excludedReasons'], {'unsupportedParameters': 1})
        self.assertEqual(result['sync']['categories']['tires']['excluded'], 1)

    def test_non_ascii_codes_are_preserved_and_unknown_season_is_not_invented(self):
        source = detail('T1');source.update(code='YST/№9,1', season='unmapped')
        row = {'code': source['code'], 'whpr': {'wh_price_rest': [{'wrh': 2017, 'rest': 4, 'price_rozn': '100'}]}}
        product = sync.public_product('tires', source, row, {2017: 'Test'}, [])
        self.assertEqual(product['sku'], 'YST/№9,1')
        self.assertRegex(product['id'], r'^4t-tires-[a-f0-9]{64}$')
        self.assertEqual(product['season'], 'unknown')

    def test_unknown_warehouse_or_invalid_retail_aborts(self):
        for field, value in [('wrh', 999999), ('rest', -1), ('price_rozn', 'NaN')]:
            row = {'code': 'T1', 'whpr': {'wh_price_rest': [{'wrh': 2017, 'rest': 4, 'price_rozn': '100', field: value}]}}
            with self.subTest(field=field), self.assertRaises(sync.SyncError):
                sync.public_product('tires', detail('T1'), row, {2017: 'Test'}, [])

    def test_warehouse_filter_and_mass_drop_guard(self):
        cfg = config();cfg['warehouse_ids'] = [2017]
        full = sync.collect_catalog(FakeSupplier(), cfg)
        self.assertTrue(all(len(p['offers']) == 1 and p['offers'][0]['id'] == 'warehouse-2017' for p in full['products']))
        fewer = deepcopy(full);fewer['products'] = fewer['products'][:2]
        with self.assertRaisesRegex(sync.SyncError, 'large_catalog_drop'):
            sync.check_drop(fewer, full, 0.5)
        sync.check_drop(fewer, full, 0.5, allow=True)

    def test_budgets_never_truncate_into_a_successful_snapshot(self):
        for key, value in [('max_pages_per_category', 1), ('max_products', 8)]:
            cfg = config();cfg[key] = value
            with self.subTest(key=key), self.assertRaises(sync.SyncError):
                sync.collect_catalog(FakeSupplier(), cfg)

    def test_photo_source_is_public_https_and_rejects_other_hosts(self):
        self.assertEqual(public_photo_url(detail('T1')), 'https://www.4tochki.ru/pictures/tyres/Test/Model/big/0.png')
        for url in ('https://evil.test/x.png', 'https://www.4tochki.ru/pictures/../private.png',
                    'https://www.4tochki.ru/pictures/x.png?password=secret', 'file:///etc/passwd'):
            self.assertIsNone(public_photo_url({'img_big_pish': url}))
        self.assertIsNone(image_extension(b'<html>not a photo</html>'))
        self.assertIsNone(image_extension(b''))

    def test_verified_photos_are_deduplicated_and_original_bytes_kept(self):
        body = (ROOT / 'frontend/assets/products/R5019.png').read_bytes()
        result = sync.collect_catalog(FakeSupplier(), config())
        cfg = config();cfg.update(photos_per_run=10, photo_time_seconds=20)
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp) / 'stage';frontend = Path(temp) / 'frontend'
            calls = []
            def download(url): calls.append(url);return body
            stats = cache_photos(result, None, frontend, stage, cfg, downloader=download)
            self.assertEqual(len(calls), 1)
            self.assertEqual(stats['downloaded'], 1)
            self.assertEqual(stats['local'], 14)
            file = stage / result['products'][0]['image']
            self.assertEqual(file.read_bytes(), body)

    def test_snapshot_write_is_atomic_and_dry_run_keeps_old_data(self):
        data = sync.collect_catalog(FakeSupplier(), config())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp);(root / 'scripts').mkdir();(root / 'data').mkdir()
            for name in ('validate-public.mjs', 'public-catalog.mjs'):
                shutil.copyfile(ROOT / 'scripts' / name, root / 'scripts' / name)
            target = root / 'data/supplier-snapshot.json';target.write_text('previous working snapshot')
            with patch.object(sync, 'ROOT', root):
                sync.publish_snapshot(deepcopy(data), target, None, config(), dry_run=True)
                self.assertEqual(target.read_text(), 'previous working snapshot')
                broken = deepcopy(data);broken['products'][0]['privateCost'] = 'secret'
                with self.assertRaisesRegex(sync.SyncError, 'public_contract_failed'):
                    sync.publish_snapshot(broken, target, None, config())
                self.assertEqual(target.read_text(), 'previous working snapshot')
                sync.publish_snapshot(data, target, None, config())
            self.assertEqual(json.loads(target.read_text())['sync']['publishedProducts'], 14)
            self.assertFalse(list(root.glob('.catalog-sync-*')))

    def test_credentials_never_reach_candidate_or_report(self):
        data = sync.collect_catalog(FakeSupplier(), config())
        data['products'][0]['model'] = 'TEST_ONLY_SECRET_9214'
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ', {'FOURTOCHKI_PASSWORD': 'TEST_ONLY_SECRET_9214'}):
            target = Path(temp) / 'catalog.json';target.write_text('old')
            with self.assertRaisesRegex(sync.SyncError, 'credential_in_public_data'):
                sync.publish_snapshot(data, target, None, config())
            self.assertEqual(target.read_text(), 'old')

    def test_live_request_envelopes_match_the_observed_wsdl(self):
        from zeep import xsd
        from lxml import etree
        from backend.catalog_check import validate_payload
        schemas = json.loads((ROOT / 'tests/fixtures/fourtochki_inputs_20260917.json').read_text())
        schemas['GetFindCamera'] = json.loads((ROOT / 'tests/fixtures/fourtochki_camera_input_20260922.json').read_text())
        def to_type(description):
            if not description.get('fields'):
                return xsd.Schema().get_type(description['type'])
            return xsd.ComplexType(xsd.Sequence([
                xsd.Element(f['name'], to_type(f), min_occurs=f['minOccurs'],
                            max_occurs=f['maxOccurs'] if f['maxOccurs'] == 'unbounded' else int(f['maxOccurs']),
                            nillable=f['nillable']) for f in description['fields']]))
        for name in ('GetWarehouses', 'GetFindTyre', 'GetFindDisk', 'GetGoodsInfo',
                     'GetFindCamera', 'GetRest', 'GetGoodsPriceRestByCode'):
            args = sync.request_arguments(name, 'synthetic-login', 'synthetic-password', page=0,
                                          codes=[f'T{i:05}' for i in range(50)], warehouse_ids=[2017],
                                          extended=True, warehouse=2017)
            type_ = to_type(schemas[name]);validate_payload(type_, args)
            element = xsd.Element(name, type_);root = etree.Element('Envelope')
            element.render(root, element(**args))
            if name.startswith('GetFind'):
                self.assertEqual(root.find('.//pageSize').text, '100')
                self.assertEqual(root.find('.//page').text, '0')
                self.assertEqual(root.find('.//wrh_list/int').text, '2017')
            if name == 'GetGoodsInfo':
                self.assertEqual(len(root.findall('.//code_list/string')), 50)
            if name == 'GetFindCamera':
                self.assertEqual(root.find('.//subtype_id_list/unsignedByte').text, '0')
                self.assertIsNone(root.find('.//subtype_id_list/int'))
            if name == 'GetRest':
                self.assertEqual(root.find('.//filter/wrh').text, '2017')
            if name == 'GetGoodsPriceRestByCode':
                self.assertEqual(len(root.findall('.//filter/code_list/string')), 50)

    def test_unsigned_byte_contract_rejects_out_of_range_and_non_integer_values(self):
        from zeep import xsd
        from backend.catalog_check import validate_payload
        from backend.supplier_check import MappingRequired
        # The user's 2026-09-22 schema uses xsd:unsignedByte, not xsd:int.
        type_ = xsd.ComplexType(xsd.Sequence([
            xsd.Element('unsignedByte', xsd.UnsignedByte(), min_occurs=0, max_occurs='unbounded')
        ]))
        validate_payload(type_, {'unsignedByte': [0, 255]})
        for value in (-1, 256, True, False, 0.0, '0', None):
            with self.subTest(value=value), self.assertRaises(MappingRequired):
                validate_payload(type_, {'unsignedByte': [value]})
        for envelope in ({'int': [0]}, {'unsignedByte': 0}):
            with self.subTest(envelope=envelope), self.assertRaises(MappingRequired):
                validate_payload(type_, envelope)

    def test_unsigned_byte_support_does_not_allow_unreviewed_integer_types(self):
        from zeep import xsd
        from backend.catalog_check import validate_payload
        from backend.supplier_check import MappingRequired
        type_ = xsd.ComplexType(xsd.Sequence([
            xsd.Element('unsignedByte', xsd.UnsignedShort(), max_occurs='unbounded')
        ]))
        with self.assertRaises(MappingRequired):
            validate_payload(type_, {'unsignedByte': [0]})

    def test_cli_skips_only_unsupported_products_and_keeps_publication_guards(self):
        # Run the __main__ entry point in a separate interpreter, just as -m
        # does. Import-only tests cannot catch duplicated exception classes.
        baseline = sync.collect_catalog(FakeSupplier(), config())
        previous = json.dumps(baseline, ensure_ascii=False).encode()
        for scenario, expected in (
            ('parameters', None), ('retail', 'invalid_retail_or_stock'),
            ('stock', 'invalid_retail_or_stock'), ('warehouse', 'warehouse_mapping_changed'),
            ('drop', 'large_catalog_drop'),
        ):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                shutil.copytree(ROOT / 'backend', root / 'backend', ignore=shutil.ignore_patterns('__pycache__'))
                for folder in ('scripts', 'config', 'data', 'frontend'):
                    (root / folder).mkdir()
                for name in ('validate-public.mjs', 'public-catalog.mjs'):
                    shutil.copyfile(ROOT / 'scripts' / name, root / 'scripts' / name)
                shutil.copyfile(ROOT / 'tests/fixtures/sync_cli_provider.py', root / 'run_cli.py')
                cfg = config();cfg['extended_categories'] = True
                (root / 'config/sync.json').write_text(json.dumps(cfg))
                target = root / 'data/supplier-snapshot.json';target.write_bytes(previous)
                result = subprocess.run([sys.executable, str(root / 'run_cli.py'), str(ROOT), scenario],
                                        cwd=root, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0 if expected is None else 1, result.stdout + result.stderr)
                report = json.loads((root / 'runtime/sync-summary.json').read_text())
                self.assertEqual(target.read_bytes(), previous)
                self.assertNotIn('fixture-password', result.stdout + result.stderr + json.dumps(report))
                if expected is None:
                    self.assertEqual(report['status'], 'verified')
                    self.assertEqual(report['products'], 13)
                    self.assertEqual(report['sync']['excludedReasons'], {'unsupportedParameters': 1})
                    self.assertEqual(report['sync']['sourceProducts'], 14)
                    self.assertIn('SYNC_OK', result.stdout)
                else:
                    self.assertEqual(report['status'], 'failed')
                    self.assertEqual(report['error'], expected)


if __name__ == '__main__':
    unittest.main()
