import hmac
import os
import re
from pathlib import Path
from typing import Annotated, Literal
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from .models import Catalog, QuoteRequest, best_offer
from .repository import get_catalog

app=FastAPI(title='IMKONEX tires & wheels',version='0.1.0',description='Read-only catalog and quote API. Supplier orders and payments are disabled.')
origins=[s.strip() for s in os.getenv('ALLOWED_ORIGINS','http://localhost:4173,http://127.0.0.1:4173').split(',') if s.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=False,allow_methods=['GET','POST'],allow_headers=['Content-Type','Authorization'])

@app.middleware('http')
async def headers(request,call_next):
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    response.headers['X-Frame-Options']='DENY'
    response.headers['X-Robots-Tag']='noindex, nofollow'
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control']='no-store'
    return response

def catalog_dependency():
    try:
        return get_catalog()
    except Exception:
        # Database URLs and provider errors must not reach public responses.
        raise HTTPException(503,'Catalog is temporarily unavailable') from None

CatalogDep=Annotated[Catalog,Depends(catalog_dependency)]

def require_admin(authorization: Annotated[str | None,Header()]=None):
    token=os.getenv('ADMIN_API_TOKEN','')
    if len(token)<32:
        raise HTTPException(503,'Admin API is not configured')
    if not authorization or not hmac.compare_digest(authorization,'Bearer '+token):
        raise HTTPException(401,'Authentication required',headers={'WWW-Authenticate':'Bearer'})

@app.get('/health')
def health(catalog: CatalogDep):
    return {'status':'ok','mode':catalog.mode,'version':'0.1.0','supplierOrdersEnabled':False}

def normalize(s):
    return re.sub(r'[\s/хx×.,-]+','',str(s).lower().replace('ё','е'))

def filter_catalog(catalog,kind,q,width,profile,diameter,pcd,season,brand,quantity,fast,min_price,max_price):
    result=[]
    for p in catalog.products:
        if kind and p.kind!=kind: continue
        if brand and p.brand not in brand: continue
        if any(value is not None and str(getattr(p,key))!=str(value) for key,value in [('width',width),('profile',profile),('season',season),('pcd',pcd)]): continue
        if diameter is not None and p.diameter!=diameter: continue
        size=f'{p.width}/{p.profile} R{p.diameter:g}' if p.kind=='tires' else f'{p.wheelWidth:g}J × {p.diameter:g} {p.pcd}'
        if q and normalize(q) not in normalize(f'{p.brand} {p.model} {p.sku} {size}'): continue
        offer=best_offer(p,quantity)
        if quantity>1 and not offer: continue
        if fast and (not offer or offer.days>3): continue
        if min_price is not None and (not offer or offer.price<min_price): continue
        if max_price is not None and (not offer or offer.price>max_price): continue
        result.append(p)
    return result

@app.get('/api/products')
def product_list(catalog: CatalogDep,kind: Literal['tires','wheels'] | None=None,q: str=Query('',max_length=200),width: int | None=None,profile: int | None=None,diameter: float | None=None,pcd: str | None=None,season: Literal['summer','winter','allseason'] | None=None,brand: Annotated[list[str] | None,Query()]=None,quantity: int=Query(1,ge=1,le=20),fast: bool=False,min_price: int | None=Query(None,ge=0),max_price: int | None=Query(None,ge=0),sort: Literal['recommended','price-up','price-down','delivery']='recommended',page: int=Query(1,ge=1),limit: int=Query(12,ge=1,le=100)):
    rows=filter_catalog(catalog,kind,q,width,profile,diameter,pcd,season,brand,quantity,fast,min_price,max_price)
    def order(p):
        o=best_offer(p,quantity)
        if sort=='delivery': return (o.days if o else float('inf'),)
        if sort.startswith('price'): return (o is None,(-1 if sort=='price-down' else 1)*(o.price if o else 0))
        return (-p.rank,)
    rows.sort(key=order)
    return {'mode':catalog.mode,'updatedAt':catalog.updatedAt,'total':len(rows),'page':page,'limit':limit,'items':[p.model_dump(exclude_none=True) for p in rows[(page-1)*limit:page*limit]]}

@app.get('/api/products/{product_id}')
def product_detail(product_id: str,catalog: CatalogDep):
    p=next((p for p in catalog.products if p.id==product_id),None)
    if p is None: raise HTTPException(404,'Product not found')
    return p.model_dump(exclude_none=True)

@app.get('/api/facets')
def facets(catalog: CatalogDep,kind: Literal['tires','wheels']='tires'):
    ps=[p for p in catalog.products if p.kind==kind]
    return {'mode':catalog.mode,'facets':{key:sorted({getattr(p,key) for p in ps if getattr(p,key) is not None}) for key in ['brand','width','profile','diameter','season','pcd','wheelWidth','et','dia']}}

@app.post('/api/quote')
def quote(request: QuoteRequest,catalog: CatalogDep):
    by_id={p.id:p for p in catalog.products}
    items=[]; unavailable=[]
    for line in request.lines:
        p=by_id.get(line.id)
        o=best_offer(p,line.quantity) if p else None
        if not o:
            unavailable.append(line.id); continue
        items.append({'id':p.id,'quantity':line.quantity,'unitPrice':o.price,'subtotal':o.price*line.quantity,'offer':o.model_dump()})
    if unavailable:
        raise HTTPException(409,{'code':'INSUFFICIENT_STOCK','productIds':unavailable})
    return {'mode':catalog.mode,'currency':'RUB','items':items,'total':sum(l['subtotal'] for l in items),'deliveryIncluded':False,'fitmentConfirmed':False,'orderCreated':False}

@app.post('/api/orders',status_code=501)
def orders_disabled():
    raise HTTPException(501,'Orders and payments are disabled in this release. Use /api/quote.')

@app.get('/api/admin/status',dependencies=[Depends(require_admin)])
def admin_status(catalog: CatalogDep):
    return {'mode':catalog.mode,'productCount':len(catalog.products),'updatedAt':catalog.updatedAt,'dataStore':os.getenv('DATA_MODE','demo'),'supplierConfigured':bool(os.getenv('FOURTOCHKI_LOGIN') and os.getenv('FOURTOCHKI_PASSWORD')),'supplierIntegrationVerified':False,'ordersEnabled':False}

if os.getenv('SERVE_FRONTEND','false').lower()=='true':
    app.mount('/',StaticFiles(directory=Path(__file__).resolve().parent.parent/'dist',html=True),name='frontend')
