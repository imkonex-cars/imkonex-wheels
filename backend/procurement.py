"""Isolated company procurement previews: no supplier writes or shared cost cache.

This review release deliberately blocks submit until the full delivery/contract
mapping has been verified. Configure each company's credentials explicitly;
identical credentials may be configured for both only if the account lists both
buyers. Contract numbers and website address IDs never identify API customers.
"""
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import hmac
import json
import os
import re
import secrets
import time

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .portal_supplier import PortalSupplier, SupplierFailure
from .pricing import policy_for, sale_price


@dataclass(frozen=True)
class Company:
    id: str
    name: str
    contract: str
    prefix: str
    name_token: str


COMPANIES = (
    Company('alites', 'ООО «АЛИТЕС»', 'Договор оферты №66063 от 21.10.2025', 'FOURTOCHKI_ALITES', 'алитес'),
    Company('inveks', 'ИНВЭКС ООО', 'Договор 804 Ч/Гр-дп от 10.11.2025', 'FOURTOCHKI_INVEKS', 'инвэкс'),
)


@dataclass(frozen=True)
class Account:
    company: Company
    customer_id: int | None
    login: str = field(repr=False)
    password: str = field(repr=False)
    invalid_customer_id: bool = False

    @property
    def ready(self):
        return bool(self.login and self.password and self.customer_id and not self.invalid_customer_id)

    def public(self):
        return {'id': self.company.id, 'name': self.company.name, 'contract': self.company.contract,
                'customerId': self.customer_id, 'configured': self.ready, 'verified': False,
                'state': 'ready_to_verify' if self.ready else 'configuration_required',
                'canSubmit': False, 'blockedReason': 'delivery_contract_mapping_not_verified'}


class Line(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    productId: str = Field(min_length=1, max_length=160)
    warehouseId: int = Field(gt=0, strict=True)
    quantity: int = Field(ge=1, le=1000, strict=True)


class Preview(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    companyId: str = Field(min_length=1, max_length=30)
    lines: list[Line] = Field(min_length=1, max_length=50)


def load_account(company_id, environ):
    company = next((c for c in COMPANIES if c.id == company_id), None)
    if company is None:
        raise HTTPException(404, 'procurement_company_not_found')
    raw = environ.get(company.prefix + '_CUSTOMER_ID', '')
    # No default buyer, contract-number parsing or URL-derived ID.
    customer_id = int(raw) if raw.isascii() and raw.isdecimal() and 0 < int(raw) < 2**31 else None
    return Account(company, customer_id, environ.get(company.prefix + '_LOGIN', ''),
                   environ.get(company.prefix + '_PASSWORD', ''), bool(raw and customer_id is None))


def _money(value):
    return float(Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def _public_draft(draft):
    return {key: value for key, value in draft.items() if not key.startswith('_')}


def register_procurement_routes(app, store, authenticated, throttle, by_id,
                                *, supplier_factory=PortalSupplier, environ=None):
    """Install authenticated `/api/manager/procurement/*` review routes.

    Manager UI: setupProcurement({api,shell,title,activeTab,getLines,toast});
    showProcurement() renders tab `procurement`; getLines() returns basket lines.
    These routes never use the global supplier account or the legacy orders table.
    """
    environ = os.environ if environ is None else environ
    with store.connect() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS procurement_drafts(
            id TEXT PRIMARY KEY, payload TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL)''')
        row = con.execute("SELECT value FROM settings WHERE key='procurement_fingerprint_salt'").fetchone()
        if row:
            salt = bytes.fromhex(json.loads(row['value']))
        else:
            salt = secrets.token_bytes(32)
            con.execute('INSERT INTO settings VALUES (?,?)', ('procurement_fingerprint_salt', json.dumps(salt.hex())))

    def fingerprint(account):
        content = json.dumps([account.company.id, account.customer_id, account.login, account.password], ensure_ascii=False)
        return hmac.new(salt, content.encode(), hashlib.sha256).hexdigest()

    def verified_account(company_id):
        account = load_account(company_id, environ)
        if not account.ready:
            raise HTTPException(409, 'procurement_company_not_configured')
        supplier = supplier_factory(login=account.login, password=account.password)
        matches = [row for row in supplier.customers() if row['id'] == account.customer_id]
        if len(matches) != 1 or matches[0].get('isLegal') is not True:
            raise HTTPException(409, 'procurement_customer_not_available')
        buyer = matches[0]
        # The account's real buyer must match the intended legal entity.
        if account.company.name_token not in re.findall(r'[а-яёa-z0-9]+', buyer['name'].casefold()):
            raise HTTPException(409, 'procurement_customer_identity_mismatch')
        return account, supplier, buyer

    @app.get('/api/manager/procurement/companies')
    def companies(session=Depends(authenticated)):
        return {'items': [load_account(c.id, environ).public() for c in COMPANIES],
                'mode': 'preview_only', 'canSubmit': False}

    @app.post('/api/manager/procurement/companies/{company_id}/verify')
    def verify(company_id: str, request: Request, session=Depends(authenticated)):
        throttle(request, 'procurement_verify', 10, 300)
        account, supplier, buyer = verified_account(company_id)
        store.audit('procurement_company_verified', company_id)
        return {**account.public(), 'verified': True, 'buyer': buyer,
                'contractVerified': False,
                'note': 'Покупатель подтверждён API. Номер договора, условия доставки и цены по договору требуют сверки.'}

    @app.post('/api/manager/procurement/companies/{company_id}/addresses')
    def addresses(company_id: str, request: Request, session=Depends(authenticated)):
        throttle(request, 'procurement_addresses', 10, 300)
        account, supplier, buyer = verified_account(company_id)
        # Unassigned addresses are not assumed to belong to either legal entity.
        rows = [row for row in supplier.addresses() if row['customerId'] == account.customer_id]
        return {'companyId': company_id, 'items': rows, 'scheduleType': 'receiving_hours',
                'note': 'Это адресная книга покупателя и часы приёма. Дата доставки заказа требует подтверждения поставщика.'}

    @app.post('/api/manager/procurement/preview')
    def preview(body: Preview, request: Request, session=Depends(authenticated)):
        throttle(request, 'procurement_preview', 15, 60)
        pairs = [(line.productId, line.warehouseId) for line in body.lines]
        if len(set(pairs)) != len(pairs):
            raise HTTPException(422, 'duplicate_order_line')
        products = []
        for line in body.lines:
            product = by_id.get(line.productId)
            if product is None:
                raise HTTPException(404, 'product_not_found')
            products.append(product)
        account, supplier, buyer = verified_account(body.companyId)
        codes = list(dict.fromkeys(p['sku'] for p in products))
        # Deliberately no Store.save_costs(): each company obtains isolated data.
        offers = supplier.purchase(codes)
        costs = {}
        now = time.time()
        for offer in offers:
            pair = (offer['sku'], offer['warehouseId'])
            if pair in costs:
                raise SupplierFailure('supplier_response_changed')
            costs[pair] = {'cost': offer['purchasePrice'], 'retail': offer.get('supplierRetailPrice'),
                           'stock': offer['stock'], 'updated': now}
        rules, offer_rules = store.rules(), store.offer_rules()
        policies = store.setting('category_pricing', {})
        lines = []
        for line, product in zip(body.lines, products):
            offer = next((o for o in product['offers'] if o['id'] == 'warehouse-' + str(line.warehouseId)), None)
            cost = costs.get((product['sku'], line.warehouseId))
            if not offer or not cost or cost['cost'] <= 0 or cost['stock'] < line.quantity:
                raise HTTPException(409, 'insufficient_supplier_stock')
            rule = offer_rules.get((product['id'], line.warehouseId), rules.get(product['id'], policy_for(product, policies)))
            sale = sale_price(offer, rule, cost)
            if sale is None:
                raise HTTPException(409, 'sale_price_unavailable')
            lines.append({'productId': product['id'], 'sku': product['sku'],
                          'name': product['brand'] + ' ' + product['model'],
                          'warehouseId': line.warehouseId, 'warehouse': offer['warehouse'],
                          'quantity': line.quantity, 'stock': cost['stock'],
                          'purchasePrice': cost['cost'], 'supplierRetailPrice': cost['retail'],
                          'salePrice': sale, 'profitPerUnit': _money(Decimal(str(sale)) - Decimal(str(cost['cost'])))})
        draft = {'id': 'IMXP-' + secrets.token_hex(12).upper(), 'status': 'preview_only',
                 'createdAt': now, 'expiresAt': now + 120, 'companyId': account.company.id,
                 'companyName': account.company.name, 'contract': account.company.contract,
                 'customerId': account.customer_id, 'buyerName': buyer['name'],
                 'contractVerified': False, 'priceScope': 'api_account',
                 'canSubmit': False, 'blockedReason': 'delivery_contract_mapping_not_verified',
                 'lines': lines, 'purchaseTotal': _money(sum(Decimal(str(l['purchasePrice'])) * l['quantity'] for l in lines)),
                 'saleTotal': _money(sum(Decimal(str(l['salePrice'])) * l['quantity'] for l in lines)),
                 '_credentialFingerprint': fingerprint(account)}
        with store.connect() as con:
            con.execute('DELETE FROM procurement_drafts WHERE expires < ?', (now,))
            con.execute('INSERT INTO procurement_drafts VALUES (?,?,?,?)',
                        (draft['id'], json.dumps(draft, ensure_ascii=False), now, draft['expiresAt']))
        store.audit('procurement_preview', draft['id'] + ':' + account.company.id)
        return _public_draft(draft)

    @app.get('/api/manager/procurement/drafts/{draft_id}')
    def draft_info(draft_id: str, session=Depends(authenticated)):
        with store.connect() as con:
            row = con.execute('SELECT payload FROM procurement_drafts WHERE id=?', (draft_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'procurement_draft_not_found')
        draft = json.loads(row['payload'])
        account = load_account(draft['companyId'], environ)
        valid_account = hmac.compare_digest(fingerprint(account), draft['_credentialFingerprint'])
        return {**_public_draft(draft), 'accountChanged': not valid_account, 'expired': draft['expiresAt'] < time.time()}

    @app.post('/api/manager/procurement/drafts/{draft_id}/submit')
    def submit(draft_id: str, session=Depends(authenticated)):
        # Explicit failure, no forwarding to legacy endpoints even when globally enabled.
        raise HTTPException(409, 'procurement_submit_not_available')
