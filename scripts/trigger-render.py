"""Optional Render hook; the secret URL and response body are never printed."""
import os
import re
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    url = os.getenv('RENDER_DEPLOY_HOOK_URL', '')
    if not url:
        print('No deploy hook configured. Render must deploy the new commit via Auto-Deploy or Manual Deploy.')
        return
    try:
        p = urlsplit(url)
        if (p.scheme != 'https' or p.hostname != 'api.render.com' or p.port not in (None, 443)
                or p.username or p.password or p.fragment or not re.fullmatch(r'/deploy/srv-[a-zA-Z0-9-]+', p.path)):
            raise ValueError('invalid_hook')
        with build_opener(NoRedirect()).open(Request(url, data=b'', method='POST'), timeout=20) as response:
            if response.status not in (200, 202):
                raise ValueError('unexpected_status')
        print('Render accepted the deployment request. Check the Render build log for completion.')
    except Exception:
        print('RENDER_DEPLOY_FAILED. The public snapshot is committed. Use Manual Deploy on Render.')
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
