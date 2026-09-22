"""Persistent private manager data. Use MANAGER_DB_PATH on a persistent volume."""
from contextlib import contextmanager
import json
import hashlib
import secrets
from pathlib import Path
import sqlite3
import time


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS prices(product_id TEXT PRIMARY KEY, rule TEXT NOT NULL, updated REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS purchase(sku TEXT NOT NULL, warehouse INTEGER NOT NULL,
                cost REAL NOT NULL, stock INTEGER NOT NULL, updated REAL NOT NULL, PRIMARY KEY(sku,warehouse));
              CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, event TEXT NOT NULL, subject TEXT NOT NULL, created REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS offer_prices(product_id TEXT NOT NULL, warehouse INTEGER NOT NULL,
                rule TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(product_id,warehouse));
              CREATE TABLE IF NOT EXISTS selections(token TEXT PRIMARY KEY,payload TEXT NOT NULL,expires REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS checkout_quotes(token TEXT PRIMARY KEY,payload TEXT NOT NULL,expires REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS customer_orders(id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
                request_key TEXT UNIQUE NOT NULL, quote_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);

            ''')
            columns={r['name'] for r in con.execute('PRAGMA table_info(purchase)')}
            if 'retail' not in columns: con.execute('ALTER TABLE purchase ADD COLUMN retail REAL')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def bind_credentials(self, login, password):
        with self.connect() as con:
            salt_row = con.execute("SELECT value FROM settings WHERE key='password_salt'").fetchone()
            salt = bytes.fromhex(salt_row['value']) if salt_row else secrets.token_bytes(32)
            digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600000)
            fingerprint = login + ':' + digest.hex()
            previous = con.execute("SELECT value FROM settings WHERE key='credentials'").fetchone()
            if not previous or previous['value'] != fingerprint:
                con.execute('DELETE FROM sessions')
                con.execute("INSERT INTO settings VALUES ('credentials',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (fingerprint,))
            con.execute("INSERT OR IGNORE INTO settings VALUES ('password_salt',?)", (salt.hex(),))
        return salt, digest

    def session(self, token):
        with self.connect() as con:
            con.execute('DELETE FROM sessions WHERE expires < ?', (time.time(),))
            row = con.execute('SELECT * FROM sessions WHERE token=?', (token,)).fetchone()
            return dict(row) if row else None

    def new_session(self, token, csrf):
        with self.connect() as con:
            con.execute('INSERT INTO sessions VALUES (?,?,?)', (token, csrf, time.time()+28800))

    def logout(self, token):
        with self.connect() as con:
            con.execute('DELETE FROM sessions WHERE token=?', (token,))

    def audit(self, event, subject):
        with self.connect() as con:
            con.execute('INSERT INTO audit(event,subject,created) VALUES (?,?,?)', (event, subject, time.time()))

    def rules(self):
        with self.connect() as con:
            return {r['product_id']: json.loads(r['rule']) for r in con.execute('SELECT * FROM prices')}

    def set_rule(self, product_id, rule):
        with self.connect() as con:
            if rule is None:
                con.execute('DELETE FROM prices WHERE product_id=?', (product_id,))
            else:
                con.execute('INSERT INTO prices VALUES (?,?,?) ON CONFLICT(product_id) DO UPDATE SET rule=excluded.rule,updated=excluded.updated',
                            (product_id, json.dumps(rule), time.time()))
        self.audit('price_changed', product_id)

    def costs(self, codes=None):
        with self.connect() as con:
            if codes is None:
                rows = con.execute('SELECT * FROM purchase').fetchall()
            elif not codes:
                return {}
            else:
                rows = con.execute('SELECT * FROM purchase WHERE sku IN ('+','.join('?' for _ in codes)+')', codes).fetchall()
            return {(r['sku'], r['warehouse']): dict(r) for r in rows}

    def save_costs(self, codes, offers):
        now = time.time()
        with self.connect() as con:
            # A missing product is out of stock; remove stale cached availability.
            con.executemany('DELETE FROM purchase WHERE sku=?', [(c,) for c in codes])
            con.executemany('INSERT INTO purchase(sku,warehouse,cost,stock,updated,retail) VALUES (?,?,?,?,?,?)',
                [(o['sku'],o['warehouseId'],o['purchasePrice'],o['stock'],now,o.get('supplierRetailPrice')) for o in offers])

    def offer_rules(self):
        with self.connect() as con:
            return {(r['product_id'],r['warehouse']):json.loads(r['rule']) for r in con.execute('SELECT * FROM offer_prices')}

    def set_offer_rule(self, product_id, warehouse, rule):
        with self.connect() as con:
            if rule is None: con.execute('DELETE FROM offer_prices WHERE product_id=? AND warehouse=?',(product_id,warehouse))
            else: con.execute('INSERT INTO offer_prices VALUES (?,?,?,?) ON CONFLICT(product_id,warehouse) DO UPDATE SET rule=excluded.rule,updated=excluded.updated',
                              (product_id,warehouse,json.dumps(rule),time.time()))
        self.audit('offer_price_changed',product_id+':'+str(warehouse))

    def setting(self, key, default=None):
        with self.connect() as con: row=con.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return json.loads(row['value']) if row else default

    def set_setting(self,key,value):
        with self.connect() as con: con.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(value)))

    def save_selection(self, payload):
        token=secrets.token_urlsafe(24);expires=time.time()+7*86400
        with self.connect() as con:
            con.execute('DELETE FROM selections WHERE expires < ?',(time.time(),))
            con.execute('INSERT INTO selections VALUES (?,?,?)',(token,json.dumps(payload),expires))
        return token,expires

    def selection(self, token):
        with self.connect() as con: row=con.execute('SELECT payload,expires FROM selections WHERE token=? AND expires>?',(token,time.time())).fetchone()
        return {**json.loads(row['payload']),'expiresAt':row['expires']} if row else None

    def create_order(self, draft):
        now = time.time()
        with self.connect() as con:
            con.execute('INSERT INTO orders VALUES (?,?,?,?,?)', (draft['id'],'draft',json.dumps(draft),now,now))

    def order(self, id):
        with self.connect() as con:
            row = con.execute('SELECT * FROM orders WHERE id=?', (id,)).fetchone()
        return {**json.loads(row['payload']), 'status': row['status']} if row else None

    def order_list(self):
        with self.connect() as con:
            rows = con.execute('SELECT * FROM orders ORDER BY created DESC LIMIT 200').fetchall()
        return [{**json.loads(r['payload']), 'status': r['status']} for r in rows]

    def update_order(self, id, status, payload):
        with self.connect() as con:
            con.execute('UPDATE orders SET status=?,payload=?,updated=? WHERE id=?', (status,json.dumps(payload),time.time(),id))

    def claim_order(self, id):
        with self.connect() as con:
            result = con.execute("UPDATE orders SET status='submitting',updated=? WHERE id=? AND status='draft'", (time.time(),id))
            return result.rowcount == 1

    def save_checkout_quote(self,payload):
        token=secrets.token_urlsafe(24)
        with self.connect() as con:
            con.execute('DELETE FROM checkout_quotes WHERE expires < ?',(time.time(),))
            con.execute('INSERT INTO checkout_quotes VALUES (?,?,?)',(token,json.dumps(payload),time.time()+600))
        return token

    def checkout_quote(self,token):
        with self.connect() as con:row=con.execute('SELECT payload FROM checkout_quotes WHERE token=? AND expires>?',(token,time.time())).fetchone()
        return json.loads(row['payload']) if row else None

    def customer_by_key(self,key):
        with self.connect() as con:row=con.execute('SELECT payload FROM customer_orders WHERE request_key=?',(key,)).fetchone()
        return json.loads(row['payload']) if row else None

    def customer_by_token(self,token):
        digest=hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as con:row=con.execute('SELECT payload FROM customer_orders WHERE token_hash=?',(digest,)).fetchone()
        return json.loads(row['payload']) if row else None

    def save_customer_order(self,payload):
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT payload FROM customer_orders WHERE request_key=? OR quote_id=?',(payload['_key'],payload['_quote'])).fetchone()
            if row:return json.loads(row['payload'])
            con.execute('INSERT INTO customer_orders VALUES (?,?,?,?,?,?)',(payload['id'],hashlib.sha256(payload['_token'].encode()).hexdigest(),payload['_key'],payload['_quote'],json.dumps(payload),payload['createdAt']))
        return payload

    def customer_orders(self):
        with self.connect() as con:rows=con.execute('SELECT payload FROM customer_orders ORDER BY created DESC LIMIT 200').fetchall()
        return [json.loads(r['payload']) for r in rows]

    def change_customer_status(self,id,status,note,updated,transitions):
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT payload FROM customer_orders WHERE id=?',(id,)).fetchone()
            if not row:return 'missing'
            p=json.loads(row['payload'])
            if p['updatedAt']!=updated:return 'conflict'
            if status not in transitions[p['status']]:return 'transition'
            p.update(status=status,updatedAt=time.time())
            p['history'].append({'status':status,'at':p['updatedAt'],'note':note})
            con.execute('UPDATE customer_orders SET payload=? WHERE id=?',(json.dumps(p),id))
        return 'ok'
