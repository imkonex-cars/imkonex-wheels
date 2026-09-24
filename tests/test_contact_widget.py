from lxml import html
from backend.site_shell import extract_contacts, extract


def test_real_tilda_destinations_are_extracted_without_executing_scripts():
    doc = html.fromstring('''<main><div class="t898 t898_animate"><a href="https://wa.me/79525205454">WA</a><a href="https://iimax.ru/Imkonex">MAX</a><a href="https://t.me/IMKONEX_GROUP">TG</a><a href="tel:+78003013688">Phone</a><script>neverRun()</script></div></main>''')
    result = extract_contacts(doc)
    assert [x['channel'] for x in result] == ['whatsapp', 'max', 'telegram', 'phone']
    assert result[-1]['url'] == 'tel:+78003013688'


def test_contacts_reject_untrusted_or_credential_bearing_urls():
    doc = html.fromstring('''<div id="imxMessenger"><a href="javascript:alert(1)">bad</a><a href="https://t.me.attacker.example/name">bad</a><a href="https://person:secret@t.me/name">bad</a><a href="https://t.me:8443/name">bad</a><a href="tel:1800,999#">bad</a><a href="http://wa.me/123456789">bad</a></div>''')
    assert extract_contacts(doc) == []


def test_generic_telegram_channel_is_not_treated_as_contact_widget():
    assert extract_contacts(html.fromstring('<main><a href="https://t.me/imkonexcar">News</a></main>')) == []


def test_header_footer_hash_changes_with_contact_destination():
    raw = '<header id="imxGlobalHeader">'+''.join('<a href="/x">x</a>' for _ in range(5))+'</header><footer id="imxPremiumFooter">'+''.join('<a href="/y">y</a>' for _ in range(5))+'</footer><div class="t898"><a href="https://t.me/IMKONEX_GROUP">TG</a></div>'
    before=extract(raw);after=extract(raw.replace('IMKONEX_GROUP','IMKONEX_NEW'))
    assert before['version'] != after['version']
    assert before['formatVersion'] == 2
    assert before['contacts'][0]['url'] == 'https://t.me/IMKONEX_GROUP'
