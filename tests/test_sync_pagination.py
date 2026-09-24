"""Synthetic regressions for bounded, complete category scan retries."""
from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend import catalog_sync as sync
from test_catalog_sync import FakeSupplier, config
from test_sync_currency import documented_camera_response


SECRET_CODE = 'SYNTHETIC-ARTICLE-NOT-FOR-LOGS'
PRIVATE_PRICE = '7654321.89'


class ChangingSearch:
    """An alias-based API whose first full attempt may be inconsistent."""
    def __init__(self, mode='overlap', *, persistent=False):
        self.mode = mode
        self.persistent = persistent
        self.attempts = 0
        self.visited = []

    def __call__(self, operation, **options):
        page = options['page']
        if page == 0:
            self.attempts += 1
        self.visited.append((self.attempts, page))
        broken = self.persistent or self.attempts == 1
        prefix = 'ABANDONED' if broken else 'ACCEPTED'
        start = max(0, page - 1) * 5
        codes = [f'{prefix}-{n}' for n in range(start, min(start + 5, 12))]
        total = 3
        if page == 1 and broken and self.mode == 'initial_overlap':
            codes = [f'{prefix}-{n}' for n in (4, 6, 7, 8, 9)]
        if page == 2 and broken:
            if self.mode == 'overlap':
                codes[0] = f'{prefix}-4'
            elif self.mode == 'repeat':
                codes = [f'{prefix}-{n}' for n in range(5)]
            elif self.mode == 'duplicates':
                codes[-1] = codes[0]
            elif self.mode == 'pagecount':
                total = 4
            elif self.mode == 'empty':
                codes = []
        return {
            'totalPages': total,
            'currencyRate': {'charCode': 'RUB', 'nominal': 1, 'value': 1},
            'price_rest_list': {'TyrePriceRest': [
                {'code': code, 'price': PRIVATE_PRICE, 'untrusted': SECRET_CODE}
                for code in codes
            ]},
        }


class CompleteScanRetryTests(unittest.TestCase):
    def setUp(self):
        self.sleep = patch.object(sync.time, 'sleep', return_value=None)
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def test_transient_overlap_restarts_category_and_discards_abandoned_rows_and_stats(self):
        call = ChangingSearch()
        stats = {'published': 0, 'excluded': 0}
        pages = sync.read_category_pages(call, 'tires', config(), stats)
        self.assertEqual([row['code'] for page in pages for row in page],
                         [f'ACCEPTED-{n}' for n in range(12)])
        self.assertEqual(call.attempts, 2)
        self.assertEqual(call.visited, [(1, 0), (1, 1), (1, 2),
                                        (2, 0), (2, 1), (2, 2), (2, 3)])
        self.assertEqual(stats['scanned'], 12)
        self.assertEqual(stats['pages'], 3)
        self.assertEqual(stats['pageBase'], 1)
        self.assertEqual(stats['published'], 0)
        self.assertEqual(stats['excluded'], 0)

    def test_transient_repeated_duplicate_drifting_and_empty_pages_restart_completely(self):
        for mode in ('repeat', 'duplicates', 'pagecount', 'empty', 'initial_overlap'):
            with self.subTest(mode=mode):
                call = ChangingSearch(mode)
                stats = {}
                pages = sync.read_category_pages(call, 'tires', config(), stats)
                self.assertEqual(call.attempts, 2)
                self.assertEqual([r['code'] for page in pages for r in page],
                                 [f'ACCEPTED-{n}' for n in range(12)])
                self.assertEqual(stats['scanned'], 12)

    def test_persistent_overlap_stops_after_three_attempts_and_emits_only_safe_diagnostics(self):
        call = ChangingSearch(persistent=True)
        output = StringIO()
        with redirect_stdout(output), self.assertRaisesRegex(
            sync.SyncError, '^repeated_or_overlapping_search_page$'
        ) as raised:
            sync.read_category_pages(call, 'tires', config(), {})
        self.assertEqual(call.attempts, 3)
        context = raised.exception.context
        self.assertEqual(context['operation'], 'GetFindTyre')
        self.assertEqual(context['kind'], 'tires')
        self.assertEqual(context['page'], 2)
        self.assertEqual(context['pageBase'], 1)
        self.assertEqual(context['rowCount'], 5)
        self.assertEqual(context['overlapRows'], 1)
        self.assertEqual(context['attempt'], 3)
        self.assertEqual(context['maxAttempts'], 3)
        allowed = {'operation', 'kind', 'page', 'pageBase', 'expectedPages', 'actualPages',
                   'rowCount', 'duplicateRows', 'overlapRows', 'attempt', 'maxAttempts', 'phase', 'reason'}
        self.assertLessEqual(set(context), allowed)
        self.assertEqual(context['reason'], 'repeated_or_overlapping_search_page')
        for forbidden in (SECRET_CODE, PRIVATE_PRICE, 'ABANDONED-', 'ACCEPTED-'):
            self.assertNotIn(forbidden, json.dumps(context))
            self.assertNotIn(forbidden, output.getvalue())

    def test_persistent_duplicate_and_full_repeated_pages_never_become_successful(self):
        for mode in ('repeat', 'duplicates', 'pagecount', 'empty', 'initial_overlap'):
            with self.subTest(mode=mode):
                call = ChangingSearch(mode, persistent=True)
                with self.assertRaises(sync.SyncError):
                    sync.read_category_pages(call, 'tires', config(), {})
                self.assertEqual(call.attempts, 3)

    def test_authentication_currency_and_global_budgets_are_not_retried(self):
        for error in (sync.AuthenticationError('authentication_failed'),
                      sync.SyncError('currency_not_confirmed_rub'),
                      sync.SyncError('api_budget_exceeded'),
                      sync.SyncError('product_budget_exceeded'),
                      sync.SyncError('response_array_changed')):
            with self.subTest(error=str(error)):
                visited = []
                def fail(name, **options):
                    visited.append(options['page'])
                    raise error
                with self.assertRaises(type(error)) as raised:
                    sync.read_category_pages(fail, 'tires', config(), {})
                self.assertIs(raised.exception, error)
                self.assertEqual(visited, [0])

    def test_non_rub_and_category_size_budgets_are_not_retried(self):
        for mode in ('currency', 'pages', 'products'):
            with self.subTest(mode=mode):
                base = FakeSupplier(count=17)
                visits = []
                cfg = config()
                if mode == 'pages':
                    cfg['max_pages_per_category'] = 2
                elif mode == 'products':
                    cfg['max_products'] = 4
                def call(name, **options):
                    visits.append(options['page'])
                    value = base(name, **options)
                    if mode == 'currency':
                        value['currencyRate']['charCode'] = 'USD'
                    return value
                with self.assertRaises(sync.SyncError):
                    sync.read_category_pages(call, 'tires', cfg, {})
                self.assertEqual(visits.count(0), 1)

    def test_fresh_successful_scans_cover_all_three_numbering_conventions(self):
        for paging_base in ('zero', 'one', 'alias'):
            with self.subTest(base=paging_base):
                call = FakeSupplier(base=paging_base, count=17)
                stats = {}
                pages = sync.read_category_pages(call, 'tires', config(), stats)
                self.assertEqual([row['code'] for page in pages for row in page],
                                 [f'T{n:05d}' for n in range(17)])
                self.assertEqual(stats['pageBase'], 0 if paging_base == 'zero' else 1)
                self.assertEqual(stats['scanned'], 17)

    def test_preflight_reads_initial_pages_without_full_scan_and_can_be_discarded(self):
        call = FakeSupplier(count=17)
        sync.read_category_pages(call, 'tires', config(), {}, probe_only=True)
        self.assertEqual([options['page'] for _, options in call.visited], [0, 1])
        pages = sync.read_category_pages(call, 'tires', config(), {})
        self.assertEqual([options['page'] for _, options in call.visited], [0, 1, 0, 1, 2, 3, 4])
        self.assertEqual(len([row for page in pages for row in page]), 17)

    def test_camera_retries_from_zero_and_requires_explicit_empty_terminal_page(self):
        attempts = 0
        visited = []
        def call(name, **options):
            nonlocal attempts
            page = options['page']
            if page == 0:
                attempts += 1
            visited.append((attempts, page))
            codes = [f'C{page}'] if page < 2 else []
            if attempts == 1 and page == 1:
                codes = ['C0']
            return documented_camera_response(codes, currency=bool(codes))
        stats = {}
        pages = sync.read_category_pages(call, 'tubes', config(), stats)
        self.assertEqual([row['code'] for page in pages for row in page], ['C0', 'C1'])
        self.assertEqual(visited, [(1, 0), (1, 1), (2, 0), (2, 1), (2, 2)])
        self.assertEqual(stats['pages'], 2)
        self.assertEqual(stats['scanned'], 2)

    def test_camera_persistent_repeat_is_not_treated_as_end_of_catalog(self):
        visits = []
        def call(name, **options):
            visits.append(options['page'])
            return documented_camera_response(['C0'])
        with self.assertRaises(sync.SyncError):
            sync.read_category_pages(call, 'tubes', config(), {})
        self.assertEqual(visits, [0, 1, 0, 1, 0, 1])

    def test_all_searches_finish_before_any_detail_download(self):
        supplier = FakeSupplier(count=17)
        result = sync.collect_catalog(supplier, config())
        self.assertEqual(len(result['products']), 34)
        first_detail = next(i for i, (name, _) in enumerate(supplier.visited) if name == 'GetGoodsInfo')
        search_calls = [(i, name, options) for i, (name, options) in enumerate(supplier.visited)
                        if name in ('GetFindTyre', 'GetFindDisk')]
        self.assertTrue(all(i < first_detail for i, _, _ in search_calls))
        for operation in ('GetFindTyre', 'GetFindDisk'):
            self.assertEqual([options['page'] for _, name, options in search_calls if name == operation],
                             [0, 1, 0, 1, 2, 3, 4])

    def test_total_product_budget_applies_across_buffered_categories_before_details(self):
        supplier = FakeSupplier(count=17)
        cfg = config()
        cfg['max_products'] = 20
        with self.assertRaisesRegex(sync.SyncError, '^product_budget_exceeded$'):
            sync.collect_catalog(supplier, cfg)
        self.assertFalse(any(name == 'GetGoodsInfo' for name, _ in supplier.visited))
        # Each category is probed once and scanned once; a global budget is not retried.
        for operation in ('GetFindTyre', 'GetFindDisk'):
            self.assertEqual(sum(name == operation and options.get('page') == 0
                                 for name, options in supplier.visited), 2)

    def test_failed_cli_retry_preserves_previous_snapshot_bytes_and_no_details_are_requested(self):
        base = FakeSupplier(count=17)
        def override(name, options):
            if name == 'GetFindTyre' and options.get('page') == 2:
                return base(name, page=1)
            return NotImplemented
        supplier = FakeSupplier(count=17, override=override)
        output = StringIO()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            target = root / 'data/supplier-snapshot.json'
            previous = b'{"prior_snapshot":"preserve_every_byte"}\n'
            target.write_bytes(previous)
            with patch.object(sync, 'ROOT', root), \
                 patch.object(sync, 'LiveSupplier', return_value=supplier), \
                 patch.object(sync, 'load_config', return_value=deepcopy(config())), \
                 patch('sys.argv', ['catalog_sync']), redirect_stdout(output), \
                 self.assertRaises(SystemExit) as stopped:
                sync.main()
            self.assertEqual(stopped.exception.code, 1)
            self.assertEqual(target.read_bytes(), previous)
            report = json.loads((root / 'runtime/sync-summary.json').read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertFalse(report['published'])
            self.assertEqual(report['diagnostic']['attempt'], 3)
            self.assertIn('SYNC_DIAGNOSTIC', output.getvalue())
            self.assertNotIn('9876543.21', json.dumps(report))
            self.assertNotIn('9876543.21', output.getvalue())
        self.assertFalse(any(name == 'GetGoodsInfo' for name, _ in supplier.visited))
        self.assertEqual(sum(name == 'GetFindTyre' and options.get('page') == 2
                             for name, options in supplier.visited), 3)


if __name__ == '__main__':
    unittest.main()
