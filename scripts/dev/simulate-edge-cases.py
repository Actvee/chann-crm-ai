import asyncio, json, sys
sys.path.insert(0, "application"); sys.path.insert(0, "tests/unit")
import httpx
import test_phase6_chat as T
from chann_app.config import settings
settings.openrouter_api_key = "k"; settings.openrouter_model = "m"
BAD = {"GENERIC_ERROR": ("ขออภัย",), "PERMISSION_LIST": ("คุณสามารถทำสิ่งเหล่านี้ได้","คุณยังไม่มีสิทธิ์"),
       "NOT_A_FEATURE": ("ระบบยังไม่มีฟังก์ชันนี้",), "AI_DOWN": ("ระบบไม่พร้อมใช้งาน",), "NOT_FOUND": ("ไม่พบ",)}
def classify(t):
    for k, ns in BAD.items():
        if any(n in t for n in ns): return k
    return "ok"
def ai(p): return httpx.AsyncClient(transport=T._ai(json.dumps(p, ensure_ascii=False)))
findings = []
async def say(c, oa, msg, expect_ok=True, ai_client=None):
    ctx = T._ctx(oa=oa, primary_role="technician" if oa=="technician" else "sales")
    r = await T.handle_chat_message(c, message=msg, ctx=ctx, ai_client=ai_client)
    kind = classify(r.text); ok = (kind=="ok")==expect_ok
    print(f"{'  ' if ok else '!!'} [{oa:10}] {msg[:40]:42} -> {kind:15} {r.text.splitlines()[0][:66]}")
    if not ok: findings.append((oa,msg,kind,r.text.splitlines()[0][:66]))
    return r

async def main():
    print("=== EDGE CASES: sales ===")
    c = T.FakeDataClient(permission_keys=["customer.create","customer.read","customer.update","deal.create","deal.read","deal.update","quote.create","quote.read","quote.update","product.manage","followup.create","followup.read","note.create","note.read","ticket.read","ticket.create","ticket.assign","service_report.read"])
    c._products=[{"id":"p1","product_id":"FAN16","product_name":"พัดลม 16 นิ้ว","unit_price":"1500.00"}]
    cust = await c.create_customer("L1", {"first_name":"สมชาย","last_name":"ใจดี","phone":"0812345678"})
    await say(c,"sales","ข้อมูลลูกค้า 0812345678")            # by phone
    await say(c,"sales","ข้อมูลลูกค้า สมชาย ใจดี")            # full name
    await say(c,"sales","สร้างดีลให้สมชาย")                    # no space
    await say(c,"sales","เพิ่มสินค้าพัดลม 2 ตัว")               # no space
    await say(c,"sales","เพิ่มสินค้า พัดลม 16 นิ้ว 3 ตัว")     # number inside name
    await say(c,"sales","สร้างใบเสนอราคา")
    await say(c,"sales","ส่วนลด 10%")
    await say(c,"sales","ส่วนลด 500")
    await say(c,"sales","ลดราคาพัดลมเหลือ 1200")               # line edit still works
    await say(c,"sales","ยกเลิกใบเสนอราคา Q-2026-0001", expect_ok=True)
    await say(c,"sales","สร้างใบเสนอราคา")                      # second quote, same deal
    await say(c,"sales","ดูบันทึก")                              # no code, context
    await say(c,"sales","บันทึกว่า ลูกค้าขอเลื่อนจ่าย")          # no code, context
    await say(c,"sales","เตือนพรุ่งนี้")                         # no code, context
    await say(c,"sales","ปิดสำเร็จ")                              # no code, context
    await say(c,"sales","สร้างดีลให้สมชาย", expect_ok=True)     # second deal after won
    await say(c,"sales","ลูกค้าคนนี้มีดีลอะไรบ้าง", ai_client=ai({"action":"read","entity":"deal","fields":{"target_name":"สมชาย"},"missing":[]}))
    await say(c,"sales","ดีลของสมชาย")
    await say(c,"sales","ประวัติ สมชาย")
    await say(c,"sales","แก้เบอร์สมชายเป็น 0899999999", ai_client=ai({"action":"update","entity":"customer","fields":{"target_name":"สมชาย","phone":"0899999999"},"missing":[]}))
    await say(c,"sales","ลบลูกค้าสมชาย", expect_ok=False)     # not supported — see how it fails
    await say(c,"sales","ยอดขายเดือนนี้", ai_client=ai({"action":"read","entity":"report","fields":{"period":"month"},"missing":[]}))

    print("\n=== EDGE CASES: technician ===")
    t = T.FakeDataClient(permission_keys=["ticket.read","ticket.update","ticket.close","service_report.create","service_report.read","warranty.read"], role="technician")
    t._tickets=[{"id":"t1","ticket_number":"T-2026-0001","status":"assigned","accept_status":"pending","assigned_to_ref":"member-1","customer_name":"ก"},
                {"id":"t2","ticket_number":"T-2026-0002","status":"assigned","accept_status":"pending","assigned_to_ref":"member-1","customer_name":"ข"}]
    await say(t,"technician","รับงาน")                          # two jobs — must ask
    await say(t,"technician","รับงาน T-2026-0002")
    await say(t,"technician","ถึงแล้ว")                          # two jobs — must ask
    await say(t,"technician","เช็คอิน T-2026-0002")
    await say(t,"technician","รายงานของฉัน")
    await say(t,"technician","งานที่เปิดรับ")                    # rich-menu tile (3 Sep audit)
    await say(t,"technician","วิธีใช้งาน")                       # rich-menu tile → help, not AI
    await say(t,"technician","งานพรุ่งนี้", expect_ok=False)
    await say(t,"technician","ลูกค้าไม่อยู่บ้าน", ai_client=ai({"action":"update","entity":"ticket","fields":{"status":"customer_absent"},"missing":[]}), expect_ok=False)

    print("\n=== EDGE CASES: customer ===")
    u = T.FakeDataClient(permission_keys=["customer.read","ticket.create","ticket.read","warranty.read","warranty.create"])
    await say(u,"customer","แอร์เสีย น้ำหยด เสียงดัง")
    await say(u,"customer","99/1 ถ.สุขุมวิท")
    await say(u,"customer","พรุ่งนี้ 10 โมง")
    await say(u,"customer","งานของฉัน")
    await say(u,"customer","ช่างจะมากี่โมง", expect_ok=True)
    await say(u,"customer","แจ้งซ่อม เครื่องซักผ้าไม่ปั่น")     # second ticket while one open
    await say(u,"customer","ABC123456")                          # bare serial
    await say(u,"customer","ราคาซ่อมเท่าไหร่", expect_ok=True)
    # The customer rich-menu tiles (3 Sep audit): none may open a ticket.
    before = len([r for r in u.recorded if r[0] == "create_ticket"])
    for tile in ("สถานะการซ่อม", "ติดต่อร้าน", "วิธีใช้งาน", "ประกันของฉัน", "แจ้งซ่อม"):
        await say(u,"customer",tile)
    after = len([r for r in u.recorded if r[0] == "create_ticket"])
    if after != before:
        findings.append(("customer", "rich-menu tiles", "OPENED_TICKET", f"{after-before} junk ticket(s)"))
        print("!! rich-menu tiles opened", after - before, "ticket(s)")
    await say(u,"customer","เลื่อนนัด 1/1/2020", expect_ok=True)   # past date → refused in words
    await say(u,"customer","เลื่อนนัด พรุ่งนี้", expect_ok=True)    # no time → 09:00 echoed

    print(f"\n=== {len(findings)} FINDINGS ===")
    for f in findings: print("  ", f)
asyncio.run(main())
