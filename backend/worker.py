"""Import a local, normalized feed when its contents change (VPS only).

The supplier-to-normalized-feed transformation is a separate integration task.
Publish feeds by atomic file rename; a failed import preserves the last snapshot.
"""
import hashlib
import os
import time
from pathlib import Path
from .models import Catalog
from .repository import import_catalog

def main():
    value=os.getenv('NORMALIZED_FEED_PATH','')
    if not value:
        raise SystemExit('Set NORMALIZED_FEED_PATH to a normalized JSON feed. No supplier sync is configured.')
    path=Path(value)
    interval=max(60,int(os.getenv('SYNC_INTERVAL_SECONDS','900')))
    previous=None
    while True:
        try:
            if path.stat().st_size>128*1024*1024: raise ValueError('Feed too large')
            raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
            if digest!=previous:
                catalog=Catalog.model_validate_json(raw)
                import_catalog(catalog)
                previous=digest
                print(f'Imported {len(catalog.products)} products; mode={catalog.mode}',flush=True)
        except Exception as error:
            print(f'Import failed ({type(error).__name__}); previous catalog retained.',flush=True)
        time.sleep(interval)

if __name__=='__main__': main()
