import json
import os
from pathlib import Path
from .models import Catalog

ROOT = Path(__file__).resolve().parent.parent

def get_catalog() -> Catalog:
    mode = os.getenv('DATA_MODE','demo')
    if mode == 'demo':
        return Catalog.model_validate_json((ROOT/'data/demo-catalog.json').read_text())
    if mode != 'postgres':
        raise RuntimeError('Unsupported DATA_MODE')
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=5, row_factory=dict_row) as con:
        # A repeatable-read snapshot prevents mixing metadata and offers from different imports.
        con.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        state=con.execute('SELECT metadata FROM catalog_state WHERE id=1').fetchone()
        if not state:
            raise RuntimeError('Catalog has not been imported')
        rows=con.execute('SELECT id, payload FROM products ORDER BY id').fetchall()
        offer_rows=con.execute('SELECT product_id, payload FROM offers ORDER BY id').fetchall()
    grouped={}
    for row in offer_rows:
        grouped.setdefault(row['product_id'],[]).append(row['payload'])
    return Catalog.model_validate({**state['metadata'],'products':[{**r['payload'],'offers':grouped.get(r['id'],[])} for r in rows]})

def migrate():
    import psycopg
    with psycopg.connect(os.environ['DATABASE_URL'],connect_timeout=5) as con:
        con.execute((ROOT/'backend/schema.sql').read_text())

def import_catalog(catalog: Catalog):
    """Replace a normalized public catalog atomically; never accepts raw SOAP responses."""
    import psycopg
    from psycopg.types.json import Jsonb
    metadata=catalog.model_dump(mode='json',exclude={'products'})
    with psycopg.connect(os.environ['DATABASE_URL'],connect_timeout=5) as con:
        con.execute('SELECT pg_advisory_xact_lock(7142026)')
        run=con.execute('INSERT INTO import_runs(product_count, is_demo) VALUES (%s,%s) RETURNING id',(len(catalog.products),catalog.mode=='demo')).fetchone()[0]
        con.execute('DELETE FROM offers')
        con.execute('DELETE FROM products')
        with con.cursor() as cur:
            cur.executemany('INSERT INTO products(id,kind,brand,payload) VALUES (%s,%s,%s,%s)',[(p.id,p.kind,p.brand,Jsonb(p.model_dump(mode='json',exclude={'offers'}))) for p in catalog.products])
            cur.executemany('INSERT INTO offers(id,product_id,stock,price,days,payload) VALUES (%s,%s,%s,%s,%s,%s)',[(p.id+':'+o.id,p.id,o.stock,o.price,o.days,Jsonb(o.model_dump(mode='json'))) for p in catalog.products for o in p.offers])
        con.execute('INSERT INTO catalog_state(id,metadata) VALUES(1,%s) ON CONFLICT(id) DO UPDATE SET metadata=excluded.metadata',(Jsonb(metadata),))
        con.execute('UPDATE import_runs SET completed_at=now() WHERE id=%s',(run,))

if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description='IMKONEX normalized catalog administration')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('migrate')
    imp=sub.add_parser('import')
    imp.add_argument('file',type=Path)
    args=parser.parse_args()
    if args.command=='migrate':
        migrate(); print('Schema ready.')
    else:
        # Validate the entire file before opening a write transaction.
        catalog=Catalog.model_validate_json(args.file.read_text())
        import_catalog(catalog)
        print(f'Imported {len(catalog.products)} products; mode={catalog.mode}.')
