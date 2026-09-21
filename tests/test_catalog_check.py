"""Observed request schemas; supplier replies below are synthetic test fixtures."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace

from lxml import etree
import pytest
from zeep import xsd

from backend import catalog_check as check
from backend.supplier_check import MappingRequired


INPUTS = json.loads((Path(__file__).parent / 'fixtures/fourtochki_inputs_20260917.json').read_text())


def observed_type(description):
    if not description.get('fields'):
        return xsd.Schema().get_type(description['type'])
    return xsd.ComplexType(xsd.Sequence([
        xsd.Element(f['name'], observed_type(f), min_occurs=f['minOccurs'],
                    max_occurs=f['maxOccurs'] if f['maxOccurs'] == 'unbounded' else int(f['maxOccurs']),
                    nillable=f['nillable']) for f in description['fields']
    ]))


def client():
    operations = {name: SimpleNamespace(name=name, input=SimpleNamespace(body=SimpleNamespace(
        type=observed_type(schema)))) for name, schema in INPUTS.items()}
    return SimpleNamespace(service=SimpleNamespace(_binding=SimpleNamespace(_operations=operations)), transport=None)


def row(code, category='tyres'):
    return {'code': code, 'brand': 'Test brand', 'model': 'Test model', 'name': 'Synthetic product ' + code,
            'width': Decimal('225') if category == 'tyres' else Decimal('7.5'), 'diameter': Decimal('17'),
            'height': Decimal('45'), 'load_index': '91', 'speed_index': 'Y', 'thorn': False,
            'bolts_count': 5, 'bolts_spacing': Decimal('114.3'), 'et': Decimal('45'),
            'dia': Decimal('67.1'), 'img_big_my': 'https://api-b2b.pwrs.ru/example.png'}


def stock(code, rest=41):
    return {'code': code, 'whpr': {'wh_price_rest': [
        {'price': Decimal('6000'), 'price_rozn': Decimal('7500'), 'rest': rest, 'wrh': 2017},
        {'price': Decimal('6100'), 'price_rozn': Decimal('7600'), 'rest': rest, 'wrh': 1431},
    ]}}


def install(monkeypatch, override=None):
    calls = []
    monkeypatch.setenv('FOURTOCHKI_LOGIN', 'check-account')
    monkeypatch.setenv('FOURTOCHKI_PASSWORD', 'check-secret')
    monkeypatch.setattr(check, 'describe_operation', lambda op: {'input': INPUTS[op.name]})
    def reply(c, name, args):
        calls.append((name, deepcopy(args)))
        if override is not None:
            value = override(name, args)
            if value is not NotImplemented:
                return value
        empty_error = {'code': None, 'comment': None}
        if name == 'GetWarehouses':
            return {'success': True, 'error': empty_error, 'warehouses': {'WarehouseInfo': [
                {'id': 2017, 'name': 'Челябинск 2', 'key': 'chelyab2', 'logisticDays': 0},
                {'id': 1431, 'name': 'Synthetic warehouse', 'key': 'test', 'logisticDays': 1},
            ]}}
        if name in ('GetFindTyre', 'GetFindDisk'):
            item = 'TyrePriceRest' if name == 'GetFindTyre' else 'DiskPriceRest'
            prefix = 'T' if name == 'GetFindTyre' else 'D'
            return {'error': empty_error, 'totalPages': 4,
                    'price_rest_list': {item: [{'code': prefix + str(i).zfill(5)} for i in range(5)]}}
        if name == 'GetGoodsInfo':
            codes = args['code_list']['string']
            tyres = codes[0].startswith('T')
            container, item = ('tyreList', 'TyreContainer') if tyres else ('rimList', 'RimContainer')
            return {'error': empty_error, container: {item: [row(c, 'tyres' if tyres else 'wheels') for c in codes]}}
        if name == 'GetGoodsPriceRestByCode':
            return {'error': empty_error, 'price_rest_list': {'price_rest': [stock(c) for c in args['filter']['code_list']['string']]}}
        raise AssertionError('Unexpected operation')
    monkeypatch.setattr(check, 'probe', reply)
    return calls


@pytest.mark.parametrize('name', check.OPERATIONS)
def test_requests_serialize_with_the_users_observed_field_names_and_types(name):
    op = client().service._binding._operations[name]
    args = check.arguments(op, 'account', 'secret', codes=['0000123'])
    element = xsd.Element(name, op.input.body.type)
    root = etree.Element('Envelope')
    element.render(root, element(**args))
    assert root.find('.//login').text == 'account'
    assert root.find('.//password').text == 'secret'
    if name in ('GetFindTyre', 'GetFindDisk'):
        assert root.find('.//page').text == '1'
        assert root.find('.//pageSize').text == '5'
        assert root.find('.//filter') is not None
        # Unspecified numeric disk limits stay omitted, not invented zeros.
        assert root.find('.//bolts_count_max') is None
    if name in ('GetGoodsInfo', 'GetGoodsPriceRestByCode'):
        assert root.find('.//code_list/string').text == '0000123'
    if name == 'GetGoodsPriceRestByCode':
        assert root.find('.//searchCodeByOccurence').text == 'false'


def test_changed_schema_stops_before_asking_for_credentials(tmp_path, monkeypatch):
    c = client()
    c.service._binding._operations['GetFindDisk'].input.body.type = xsd.ComplexType(xsd.Sequence([
        xsd.Element('login', xsd.String()), xsd.Element('password', xsd.String()),
        xsd.Element('unknownRequired', xsd.Int()),
    ]))
    monkeypatch.setattr(check, 'describe_operation', lambda op: {'name': op.name})
    monkeypatch.setattr('builtins.input', lambda *_: pytest.fail('No credential prompt before validation'))
    result = check.run_catalog_check(tmp_path / 'r.json', tmp_path / 'r.html', lambda: c)
    assert result['status'] == 'mapping_required'
    assert result['credentialsSent'] is False
    assert result['calls'] == []


def test_complete_sample_checks_both_categories_and_keeps_stock_separate(tmp_path, monkeypatch):
    calls = install(monkeypatch)
    result = check.run_catalog_check(tmp_path / 'r.json', tmp_path / 'r.html', client)
    saved = json.loads((tmp_path / 'r.json').read_text())
    assert result['status'] == 'sample_verified'
    assert len(calls) == 7
    assert saved['catalogIntegrationVerified'] is False
    assert saved['siteUpdated'] is False
    for category in ('tyres', 'wheels'):
        data = saved['categories'][category]
        assert len(data['products']) == 3
        assert data['availableProducts'] == 3
        assert data['missingDetails'] == []
        assert data['products'][0]['offers'][0]['warehouseName'] == 'Челябинск 2'
        assert [o['rest'] for o in data['products'][0]['offers']] == [41, 41]
        assert 'totalStock' not in data['products'][0]
    assert saved['categories']['wheels']['products'][0]['parameters']['bolts_count'] == 5
    combined = (tmp_path / 'r.json').read_text() + (tmp_path / 'r.html').read_text()
    assert 'check-secret' not in combined and 'check-account' not in combined
    assert 'rimList' in json.dumps(saved['calls'])


def test_explicit_auth_failure_stops_all_queries_and_redacts_comment(tmp_path, monkeypatch, capsys):
    calls = install(monkeypatch, lambda n, a: {
        'success': False, 'error': {'code': 30, 'comment': 'Ошибка авторизации check-secret'},
    })
    result = check.run_catalog_check(tmp_path / 'r.json', tmp_path / 'r.html', client)
    assert result['status'] == 'authentication_failed'
    assert len(calls) == 1
    assert result['calls'][0]['providerError']['code'] == 30
    assert 'check-secret' not in (tmp_path / 'r.json').read_text()
    assert 'check-secret' not in capsys.readouterr().out


def test_zero_based_fallback_is_bounded_and_recorded(tmp_path, monkeypatch):
    def empty_page(name, args):
        if name in ('GetFindTyre', 'GetFindDisk') and args['page'] == 1:
            item = 'TyrePriceRest' if name == 'GetFindTyre' else 'DiskPriceRest'
            return {'error': None, 'totalPages': 1, 'price_rest_list': {item: []}}
        return NotImplemented
    calls = install(monkeypatch, empty_page)
    result = check.run_catalog_check(tmp_path / 'r.json', tmp_path / 'r.html', client)
    assert result['status'] == 'sample_verified'
    assert len(calls) == check.MAX_CALLS == 9
    assert all(c['searchPage'] == 0 for c in result['categories'].values())


def test_no_goods_is_partial_not_claimed_as_verified(tmp_path, monkeypatch):
    def no_disks(name, args):
        if name == 'GetFindDisk':
            return {'error': None, 'totalPages': 0, 'price_rest_list': None}
        return NotImplemented
    install(monkeypatch, no_disks)
    result = check.run_catalog_check(tmp_path / 'r.json', tmp_path / 'r.html', client)
    assert result['status'] == 'partial'
    assert result['categories']['tyres']['verified'] is True
    assert result['categories']['wheels']['verified'] is False


def test_details_or_prices_missing_cannot_be_called_verified(tmp_path, monkeypatch):
    def no_prices(name, args):
        if name == 'GetGoodsPriceRestByCode':
            return {'error': None, 'price_rest_list': None}
        return NotImplemented
    install(monkeypatch, no_prices)
    result = check.run_catalog_check(tmp_path / 'r.json', tmp_path / 'r.html', client)
    assert result['status'] == 'partial'
    assert all(not c['verified'] for c in result['categories'].values())


def test_unknown_codes_never_get_joined_to_requested_product():
    info = {'tyreList': {'TyreContainer': [row('wanted'), row('unwanted')]}}
    prices = {'price_rest_list': {'price_rest': [stock('unwanted')]}}
    result = check.normalise_products('tyres', ['wanted'], info, prices, {})
    assert [r['code'] for r in result] == ['wanted']
    assert result[0]['offers'] == []


def test_duplicate_warehouse_offers_are_rejected_instead_of_counted_twice():
    info = {'tyreList': {'TyreContainer': [row('wanted')]}}
    s = stock('wanted')
    s['whpr']['wh_price_rest'].append(s['whpr']['wh_price_rest'][0].copy())
    with pytest.raises(check.ResponseMappingRequired):
        check.normalise_products('tyres', ['wanted'], info, {'price_rest_list': {'price_rest': [s]}}, {})


def test_diagnostic_samples_cannot_be_mistaken_for_live_arrays():
    with pytest.raises(check.ResponseMappingRequired):
        check.array_at({'items': {'count': 41, 'firstItems': []}}, 'items')


@pytest.mark.parametrize('value', [None, 0, -1, True, 'NaN', 'Infinity', '-Infinity', 'invalid'])
def test_invalid_or_zero_price_stock_is_not_treated_as_available(value):
    assert check.positive_number(value) is False


def test_html_escapes_supplier_content_and_removes_unsafe_photo_links(tmp_path, monkeypatch):
    def hostile(name, args):
        if name == 'GetGoodsInfo':
            codes = args['code_list']['string']
            container, item = ('tyreList', 'TyreContainer') if codes[0].startswith('T') else ('rimList', 'RimContainer')
            return {container: {item: [{**row(c), 'name': '<script>check-secret</script>',
                                       'img_big_my': 'javascript:alert(1)'} for c in codes]}}
        return NotImplemented
    install(monkeypatch, hostile)
    check.run_catalog_check(tmp_path / 'r.json', tmp_path / 'r.html', client)
    html = (tmp_path / 'r.html').read_text()
    assert '<script>' not in html and 'javascript:' not in html
    assert '&lt;script&gt;' in html
    assert 'check-secret' not in html
    assert 'Content-Security-Policy' in html


def test_transport_failure_saves_new_readable_report_without_credentials(tmp_path, monkeypatch):
    def fail():
        raise TimeoutError('Secret transport details')
    monkeypatch.setattr('builtins.input', lambda *_: pytest.fail('No credentials on failure'))
    result = check.run_catalog_check(tmp_path / 'r.json', tmp_path / 'r.html', fail)
    assert result['status'] == 'check_failed'
    assert result['credentialsSent'] is False
    assert 'Secret transport details' not in (tmp_path / 'r.json').read_text()
    assert 'Проверка не завершена' in (tmp_path / 'r.html').read_text()
