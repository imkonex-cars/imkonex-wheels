"""Bounded cache of supplier originals; no credentials and no image rewriting."""
import hashlib
import io
from pathlib import Path
import re
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

FALLBACK = 'assets/product-unavailable.svg'
MAX_IMAGE_BYTES = 2_000_000
LOCAL_PHOTO = re.compile(r'assets/products/[a-zA-Z0-9][a-zA-Z0-9_-]*\.(png|jpg|jpeg|webp)\Z')


def public_photo_url(row):
    for field in ('img_big_pish', 'img_big', 'img_small'):
        value = row.get(field)
        if not isinstance(value, str):
            continue
        try:
            p = urlsplit(value)
            if (p.scheme in ('http', 'https') and p.hostname == 'www.4tochki.ru'
                    and not p.username and not p.password and not p.query and not p.fragment
                    and p.port in (None, 80, 443) and '..' not in p.path
                    and re.fullmatch(r'/pictures/[a-zA-Z0-9_./-]+\.(png|jpg|jpeg|webp)', p.path)):
                return urlunsplit(('https', 'www.4tochki.ru', p.path, '', ''))
        except ValueError:
            continue
    return None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_photo(url):
    if public_photo_url({'img_big_pish': url}) != url:
        return None
    # A separate anonymous request never carries SOAP credentials/cookies.
    request = Request(url, headers={'User-Agent': 'IMKONEX-PHOTO-CACHE/0.4.0', 'Accept': 'image/*'})
    try:
        deadline = time.monotonic() + 8
        with build_opener(NoRedirect()).open(request, timeout=6) as response:
            chunks, size = [], 0
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_IMAGE_BYTES or time.monotonic() > deadline:
                    return None
                chunks.append(chunk)
            body = b''.join(chunks)
            if response.status != 200 or not 0 < len(body) <= MAX_IMAGE_BYTES:
                return None
            return body
    except Exception:
        return None


def image_extension(body):
    from PIL import Image
    if not body or len(body) > MAX_IMAGE_BYTES:
        return None
    try:
        with Image.open(io.BytesIO(body)) as image:
            if not 16 <= image.width <= 4096 or not 16 <= image.height <= 4096 or image.width * image.height > 8_000_000:
                return None
            ext = {'PNG': 'png', 'JPEG': 'jpg', 'WEBP': 'webp'}.get(image.format)
            image.verify()
            return ext
    except Exception:
        return None


def cache_photos(data, previous, frontend, stage, config, *, downloader=None):
    downloader = downloader or download_photo
    old = {p['id']: p for p in (previous or {}).get('products', [])}
    directory = frontend / 'assets/products'
    size = sum(p.stat().st_size for p in directory.glob('*') if p.is_file())
    result, attempts, by_url = {'downloaded': 0, 'local': 0, 'remote': 0, 'unavailable': 0}, 0, {}
    deadline = time.monotonic() + config['photo_time_seconds']
    # Rotate pending URLs between runs, so a permanently broken URL cannot starve later models.
    day = time.strftime('%Y-%m-%d-%H', time.gmtime())
    ordered = sorted(data['products'], key=lambda p: hashlib.sha256((day + p['image']).encode()).digest())
    for p in ordered:
        url, prior = p['image'], old.get(p['id'], {})
        prior_image = prior.get('image', '')
        same_model = all(prior.get(k) == p.get(k) for k in ('kind', 'brand', 'model', 'color'))
        if same_model and LOCAL_PHOTO.fullmatch(prior_image) and (frontend / prior_image).is_file():
            p['image'] = prior_image
        elif url in by_url:
            p['image'] = by_url[url]
        elif url.startswith('https://'):
            stem = '4t-' + hashlib.sha256(url.encode()).hexdigest()[:24]
            existing = next((f'assets/products/{stem}.{ext}' for ext in ('png', 'jpg', 'webp')
                             if (directory / f'{stem}.{ext}').is_file()), None)
            if existing:
                p['image'] = existing
            elif attempts < config['photos_per_run'] and time.monotonic() < deadline and size < config['photo_cache_bytes']:
                attempts += 1
                body = downloader(url)
                ext = image_extension(body)
                if ext and size + len(body) <= config['photo_cache_bytes']:
                    local = f'assets/products/{stem}.{ext}'
                    destination = stage / local
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(body)
                    size += len(body)
                    result['downloaded'] += 1
                    p['image'] = local
            by_url[url] = p['image']
        if p['image'].startswith('assets/products/'):
            result['local'] += 1
        elif p['image'].startswith('https://'):
            result['remote'] += 1
        else:
            result['unavailable'] += 1
    return result
