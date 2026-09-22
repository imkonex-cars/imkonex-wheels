"""Same-origin storefront and authenticated manager portal, version 0.5.0."""
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from decimal import Decimal, ROUND_CEILING
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, ConfigDict, Field

from .portal_store import Store
from .portal_supplier import PortalSupplier, SupplierFailure

ROOT = Path(__file__).resolve().parent.parent
COOKIE = 'imkonex_manager'


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Login(Input):
    login: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=512)


class Rule(Input):
    mode: Literal['supplier', 'fixed', 'markup']
    price: float = Field(default=0, ge=0, le=100000000)
    percent: float = Field(default=0, ge=0, le=1000)
    minimum: float = Field(default=0, ge=0, le=1000000)


class Ids(Input):
    ids: list[str] = Field(min_length=1, max_length=50)


class OrderLine(Input):
    productId: str = Field(min_length=1, max_length=160)
    warehouseId: int = Field(gt=0)
    quantity: int = Field(ge=1, le=1000)


class Draft(Input):
    lines: list[OrderLine] = Field(min_length=1, max_length=50)
    test: bool = True
    customerId: int | None = Field(default=None, gt=0)
    clientName: str = Field(default='', max_length=150)
    clientPhone: str = Field(default='', max_length=80)


class Confirm(Input):
    confirmation: str = Field(min_length=1, max_length=100)


class Reconcile(Input):
    providerId: int = Field(gt=0)


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def warehouse_id(offer):
    try:
        return int(offer['id'].removeprefix('warehouse-'))
    except (ValueError, KeyError):
        raise HTTPException(409, 'warehouse_mapping_unavailable') from None


def sale_price(offer, rule, cost, now=None):
    if not rule or rule['mode'] == 'supplier':
        return offer['price']
    if rule['mode'] == 'fixed':
        return rule['price']
    if not cost or cost['cost'] <= 0 or (now or time.time())-cost['updated'] > 86400:
        return None
    base = Decimal(str(cost['cost']))
    markup = max(base*Decimal(str(rule['percent']))/100, Decimal(str(rule['minimum'])))
    return float(((base+markup)/10).quantize(Decimal('1'), rounding=ROUND_CEILING)*10)


def create_app(*, db_path=None, catalog_path=None, supplier=None, password=None, local=False):
    app = FastAPI(title='IMKONEX', version='0.5.0', docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
    store = Store(db_path or os.getenv('MANAGER_DB_PATH', str(ROOT/'runtime/manager.sqlite3')))
    api = supplier or PortalSupplier()
    path = Path(catalog_path or ROOT/'data/supplier-snapshot.json')
    catalog = json.loads(path.read_text(encoding='utf-8-sig'))
    by_id = {p['id']: p for p in catalog['products']}
    by_sku = {p['sku']: p for p in catalog['products']}
    # Secrets stay on the server; no fallback/test account is provisioned.
    configured_password = password if password is not None else os.getenv('MANAGER_PASSWORD', '')
    password_salt, password_digest = store.bind_credentials(os.getenv('MANAGER_LOGIN','manager'), configured_password)
    secure_cookie = not (local or os.getenv('LOCAL_DEV') == 'true')
    buckets = defaultdict(deque)
    lock = threading.RLock()
    fitment_cache = {}
    rendered = {'data': None, 'until': 0}
    app.state.store, app.state.supplier = store, api

    def throttle(request, scope, limit, seconds=60):
        # Ignore arbitrary X-Forwarded-For headers. One server worker is configured.
        key = (scope, request.client.host if request.client else 'unknown')
        with lock:
            now = time.monotonic()
            if len(buckets) > 5000:
                for stale in [k for k,v in buckets.items() if not v or v[-1] < now-600]:
                    buckets.pop(stale, None)
            queue = buckets[key]
            while queue and queue[0] < now-seconds:
                queue.popleft()
            if len(queue) >= limit:
                raise HTTPException(429, 'too_many_requests', headers={'Retry-After': str(seconds)})
            queue.append(now)

    def origin(request):
        expected = (os.getenv('PUBLIC_ORIGIN') or os.getenv('RENDER_EXTERNAL_URL') or str(request.base_url)).rstrip('/')
        if request.headers.get('origin', '').rstrip('/') != expected:
            raise HTTPException(403, 'origin_rejected')

    def authenticated(request: Request):
        raw = request.cookies.get(COOKIE, '')
        session = store.session(token_hash(raw)) if raw else None
        if not session:
            raise HTTPException(401, 'login_required')
        if request.method not in ('GET', 'HEAD'):
            origin(request)
            if not hmac.compare_digest(request.headers.get('x-csrf-token', '').encode(), session['csrf'].encode()):
                raise HTTPException(403, 'csrf_rejected')
        return session

    def invalidate():
        with lock:
            rendered['until'] = 0

    @asynccontextmanager
    async def lifespan(app):
        from .portal_jobs import start_refresh
        stop = start_refresh(store, api, by_id, invalidate)
        try:
            yield
        finally:
            stop.set()
    app.router.lifespan_context = lifespan

    def public_catalog():
        with lock:
            if rendered['until'] > time.time():
                return rendered['data']
        rules, costs = store.rules(), store.costs()
        products = []
        for p in catalog['products']:
            rule = rules.get(p['id'])
            offers = []
            for o in p['offers']:
                wid = warehouse_id(o)
                price = sale_price(o, rule, costs.get((p['sku'], wid)))
                if price is not None:
                    offers.append({**o, 'price': price})
            products.append({**p, 'offers': offers})
        result = {**catalog, 'products': products}
        with lock:
            rendered.update(data=result, until=time.time()+15)
        return result

    def product(id):
        if id not in by_id:
            raise HTTPException(404, 'product_not_found')
        return by_id[id]

    def purchase(codes):
        rows = api.purchase(list(dict.fromkeys(codes)))
        store.save_costs(codes, rows)
        invalidate()
        return store.costs(codes)

    def manager_product(p, rules, costs):
        rule = rules.get(p['id'])
        offers = []
        for o in p['offers']:
            wid = warehouse_id(o)
            cost = costs.get((p['sku'], wid))
            sale = sale_price(o, rule, cost)
            offers.append({**o, 'warehouseId': wid, 'salePrice': sale,
                'purchasePrice': cost['cost'] if cost else None,
                'purchaseCheckedAt': cost['updated'] if cost else None,
                'liveStock': cost['stock'] if cost else None,
                'profit': round(sale-cost['cost'],2) if sale is not None and cost else None})
        return {**p, 'offers': offers, 'priceRule': rule or {'mode':'supplier'}}

    @app.middleware('http')
    async def headers(request, call_next):
        if request.method not in ('GET','HEAD'):
            try:
                size = int(request.headers.get('content-length', '0'))
            except ValueError:
                return JSONResponse({'detail':'invalid_request'},status_code=400)
            if size > 65536:
                return JSONResponse({'detail':'request_too_large'},status_code=413)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 65536:
                    return JSONResponse({'detail':'request_too_large'},status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers.update({'X-Content-Type-Options':'nosniff', 'X-Frame-Options':'DENY',
            'Referrer-Policy':'strict-origin-when-cross-origin',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https://www.4tochki.ru https://api-b2b.pwrs.ru data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"})
        if request.url.path.startswith(('/api/manager','/manager')):
            response.headers['Cache-Control']='no-store'
            response.headers['X-Robots-Tag']='noindex, nofollow'
        if request.url.path in ('/data/catalog.js','/config.js'):
            response.headers['Cache-Control']='no-cache'
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Default validation payloads can echo a password or customer input.
        return JSONResponse({'detail':'invalid_request'}, status_code=422)

    @app.exception_handler(SupplierFailure)
    async def supplier_error(request, exc):
        return JSONResponse({'detail':exc.code, 'providerCode':exc.provider_code},status_code=503)

    @app.get('/health')
    def health():
        return {'status':'ok','version':'0.5.0'}

    @app.get('/config.js')
    def runtime_config():
        value = {'mode':'snapshot','version':'0.5.0','apiBase':'','portal':True}
        return Response('window.IMKONEX_CONFIG = Object.freeze('+json.dumps(value)+');', media_type='text/javascript')

    @app.get('/data/catalog.js')
    def catalog_script():
        value = json.dumps(public_catalog(),ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
        return Response('window.IMKONEX_DATA = '+value+';',media_type='text/javascript')

    @app.get('/api/fitment/{stage}')
    def fitment(request: Request, stage: Literal['makes','models','years','modifications','products'],
                make: str=Query('',max_length=100), model: str=Query('',max_length=150),
                begin: int=Query(0,ge=0,le=2100), end: int=Query(0,ge=0,le=2100),
                modification: str=Query('',max_length=200)):
        if stage != 'makes' and not make or stage in ('years','modifications','products') and not model:
            raise HTTPException(422,'choose_vehicle')
        if stage in ('modifications','products') and (begin < 1900 or end < begin):
            raise HTTPException(422,'choose_vehicle_year')
        if stage == 'products' and not modification:
            raise HTTPException(422,'choose_vehicle_modification')
        parameters = dict(make=make, model=model, begin=begin, end=end, modification=modification)
        key = (stage,json.dumps(parameters,sort_keys=True))
        with lock:
            cached = fitment_cache.get(key)
        if cached and cached[0] > time.time():
            return cached[1]
        throttle(request,'fitment',60)
        rows = api.fitment(stage,parameters)
        if stage == 'products':
            result = {'productIds':[by_sku[c]['id'] for c in dict.fromkeys(rows) if c in by_sku],
                      'fitment':'standard','vehicle':parameters}
        else:
            result = {'items':rows}
        with lock:
            if len(fitment_cache) >= 500:
                fitment_cache.pop(next(iter(fitment_cache)))
            fitment_cache[key]=(time.time()+(600 if stage=='products' else 86400),result)
        return result

    @app.post('/api/manager/login')
    def login(body: Login, request: Request, response: Response):
        origin(request); throttle(request,'login',8,300)
        if len(configured_password) < 12:
            raise HTTPException(503,'manager_password_not_configured')
        digest = hashlib.pbkdf2_hmac('sha256',body.password.encode(),password_salt,600000)
        valid_password = hmac.compare_digest(digest,password_digest)
        valid_login = hmac.compare_digest(body.login.encode(),os.getenv('MANAGER_LOGIN','manager').encode())
        if not (valid_login and valid_password):
            raise HTTPException(401,'invalid_login')
        token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
        store.new_session(token_hash(token),csrf)
        response.set_cookie(COOKIE,token,max_age=28800,secure=secure_cookie,httponly=True,samesite='strict',path='/')
        store.audit('login','manager')
        return {'csrf':csrf,'login':body.login}

    @app.get('/api/manager/session')
    def session(session=Depends(authenticated)):
        return {'csrf':session['csrf'],'login':os.getenv('MANAGER_LOGIN','manager')}

    @app.post('/api/manager/logout')
    def logout(request: Request,response: Response,session=Depends(authenticated)):
        store.logout(token_hash(request.cookies[COOKIE]));response.delete_cookie(COOKIE,path='/')
        return {'ok':True}

    @app.get('/api/manager/status')
    def status(session=Depends(authenticated)):
        return {'version':'0.5.0','products':len(by_id),'updatedAt':catalog['updatedAt'],
                'supplierConfigured':api.configured,'ordersEnabled':os.getenv('SUPPLIER_ORDERS_ENABLED')=='true',
                'rules':len(store.rules()),'orders':len(store.order_list())}

    @app.get('/api/manager/products')
    def manager_products(q: str=Query('',max_length=200),page: int=Query(1,ge=1),session=Depends(authenticated)):
        words=q.casefold().split()
        rows=[p for p in by_id.values() if all(w in (p['sku']+' '+p['brand']+' '+p['model']+' '+p['description']).casefold() for w in words)]
        shown=rows[(page-1)*20:page*20];rules=store.rules();costs=store.costs([p['sku'] for p in shown])
        return {'total':len(rows),'page':page,'items':[manager_product(p,rules,costs) for p in shown]}

    @app.post('/api/manager/purchase/refresh')
    def refresh_purchase(body: Ids,request: Request,session=Depends(authenticated)):
        throttle(request,'purchase',20)
        rows=[product(id) for id in dict.fromkeys(body.ids)]
        costs=purchase([p['sku'] for p in rows]);rules=store.rules()
        return {'items':[manager_product(p,rules,costs) for p in rows]}

    @app.put('/api/manager/prices/{id}')
    def price(id: str,body: Rule,session=Depends(authenticated)):
        p=product(id)
        if body.mode=='fixed' and body.price<=0:
            raise HTTPException(422,'positive_price_required')
        if body.mode=='markup':
            costs=purchase([p['sku']])
            if not any(r['cost']>0 and r['stock']>0 for r in costs.values()):
                raise HTTPException(409,'purchase_price_unavailable')
        store.set_rule(id,None if body.mode=='supplier' else body.model_dump());invalidate()
        return {'ok':True}

    @app.get('/api/manager/orders')
    def order_list(session=Depends(authenticated)):
        return {'items':store.order_list()}

    @app.post('/api/manager/orders/preview')
    def preview(body: Draft,request: Request,session=Depends(authenticated)):
        throttle(request,'order_preview',15)
        pairs=[(l.productId,l.warehouseId) for l in body.lines]
        if len(set(pairs))!=len(pairs):
            raise HTTPException(422,'duplicate_order_line')
        selected=[product(l.productId) for l in body.lines]
        costs=purchase([p['sku'] for p in selected]);rules=store.rules();lines=[]
        for line,p in zip(body.lines,selected):
            original=next((o for o in p['offers'] if warehouse_id(o)==line.warehouseId),None)
            cost=costs.get((p['sku'],line.warehouseId))
            if not original or not cost or cost['stock']<line.quantity or cost['cost']<=0:
                raise HTTPException(409,'insufficient_supplier_stock')
            sale=sale_price(original,rules.get(p['id']),cost)
            if sale is None:
                raise HTTPException(409,'sale_price_unavailable')
            lines.append({'productId':p['id'],'sku':p['sku'],'name':p['brand']+' '+p['model'],
                          'warehouseId':line.warehouseId,'warehouse':original['warehouse'],'quantity':line.quantity,
                          'purchasePrice':cost['cost'],'salePrice':sale})
        now=time.time()
        draft={'id':'IMX-'+secrets.token_hex(8).upper(),'createdAt':now,'expiresAt':now+120,
               'confirmation':secrets.token_urlsafe(32),'test':body.test,'customerId':body.customerId,
               'clientName':body.clientName,'clientPhone':body.clientPhone,'lines':lines,
               'purchaseTotal':round(sum(l['purchasePrice']*l['quantity'] for l in lines),2),
               'saleTotal':round(sum(l['salePrice']*l['quantity'] for l in lines),2)}
        store.create_order(draft);store.audit('order_preview',draft['id'])
        return {**draft,'status':'draft'}

    @app.post('/api/manager/orders/{id}/submit')
    def submit(id: str,body: Confirm,session=Depends(authenticated)):
        draft=store.order(id)
        if not draft:raise HTTPException(404,'order_not_found')
        if not hmac.compare_digest(body.confirmation.encode(),draft['confirmation'].encode()):
            raise HTTPException(403,'confirmation_mismatch')
        if draft['status']=='submitted':return draft
        if draft['status']!='draft':raise HTTPException(409,'order_already_processed')
        if os.getenv('SUPPLIER_ORDERS_ENABLED')!='true':raise HTTPException(503,'supplier_orders_disabled')
        if draft['expiresAt']<time.time():raise HTTPException(409,'preview_expired')
        # Claim durably before the external side effect: parallel/repeated clicks cannot resend.
        if not store.claim_order(id):raise HTTPException(409,'order_already_processed')
        try:
            latest=purchase([l['sku'] for l in draft['lines']])
            for line in draft['lines']:
                cost=latest.get((line['sku'],line['warehouseId']))
                if not cost or cost['stock']<line['quantity'] or cost['cost']!=line['purchasePrice']:
                    store.update_order(id,'changed',draft)
                    raise HTTPException(409,'purchase_changed_review_again')
        except SupplierFailure:
            store.update_order(id,'check_failed',draft)
            raise
        try:
            answer=api.create_order(draft)
        except SupplierFailure as exc:
            known=exc.code in ('supplier_rejected','supplier_schema_unavailable','supplier_schema_changed','supplier_method_unavailable','supplier_not_configured')
            draft['error']=exc.code;draft['providerCode']=exc.provider_code
            store.update_order(id,'rejected' if known else 'unknown',draft)
            store.audit('order_rejected' if known else 'order_unknown',id)
            raise
        except Exception:
            store.update_order(id,'unknown',draft);store.audit('order_unknown',id)
            raise HTTPException(503,'order_result_unknown') from None
        draft.update(answer);draft['submittedAt']=time.time()
        store.update_order(id,'submitted',draft);store.audit('order_submitted',id)
        return {**draft,'status':'submitted'}

    @app.post('/api/manager/orders/{id}/refresh')
    def refresh_order(id: str,request: Request,session=Depends(authenticated)):
        throttle(request,'order_status',30)
        order=store.order(id)
        if not order:raise HTTPException(404,'order_not_found')
        if not order.get('providerId'):raise HTTPException(409,'provider_order_not_linked')
        order['providerStatus']=api.order_info(order['providerId']);order['statusCheckedAt']=time.time()
        history=order.setdefault('history',[])
        name=order['providerStatus'].get('statusName','')
        if name and (not history or history[-1]['name']!=name):history.append({'name':name,'at':time.time()})
        store.update_order(id,order['status'],order)
        return order

    @app.post('/api/manager/orders/{id}/reconcile')
    def reconcile(id: str,body: Reconcile,session=Depends(authenticated)):
        order=store.order(id)
        if not order or order['status'] not in ('unknown','submitting'):
            raise HTTPException(409,'reconciliation_not_required')
        info=api.order_info(body.providerId)
        order.update(providerId=body.providerId,providerStatus=info,statusCheckedAt=time.time(),manuallyLinked=True)
        store.update_order(id,'submitted',order);store.audit('order_manually_linked',id)
        return {**order,'status':'submitted'}

    @app.get('/manager')
    def manager_page():
        return RedirectResponse('/manager/', status_code=307)

    # Only the compiled frontend is reachable. runtime/, backend/, .env and data/ are not mounted.
    app.mount('/',StaticFiles(directory=ROOT/'dist',html=True,check_dir=False),name='storefront')
    return app


app=create_app()
