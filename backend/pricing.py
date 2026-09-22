"""One private pricing engine for the storefront, quotes and manager edits."""
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
import time

MIN_GAIN = Decimal('100.00')
CATEGORIES = {'passenger':'Легковые','truck':'Грузовые','moto':'Мото','special':'Спецтехника',
              'other':'Прочие шины','tires':'Все шины','wheels':'Диски','tubes':'Камеры',
              'sensors':'Датчики давления','consumables':'Расходники','oils':'Масла','all':'Все товары'}

def category(product):
    return product.get('vehicleCategory') or ('other' if product['kind']=='tires' else product['kind'])

def policy_for(product, policies):
    return policies.get(category(product), policies.get(product['kind'], policies.get('all',{'mode':'supplier'})))

def fresh_cost(cost, now=None):
    return bool(cost and cost['cost']>0 and 0 <= (time.time() if now is None else now)-cost['updated'] <= 86400)

def price_result(offer, rule, cost, now=None):
    if not fresh_cost(cost,now):return {'price':None,'floor':None,'adjusted':False}
    rule=rule or {'mode':'supplier'}
    base=Decimal(str(cost['cost']))
    floor=base+max(MIN_GAIN,Decimal(str(rule.get('minimum',100))))
    retail=Decimal(str(cost.get('retail') or 0))
    mode=rule['mode']
    if mode in ('supplier','discount'):
        if retail<=0:return {'price':None,'floor':float(floor),'adjusted':False}
        proposed=retail*(1-Decimal(str(rule.get('percent',0)))/100) if mode=='discount' else retail
    elif mode=='fixed':proposed=Decimal(str(rule['price']))
    elif mode=='markup':proposed=base*(1+Decimal(str(rule['percent']))/100)
    elif mode=='profit':proposed=base+Decimal(str(rule['amount']))
    else:raise ValueError('unknown_price_mode')
    step=Decimal('.01') if mode in ('supplier','fixed') else Decimal(str(rule.get('roundTo',10)))
    rounded=(max(proposed,floor)/step).quantize(Decimal('1'),rounding=ROUND_CEILING if step==10 else ROUND_HALF_UP)*step
    rounded=max(rounded,floor.quantize(Decimal('.01'),rounding=ROUND_CEILING))
    return {'price':float(rounded) if 0<rounded<=100000000 else None,
            'floor':float(floor),'adjusted':proposed<floor}

def sale_price(offer,rule,cost,now=None):return price_result(offer,rule,cost,now)['price']
