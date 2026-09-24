import json
import pytest
from backend.sms_ru import SmsRuSender


class Reply:
    def __init__(self, payload, status=200): self.payload, self.status_code = payload, status
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, size): yield json.dumps(self.payload).encode()


def test_only_specific_phone_acceptance_is_success(monkeypatch):
    calls=[]
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Reply({'status':'OK','status_code':100,'sms':{'79991112233':{'status':'OK','status_code':100}}})
    monkeypatch.setattr('backend.sms_ru.requests.post',post)
    SmsRuSender('synthetic-secret').send('+79991112233','synthetic OTP','request-1')
    assert len(calls)==1
    url, request=calls[0]
    assert url=='https://sms.ru/sms/send' and request['allow_redirects'] is False
    assert request['data']['api_id']=='synthetic-secret' and request['data']['ttl']==5
    assert 'test' not in request['data'] and 'params' not in request


@pytest.mark.parametrize('payload,status',[
    ({'status':'OK','status_code':100,'sms':{'79991112233':{'status':'ERROR','status_code':201}}},200),
    ({'status':'OK','status_code':100,'sms':{}},200),
    ({'status':'ERROR','status_code':200},200),({},302),({'x':'z'*17000},200)])
def test_rejections_never_look_like_sent(monkeypatch,payload,status):
    calls=[]
    def post(url,**kwargs): calls.append(url);return Reply(payload,status)
    monkeypatch.setattr('backend.sms_ru.requests.post',post)
    with pytest.raises(RuntimeError,match='^sms_delivery_unavailable$'):
        SmsRuSender('synthetic-secret').send('+79991112233','synthetic OTP','id-1')
    assert len(calls)==1
