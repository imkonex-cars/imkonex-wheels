"""Private search and warehouse metadata; only explicit safe fields leave the API."""
import hashlib
import re

def warehouse_meta(row):
    color=row.get('background-color','')
    if not isinstance(color,str) or not re.fullmatch(r'#[0-9A-Fa-f]{6}',color):color='#78909c'
    return {'id':row['id'],'name':str(row.get('name') or '')[:250],
            'shortName':str(row.get('shortName') or row['id'])[:30], 'color':color,
            'haveDelivery':row.get('haveDelivery') is True,'havePickup':row.get('havePickup') is True,
            'isPaidDelivery':row.get('isPaidDelivery') is True,
            'logisticDays':row.get('logisticDays') if type(row.get('logisticDays')) is int and row['logisticDays']>=0 else None}

def fallback_warehouse(wid,name):
    palette=['#6da34d','#9183ce','#b78a4e','#dd9135','#609cad','#b37491']
    return {'id':wid,'name':name,'shortName':name[:18],
            'color':palette[int(hashlib.sha256(str(wid).encode()).hexdigest()[:4],16)%len(palette)],
            'haveDelivery':None,'havePickup':None,'logisticDays':None,'isPaidDelivery':None}

def normalized(value):return re.sub(r'[\s/хx×.,-]+','',str(value).casefold().replace('ё','е'))

def search(products, *, q='',kind='',brand='',season='',width='',profile='',diameter='',pcd='',warehouses='',min_stock=1):
    ids=None if not warehouses else {int(w) for w in warehouses.split(',') if w.isdecimal()}
    words=[normalized(w) for w in q.split()]
    out=[]
    for p in products:
        if any(value and str(p.get(key,''))!=value for key,value in [('kind',kind),('brand',brand),('season',season),('width',width),('profile',profile),('diameter',diameter),('pcd',pcd)]):continue
        text=normalized(' '.join(str(p.get(k,'')) for k in ['sku','brand','model','description','sizeLabel']))
        if not all(w in text for w in words):continue
        offers=[o for o in p['offers'] if o['stock']>=min_stock and (ids is None or int(o['id'].removeprefix('warehouse-')) in ids)]
        if offers:out.append({**p,'offers':offers})
    return out
