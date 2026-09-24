#!/usr/bin/env python3
"""Read-only discovery for one configured company's API access. No orders/SMS."""
import argparse
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.portal_supplier import PortalSupplier, SupplierFailure

parser=argparse.ArgumentParser(description='Получить покупателей API без создания заказов.')
parser.add_argument('company',choices=['alites','inveks'])
args=parser.parse_args()
prefix='FOURTOCHKI_'+args.company.upper()
login,password=os.getenv(prefix+'_LOGIN',''),os.getenv(prefix+'_PASSWORD','')
if not login or not password:
    sys.exit('Доступ компании не настроен в закрытом окружении сервера.')
try:
    supplier=PortalSupplier(login=login,password=password)
    rows=supplier.customers()
    print('Покупатели, доступные указанному API-аккаунту:')
    for row in rows:
        name=' '.join(row['name'].split())
        print(f"ID {row['id']} | {name} | юридическое лицо: {'да' if row['isLegal'] else 'нет'}")
    print('ID покупателя не подтверждает договор. Сверьте название и договор в кабинете поставщика.')
except SupplierFailure as error:
    sys.exit('Запрос не выполнен: '+error.code)
