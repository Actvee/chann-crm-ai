#!/usr/bin/env python3
"""Sentences a RULE decides and answers badly — the conversion's shortlist.

measure-road-share.py counts which road a sentence takes; map-rule-roads.py
names the branch. Neither says whether the answer was any GOOD. This plays
every sales corpus utterance through the real router with the model stubbed,
keeps the ones no model call was made for, and prints those whose reply looks
like a failure: asking for something the sentence gave, not finding a record
that exists, or shrugging.

    python3 scripts/dev/find-bad-rule-answers.py

First run, 11 ก.ย. 2569: 29 sentences, and most were one shape — the rule
took the tail of the sentence verbatim as a name or a code, courtesy words
and all ("ไม่พบดีลรหัส D-2026-0001 ครับ" about a deal that was right there).
Cleaning the lookup terms took it to 22, and every one of those was checked
by hand: English names against a Thai-named fixture, notes with genuinely no
record in context, an unseeded warranty, and confirm prompts this heuristic
over-flags.

It over-reports on purpose. A shortlist to read, not a gate.
"""
import asyncio, sys, json, copy, httpx, pathlib
ROOT = pathlib.Path("/home/thanawinmax2/stage-fix/registry")
sys.path[:0]=[str(ROOT/'application'), str(ROOT/'data'), str(ROOT/'tests/unit')]
from test_phase6_chat import FakeDataClient, _ctx, _ai
from chann_app.config import settings
settings.openrouter_api_key="k"; settings.openrouter_model="m"
from chann_app.services import chat as C
from chann_data.permissions import DEFAULT_ROLE_TEMPLATES
import chat_corpus
CUST={"id":"CUST-1","customer_id":"C-2026-0001","first_name":"สมชาย","last_name":"ใจดี","phone":"0812345678","stage":"lead","notes":None}
DEAL={"id":"DEAL-1","deal_id":"D-2026-0001","stage":"proposed","contact_id":"CUST-1","notes":None,
      "products":[{"id":"L1","product_name":"พัดลม","quoted_unit_price":"1200","qty":1}],"amount":"500000.00"}
QUOTE={"id":"QUOTE-1","quote_id":"Q-2026-0001","status":"sent","deal_id":"DEAL-1","contact_id":"CUST-1","items":[],"total":"1000.00"}
TICKET={"id":"t1","ticket_number":"T-2026-0001","status":"assigned","accept_status":"accepted","assigned_to_ref":"member-1",
        "customer_chann_uid":"CHN-S-000001","customer_name":"สมชาย","service_address":"99/1","issue_description":"แอร์ไม่เย็น",
        "scheduled_date":"2026-09-12","scheduled_time":"10:00"}
S=json.dumps({"action":"suggest","entity":"","fields":{},"missing":[]})
# What a bad answer looks like: the system asking for something, not finding
# something, or shrugging. A correct list or a real confirmation is neither.
BAD=("ยังไม่แน่ใจ","ไม่พบ","ระบุรหัส","ระบุชื่อ","กรุณาระบุ","ไม่เข้าใจ","ยังทำรายการนี้ไม่ได้",
     "คุณสามารถทำสิ่งเหล่านี้ได้","ยังไม่มีสิทธิ์","ขออภัย","พิมพ์รหัสด้วย","แก้ของดีลหรือใบเสนอราคาไหน")
class T(httpx.AsyncBaseTransport):
    def __init__(s,i): s.i=i; s.n=0
    async def handle_async_request(s,r):
        s.n+=1; return await s.i.handle_async_request(r)
async def main():
    out=[]
    for case in chat_corpus.SALES:
        msgs=list(case.get("pre") or [])+[case["text"]]
        c=FakeDataClient(role="sales",permission_keys=sorted(DEFAULT_ROLE_TEMPLATES['admin']),
                         customers=[copy.deepcopy(CUST)],deals=[copy.deepcopy(DEAL)],quotes=[copy.deepcopy(QUOTE)])
        c._tickets=[copy.deepcopy(TICKET)]
        t=None
        for m in msgs:
            t=T(_ai(S))
            try:
                r=await C.handle_chat_message(c,ctx=_ctx(primary_role="sales",oa="sales"),message=m,
                                              language="th",ai_client=httpx.AsyncClient(transport=t))
            except Exception as e:
                r=type("X",(),{"text":f"RAISED {type(e).__name__}"})()
        text=(r.text or "").replace("\n"," ")
        if t.n:            # reached the model already — not a conversion candidate
            continue
        if any(b in text for b in BAD):
            out.append({"text":case["text"],"intent":case.get("intent"),"reply":text[:90]})
    print(json.dumps(out,ensure_ascii=False,indent=1))
    print(f"\n{len(out)} sales sentences a rule decided and answered badly", file=sys.stderr)
asyncio.run(main())
