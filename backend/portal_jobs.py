"""Refresh only the private purchase prices used by saved markup rules."""
import threading
import time


def refresh_markups(store, api, by_id, stop, invalidate):
    if not api.configured:
        return
    codes = list(dict.fromkeys(by_id[id]['sku'] for id,rule in store.rules().items()
                              if id in by_id and rule.get('mode') == 'markup'))
    deadline = time.monotonic() + 600
    for start in range(0, len(codes), 50):
        if stop.is_set() or time.monotonic() > deadline:
            return
        batch = codes[start:start+50]
        try:
            rows = api.purchase(batch)
            store.save_costs(batch, rows)
            invalidate()
        except Exception:
            # Old prices expire independently. Never publish guessed prices.
            store.audit('markup_refresh_failed', 'supplier')
            return
        if stop.wait(1):
            return
    if codes:
        store.audit('markup_refresh_complete', str(len(codes)))


def start_refresh(store, api, by_id, invalidate):
    stop = threading.Event()
    def loop():
        while not stop.is_set():
            refresh_markups(store, api, by_id, stop, invalidate)
            if stop.wait(21600):
                break
    worker = threading.Thread(target=loop, name='private-purchase-refresh', daemon=True)
    worker.start()
    return stop
