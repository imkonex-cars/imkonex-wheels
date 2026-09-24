"""SMS.ru adapter: https://sms.ru/api/send. No retries or credential logging."""
import json
import requests


class SmsRuSender:
    def __init__(self, api_id, sender=''):
        if not isinstance(api_id, str) or not api_id.strip():
            raise ValueError('sms_not_configured')
        self._api_id, self._sender = api_id, sender

    def send(self, phone, message, idempotency_key):
        # The OTP service owns durable suppression of duplicate sends. SMS.ru's
        # documented send method has no Idempotency-Key guarantee; never retry.
        number = phone.removeprefix('+')
        data = {'api_id': self._api_id, 'to': number, 'msg': message, 'json': 1, 'ttl': 5}
        if self._sender: data['from'] = self._sender
        try:
            with requests.post('https://sms.ru/sms/send', data=data,
                               timeout=(5, 15), allow_redirects=False, stream=True) as response:
                if response.status_code != 200: raise ValueError()
                raw = bytearray()
                for chunk in response.iter_content(4096):
                    raw.extend(chunk)
                    if len(raw) > 16384: raise ValueError()
                payload = json.loads(raw)
            result = payload.get('sms', {}).get(number, {})
            if (payload.get('status') != 'OK' or payload.get('status_code') != 100
                    or result.get('status') != 'OK' or result.get('status_code') != 100):
                raise ValueError()
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            raise RuntimeError('sms_delivery_unavailable') from None
