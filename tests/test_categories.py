"""Extended catalog imports use explicit supplier categories and public retail only."""
from copy import deepcopy
from pathlib import Path
import json
import subprocess
import unittest
from backend import catalog_sync as sync
from backend.catalog_types import extended_product,TYPE_CATEGORY
from test_catalog_sync import FakeSupplier,config,detail
ROOT=Path(__file__).resolve().parent.parent

class ExtendedSupplier(FakeSupplier):
    def __call__(self,name,**opts):
        if name=='GetFindCamera':
            page=opts['page'];rows=[] if page>1 else [{'code':'C1','whpr':{'wh_price_rest':[{'wrh':2017,'rest':8,'price':'9876543.21','price_rozn':'2000'}]}}]
            return {'totalPages':1,'currencyRate':{'charCode':'RUB','nominal':1,'value':1},'price_rest_list':{'CameraPriceRest':rows}}
        if name=='GetGoodsInfo' and opts['codes'][0].startswith('C'):
            return {'cameraList':{'CameraContainer':[{'code':'C1','brand':'Tube brand','model':None,'name':'Камера 12.00-20 TR-179','diameter':'20','sub_type_id':0}]}}
        result=super().__call__(name,**opts)
        if name=='GetFindTyre':
            for row in result['price_rest_list']['TyrePriceRest']:
                row['type']=list(TYPE_CATEGORY)[int(row['code'][1:])%len(TYPE_CATEGORY)]
        return result

class ExtendedTests(unittest.TestCase):
    def test_categories_use_explicit_type_never_dimensions(self):
        row={'code':'T1','whpr':{'wh_price_rest':[{'wrh':2017,'rest':4,'price':'9876543.21','price_rozn':5000}]}}
        for raw,expected in TYPE_CATEGORY.items():
            p=extended_product('tires',detail('T1'),{**row,'type':raw},{2017:'Test'},[])
            self.assertEqual(p['vehicleCategory'],expected)
            self.assertNotIn('9876543.21',json.dumps(p))
        unknown=extended_product('tires',detail('T1'),row,{2017:'Test'},[])
        self.assertEqual(unknown['vehicleCategory'],'other')

    def test_camera_contract_and_allseason_nonstandard_dimensions(self):
        d=detail('T1');d.update(diameter=38,width=16.9,height=None,season='u',constr=None)
        row={'code':'T1','type':'selhoz','whpr':{'wh_price_rest':[{'wrh':2017,'rest':4,'price_rozn':5000}]}}
        p=extended_product('tires',d,row,{2017:'Test'},[])
        self.assertEqual((p['vehicleCategory'],p['season'],p['profile'],p['construction']),('special','allseason',None,None))
        self.assertEqual(sync.request_arguments('GetFindCamera','x','y')['filter'],{'subtype_id_list':{'unsignedByte':[0]}})
        self.assertEqual(sync.request_arguments('GetFindTyre','x','y',extended=True)['filter']['quality'],0)

    def test_extended_import_is_complete_and_passes_public_contract(self):
        cfg=config();cfg.update(extended_categories=True,include_tubes=True)
        data=sync.collect_catalog(ExtendedSupplier(),cfg)
        self.assertEqual(data['schemaVersion'],4);self.assertEqual(len(data['products']),15)
        tube=next(p for p in data['products'] if p['kind']=='tubes')
        self.assertEqual(tube['subtype'],0);self.assertEqual(data['sync']['categories']['tubes']['published'],1)
        script="import {validateSnapshot} from './scripts/public-catalog.mjs';let s='';for await(const b of process.stdin)s+=b;validateSnapshot(JSON.parse(s));"
        result=subprocess.run(['node','--input-type=module','-e',script],input=json.dumps(data),text=True,capture_output=True,cwd=ROOT)
        self.assertEqual(result.returncode,0,result.stderr)
        changed=deepcopy(data);changed['products'][0]['purchasePrice']=123
        result=subprocess.run(['node','--input-type=module','-e',script],input=json.dumps(changed),text=True,capture_output=True,cwd=ROOT)
        self.assertNotEqual(result.returncode,0)

    def test_unavailable_cameras_do_not_erase_other_categories(self):
        def fake(page):
            if page==1:raise sync.ProviderError('provider_rejected_request')
            return {'totalPages':0,'currencyRate':{'charCode':'RUB','nominal':1,'value':1},'price_rest_list':{'CameraPriceRest':[]}}
        stats={};self.assertEqual(list(sync.search_pages(lambda name,**kw:fake(kw['page']),'tubes',config(),stats)),[])
        self.assertEqual(stats['scanned'],0)
