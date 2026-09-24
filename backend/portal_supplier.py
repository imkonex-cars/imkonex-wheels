"""Private SOAP adapter. Contracts: official 4tochki Help, checked 2026-09-22.

Array element names and XML field order are resolved from the live WSDL.
No credentials, purchase prices or raw responses are logged or published.
CreateOrder is never retried here, including after a network timeout.
"""
import os
import threading
import time
from decimal import Decimal
from urllib.parse import urlsplit

from .supplier_check import create_check_client, provider_error


class SupplierFailure(RuntimeError):
    def __init__(self, code, provider_code=None):
        super().__init__(code)
        self.code, self.provider_code = code, provider_code


READS = frozenset({'GetMarkaAvto', 'GetModelAvto', 'GetYearAvto',
    'GetModificationAvto', 'GetGoodsByCar', 'GetGoodsPriceRestByCode',
    'GetWarehouses', 'GetOrderInfo2', 'GetGoodsInfo', 'GetCustomerList', 'GetAddressList'})


def pack(type_, value):
    """Pack documented values into the actual WSDL; reject unknown fields."""
    fields = dict(getattr(type_, 'elements', []))
    if not fields:
        return value
    if isinstance(value, list):
        if len(fields) != 1:
            raise SupplierFailure('supplier_schema_changed')
        name, element = next(iter(fields.items()))
        if not element.accepts_multiple:
            raise SupplierFailure('supplier_schema_changed')
        return {name: [pack(element.type, item) for item in value]}
    if not isinstance(value, dict):
        raise SupplierFailure('supplier_schema_changed')
    result = {}
    for name, child in value.items():
        matches = [field for field in fields if field.casefold() == name.casefold()]
        if len(matches) != 1:
            raise SupplierFailure('supplier_schema_changed')
        actual = matches[0]
        element = fields[actual]
        if element.accepts_multiple:
            if not isinstance(child, list):
                raise SupplierFailure('supplier_schema_changed')
            result[actual] = [pack(element.type, item) for item in child]
        else:
            result[actual] = pack(element.type, child)
    return result


def array(value, name):
    value = value.get(name) if isinstance(value, dict) else None
    if value is None:
        return []
    if isinstance(value, dict) and len(value) == 1:
        value = next(iter(value.values()))
    if value is None:
        return []
    if not isinstance(value, list):
        raise SupplierFailure('supplier_response_changed')
    return value


def public_text(value, limit=300):
    return ('' if value is None else str(value))[:limit]


class PortalSupplier:
    def __init__(self, *, login=None, password=None):
        # Explicit profiles never fall back to the global account.
        if (login is None) != (password is None):
            raise ValueError("Both explicit credentials are required")
        self._explicit = login is not None
        self._login, self._password = login, password
        self.lock = threading.RLock()
        self.client = None

    @property
    def configured(self):
        return bool(self._credentials()[0] and self._credentials()[1])

    def _credentials(self):
        if self._explicit:
            return self._login, self._password
        return os.getenv('FOURTOCHKI_LOGIN', ''), os.getenv('FOURTOCHKI_PASSWORD', '')

    def call(self, name, arguments=None, *, write=False):
        if (write and name != 'CreateOrder') or (not write and name not in READS):
            raise SupplierFailure('supplier_operation_forbidden')
        if not self.configured:
            raise SupplierFailure('supplier_not_configured')
        from zeep.helpers import serialize_object
        with self.lock:
            try:
                if self.client is None:
                    self.client = create_check_client()
                operations = self.client.service._binding._operations
                if name not in operations:
                    raise SupplierFailure('supplier_method_unavailable')
                login, password = self._credentials()
                # Callers cannot replace the selected account through arguments.
                values = {**(arguments or {}), 'login': login, 'password': password}
                packed = pack(operations[name].input.body.type, values)
                # Build XML before making a write: shape errors cannot create an order.
                self.client.create_message(self.client.service, name, **packed)
            except SupplierFailure:
                raise
            except Exception:
                raise SupplierFailure('supplier_schema_unavailable') from None
            self.client.transport.deadline = time.monotonic() + 50
            try:
                result = serialize_object(getattr(self.client.service, name)(**packed))
            except Exception:
                # The caller must regard write results as unknown and never resend.
                raise SupplierFailure('order_result_unknown' if write else 'supplier_unavailable') from None
            if isinstance(result, dict) and name + 'Result' in result:
                result = result[name + 'Result']
            if not isinstance(result, dict):
                raise SupplierFailure('order_result_unknown' if write else 'supplier_response_changed')
            if provider_error(result) and not (write and type(result.get('orderID')) is int and result['orderID'] > 0):
                code = (result.get('error') or {}).get('code')
                raise SupplierFailure('supplier_rejected', code if type(code) is int else None)
            return result

    def fitment(self, stage, p):
        if stage == 'makes':
            return array(self.call('GetMarkaAvto'), 'marka_list')
        arguments = {'marka': p['make']}
        if stage == 'models':
            return array(self.call('GetModelAvto', arguments), 'model_list')
        arguments['model'] = p['model']
        if stage == 'years':
            rows = array(self.call('GetYearAvto', arguments), 'yearAvto_list')
            return [{'begin': int(r['year_begin']), 'end': int(r['year_end'])} for r in rows]
        arguments.update(year_beg=p['begin'], year_end=p['end'])
        if stage == 'modifications':
            return array(self.call('GetModificationAvto', arguments), 'modification_list')
        if stage != 'products':
            raise SupplierFailure('invalid_fitment_stage')
        arguments.update(year_beg=str(p['begin']), year_end=str(p['end']),
                         modification=p['modification'], podbor_type=[1], type=['tire', 'disk'])
        # Only standard fitments. No guessed dimensions or generic model lists.
        result = self.call('GetGoodsByCar', {'filter': arguments})
        return [public_text(r.get('code'), 100) for r in array(result, 'price_rest_list') if r.get('code')]

    def purchase(self, codes):
        result = self.call('GetGoodsPriceRestByCode', {'filter': {
            'code_list': codes, 'searchCodeByOccurence': False, 'include_paid_delivery': True}})
        rows = []
        for product in array(result, 'price_rest_list'):
            code = public_text(product.get('code'), 100)
            if code not in codes:
                raise SupplierFailure('supplier_response_changed')
            for offer in array(product, 'whpr'):
                cost = Decimal(str(offer.get('price') or 0))
                stock, warehouse = offer.get('rest'), offer.get('wrh')
                if not cost.is_finite() or not Decimal('0') <= cost <= Decimal('100000000'):
                    raise SupplierFailure('supplier_response_changed')
                if type(stock) is not int or stock < 0 or type(warehouse) is not int or warehouse <= 0:
                    raise SupplierFailure('supplier_response_changed')
                retail = offer.get('price_rozn')
                if retail is not None:
                    retail = Decimal(str(retail))
                    if not retail.is_finite() or not 0 <= retail <= 100000000:
                        raise SupplierFailure('supplier_response_changed')
                rows.append({'sku': code, 'warehouseId': warehouse,
                             'purchasePrice': float(cost), 'supplierRetailPrice': float(retail) if retail is not None else None, 'stock': stock})
        return rows

    def warehouses(self, address_id=None):
        from .manager_catalog import warehouse_meta
        if address_id is not None and (type(address_id) is not int or address_id <= 0):
            raise SupplierFailure('invalid_address_id')
        result=self.call('GetWarehouses', {'address_id': address_id} if address_id is not None else None)
        rows=array(result,'warehouses')
        if not rows or any(type(r.get('id')) is not int or r['id']<=0 for r in rows):
            raise SupplierFailure('supplier_response_changed')
        return [warehouse_meta(r) for r in rows]

    def details(self, code, kind):
        from .product_attributes import product_attributes
        containers={'tires':['tyreList'],'wheels':['rimList'],'tubes':['cameraList'],
                    'sensors':['pressureSensorList'],'oils':['oilList'],
                    'consumables':['fastenerList','sparePartList','cameraList']}
        result=self.call('GetGoodsInfo',{'code_list':[code]})
        rows=[r for name in containers.get(kind,[]) for r in array(result,name) if r.get('code')==code]
        if len(rows)!=1:raise SupplierFailure('supplier_response_changed')
        return product_attributes(kind,rows[0])

    def create_order(self, draft):
        order = {'product_list': [{'code': r['sku'], 'quantity': r['quantity'],
                                  'wrh': r['warehouseId'], 'priceIn': Decimal(str(r['salePrice']))}
                                 for r in draft['lines']],
                 'comment': 'IMKONEX ' + draft['id'], 'base_order': {'orderNumber': draft['id']},
                 'is_test': draft['test'],
                 'skip_error_61': False, 'useBonus': False}
        if draft.get('customerId'):
            order['customerID'] = draft['customerId']
        result = self.call('CreateOrder', {'order': order}, write=True)
        if type(result.get('orderID')) is not int or result['orderID'] <= 0:
            raise SupplierFailure('order_result_unknown')
        # Keep any accepted identifier even if the provider returns fewer lines.
        return {'providerId': result['orderID'], 'providerNumber': public_text(result.get('orderNumber'), 100),
                'providerCreatedAt': public_text(result.get('createDate'), 100),
                'providerReviewRequired': result.get('success') is not True or provider_error(result)
                    or provider_error({'errors': result.get('error_product_list')})}

    def order_info(self, provider_id):
        result = self.call('GetOrderInfo2', {'orderId': provider_id})
        # Explicit private DTO. Verification codes and raw provider URLs are omitted.
        return {key: public_text(result.get(key), 1000) for key in (
            'statusName', 'statusKey', 'paymentPercent', 'shipmentDate', 'shipmentType',
            'deliveryAddress', 'pickupWarehouseName', 'pickupWarehouseAddress',
            'deliveryIntervalStart', 'deliveryIntervalEnd', 'payBefore', 'parent')}


    def customers(self):
        """Private buyer identities returned by the selected API account."""
        rows = array(self.call('GetCustomerList'), 'Items')
        if any(type(r.get('ID')) is not int or r['ID'] <= 0 or not isinstance(r.get('name'), str)
               or type(r.get('isLegal')) is not bool for r in rows):
            raise SupplierFailure('supplier_response_changed')
        return [{'id': r['ID'], 'name': public_text(r['name'], 300), 'isLegal': r['isLegal']} for r in rows]

    def addresses(self):
        """Address book, including receiving schedules; not promised delivery slots."""
        # The outer filter is required; 0 explicitly means any payment type.
        rows = array(self.call('GetAddressList', {'filter': {'paymentType': 0}}), 'getAddressListItems')
        result = []
        for row in rows:
            if type(row.get('addressId')) is not int or row['addressId'] <= 0:
                raise SupplierFailure('supplier_response_changed')
            customer_id = row.get('customerId')
            if customer_id is not None and (type(customer_id) is not int or customer_id <= 0):
                raise SupplierFailure('supplier_response_changed')
            schedule = array(row, 'schedule')
            result.append({'id': row['addressId'], 'customerId': customer_id,
                **{key: public_text(row.get(key), 500) for key in (
                    'addressName', 'cityName', 'streetName', 'houseNumber', 'housing',
                    'building', 'office', 'paymentType', 'contactName', 'phoneNumber')},
                'schedule': [{key: public_text(day.get(key), 100) for key in
                    ('dayOfWeek', 'hourFrom', 'hourTo')} | {'enabled': day.get('enabled') is True}
                    for day in schedule]})
        return result

    def order_fulfillment(self, provider_id):
        """Private pickup secret DTO; NEVER publish based on paymentPercent.

        The retail payment ledger and authenticated customer/order ownership must
        gate any future customer endpoint. No URL is fetched by this method.
        """
        if type(provider_id) is not int or provider_id <= 0:
            raise SupplierFailure('invalid_provider_order_id')
        result = self.call('GetOrderInfo2', {'orderId': provider_id})
        dto = {key: public_text(result.get(key), 1000) for key in (
            'statusName', 'statusKey', 'shipmentDate', 'shipmentType',
            'deliveryAddress', 'pickupWarehouseName', 'pickupWarehouseAddress',
            'deliveryIntervalStart', 'deliveryIntervalEnd')}
        code = result.get('verificationCode')
        dto['verificationCode'] = public_text(code, 300) if isinstance(code, str) else ''
        qr = result.get('verificationQR')
        dto['verificationQR'] = safe_verification_qr(qr)
        return dto


def safe_verification_qr(value):
    """Allow only HTTPS supplier URLs; absence/unknown representation stays empty."""
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        return ''
    try:
        url = urlsplit(value)
        if url.scheme != 'https' or url.username is not None or url.password is not None:
            return ''
        if url.port not in (None, 443) or url.hostname not in ('b2b.4tochki.ru', 'api-b2b.4tochki.ru'):
            return ''
    except ValueError:
        return ''
    return value
