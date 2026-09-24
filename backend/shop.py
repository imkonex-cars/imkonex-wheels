"""Customer requests and private manager workflow; no card/payment simulation."""
import hashlib
import json
import re
import secrets
import time
from decimal import Decimal
from typing import Literal
from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .pricing import category

STATUSES={'new':'Заявка получена','confirmed':'Подтверждён менеджером','preparing':'Комплектуем',
          'ready':'Готов к выдаче','shipped':'Передан в доставку','completed':'Получен','cancelled':'Отменён'}
TRANSITIONS={'new':{'confirmed','cancelled'},'confirmed':{'preparing','cancelled'},
             'preparing':{'ready','shipped','cancelled'},'ready':{'shipped','completed','cancelled'},
             'shipped':{'completed'},'completed':set(),'cancelled':set()}
DEFAULT_SETTINGS={
 'delivery':'Доставку согласуем при подтверждении заказа: уточним ваш город, стоимость и срок. Самовывоз возможен только после подтверждения менеджером конкретного пункта выдачи. Наличие на складе не означает готовность к выдаче в офисе.',
 'payment':'Способ и срок оплаты менеджер согласует после проверки наличия и стоимости доставки. При отправке заявки деньги не списываются. Онлайн-оплата на сайте пока не подключена.',
 'contactPhone':'8 800 301-36-88','contactEmail':'sales@imkonex.com',
 'pickupAddress':'Пункт выдачи согласуется с менеджером',
 'returns':'По вопросам возврата и обмена свяжитесь с менеджером и сообщите номер заказа. Условия зависят от состояния товара и обстоятельств покупки.'}

class Input(BaseModel):model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
class Line(Input):
    productId:str=Field(min_length=1,max_length=160)
    quantity:int=Field(ge=1,le=1000)
    warehouseId:int|None=Field(default=None,gt=0)
class Quote(Input):lines:list[Line]=Field(min_length=1,max_length=50)
class Availability(Input):ids:list[str]=Field(min_length=1,max_length=12)
class Checkout(Input):
    quoteId:str=Field(pattern=r'^[A-Za-z0-9_-]{32}$')
    key:str=Field(min_length=24,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')
    name:str=Field(min_length=2,max_length=100)
    phone:str=Field(min_length=7,max_length=40)
    city:str=Field(min_length=2,max_length=150)
    delivery:Literal['delivery','pickup','discuss']='discuss'
    comment:str=Field(default='',max_length=1000)
    consent:Literal[True]
    consentVersion:Literal['2026-09-24']

    @field_validator('consent', mode='before')
    @classmethod
    def affirmative_consent(cls,value):
        if value is not True:raise ValueError('consent_required')
        return value
class Settings(Input):
    delivery:str=Field(min_length=20,max_length=5000)
    payment:str=Field(min_length=20,max_length=5000)
    returns:str=Field(min_length=20,max_length=5000)
    contactPhone:str=Field(min_length=7,max_length=40)
    contactEmail:str=Field(min_length=5,max_length=150)
    pickupAddress:str=Field(min_length=5,max_length=500)
class StatusChange(Input):
    status:Literal['new','confirmed','preparing','ready','shipped','completed','cancelled']
    note:str=Field(default='',max_length=1000)
    updatedAt:float

def public_order(order):
    return {k:order[k] for k in ['id','createdAt','updatedAt','status','lines','total','history']}

def install_shop(app,store,by_id,purchase,quoted_products,authenticated,origin,throttle):
    from .portal import ROOT, warehouse_id
    def current_lines(lines):
        pairs=set();codes=[]
        for l in lines:
            if l.productId not in by_id:raise HTTPException(404,'product_not_found')
            if (l.productId,l.warehouseId) in pairs:raise HTTPException(422,'duplicate_order_line')
            pairs.add((l.productId,l.warehouseId));codes.append(by_id[l.productId]['sku'])
        costs=purchase(list(dict.fromkeys(codes)))
        current=quoted_products([l.productId for l in lines],costs);out=[];used={}
        for line in lines:
            p=current[line.productId]
            offers=[o for o in p['offers'] if o['stock']-used.get((p['id'],warehouse_id(o)),0)>=line.quantity and (line.warehouseId is None or warehouse_id(o)==line.warehouseId)]
            if not offers:raise HTTPException(409,'stock_or_price_unavailable')
            o=min(offers,key=lambda o:(o.get('days') if o.get('days') is not None else 999,o['price']))
            wid=warehouse_id(o);pair=(p['id'],wid);used[pair]=used.get(pair,0)+line.quantity
            out.append({'productId':p['id'],'sku':p['sku'],'warehouseId':wid,'warehouse':o['warehouse'],
                'name':p['brand']+' '+p['model'],'description':p['description'],'image':p['image'],
                'price':o['price'],'quantity':line.quantity,
                'subtotal':float((Decimal(str(o['price']))*line.quantity).quantize(Decimal('.01')))})
        return out

    @app.post('/api/shop/availability')
    def availability(body:Availability,request:Request):
        origin(request);throttle(request,'public_availability',30,60)
        ids=set(body.ids)
        if any(pid not in by_id for pid in ids):raise HTTPException(404,'product_not_found')
        costs=purchase([by_id[pid]['sku'] for pid in ids])
        return {'items':list(quoted_products(ids,costs).values())}

    @app.get('/api/shop/settings')
    def public_settings():return {**DEFAULT_SETTINGS,**store.setting('shop_settings',{})}

    @app.put('/api/manager/shop-settings')
    def settings(body:Settings,session=Depends(authenticated)):
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',body.contactEmail):raise HTTPException(422,'invalid_request')
        if not re.fullmatch(r'[+()\d\s-]+',body.contactPhone):raise HTTPException(422,'invalid_request')
        store.set_setting('shop_settings',body.model_dump());store.audit('shop_settings_changed','manager')
        return {'ok':True}

    @app.post('/api/shop/quote')
    def quote(body:Quote,request:Request):
        origin(request);throttle(request,'checkout_quote',20,60)
        lines=current_lines(body.lines)
        payload={'lines':lines,'total':float(sum(Decimal(str(l['subtotal'])) for l in lines)), 'createdAt':time.time()}
        token=store.save_checkout_quote(payload)
        return {**payload,'quoteId':token,'expiresAt':time.time()+600}

    @app.post('/api/shop/orders')
    def checkout(body:Checkout,request:Request):
        origin(request);throttle(request,'checkout',20,60)
        if not 7<=len(re.sub(r'\D','',body.phone))<=15 or not body.name.strip() or not body.city.strip():raise HTTPException(422,'invalid_request')
        auth=getattr(app.state,'customer_auth',None)
        customer=auth.current(request,required=False) if auth else None
        if customer:
            from .customer_auth import normalize_phone
            if normalize_phone(body.phone)!=customer['phone']:raise HTTPException(422,'checkout_phone_mismatch')
        customer_id=customer['id'] if customer else None
        digest_payload=body.model_dump()
        if customer_id:digest_payload['customerAccountId']=customer_id
        digest=hashlib.sha256(json.dumps(digest_payload,sort_keys=True).encode()).hexdigest()
        previous=store.customer_by_key(body.key)
        if previous:
            if previous['_requestHash']!=digest:raise HTTPException(409,'checkout_key_conflict')
            return {'id':previous['id'],'token':previous['_token'],'status':previous['status']}
        cached=store.checkout_quote(body.quoteId)
        if not cached:raise HTTPException(409,'checkout_quote_expired')
        lines=current_lines([Line(productId=l['productId'],quantity=l['quantity'],warehouseId=l['warehouseId']) for l in cached['lines']])
        if any(a['price']!=b['price'] for a,b in zip(lines,cached['lines'])):raise HTTPException(409,'checkout_price_changed')
        now=time.time();token=secrets.token_urlsafe(32)
        order={'id':'IMX-'+time.strftime('%y%m%d')+'-'+secrets.token_hex(4).upper(),
               'createdAt':now,'updatedAt':now,'status':'new','lines':lines,'total':cached['total'],
               'customer':{'name':body.name.strip(),'phone':body.phone,'city':body.city.strip(),'delivery':body.delivery,'comment':body.comment},
               'consentAt':now,'consentVersion':body.consentVersion,'consentPurpose':'order_request','history':[{'status':'new','at':now,'note':'Менеджер проверит наличие и свяжется с вами.'}],
               '_token':token,'_requestHash':digest,'_key':body.key,'_quote':body.quoteId}
        if customer_id:order['customerAccountId']=customer_id
        saved=store.save_customer_order(order)
        if saved['_requestHash']!=digest:raise HTTPException(409,'checkout_key_conflict')
        return {'id':saved['id'],'token':saved['_token'],'status':saved['status']}

    @app.get('/api/shop/orders/{token}')
    def customer_status(token:str,request:Request):
        throttle(request,'order_status',60)
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):raise HTTPException(404,'order_not_found')
        order=store.customer_by_token(token)
        if not order:raise HTTPException(404,'order_not_found')
        # No name, phone, address, procurement or supplier order payload in public status.
        return public_order(order)

    @app.get('/api/manager/customer-orders')
    def customers(session=Depends(authenticated)):
        return {'items':[dict(public_order(o),customer=o['customer'],token=o['_token']) for o in store.customer_orders()], 'statuses':STATUSES}

    @app.put('/api/manager/customer-orders/{id}/status')
    def update_customer(id:str,body:StatusChange,session=Depends(authenticated)):
        outcome=store.change_customer_status(id,body.status,body.note,body.updatedAt,TRANSITIONS)
        if outcome=='missing':raise HTTPException(404,'order_not_found')
        if outcome=='conflict':raise HTTPException(409,'order_state_changed')
        if outcome=='transition':raise HTTPException(422,'invalid_order_transition')
        store.audit('customer_order_status',id);return {'ok':True}

    @app.get('/order/')
    def order_page():return FileResponse(ROOT/'dist/order/index.html',headers={'Cache-Control':'no-store'})
