"""Single persistent manager process; no credentials in shell arguments."""
import os
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)
os.umask(0o077)
port = str(int(os.getenv('PORT', '10000')))
# Ignore unsolicited proxy forwarding headers. PUBLIC_ORIGIN/RENDER_EXTERNAL_URL
# determines the trusted browser origin independently of proxy headers.
os.execvp('uvicorn', ['uvicorn', 'backend.portal:app', '--host', '0.0.0.0',
                     '--port', port, '--workers', '1', '--no-proxy-headers'])
