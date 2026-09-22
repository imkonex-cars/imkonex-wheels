"""Private procurement cache for all products; never exported to public JSON/git."""
import threading
import time


def refresh_markups(store, api, by_id, stop, invalidate):
    if not api.configured:return
    costs=store.costs();now=time.time();checked={}
    for (sku,_),row in costs.items():checked[sku]=max(checked.get(sku,0),row['updated'])
    codes=sorted({p['sku'] for p in by_id.values()},key=lambda sku:checked.get(sku,0))
    codes=[sku for sku in codes if now-checked.get(sku,0)>21600]
    state={'status':'running','total':len(codes),'done':0,'startedAt':now}
    store.set_setting('purchase_sync',state)
    deadline=time.monotonic()+2400
    for start in range(0,len(codes),50):
        if stop.is_set() or time.monotonic()>deadline:
            store.set_setting('purchase_sync',{**state,'status':'paused'});return
        batch=codes[start:start+50]
        try:
            rows=api.purchase(batch);store.save_costs(batch,rows);invalidate()
        except Exception:
            store.set_setting('purchase_sync',{**state,'status':'unavailable'})
            store.audit('purchase_refresh_failed','supplier');return
        state['done']=start+len(batch);store.set_setting('purchase_sync',state)
        if stop.wait(.5):return
    store.set_setting('purchase_sync',{**state,'status':'ready','finishedAt':time.time()})


def start_refresh(store,api,by_id,invalidate):
    stop=threading.Event()
    def loop():
        while not stop.is_set():
            refresh_markups(store,api,by_id,stop,invalidate)
            if stop.wait(900):break
    threading.Thread(target=loop,name='private-purchase-refresh',daemon=True).start()
    return stop
