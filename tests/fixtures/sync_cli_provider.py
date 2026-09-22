"""Run the actual CLI entry point with synthetic, strictly offline SOAP replies.

This file is a test harness only. No production code loads it.
"""
import json
import os
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
from unittest.mock import patch
import warnings

from zeep import xsd

source = Path(sys.argv[1])
scenario = sys.argv[2]
sys.path.append(str(source / 'tests'))
from test_catalog_sync import FakeSupplier


def observed_type(description):
    if not description.get('fields'):
        return xsd.Schema().get_type(description['type'])
    return xsd.ComplexType(xsd.Sequence([
        xsd.Element(field['name'], observed_type(field), min_occurs=field['minOccurs'],
                    max_occurs=field['maxOccurs'] if field['maxOccurs'] == 'unbounded' else int(field['maxOccurs']),
                    nillable=field['nillable']) for field in description['fields']
    ]))


schemas = json.loads((source / 'tests/fixtures/fourtochki_inputs_20260917.json').read_text())
operations = {name: SimpleNamespace(input=SimpleNamespace(body=SimpleNamespace(type=observed_type(schema))))
              for name, schema in schemas.items()}
client = SimpleNamespace(
    service=SimpleNamespace(_binding=SimpleNamespace(_operations=operations)),
    transport=SimpleNamespace(session=SimpleNamespace(headers={}, close=lambda: None)),
)


def change(name, options):
    result = FakeSupplier()(name, **options)
    if name == 'GetGoodsInfo' and scenario in ('parameters', 'drop'):
        for container in result.values():
            for rows in container.values():
                for row in rows:
                    if (scenario == 'parameters' and row['code'] == 'T00000') or (
                            scenario == 'drop' and row['code'][-5:] != '00000'):
                        row['width'] = 'unsupported-size'
    if name == 'GetFindTyre' and scenario in ('retail', 'stock', 'warehouse'):
        rows = result['price_rest_list']['TyrePriceRest']
        if rows:
            field, value = {'retail': ('price_rozn', 'NaN'), 'stock': ('rest', -1),
                            'warehouse': ('wrh', 999999)}[scenario]
            rows[0]['whpr']['wh_price_rest'][0][field] = value
    return result


supplier = FakeSupplier(override=change)


def probe(client, name, arguments):
    options = {}
    if name in ('GetFindTyre', 'GetFindDisk'):
        options['page'] = arguments['page']
    elif name == 'GetGoodsInfo':
        options['codes'] = arguments['code_list']['string']
    return supplier(name, **options)


def forbid_network(*args, **kwargs):
    raise AssertionError('No real network request is allowed in the CLI regression test')


with patch('backend.supplier_check.create_check_client', return_value=client), \
        patch('backend.supplier.probe', side_effect=probe), \
        patch('requests.sessions.Session.request', side_effect=forbid_network), \
        patch('backend.catalog_photos.download_photo', side_effect=forbid_network), \
        patch('time.sleep'), \
        patch.dict(os.environ, {'FOURTOCHKI_LOGIN': 'fixture-login', 'FOURTOCHKI_PASSWORD': 'fixture-password'}), \
        patch.object(sys, 'argv', ['backend.catalog_sync', '--config', 'config/sync.json', '--dry-run']), \
        warnings.catch_warnings():
    # The imported helper already loads the canonical module. Executing it as
    # __main__ must still use the same exception classes as its dependencies.
    warnings.filterwarnings('ignore', message=".*found in sys.modules.*", category=RuntimeWarning)
    runpy.run_module('backend.catalog_sync', run_name='__main__')
