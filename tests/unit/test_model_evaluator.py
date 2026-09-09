"""Evaluation must fail honestly and stay offline unless explicitly enabled."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

SCRIPT=Path(__file__).resolve().parents[2]/'scripts/agent-test/evaluate-model.py'
spec=importlib.util.spec_from_file_location('model_evaluator',SCRIPT)
evaluator=importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluator)


def test_dry_run_cannot_call_model_even_with_env_key():
    result=subprocess.run([sys.executable,str(SCRIPT)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    report=json.loads(result.stdout)
    assert report['network_calls']==0 and report['acceptance']=='NOT_RUN'
    assert report['cases']>=49


def test_wrong_field_and_missing_slot_are_failures():
    errors=evaluator.score({'action':'create','entity':'customer','fields':{'phone':'invented'},'missing':[]},
        {'actions':['update'],'entity':'profile','fields':{'phone':'0899999999'},'missing_include':['last_name']})
    assert len(errors)==4


def test_unknown_assertion_cannot_silently_pass(tmp_path):
    p=tmp_path/'cases.json'
    p.write_text(json.dumps([{'id':'x','message':'hello','role':'sales','permissions':[],
                            'expect':{'actions':['read'],'typo':'ignored'}}]))
    with pytest.raises(ValueError,match='expectation'):evaluator.load_cases(p)


@pytest.mark.asyncio
async def test_real_path_with_mock_transport_scores_and_accounts_for_every_repeat(monkeypatch):
    from chann_app.config import settings
    monkeypatch.setattr(settings,'openrouter_api_key','synthetic-test-key')
    monkeypatch.setattr(settings,'openrouter_model','synthetic-test-model')
    requests=[]
    def respond(request):
        body=json.loads(request.content);requests.append(body)
        assert body['reasoning']=={'enabled':False}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'action':'read','entity':'customer'})}}],
                                        'usage':{'prompt_tokens':10,'completion_tokens':5,'cost':.001}})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(respond),**kw))
    cases=[{'id':'test','message':'ดูลูกค้า','role':'sales','permissions':['customer.read'],
            'expect':{'actions':['read'],'entity':'customer'}}]
    report=await evaluator.evaluate(cases,3)
    assert report['summary']['passed']==3
    assert report['summary']['varying_outputs']==[]
    assert len(requests)==report['usage']['http_requests']==3
    assert report['usage']['prompt_tokens']==30
    assert 'synthetic-test-key' not in json.dumps(report)
