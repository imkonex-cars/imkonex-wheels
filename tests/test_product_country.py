"""Country of manufacture must not be guessed from an unverified API alias."""
import pytest

from backend.product_attributes import product_attributes


@pytest.mark.parametrize('kind', ['tires', 'wheels', 'tubes', 'sensors', 'oils', 'consumables'])
def test_country_is_not_inferred_from_brand_or_unverified_api_fields(kind):
    detail = {
        'brand': 'Example Russian brand',
        'country': 'UNVERIFIED_COUNTRY',
        'brand_country': 'BRAND_ORIGIN_ONLY',
        'country_of_manufacture': 'UNVERIFIED_ALIAS',
        'manufacturer_code_list': ['BRAND_ORIGIN_ONLY-123'],
    }
    assert product_attributes(kind, detail) == []


def test_missing_country_does_not_drop_supported_characteristics():
    assert product_attributes('tires', {'noise': '72 дБ', 'country': 'UNVERIFIED_COUNTRY'}) == [
        {'label': 'Шумность', 'value': '72 дБ'},
    ]
