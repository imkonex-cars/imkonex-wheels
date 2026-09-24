"""Regression coverage for supplier currency metadata; all responses are synthetic."""
from contextlib import redirect_stdout
from copy import deepcopy
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend import catalog_sync as sync
from test_catalog_sync import FakeSupplier, config


ROOT = Path(__file__).resolve().parent.parent


def camera_response(rows=None, currency=None, total=0):
    return {
        'totalPages': total,
        'currencyRate': currency,
        'price_rest_list': {'CameraPriceRest': [] if rows is None else rows},
    }


def camera_row():
    return {'code': 'C1', 'whpr': {'wh_price_rest': [
        {'wrh': 2017, 'rest': 4, 'price': '9876543.21', 'price_rozn': '2000'},
    ]}}


def documented_camera_response(codes=(), *, currency=True, items_null=False):
    """Current WSDL shape; deliberately no invented totalPages field."""
    rows = []
    for code in codes:
        row = camera_row()
        row.update(code=code, name='Synthetic camera ' + code,
                   brand='Synthetic camera brand', subtype_id=0)
        rows.append(row)
    return {
        'success': True,
        'error': None,
        'CurrencyRateItem': ({'charCode': 'RUB', 'nominal': 1, 'numCode': 643,
                              'value': Decimal('1')} if currency else None),
        'ResultItems': None if items_null else {'GetFindCameraContainer': rows},
        'WarehouseLogisticEnumerable': None,
    }


class CurrencyMetadataTests(unittest.TestCase):
    def test_documented_pascal_case_and_existing_lower_camel_case_are_accepted(self):
        for outer, fields in (
            ('currencyRate', {'CharCode': 'RUB', 'Nominal': 1, 'Value': Decimal('1.0000')}),
            ('currencyRate', {'charCode': 'RUB', 'nominal': 1, 'value': 1}),
        ):
            with self.subTest(outer=outer, fields=fields):
                sync.rub_prices({outer: fields})

    def test_missing_foreign_or_nonunit_currency_is_never_assumed_to_be_rub(self):
        invalid = [
            {}, {'currencyRate': None}, {'currencyRate': []},
            {'currencyRate': {'CharCode': 'USD', 'Nominal': 1, 'Value': 1}},
            {'currencyRate': {'CharCode': 'CNY', 'Nominal': 1, 'Value': 1}},
            {'currencyRate': {'CharCode': 'RUB', 'Nominal': 100, 'Value': 100}},
            {'currencyRate': {'CharCode': 'RUB', 'Nominal': 1, 'Value': '0.9999'}},
            {'currencyRate': {'CharCode': 'RUB', 'Nominal': 1, 'Value': '1.000000000000000001'}},
            {'currencyRate': {'CharCode': 'RUB', 'Nominal': '1.000000000000000001', 'Value': 1}},
            {'currencyRate': {'CharCode': 'RUB', 'Nominal': 1, 'Value': None}},
            {'currencyRate': {'CharCode': 'RUB', 'Value': 1}},
            {'currencyRate': {'Nominal': 1, 'Value': 1}},
            {'currencyRate': {'CharCode': 'RUB', 'Nominal': True, 'Value': True}},
            {'currencyRate': {'CharCode': 'RUB', 'Nominal': 1, 'Value': 'NaN'}},
        ]
        for response in invalid:
            with self.subTest(response=response), self.assertRaisesRegex(
                sync.SyncError, '^currency_not_confirmed_rub$'
            ):
                sync.rub_prices(response)

    def test_conflicting_aliases_cannot_override_a_currency_rejection(self):
        rub = {'CharCode': 'RUB', 'Nominal': 1, 'Value': 1}
        responses = [
            {'currencyRate': {**rub, 'charCode': 'USD'}},
            {'currencyRate': {**rub, 'nominal': 100}},
            {'currencyRate': {**rub, 'value': 80}},
        ]
        for response in responses:
            with self.subTest(response=response), self.assertRaisesRegex(
                sync.SyncError, '^currency_not_confirmed_rub$'
            ):
                sync.rub_prices(response)

    def test_currency_error_has_safe_operation_and_page_context(self):
        secret = 'SYNTHETIC-UNTRUSTED-TEXT-DO-NOT-LOG'
        response = {
            'currencyRate': {'CharCode': secret, 'Nominal': secret, 'Value': secret},
            'password': secret,
            'price_rest_list': {'CameraPriceRest': [camera_row()]},
        }
        with self.assertRaises(sync.SyncError) as raised:
            sync.rub_prices(response, operation='GetFindCamera', page=0, row_count=1)
        context = raised.exception.context
        self.assertEqual(context['operation'], 'GetFindCamera')
        self.assertEqual(context['page'], 0)
        serialized = json.dumps(context, ensure_ascii=False)
        self.assertNotIn(secret, serialized)
        self.assertNotIn('9876543.21', serialized)
        self.assertNotIn('2000', serialized)
        self.assertEqual(str(raised.exception), 'currency_not_confirmed_rub')

    def test_untrusted_context_arguments_cannot_be_echoed_into_logs(self):
        secret = 'SYNTHETIC-UNTRUSTED-CONTEXT-DO-NOT-LOG'
        with self.assertRaises(sync.SyncError) as raised:
            sync.rub_prices({}, operation=secret, page=secret, row_count=secret)
        self.assertNotIn(secret, json.dumps(raised.exception.context))


class CurrencyPagingTests(unittest.TestCase):
    def test_empty_category_can_have_null_or_absent_currency(self):
        for omit in (False, True):
            calls = []
            def call(name, **options):
                calls.append((name, options['page']))
                response = camera_response()
                if omit:
                    response.pop('currencyRate')
                return response
            stats = {}
            with self.subTest(omit=omit):
                self.assertEqual(list(sync.search_pages(call, 'tubes', config(), stats)), [])
                self.assertEqual(stats['scanned'], 0)
                self.assertEqual(stats['pages'], 0)
                self.assertEqual(calls, [('GetFindCamera', 0), ('GetFindCamera', 1)])

    def test_nonempty_camera_page_requires_currency_metadata(self):
        stats = {}
        def call(name, **options):
            return camera_response([camera_row()], total=1)
        with self.assertRaisesRegex(sync.SyncError, '^currency_not_confirmed_rub$') as raised:
            list(sync.search_pages(call, 'tubes', config(), stats))
        self.assertEqual(raised.exception.context['operation'], 'GetFindCamera')
        self.assertEqual(raised.exception.context['page'], 0)

    def test_empty_page_in_nonempty_scan_is_not_treated_as_an_empty_category(self):
        def call(name, **options):
            return camera_response(total=1)
        with self.assertRaisesRegex(sync.SyncError, 'unexpected_empty_search_page'):
            list(sync.search_pages(call, 'tubes', config(), {}))

    def test_malformed_rows_are_not_an_empty_currency_exemption(self):
        def call(name, **options):
            return camera_response({'count': 1, 'firstItems': []}, total=1)
        with self.assertRaisesRegex(sync.SyncError, 'response_array_changed'):
            list(sync.search_pages(call, 'tubes', config(), {}))

    def test_empty_tubes_do_not_block_complete_tires_and_wheels(self):
        def override(name, options):
            if name == 'GetFindCamera':
                return camera_response()
            return NotImplemented
        supplier = FakeSupplier(override=override)
        cfg = config()
        cfg.update(extended_categories=True, include_tubes=True)
        result = sync.collect_catalog(supplier, cfg)
        self.assertEqual(len(result['products']), 14)
        self.assertEqual(result['sync']['categories']['tubes']['published'], 0)
        self.assertEqual(result['sync']['categories']['tubes']['scanned'], 0)
        self.assertNotIn('9876543.21', json.dumps(result))

    def test_all_enabled_searches_are_probed_then_freshly_scanned_before_details(self):
        def override(name, options):
            if name == 'GetFindCamera':
                return camera_response()
            return NotImplemented
        supplier = FakeSupplier(override=override)
        cfg = config()
        cfg.update(extended_categories=True, include_tubes=True)
        sync.collect_catalog(supplier, cfg)
        first_detail = next(i for i, (name, _) in enumerate(supplier.visited) if name == 'GetGoodsInfo')
        for operation in ('GetFindTyre', 'GetFindDisk', 'GetFindCamera'):
            for page in (0, 1):
                visits = [i for i, (name, options) in enumerate(supplier.visited)
                          if name == operation and options.get('page') == page]
                self.assertEqual(len(visits), 2, (operation, page, supplier.visited))
                self.assertTrue(all(visit < first_detail for visit in visits))
        self.assertFalse(any(name.startswith('GetFind') for name, _ in supplier.visited[first_detail:]))

    def test_bad_camera_currency_stops_before_details_or_full_tire_scan(self):
        def override(name, options):
            if name == 'GetFindCamera':
                return camera_response([camera_row()], total=1)
            return NotImplemented
        supplier = FakeSupplier(count=37, override=override)
        cfg = config()
        cfg.update(extended_categories=True, include_tubes=True)
        with self.assertRaisesRegex(sync.SyncError, '^currency_not_confirmed_rub$'):
            sync.collect_catalog(supplier, cfg)
        self.assertFalse(any(name == 'GetGoodsInfo' for name, _ in supplier.visited))
        self.assertFalse(any(options.get('page', 0) > 1 for _, options in supplier.visited))

    def test_discarded_preflight_keeps_zero_based_and_one_based_fresh_scans_complete(self):
        for base in ('zero', 'one', 'alias'):
            supplier = FakeSupplier(base=base, count=17)
            with self.subTest(base=base):
                result = sync.collect_catalog(supplier, config())
                self.assertEqual(len(result['products']), 34)
                for operation in ('GetFindTyre', 'GetFindDisk'):
                    pages = [options['page'] for name, options in supplier.visited if name == operation]
                    final_page = 3 if base == 'zero' else 4
                    self.assertEqual(pages, [0, 1, 0, 1, *range(2, final_page + 1)])

    def test_later_non_rub_page_is_still_rejected_after_successful_prefetch(self):
        base = FakeSupplier(count=17)
        def override(name, options):
            if name == 'GetFindDisk' and options.get('page') == 3:
                response = base(name, **options)
                response['currencyRate']['charCode'] = 'CNY'
                return response
            return NotImplemented
        with self.assertRaisesRegex(sync.SyncError, '^currency_not_confirmed_rub$') as raised:
            sync.collect_catalog(FakeSupplier(count=17, override=override), config())
        self.assertEqual(raised.exception.context['operation'], 'GetFindDisk')
        self.assertEqual(raised.exception.context['page'], 3)

    def test_cli_failure_keeps_previous_snapshot_and_logs_only_safe_context(self):
        secret = 'SYNTHETIC-UNTRUSTED-TEXT-DO-NOT-LOG'
        def override(name, options):
            if name == 'GetFindCamera':
                response = camera_response([camera_row()], currency={
                    'CharCode': secret, 'Nominal': 1, 'Value': secret,
                }, total=1)
                response['password'] = secret
                return response
            return NotImplemented
        supplier = FakeSupplier(count=37, override=override)
        cfg = config()
        cfg.update(extended_categories=True, include_tubes=True)
        output = StringIO()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'data').mkdir()
            target = root / 'data/supplier-snapshot.json'
            previous = b'{"prior_snapshot":"must_remain_unchanged"}\n'
            target.write_bytes(previous)
            with patch.object(sync, 'ROOT', root), \
                 patch.object(sync, 'LiveSupplier', return_value=supplier), \
                 patch.object(sync, 'load_config', return_value=deepcopy(cfg)), \
                 patch('sys.argv', ['catalog_sync']), redirect_stdout(output), \
                 self.assertRaises(SystemExit) as stopped:
                sync.main()
            self.assertEqual(stopped.exception.code, 1)
            self.assertEqual(target.read_bytes(), previous)
            report_text = (root / 'runtime/sync-summary.json').read_text()
            report = json.loads(report_text)
            self.assertEqual(report['status'], 'failed')
            self.assertFalse(report['published'])
            self.assertEqual(report['error'], 'currency_not_confirmed_rub')
            self.assertIn('GetFindCamera', report_text)
            for private in (secret, '9876543.21', '2000'):
                self.assertNotIn(private, report_text)
                self.assertNotIn(private, output.getvalue())
            self.assertFalse(any(name == 'GetGoodsInfo' for name, _ in supplier.visited))


class DocumentedCameraContractTests(unittest.TestCase):
    def test_zero_based_pages_read_to_empty_even_when_prior_page_is_short(self):
        visited = []
        def call(name, **options):
            self.assertEqual(name, 'GetFindCamera')
            page = options['page']
            visited.append(page)
            return documented_camera_response([f'C{page}'] if page < 2 else [],
                                              currency=page < 2)
        stats = {}
        rows = [row for page in sync.search_pages(call, 'tubes', config(), stats) for row in page]
        self.assertEqual([row['code'] for row in rows], ['C0', 'C1'])
        self.assertEqual(visited, [0, 1, 2])
        self.assertEqual(stats['scanned'], 2)
        self.assertEqual(stats['pages'], 2)
        self.assertEqual(stats['pageBase'], 0)

    def test_successful_empty_result_with_null_currency_is_a_complete_empty_category(self):
        for items_null in (True, False):
            visited = []
            def call(name, **options):
                visited.append(options['page'])
                return documented_camera_response(currency=False, items_null=items_null)
            stats = {}
            with self.subTest(items_null=items_null):
                self.assertEqual(list(sync.search_pages(call, 'tubes', config(), stats)), [])
                self.assertEqual(visited, [0])
                self.assertEqual(stats['pages'], 0)
                self.assertEqual(stats['scanned'], 0)

    def test_nonempty_current_camera_result_requires_its_own_currency(self):
        def call(name, **options):
            return documented_camera_response(['C1'], currency=False)
        with self.assertRaisesRegex(sync.SyncError, '^currency_not_confirmed_rub$') as raised:
            list(sync.search_pages(call, 'tubes', config(), {}))
        self.assertEqual(raised.exception.context['operation'], 'GetFindCamera')
        self.assertEqual(raised.exception.context['page'], 0)

    def test_later_camera_page_currency_is_checked_independently(self):
        def call(name, **options):
            response = documented_camera_response([f'C{options["page"]}'])
            if options['page'] == 1:
                response['CurrencyRateItem']['charCode'] = 'USD'
            return response
        with self.assertRaisesRegex(sync.SyncError, '^currency_not_confirmed_rub$') as raised:
            list(sync.search_pages(call, 'tubes', config(), {}))
        self.assertEqual(raised.exception.context['page'], 1)

    def test_repeated_camera_codes_cannot_run_until_budget_or_publish(self):
        visited = []
        def call(name, **options):
            visited.append(options['page'])
            return documented_camera_response(['C1'])
        with self.assertRaises(sync.SyncError):
            list(sync.search_pages(call, 'tubes', config(), {}))
        self.assertEqual(visited, [0, 1])

    def test_current_camera_honours_page_size_and_total_product_budgets(self):
        for budget in ('pages', 'products', 'page_size'):
            cfg = config()
            cfg.update(max_pages_per_category=2, max_products=2)
            def call(name, **options):
                page = options['page']
                codes = [f'C{page}-{i}' for i in range(6)] if budget == 'page_size' else [f'C{page}']
                return documented_camera_response(codes)
            if budget == 'pages':
                cfg['max_products'] = 100
            elif budget == 'products':
                cfg['max_pages_per_category'] = 100
            with self.subTest(budget=budget), self.assertRaises(sync.SyncError):
                list(sync.search_pages(call, 'tubes', cfg, {}))

    def test_mixed_current_and_legacy_envelopes_are_not_silently_preferred(self):
        response = documented_camera_response(['C1'])
        response.update(camera_response([camera_row()], total=1))
        with self.assertRaises(sync.SyncError):
            list(sync.search_pages(lambda *a, **kw: deepcopy(response), 'tubes', config(), {}))

    def test_malformed_current_array_is_not_treated_as_empty(self):
        for value in ({}, {'GetFindCameraContainer': {'count': 1, 'firstItems': []}}):
            response = documented_camera_response(currency=False)
            response['ResultItems'] = value
            with self.subTest(value=value), self.assertRaises(sync.SyncError):
                list(sync.search_pages(lambda *a, **kw: deepcopy(response), 'tubes', config(), {}))

    def test_current_camera_currency_failure_is_detected_before_detail_scan(self):
        def override(name, options):
            if name == 'GetFindCamera':
                return documented_camera_response(['C1'], currency=False)
            return NotImplemented
        supplier = FakeSupplier(count=37, override=override)
        cfg = config()
        cfg.update(extended_categories=True, include_tubes=True)
        with self.assertRaisesRegex(sync.SyncError, '^currency_not_confirmed_rub$'):
            sync.collect_catalog(supplier, cfg)
        self.assertFalse(any(name == 'GetGoodsInfo' for name, _ in supplier.visited))
        self.assertFalse(any(options.get('page', 0) > 1 for _, options in supplier.visited))

    def test_current_camera_full_collection_preserves_private_price_boundary(self):
        def override(name, options):
            if name == 'GetFindCamera':
                return documented_camera_response(['C1'] if options['page'] == 0 else [],
                                                  currency=options['page'] == 0)
            if name == 'GetGoodsInfo' and options['codes'] == ['C1']:
                return {'cameraList': {'CameraContainer': [{
                    'code': 'C1', 'brand': 'Synthetic camera brand', 'model': None,
                    'name': 'Synthetic camera C1', 'diameter': '20', 'sub_type_id': 0,
                }]}}
            return NotImplemented
        supplier = FakeSupplier(override=override)
        cfg = config()
        cfg.update(extended_categories=True, include_tubes=True)
        result = sync.collect_catalog(supplier, cfg)
        self.assertEqual(len(result['products']), 15)
        camera = next(product for product in result['products'] if product['kind'] == 'tubes')
        self.assertEqual(camera['sku'], 'C1')
        self.assertEqual(camera['offers'][0]['price'], 2000)
        self.assertNotIn('9876543.21', json.dumps(result))
        camera_pages = [options['page'] for name, options in supplier.visited if name == 'GetFindCamera']
        self.assertEqual(camera_pages, [0, 0, 1])
        first_detail = next(i for i, (name, _) in enumerate(supplier.visited) if name == 'GetGoodsInfo')
        camera_probe = next(i for i, (name, options) in enumerate(supplier.visited)
                            if name == 'GetFindCamera' and options['page'] == 0)
        self.assertLess(camera_probe, first_detail)


if __name__ == '__main__':
    unittest.main()
