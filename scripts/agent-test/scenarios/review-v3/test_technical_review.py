"""Explicit review probes; failures retain the desired behavior as regression tests.
Run only this file explicitly. All requests/stores/identities are synthetic.
"""
import base64
import hashlib
import hmac
import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

ROOT=Path(__file__).resolve().parents[4]
for d in ('application','data','tests/unit'):
    sys.path.insert(0,str(ROOT/d))

from chann_app.services.documents.design import sanitise,TemplateRejected
from chann_app.services.documents.docx import check_docx_bytes,DocxConversionError
from test_document_templates_docx import _app,_Client,_Store,_url


@pytest.mark.parametrize('css',[
    r'p { background: u\72l(https://example.invalid/a) }',
    r'@\69mport "https://example.invalid/a.css";',
    r'p { background: \75rl(https://example.invalid/a) }',
])
def test_ai_template_blocks_escaped_external_css(css):
    """Reserved nonresolving URL; sanitizer only, no rendering or requests."""
    with pytest.raises(TemplateRejected):
        sanitise('<style>'+css+'</style><p>synthetic template</p>')


@pytest.mark.parametrize('html',[
    '<p>synthetic</p><script>console.log("review-only")</script>',
    '<p onclick="console.log(1)">synthetic</p>',
    '<iframe src="https://example.invalid/"></iframe><p>synthetic</p>',
])
def test_uploaded_html_must_not_store_active_content(monkeypatch,html):
    from chann_app.services.storage import base as storage
    store=_Store();client=_Client()
    monkeypatch.setattr(storage,'get_document_store',lambda:store)
    with _app(client) as http:
        response=http.post(_url('/upload'),json={'template_name':'Audit synthetic','html':html})
    if response.status_code==201:
        compiled=list(store.objects.values())
        assert not any(html.encode() in blob for blob in compiled), 'Active HTML accepted and stored unchanged; preview external-open path needs isolation'
    else:
        assert response.status_code in (400,422)


def test_docx_preflight_bounds_uncompressed_content():
    # Only 8 MiB generated locally; demonstrates amplification without a bomb.
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('word/document.xml','<document>synthetic</document>')
        z.writestr('word/unused.xml','A'*(8*1024*1024))
    payload=buffer.getvalue()
    assert len(payload)<20_000
    with pytest.raises(DocxConversionError):
        check_docx_bytes(payload,filename='synthetic.docx')


class WebhookStore:
    """The Data tier's webhook-event table, in memory.

    Extended from the review's original three-line "have I seen this id?"
    to the claim/finish protocol the fix introduces. The ASSERTIONS in
    every test below are the review's, unchanged; only this fake follows
    the interface the Application tier now calls.
    """
    def __init__(self):self.events={}
    async def claim_webhook_event(self,event_id,oa):
        row=self.events.get(event_id)
        if row is None:
            self.events[event_id]={'status':'processing','reply':None,'oa':oa}
            return {'state':'new','reply':None}
        if row['status']=='handled':return {'state':'reply_pending','reply':row['reply']}
        if row['status']=='processing':return {'state':'in_progress','reply':None}
        return {'state':'duplicate','reply':None}
    async def finish_webhook_event(self,event_id,state,reply=None):
        row=self.events.get(event_id)
        if row is None:return
        if state=='failed':
            if row['status']!='handled':self.events.pop(event_id,None)
            return
        if state=='handled' and row['status']!='done':
            row['status']='handled';row['reply']=reply
        elif state=='done':
            row['status']='done';row['reply']=None
    async def record_message_entity(self,*a,**k):return None
    async def get_display_preferences(self,uid):return {}
    async def aclose(self):pass


def setup_webhook(monkeypatch):
    from chann_app.line import webhook as w
    store=WebhookStore()
    monkeypatch.setattr(w,'DataClient',lambda:store)
    monkeypatch.setattr(w,'channel_secret',lambda oa:'synthetic-review-secret')
    monkeypatch.setattr(w,'is_unregistered',lambda ctx:False)
    monkeypatch.setattr(w.live_chat,'sweep',AsyncMock())
    ctx=SimpleNamespace(chann_uid='synthetic-user',license_id='synthetic-tenant')
    monkeypatch.setattr(w,'resolve_context',AsyncMock(return_value=ctx))
    monkeypatch.setattr(w,'handle_chat_message',AsyncMock(return_value=w.ChatReply(text='synthetic reply')))
    monkeypatch.setattr(w,'reply_messages',AsyncMock(return_value=['synthetic-outbound']))
    app=FastAPI();app.include_router(w.router)
    event={'type':'message','webhookEventId':'synthetic-event-1','source':{'type':'user','userId':'synthetic-line-user'},'message':{'type':'text','id':'synthetic-inbound','text':'สวัสดี'},'replyToken':'synthetic-token'}
    return w,app,event,ctx


async def send(http,event):
    raw=json.dumps({'events':[event]},ensure_ascii=False).encode()
    signature=base64.b64encode(hmac.new(b'synthetic-review-secret',raw,hashlib.sha256).digest()).decode()
    return await http.post('/webhook/line/sales',content=raw,headers={'x-line-signature':signature,'Content-Type':'application/json'})


@pytest.mark.asyncio
async def test_failed_identity_resolution_is_retryable_on_redelivery(monkeypatch):
    w,app,event,ctx=setup_webhook(monkeypatch)
    w.resolve_context.side_effect=[RuntimeError('synthetic temporary dependency outage'),ctx]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://test') as http:
        first=await send(http,event)
        event['deliveryContext']={'isRedelivery':True}
        second=await send(http,event)
    assert first.status_code==500
    assert second.status_code==200
    assert w.handle_chat_message.await_count==1, 'Retry acknowledged 200 but original message never processed after identity recovered'


@pytest.mark.asyncio
async def test_failed_reply_is_retried_without_repeating_business_action(monkeypatch):
    w,app,event,ctx=setup_webhook(monkeypatch)
    w.reply_messages.side_effect=[w.LineReplyError('synthetic temporary send failure'),['synthetic-retry-id']]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://test') as http:
        first=await send(http,event)
        event['deliveryContext']={'isRedelivery':True}
        second=await send(http,event)
    assert first.status_code==503 and second.status_code==200
    assert w.handle_chat_message.await_count==1
    assert w.reply_messages.await_count==2, 'Business action ran once, but failed outbound reply is permanently dropped'


@pytest.mark.asyncio
async def test_successful_redelivery_does_not_repeat_business_action(monkeypatch):
    w,app,event,ctx=setup_webhook(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as http:
        assert (await send(http,event)).status_code==200
        assert (await send(http,event)).status_code==200
    assert w.handle_chat_message.await_count==w.reply_messages.await_count==1


@pytest.mark.asyncio
async def test_bad_signature_does_not_reach_identity_or_business(monkeypatch):
    w,app,event,ctx=setup_webhook(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as http:
        response=await http.post('/webhook/line/sales',json={'events':[event]},headers={'x-line-signature':'invalid'})
    assert response.status_code==401
    assert w.resolve_context.await_count==w.handle_chat_message.await_count==0
