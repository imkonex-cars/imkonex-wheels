"""Published main-site navigation, sanitized and cached; no remote scripts execute."""
import hashlib
import json
from pathlib import Path
import threading
import time
from urllib.parse import urljoin, urlsplit
import requests
from lxml import html

SOURCE = 'https://www.imkonex.com/'
ORIGINS = {'https://www.imkonex.com', 'https://imkonex.com', 'https://catalog.imkonex.com', 'https://wheels.imkonex.com'}
TAGS = {'header','footer','div','span','a','img','button','nav','section','p','ul','li','h2','h3','h4','strong','small','b','i','br'}
ATTRS = {'id','class','href','src','alt','title','type','role','target','width','height','loading','tabindex'}
LOGOS = {'imkonex-logo-no-acti.svg','imkonex-logo-active.svg','Imkonex-cars3.svg','Imkonex-cars3-active.svg'}

def extract(raw):
    doc = html.fromstring(raw)
    result = {}
    for key, identity in [('header','imxGlobalHeader'),('footer','imxPremiumFooter')]:
        nodes = doc.xpath('//*[@id=$id]', id=identity)
        if len(nodes) != 1:
            raise ValueError('main_site_markup_changed')
        root = nodes[0]
        for node in list(root.iterdescendants()):
            if not isinstance(node.tag,str) or node.tag.lower() not in TAGS:
                if node.getparent() is not None: node.drop_tree() if hasattr(node,'drop_tree') else node.getparent().remove(node)
        for node in root.iter():
            for attr in list(node.attrib):
                if attr not in ATTRS and not attr.startswith('aria-'):
                    del node.attrib[attr]
            if node.tag == 'button': node.set('type','button')
            for attr in ['href','src']:
                value = node.get(attr)
                if not value: continue
                url = urljoin(SOURCE,value)
                parsed = urlsplit(url)
                if attr == 'src':
                    name = parsed.path.rsplit('/',1)[-1]
                    if parsed.hostname == 'static.tildacdn.com' and name in LOGOS:
                        node.set('src','/shared/brand/'+name)
                    else: node.attrib.pop('src',None)
                elif parsed.scheme in ('https','mailto','tel') and not parsed.username and not parsed.password:
                    node.set('href',url)
                else: node.attrib.pop('href',None)
            if node.get('target') == '_blank': node.set('rel','noopener noreferrer')
        result[key] = html.tostring(root,encoding='unicode',with_tail=False)
        if len(root.xpath('.//a[@href]')) < 5: raise ValueError('incomplete_main_navigation')
    result['version'] = hashlib.sha256((result['header']+result['footer']).encode()).hexdigest()[:20]
    result['checkedAt'] = int(time.time())
    return result

class SiteShell:
    def __init__(self, cache, fallback):
        self.cache=Path(cache);self.lock=threading.RLock();self.last_error=None
        try: self.value=json.loads(self.cache.read_text())
        except (OSError,ValueError): self.value=json.loads(Path(fallback).read_text())
    def refresh(self):
        try:
            with requests.get(SOURCE,timeout=(5,15),stream=True,allow_redirects=False) as r:
                r.raise_for_status();data=bytearray()
                for part in r.iter_content(65536):
                    data.extend(part)
                    if len(data)>2_000_000: raise ValueError('main_site_too_large')
            value=extract(bytes(data))
            temp=self.cache.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False));temp.replace(self.cache)
            with self.lock:self.value=value;self.last_error=None
        except Exception:
            self.last_error='main_site_refresh_unavailable'
    def start(self,stop):
        def loop():
            while not stop.is_set():
                self.refresh()
                if stop.wait(300):break
        threading.Thread(target=loop,name='shared-navigation',daemon=True).start()
