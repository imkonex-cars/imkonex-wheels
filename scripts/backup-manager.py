"""Consistent private SQLite backup, including committed WAL transactions.

Usage: python scripts/backup-manager.py /var/data/backups/manager-YYYYMMDD.sqlite3
Download the backup into private storage; never add it to GitHub or static assets.
"""
import os
from pathlib import Path
import sqlite3
import sys

if len(sys.argv) != 2:
    raise SystemExit('Specify a NEW private backup path.')
source = Path(os.environ.get('MANAGER_DB_PATH', 'runtime/manager.sqlite3')).resolve()
target = Path(sys.argv[1]).resolve()
if not source.is_file() or target.exists() or source == target:
    raise SystemExit('Source must exist and destination must be a new file.')
target.parent.mkdir(parents=True, exist_ok=True)
os.umask(0o077)
with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
target.chmod(0o600)
print('Private backup created.')
