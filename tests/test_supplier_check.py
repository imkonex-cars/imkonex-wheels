import json
from types import SimpleNamespace

import pytest
import requests
from zeep import xsd

from backend.supplier_check import (
    MappingRequired, build_arguments, describe_type, provider_error, redact_literals, run_check, scrub,
)


def operation(*fields):
    return SimpleNamespace(input=SimpleNamespace(body=SimpleNamespace(type=xsd.ComplexType(xsd.Sequence(list(fields))))))


def auth_fields():
    return [xsd.Element('login', xsd.String()), xsd.Element('password', xsd.String())]


def test_maps_observed_credentials_but_never_invents_required_fields():
    op = operation(*auth_fields(), xsd.Element('partnerId', xsd.Int()))
    with pytest.raises(MappingRequired):
        build_arguments(op, 'tester', 'secret')
    op = operation(*auth_fields(), xsd.Element('extra', xsd.String(), min_occurs=0))
    assert build_arguments(op, 'tester', 'secret') == {'login': 'tester', 'password': 'secret'}


def test_maps_nested_auth_and_single_article_array():
    op = operation(
        xsd.Element('auth', xsd.ComplexType(xsd.Sequence(auth_fields()))),
        xsd.Element('codes', xsd.ComplexType(xsd.Sequence([
            xsd.Element('string', xsd.String(), max_occurs='unbounded')]))),
    )
    assert build_arguments(op, 'tester', 'secret', '000123') == {
        'auth': {'login': 'tester', 'password': 'secret'}, 'codes': {'string': ['000123']}}


def test_code_cannot_be_lost_or_converted_to_number():
    with pytest.raises(MappingRequired):
        build_arguments(operation(*auth_fields()), 'tester', 'secret', '000123')
    with pytest.raises(MappingRequired):
        build_arguments(operation(*auth_fields(), xsd.Element('code', xsd.Int())), 'tester', 'secret', '000123')


def test_optional_unmapped_branch_does_not_count_as_auth():
    incomplete = xsd.ComplexType(xsd.Sequence([*auth_fields(), xsd.Element('requiredUnknown', xsd.Int())]))
    with pytest.raises(MappingRequired):
        build_arguments(operation(xsd.Element('auth', incomplete, min_occurs=0)), 'tester', 'secret')


def test_public_schema_contains_details_needed_for_mapping():
    details = describe_type(operation(*auth_fields()).input.body.type)
    assert details['fields'][0]['name'] == 'login'
    assert details['fields'][0]['minOccurs'] == 1
    assert 'string' in details['fields'][0]['type']


def test_diagnostic_redacts_nested_secrets_and_limits_response_samples():
    raw = {'Password': 'secret-value', 'unknownEcho': 'login=account-x password=secret-value',
           'items': [{'AccessToken': 'abc', 'price': 1300, 'stock': 4}] * 20}
    result = scrub(raw, ('account-x', 'secret-value'))
    text = json.dumps(result)
    assert all(secret not in text for secret in ('account-x', 'secret-value', 'abc'))
    assert result['items']['count'] == 20
    assert len(result['items']['firstItems']) == 3
    assert result['items']['firstItems'][0]['price'] == 1300


def test_literal_secret_redaction_preserves_json_numbers_and_booleans():
    cleaned = redact_literals({'flag': True, 'stock': 123, 'echo': 'true 123'}, ('true', '123'))
    assert json.loads(json.dumps(cleaned)) == {'flag': True, 'stock': 123, 'echo': '[redacted] [redacted]'}


@pytest.mark.parametrize('response,expected', [
    ({'ErrorCode': 0, 'items': []}, False),
    ({'ErrorCode': 7}, True),
    ({'Result': {'ErrorMessage': 'Invalid credentials'}}, True),
    ({'success': True, 'error': {'code': None, 'comment': None}}, False),
    ({'success': True, 'error': {'code': 0, 'comment': '   '}}, False),
    ({'success': False, 'error': {'code': None, 'comment': None}}, True),
    ({'success': False, 'error': None}, True),
    ({'success': True, 'error': {'code': 7, 'comment': None}}, True),
    ({'success': True, 'error': {'code': None, 'comment': 'Access denied'}}, True),
    ({'errors': [None, {'code': '0', 'comment': ''}]}, False),
    ({'errors': [None, {'code': '1', 'comment': ''}]}, True),
])
def test_explicit_provider_errors_stop_further_requests(response, expected):
    assert provider_error(response) == expected


def test_network_failure_saves_report_without_asking_for_credentials(tmp_path, monkeypatch):
    def fail_client():
        raise requests.Timeout('provider detail which must not be copied')
    def forbidden_input(*args):
        raise AssertionError('Must not ask for credentials before loading schema')
    monkeypatch.setattr('builtins.input', forbidden_input)
    target = tmp_path / 'report.json'
    result = run_check(target, client_factory=fail_client)
    saved = json.loads(target.read_text())
    assert saved == result
    assert saved['status'] == 'check_failed'
    assert saved['error']['type'] == 'timeout'
    assert saved['credentialsSent'] is False
    assert 'provider detail' not in target.read_text()


def test_authenticated_flow_only_calls_read_methods_and_stops_after_provider_error(tmp_path, monkeypatch):
    from backend import supplier_check
    ops = {name: operation(*auth_fields(), *([xsd.Element('code', xsd.String())] if name != 'GetWarehouses' else []))
           for name in ['GetWarehouses', 'GetGoodsInfo', 'GetGoodsPriceRestByCode']}
    fake_client = SimpleNamespace(service=SimpleNamespace(_binding=SimpleNamespace(_operations=ops)), transport=None)
    calls = []
    def fake_probe(client, name, arguments):
        calls.append(name)
        assert arguments['login'] == 'check-account'
        assert arguments['password'] == 'check-password'
        return {'ErrorCode': 4, 'ErrorMessage': 'Denied: check-password'}
    monkeypatch.setenv('FOURTOCHKI_LOGIN', 'check-account')
    monkeypatch.setenv('FOURTOCHKI_PASSWORD', 'check-password')
    monkeypatch.setattr(supplier_check, 'describe_operation', lambda op: {'input': describe_type(op.input.body.type)})
    monkeypatch.setattr(supplier_check, 'probe', fake_probe)
    path = tmp_path / 'report.json'
    report = run_check(path, sample_sku='000123', client_factory=lambda: fake_client)
    assert calls == ['GetWarehouses']
    assert report['status'] == 'provider_error'
    assert report['catalogIntegrationVerified'] is False
    assert 'check-password' not in path.read_text()
    assert 'check-account' not in path.read_text()


def test_authenticated_flow_fetches_only_the_requested_article(tmp_path, monkeypatch):
    from backend import supplier_check
    ops = {name: operation(*auth_fields(), *([xsd.Element('code', xsd.String())] if name != 'GetWarehouses' else []))
           for name in ['GetWarehouses', 'GetGoodsInfo', 'GetGoodsPriceRestByCode']}
    fake_client = SimpleNamespace(service=SimpleNamespace(_binding=SimpleNamespace(_operations=ops)), transport=None)
    calls = []
    def fake_probe(client, name, arguments):
        calls.append(name)
        if name != 'GetWarehouses':
            assert arguments['code'] == '000123'
        return {'ErrorCode': 0, 'Items': [{'Code': '000123', 'Stock': 4, 'Price': 5000}]}
    monkeypatch.setenv('FOURTOCHKI_LOGIN', 'check-account')
    monkeypatch.setenv('FOURTOCHKI_PASSWORD', 'check-password')
    monkeypatch.setattr(supplier_check, 'describe_operation', lambda op: {'input': describe_type(op.input.body.type)})
    monkeypatch.setattr(supplier_check, 'probe', fake_probe)
    report = run_check(tmp_path / 'report.json', sample_sku='000123', client_factory=lambda: fake_client)
    assert calls == ['GetWarehouses', 'GetGoodsInfo', 'GetGoodsPriceRestByCode']
    assert report['status'] == 'responses_received'
    assert all(c['businessSuccess'] == 'requires_response_review' for c in report['calls'])


def test_observed_warehouse_success_does_not_block_article_requests(tmp_path, monkeypatch):
    from backend import supplier_check
    # Exact status/error envelope from the user's report. Warehouse excerpt only;
    # this is not a new live API call or a claim that all 41 rows are present here.
    warehouse_response = {
        'error': {'code': None, 'comment': None}, 'success': True,
        'warehouses': {'WarehouseInfo': {'count': 41, 'firstItems': [
            {'id': 2017, 'key': 'chelyab2', 'name': 'Челябинск 2',
             'haveDelivery': True, 'havePickup': True, 'logisticDays': 0},
            {'id': 754, 'key': 'oh_cheltrt', 'name': 'ОХ г. Челябинск Троицкий'},
            {'id': 1524, 'key': 'oh_chstosh', 'name': 'ОХ г. Челябинск СТОШИН'},
        ]}},
    }
    assert not provider_error(warehouse_response)
    ops = {name: operation(*auth_fields(), *([xsd.Element('code', xsd.String())] if name != 'GetWarehouses' else []))
           for name in ['GetWarehouses', 'GetGoodsInfo', 'GetGoodsPriceRestByCode']}
    client = SimpleNamespace(service=SimpleNamespace(_binding=SimpleNamespace(_operations=ops)), transport=None)
    calls = []
    def fake_probe(client, name, arguments):
        calls.append(name)
        if name == 'GetWarehouses':
            return warehouse_response
        assert arguments['code'] == 'YSTX5R1510'
        # Synthetic follow-up envelope; no real product data has been received.
        return {'success': True, 'error': {'code': None, 'comment': None}}
    monkeypatch.setenv('FOURTOCHKI_LOGIN', 'test-account')
    monkeypatch.setenv('FOURTOCHKI_PASSWORD', 'test-password')
    monkeypatch.setattr(supplier_check, 'describe_operation', lambda op: {'input': describe_type(op.input.body.type)})
    monkeypatch.setattr(supplier_check, 'probe', fake_probe)
    report = run_check(tmp_path / 'report.json', sample_sku='YSTX5R1510', client_factory=lambda: client)
    assert calls == ['GetWarehouses', 'GetGoodsInfo', 'GetGoodsPriceRestByCode']
    assert report['status'] == 'responses_received'
    assert report['catalogIntegrationVerified'] is False
