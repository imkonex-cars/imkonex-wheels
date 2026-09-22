"""Same-origin storefront and authenticated manager portal, version 0.7.0."""
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
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
    mode: Literal['supplier', 'fixed', 'markup', 'profit', 'discount']
    price: float = Field(default=0, ge=0, le=100000000)
    percent: float = Field(default=0, ge=0, le=1000)
    minimum: float = Field(default=0, ge=0, le=1000000)
    amount: float = Field(default=0, ge=-100000000, le=100000000)
    roundTo: Literal[0.01, 10] = 10


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


class SelectionLine(Input):
    productId: str = Field(min_length=1,max_length=160)
    quantity: int = Field(ge=1,le=1000)
    warehouseId: int | None = Field(default=None,gt=0)

class Selection(Input):
    lines: list[SelectionLine] = Field(min_length=1,max_length=50)

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


from .pricing import sale_price, price_result, policy_for, category, CATEGORIES, fresh_cost


def create_app(*, db_path=None, catalog_path=None, supplier=None, password=None, local=False):
    app = FastAPI(title='IMKONEX', version='0.7.0', docs_url=None, redoc_url=None, openapi_url=None)
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
    details_cache = {}
    rendered = {'data': None, 'until': 0, 'generation':0}
    app.state.store, app.state.supplier = store, api
    from .site_shell import SiteShell, ORIGINS
    from .manager_catalog import search, fallback_warehouse
    fallback=ROOT/'dist/shared/fallback.json'
    if not fallback.exists():fallback=ROOT/'frontend/shared/fallback.json'
    site_shell=SiteShell(store.path.parent/'shared-shell.json',fallback)
    app.state.site_shell=site_shell

    def all_rules():return store.rules(),store.offer_rules()
    def chosen_rule(p,wid,rules,offer_rules):
        return offer_rules.get((p['id'],wid),rules.get(p['id'],policy_for(p,policies)))

    policies=store.setting('category_pricing',{})

    base_warehouses={}
    for p in catalog['products']:
        for o in p['offers']:
            wid=warehouse_id(o)
            if wid not in base_warehouses:base_warehouses[wid]=fallback_warehouse(wid,o['warehouse'])
    def warehouse_list():
        known={**base_warehouses,**{r['id']:r for r in store.setting('warehouses',[])}}
        return sorted(known.values(),key=lambda w:(w['logisticDays'] if w['logisticDays'] is not None else 999,w['name']))


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
            rendered['generation'] += 1

    @asynccontextmanager
    async def lifespan(app):
        from .portal_jobs import start_refresh
        stop = start_refresh(store, api, by_id, invalidate)
        site_shell.start(stop)
        try:
            yield
        finally:
            stop.set()
    app.router.lifespan_context = lifespan

    def public_catalog():
        with lock:
            if rendered['until'] > time.time():
                return rendered['data']
            generation=rendered['generation']
        rules, offer_rules = all_rules()
        costs=store.costs()
        products = []
        for p in catalog['products']:
            rule = rules.get(p['id'])
            offers = []
            for o in p['offers']:
                wid = warehouse_id(o)
                cost=costs.get((p['sku'],wid))
                if cost and time.time()-cost['updated']<=86400 and cost['stock']<=0:continue
                price = sale_price(o, chosen_rule(p,wid,rules,offer_rules), cost)
                if price is not None:
                    offers.append({**o, 'price': price, 'stock':cost['stock'] if cost and time.time()-cost['updated']<=86400 else o['stock']})
            products.append({**p, 'vehicleCategory':category(p), 'offers': offers})
        result = {**catalog, 'products': products}
        with lock:
            if rendered['generation']==generation:rendered.update(data=result, until=time.time()+15)
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

    def manager_product(p, rules, costs, offer_rules=None):
        offer_rules=offer_rules if offer_rules is not None else store.offer_rules()
        meta={r['id']:r for r in warehouse_list()}
        offers=[]
        for o in p['offers']:
            wid=warehouse_id(o);cost=costs.get((p['sku'],wid))
            rule=chosen_rule(p,wid,rules,offer_rules);computed=price_result(o,rule,cost);sale=computed['price']
            gain=round(sale-cost['cost'],2) if sale is not None and cost and cost['cost']>0 else None
            offers.append({**o,'warehouseId':wid,'warehouseInfo':meta[wid],
                'priceRule':rule or {'mode':'supplier'},'salePrice':sale,
                'minimumPrice':computed['floor'],'floorApplied':computed['adjusted'],
                'purchasePrice':cost['cost'] if cost else None,
                'supplierRetailPrice':cost.get('retail') if cost else None,
                'purchaseCheckedAt':cost['updated'] if cost else None,
                'purchaseStale':not cost or time.time()-cost['updated']>86400,
                'liveStock':cost['stock'] if cost and time.time()-cost['updated']<=86400 else None,'profit':gain,
                'markupPercent':round(gain/cost['cost']*100,2) if gain is not None else None,
                'marginPercent':round(gain/sale*100,2) if gain is not None and sale else None})
        return {**p,'vehicleCategory':category(p),'offers':offers,'priceRule':rules.get(p['id']) or {'mode':'supplier'}}

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
        if request.url.path.startswith(('/api/manager','/manager','/api/selections','/s/','/api/shop','/order/')):
            response.headers['Cache-Control']='no-store'
            response.headers['X-Robots-Tag']='noindex, nofollow'
        if request.url.path in ('/data/catalog.js','/config.js'):
            response.headers['Cache-Control']='no-cache'
        if request.url.path=='/api/site-shell' or request.url.path.startswith('/shared/'):
            incoming=request.headers.get('origin','')
            if incoming in ORIGINS:
                response.headers['Access-Control-Allow-Origin']=incoming
                response.headers['Vary']='Origin'
            response.headers['Cache-Control']='public, max-age=60'
        if request.url.path.startswith(('/api/shop','/order/')):response.headers['Referrer-Policy']='no-referrer'
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
        return {'status':'ok','version':'0.7.0'}

    @app.get('/config.js')
    def runtime_config():
        value = {'mode':'snapshot','version':'0.7.0','apiBase':'','portal':True}
        return Response('window.IMKONEX_CONFIG = Object.freeze('+json.dumps(value)+');', media_type='text/javascript')

    @app.get('/data/catalog.js')
    def catalog_script():
        value = json.dumps(public_catalog(),ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
        return Response('window.IMKONEX_DATA = '+value+';',media_type='text/javascript')

    @app.get('/api/site-shell')
    def shared_shell():return site_shell.value

    @app.get('/api/products/{id}/details')
    def product_details(id:str, request:Request):
        p=product(id)
        with lock:cached=details_cache.get(id)
        if cached and cached[0]>time.time():return cached[1]
        throttle(request,'product_details',30,60)
        attributes=api.details(p['sku'],p['kind'])
        result={'id':p['id'],'attributes':attributes}
        with lock:
            if len(details_cache)>=1000:details_cache.pop(next(iter(details_cache)))
            details_cache[id]=(time.time()+86400,result)
        return result

    @app.post('/api/selections')
    def make_selection(body:Selection,request:Request):
        origin(request);throttle(request,'selection',30,3600)
        selected=[product(l.productId) for l in body.lines]
        costs=purchase(list(dict.fromkeys(p['sku'] for p in selected)))
        current=quoted_products([p['id'] for p in selected],costs);lines=[];pairs=set();used={}
        for line in body.lines:
            pair=(line.productId,line.warehouseId)
            if pair in pairs:raise HTTPException(422,'duplicate_selection_line')
            pairs.add(pair);p=current.get(line.productId)
            if not p:raise HTTPException(404,'product_not_found')
            offers=[o for o in p['offers'] if o['stock']-used.get((p['id'],warehouse_id(o)),0)>=line.quantity and (line.warehouseId is None or warehouse_id(o)==line.warehouseId)]
            if not offers:raise HTTPException(409,'selection_stock_changed')
            o=min(offers,key=lambda x:(x.get('days') if x.get('days') is not None else 999,x['price']))
            pair=(p['id'],warehouse_id(o));used[pair]=used.get(pair,0)+line.quantity
            lines.append({'productId':p['id'],'sku':p['sku'],'name':p['brand']+' '+p['model'],
                          'description':p['description'],'image':p['image'],'quantity':line.quantity,
                          'price':o['price'],'subtotal':round(o['price']*line.quantity,2)})
        payload={'createdAt':time.time(),'lines':lines,'total':round(sum(l['subtotal'] for l in lines),2)}
        token,expires=store.save_selection(payload)
        return {'url':'/s/'+token,'expiresAt':expires,**payload}

    @app.get('/api/selections/{token}')
    def view_selection(token:str):
        if len(token)>100:raise HTTPException(404,'selection_not_found')
        item=store.selection(token)
        if not item:raise HTTPException(404,'selection_expired')
        return item

    @app.get('/s/{token}')
    def selection_page(token:str):
        from fastapi.responses import FileResponse
        return FileResponse(ROOT/'dist/selection/index.html',headers={'X-Robots-Tag':'noindex, nofollow','Cache-Control':'no-store'})

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
        return {'version':'0.7.0','products':len(by_id),'updatedAt':catalog['updatedAt'],
                'supplierConfigured':api.configured,'ordersEnabled':os.getenv('SUPPLIER_ORDERS_ENABLED')=='true',
                'rules':len(store.rules())+len(store.offer_rules()),'orders':len(store.order_list()),
                'purchaseSync':store.setting('purchase_sync',{}),'customerOrders':len(store.customer_orders()),
                'navigationCheckedAt':site_shell.value.get('checkedAt'), 'navigationStatus':'cached' if site_shell.last_error else 'ready'}

    @app.get('/api/manager/catalog-filters')
    def manager_filters(kind:str='',category_filter:str='',session=Depends(authenticated)):
        from .manager_catalog import VALUES, facet_value
        fields=['kind','brand','season','width','profile','diameter','pcd','wheelWidth','et','dia',*VALUES]
        selected=[p for p in by_id.values() if (not kind or p['kind']==kind) and (not category_filter or category(p)==category_filter)]
        return {'warehouses':warehouse_list(),'facets':{k:sorted({str(facet_value(p,k)) for p in selected if facet_value(p,k) is not None}) for k in fields}}

    @app.post('/api/manager/warehouses/refresh')
    def refresh_warehouses(request:Request,session=Depends(authenticated)):
        throttle(request,'warehouses',4,300)
        rows=api.warehouses();store.set_setting('warehouses',rows)
        return {'warehouses':warehouse_list()}

    @app.get('/api/manager/products')
    def manager_products(q:str=Query('',max_length=200),page:int=Query(1,ge=1),kind:str='',brand:str='',season:str='',
                         width:str='',profile:str='',diameter:str='',pcd:str='',category_filter:str='',warehouses:str=Query('',max_length=4000),
                         min_stock:int=Query(1,ge=1,le=1000),features:str=Query('{}',max_length=3000),session=Depends(authenticated)):
        from .manager_catalog import VALUES,facet_value
        try:feature_values=json.loads(features)
        except ValueError:raise HTTPException(422,'invalid_request') from None
        if not isinstance(feature_values,dict) or any(k not in VALUES or not isinstance(v,str) for k,v in feature_values.items()):raise HTTPException(422,'invalid_request')
        costs=store.costs()
        # Availability filters honor the latest private cache where fresh.
        available=[]
        for p in by_id.values():
            offers=[]
            for o in p['offers']:
                c=costs.get((p['sku'],warehouse_id(o)))
                offers.append({**o,'stock':c['stock'] if c and time.time()-c['updated']<=86400 else o['stock']})
            if (not category_filter or category(p)==category_filter) and all(not v or str(facet_value(p,k))==v for k,v in feature_values.items()):available.append({**p,'offers':offers})
        rows=search(available,q=q,kind=kind,brand=brand,season=season,width=width,profile=profile,diameter=diameter,pcd=pcd,warehouses=warehouses,min_stock=min_stock)
        shown=rows[(page-1)*20:page*20];rules,offer_rules=all_rules()
        return {'total':len(rows),'page':page,'items':[manager_product(p,rules,costs,offer_rules) for p in shown]}

    @app.post('/api/manager/purchase/refresh')
    def refresh_purchase(body: Ids,request: Request,session=Depends(authenticated)):
        throttle(request,'purchase',20)
        rows=[product(id) for id in dict.fromkeys(body.ids)]
        costs=purchase([p['sku'] for p in rows]);rules=store.rules()
        if not store.setting('warehouses'):
            try:store.set_setting('warehouses',api.warehouses())
            except (SupplierFailure,AttributeError):pass
        return {'items':[manager_product(p,rules,costs) for p in rows]}

    @app.put('/api/manager/prices/{id}')
    def price(id: str,body: Rule,warehouse:int|None=Query(default=None,gt=0),session=Depends(authenticated)):
        p=product(id)
        if body.mode=='fixed' and body.price<=0:
            raise HTTPException(422,'positive_price_required')
        if warehouse is not None and not any(warehouse_id(o)==warehouse for o in p['offers']):raise HTTPException(409,'warehouse_mapping_unavailable')
        if body.mode in ('markup','profit','fixed','discount','supplier'):
            costs=purchase([p['sku']])
            if not any(r['cost']>0 and r['stock']>0 and (warehouse is None or r['warehouse']==warehouse) for r in costs.values()):
                raise HTTPException(409,'purchase_price_unavailable')
        if warehouse is None:store.set_rule(id,None if body.mode=='supplier' else body.model_dump())
        else:store.set_offer_rule(id,warehouse,body.model_dump())
        invalidate()
        return {'ok':True, 'items':[manager_product(p,store.rules(),store.costs([p['sku']]))]}

    @app.delete('/api/manager/prices/{id}')
    def inherit_price(id:str,warehouse:int=Query(gt=0),session=Depends(authenticated)):
        product(id);store.set_offer_rule(id,warehouse,None);invalidate();return {'ok':True}

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
        costs=purchase([p['sku'] for p in selected]);rules,offer_rules=all_rules();lines=[]
        for line,p in zip(body.lines,selected):
            original=next((o for o in p['offers'] if warehouse_id(o)==line.warehouseId),None)
            cost=costs.get((p['sku'],line.warehouseId))
            if not original or not cost or cost['stock']<line.quantity or cost['cost']<=0:
                raise HTTPException(409,'insufficient_supplier_stock')
            sale=sale_price(original,chosen_rule(p,line.warehouseId,rules,offer_rules),cost)
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
    @app.get('/api/manager/pricing')
    def category_prices(session=Depends(authenticated)):
        costs=store.costs();groups=[]
        for key,label in CATEGORIES.items():
            ps=[p for p in by_id.values() if key=='all' or category(p)==key or p['kind']==key]
            rows=[]
            for p in ps:
                for o in p['offers']:
                    c=costs.get((p['sku'],warehouse_id(o)))
                    if fresh_cost(c) and c.get('retail') and c['retail']>0 and c['stock']>0:rows.append(c)
            total_retail=sum(c['retail'] for c in rows)
            groups.append({'category':key,'label':label,'products':len(ps),'checkedOffers':len(rows),
                'supplierDiscount':round((1-sum(c['cost'] for c in rows)/total_retail)*100,2) if total_retail else None,
                'rule':policies.get(key), 'minimumGain':100})
        return {'groups':groups,'sync':store.setting('purchase_sync',{})}

    @app.post('/api/manager/pricing/{key}/preview')
    def preview_policy(key:str,body:Rule,session=Depends(authenticated)):
        if key not in CATEGORIES or body.mode not in ('supplier','markup','discount'):raise HTTPException(422,'invalid_request')
        if body.mode=='discount' and body.percent>100:raise HTTPException(422,'invalid_request')
        rows=[];unknown=0;overridden=0;adjusted=0;affected=0;costs=store.costs();rules,offer_rules=all_rules()
        for p in by_id.values():
            if not (key=='all' or category(p)==key or p['kind']==key):continue
            for o in p['offers']:
                wid=warehouse_id(o)
                # More specific rules retain precedence; report them explicitly.
                inherited=key=='all' and (category(p) in policies or p['kind'] in policies) or key==p['kind'] and category(p)!=key and category(p) in policies
                if p['id'] in rules or (p['id'],wid) in offer_rules or inherited:overridden+=1;continue
                c=costs.get((p['sku'],wid));result=price_result(o,body.model_dump(),c)
                if result['price'] is None:unknown+=1;continue
                affected+=1;adjusted+=int(result['adjusted'])
                if len(rows)<12:rows.append({'sku':p['sku'],'warehouse':o['warehouse'],'purchase':c['cost'],'retail':c.get('retail'),'sale':result['price'],'gain':round(result['price']-c['cost'],2),'floorApplied':result['adjusted']})
        return {'affected':affected,'unknown':unknown,'overridden':overridden,'floorApplied':adjusted,'examples':rows}

    @app.put('/api/manager/pricing/{key}')
    def save_policy(key:str,body:Rule,session=Depends(authenticated)):
        if key not in CATEGORIES or body.mode not in ('supplier','markup','discount'):raise HTTPException(422,'invalid_request')
        if body.mode=='discount' and body.percent>100:raise HTTPException(422,'invalid_request')
        with lock:
            policies[key]=body.model_dump();store.set_setting('category_pricing',policies)
        store.audit('category_price_changed',key);invalidate();return {'ok':True}

    @app.delete('/api/manager/pricing/{key}')
    def remove_policy(key:str,session=Depends(authenticated)):
        if key not in CATEGORIES:raise HTTPException(422,'invalid_request')
        with lock:
            policies.pop(key,None);store.set_setting('category_pricing',policies)
        store.audit('category_price_reset',key);invalidate();return {'ok':True}

    def quoted_products(ids,costs):
        # Transactional checkout must not read a public cache being rebuilt by another request.
        rules,offer_rules=all_rules();result={}
        for pid in ids:
            p=product(pid);offers=[]
            for o in p['offers']:
                wid=warehouse_id(o);cost=costs.get((p['sku'],wid))
                value=sale_price(o,chosen_rule(p,wid,rules,offer_rules),cost)
                if value is not None and cost['stock']>0:offers.append({**o,'price':value,'stock':cost['stock']})
            result[pid]={**p,'offers':offers}
        return result

    from .shop import install_shop
    install_shop(app,store,by_id,purchase,quoted_products,authenticated,origin,throttle)

    app.mount('/',StaticFiles(directory=ROOT/'dist',html=True,check_dir=False),name='storefront')
    return app


app=create_app()
