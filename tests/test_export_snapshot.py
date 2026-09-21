from copy import deepcopy
import json
import pytest
from backend.export_snapshot import convert_report, load_report


@pytest.fixture
def report():
    offer = {'warehouseId': 2017, 'warehouseName': 'Челябинск 2', 'warehouseKnown': True,
             'rest': 2, 'price': '9876543', 'price_rozn': '1000.25'}
    def category(product):
        return {'verified': True, 'currencyRate': {'charCode': 'RUB', 'nominal': 1, 'value': '1.0'},
                'products': [product]}
    tire = {'code': '00123', 'brand': 'Example', 'model': 'Winter', 'name': '225/45R18 XL',
            'photoUrl': None, 'offers': [deepcopy(offer)],
            'parameters': {'diameter': '18', 'width': '225', 'height': '45', 'season': 'w',
                           'load_index': '95', 'speed_index': 'V', 'thorn': False, 'strengthening': False}}
    rim = {'code': 'RIM01', 'brand': 'Example', 'model': 'Disk', 'name': '6x15 4x100',
           'photoUrl': None, 'offers': [deepcopy(offer)],
           'parameters': {'diameter': '15', 'width': '6', 'bolts_count': 4, 'bolts_spacing': '100',
                          'bolt_spacing2': '0', 'et': '45', 'dia': '54.1', 'color': 'BD', 'type': 0}}
    return {'checkVersion': '1.3', 'status': 'sample_verified', 'checkedAt': '2026-09-17T19:22:00+00:00',
            'password': 'PRIVATE_TEST_ONLY', 'categories': {'tyres': category(tire), 'wheels': category(rim)},
            'calls': [{'operation': 'GetGoodsInfo', 'status': 'response_received', 'response': {
                'tyreList': {'TyreContainer': {'count': 1, 'firstItems': [{'code': '00123', 'constr': 'R', 'tonnage': 'XL'}]}},
                'rimList': {'RimContainer': {'count': 1, 'firstItems': [{'code': 'RIM01', 'color_explanation': 'Чёрный'}]}}}}]}


def test_export_is_public_and_preserves_known_vs_unknown(report):
    snapshot = convert_report(report)
    rendered = json.dumps(snapshot)
    assert '9876543' not in rendered and 'PRIVATE_TEST_ONLY' not in rendered
    tire, rim = snapshot['products']
    assert tire['sku'] == '00123' and tire['xl'] is True
    assert tire['runflat'] is None and rim['type'] is None
    assert tire['offers'][0]['price'] == 1000.25
    assert tire['offers'][0]['days'] is None
    assert snapshot['mode'] == 'snapshot'


@pytest.mark.parametrize('retail', [None, '', '0', 0])
def test_missing_retail_price_never_falls_back_to_purchase_price(report, retail):
    report['categories']['tyres']['products'][0]['offers'][0]['price_rozn'] = retail
    assert convert_report(report)['products'][0]['offers'] == []


@pytest.mark.parametrize('change', ['auth', 'currency', 'details', 'duplicate', 'warehouse', 'photo'])
def test_unverified_or_ambiguous_input_is_rejected(report, change):
    source = report['categories']['tyres']['products'][0]
    if change == 'auth': report['status'] = 'provider_error'
    if change == 'currency': report['categories']['tyres']['currencyRate']['charCode'] = 'USD'
    if change == 'details': report['calls'] = []
    if change == 'duplicate': source['offers'] *= 2
    if change == 'warehouse': source['offers'][0]['warehouseKnown'] = False
    if change == 'photo': source['photoUrl'] = 'https://untrusted.invalid/picture.png'
    with pytest.raises(ValueError): convert_report(report)


def test_fenced_report_loading_does_not_write_or_send_input(report, tmp_path):
    file = tmp_path / 'report.md'
    file.write_text('```json\n' + json.dumps(report) + '\n```', encoding='utf-8')
    assert load_report(file) == report
    assert len(list(tmp_path.iterdir())) == 1
