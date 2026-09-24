# วิธีใช้ LINE ทีมขาย / CS

ทุกอย่างที่ทำได้บนแดชบอร์ด พิมพ์ในแชทได้เหมือนกัน ตั้งร้านให้พร้อมก่อน แล้วเดินงานซ่อมตามลำดับ · เมนูมี 2 หน้า แตะ "เพิ่มเติม" บนหัวเมนู: แชทลูกค้า ดีล สินค้า ทีมช่าง ข้อมูลบริษัท สลับภาษา

> รูปแต่ละขั้น: สร้างจาก prompt ในวงเล็บ แล้วใส่ URL ลง `application/chann_app/help_images.json` และ `presentation/lib/help-images.json` (key ตามชื่อ slot) ระบบจะส่งรูปในแชทและโชว์บนหน้าวิธีใช้เอง

## 1. ตั้งร้านให้พร้อม

รหัสร้าน ทีมช่าง ข้อมูลบริษัท และเวลาตอบ (SLA) — ตั้งครั้งเดียวก่อนเริ่มงาน

- ดูรหัสร้าน ให้ลูกค้าใช้ผูก LINE กับร้าน → พิมพ์ "ข้อมูลร้าน"
- ดูเวลาเตือนที่ตั้งไว้ → พิมพ์ "ดู SLA"
- ตั้งเวลาเตือน — เตือนผู้จ่ายงาน/ช่าง/ผู้อนุมัติเมื่อเกินเวลา และแจ้งเจ้าของเมื่อยังไม่ขยับ (ตั้งที่หน้าข้อมูลบริษัทก็ได้) → พิมพ์ "ตั้ง SLA งานไม่มีคนรับ 1 ชม. ช่างไม่ตอบ 30 นาที เลยนัด 15 นาที แจ้งเจ้าของหลัง 1 ชม. อนุมัติค้าง 4 ชม."
- ขอรหัสเชิญให้คนเข้าร่วม — ระบบถามว่าช่างหรือทีมขาย → พิมพ์ "ขอรหัสเชิญ"
- หรือระบุเลย → พิมพ์ "ขอรหัสเชิญช่าง / ขอรหัสเชิญ Sales"
- ลูกค้าไม่ใช้รหัสเชิญ — ได้รหัสร้านให้ลูกค้าพิมพ์ใน LINE ลูกค้า → พิมพ์ "ขอรหัสเชิญลูกค้า"
- สร้างทีมช่าง → พิมพ์ "สร้างทีมช่าง แอร์"
- เพิ่มคนเข้าทีม → พิมพ์ "เพิ่ม สมศักดิ์ เข้าทีม แอร์ เป็นหัวหน้า"
- ข้อมูลบริษัทสำหรับเอกสาร → พิมพ์ "ข้อมูลบริษัท"
- ให้ลูกค้าที่ผูกร้านเข้ารายชื่อทันที (ถามได้ "…ตอนนี้เปิดอยู่ไหม") → พิมพ์ "ตั้งค่ารับลูกค้าใหม่อัตโนมัติ เปิด"

**เอกสารแบบของร้านเอง**
- ให้ AI ร่างแบบให้ก่อน — ยังไม่ใช้จริงจนกว่าจะกด "ใช้เลย" มีปุ่มดูตัวอย่างและไฟล์ Word ไว้แก้เอง → พิมพ์ "ออกแบบใบเสนอราคา / ออกแบบใบรายงานการซ่อม"
- หรือบนแดชบอร์ด > แบบฟอร์มเอกสาร: อัปไฟล์ Word (.docx มีตัวอย่างให้โหลด) → ดูตัวอย่าง → เผยแพร่ → เลือกใช้แบบนี้ (เอกสารที่ออกไปแล้วไม่เปลี่ยน)
- ไม่เลือกหรือเลิกใช้ = กลับไปใช้แบบมาตรฐาน · "เลิกใช้รุ่นนี้" ระบบบอกก่อนว่าเอกสารใหม่จะใช้รุ่นไหนแทน
- ต้องมีสิทธิ์ "ตั้งค่าร้าน"

พิมพ์: `สร้างทีมช่าง แอร์`

[IMAGE: sales-setup — แผนผังร้าน: กล่อง 'ร้าน (รหัส ABCD01)' เชื่อมไป 'ทีมช่าง แอร์ (หัวหน้า สมศักดิ์)' และ 'ลูกค้า' ธีมเขียว]

_EN: Set the shop up — The shop code, technician teams, company details and reply times (SLA) — set once before work starts._

_- Shop code, for customers to link with → type "ข้อมูลร้าน"_
_- See the reply times → type "ดู SLA"_
_- Set the reply times — dispatchers, technicians and approvers are reminded past the limit, the owner when nothing moves (also on the company page) → type "ตั้ง SLA งานไม่มีคนรับ 1 ชม. ช่างไม่ตอบ 30 นาที เลยนัด 15 นาที แจ้งเจ้าของหลัง 1 ชม. อนุมัติค้าง 4 ชม."_
_- An invite code — it asks technician or sales → type "ขอรหัสเชิญ"_
_- Or say which → type "ขอรหัสเชิญช่าง / ขอรหัสเชิญ Sales"_
_- Customers do not use invite codes — this gives the shop code they type in the customer LINE → type "ขอรหัสเชิญลูกค้า"_
_- Create a technician team → type "สร้างทีมช่าง แอร์"_
_- Add someone to a team → type "เพิ่ม สมศักดิ์ เข้าทีม แอร์ เป็นหัวหน้า"_
_- Company details for documents → type "ข้อมูลบริษัท"_
_- Linked customers join the list at once (ask "…is it on?") → type "ตั้งค่ารับลูกค้าใหม่อัตโนมัติ เปิด"_

**Your own document layout**
_- Let the AI draft one — nothing is in use until you press use it; preview and a Word file to edit → type "ออกแบบใบเสนอราคา / ออกแบบใบรายงานการซ่อม"_
_- Or dashboard > document templates: upload a .docx (samples to download) → preview → publish → choose this one (issued documents never change)_
_- No choice or none active = the built-in layout · retire tells you first what will render instead_
_- Needs the shop settings permission_

## 2. บันทึกเครื่องที่ขาย

ลงทะเบียนเครื่องที่ขายไว้กับลูกค้า — ลูกค้าดูประกันเองได้ และงานซ่อมผูกกับเครื่องให้อัตโนมัติ

- ลงทะเบียนเครื่องให้ลูกค้า — ผูกกับเรคอร์ดลูกค้าคนนั้นทันที (ระบบตอบ "ผูกกับลูกค้า สมชาย (C-…) แล้ว") → พิมพ์ "ลงทะเบียนสินค้า SN12345678 แอร์ ให้ลูกค้า สมชาย"
- ไม่มีลูกค้าชื่อนั้น ระบบถามก่อน ไม่ลงทะเบียนเงียบ ๆ · ไม่ระบุลูกค้าก็ลงทะเบียนได้
- ลูกค้าพิมพ์ S/N นี้ใน LINE ลูกค้าเพื่อดูประกันเอง
- ลูกค้าที่ลงทะเบียนเครื่องเอง จะเข้ารายชื่อเป็น "ลูกค้า" ทันที (ไม่ใช่ลูกค้ามุ่งหวัง) และเครื่องผูกกับเรคอร์ดนั้นให้เอง — ร้านได้แจ้งเตือน
- วันที่ซื้อใส่ทีหลังได้ (ยังไม่กำหนดวันหมดประกันจนกว่าจะรู้) → พิมพ์ "วันที่ซื้อ SN12345678 1 ก.ย. 2569"
- หรือใส่ตั้งแต่แรก → พิมพ์ "ลงทะเบียนสินค้า SN12345678 แอร์ ซื้อวันที่ 1 ก.ย. 2569 ประกัน 2 ปี"
- ระยะประกันเริ่มต้นมาจากสินค้าแต่ละตัว (หรือช่องระยะประกันในหน้าสินค้า) → พิมพ์ "สินค้า FAN01 รับประกัน 2 ปี"
- ดูเครื่องทั้งหมด — บอกลูกค้าที่ผูกและว่าผูก LINE แล้วหรือยัง → พิมพ์ "รายการประกัน"
- เปิดงานให้ลูกค้าที่โทรมา — ระบบผูกงานกับเครื่องที่คนนั้นลงทะเบียนไว้ให้เอง มีหลายเครื่องจะมีปุ่มให้เลือก → พิมพ์ "เปิดงานให้ สมชาย แอร์ไม่เย็น"

พิมพ์: `ลงทะเบียนสินค้า SN12345678 แอร์ ให้ลูกค้า สมชาย`

[IMAGE: sales-units — หน้าจอ 'ทะเบียนสินค้า' ธีมเขียว: ปุ่ม 'บันทึกเครื่อง' ที่หัวข้อ ช่องค้นหา ปุ่ม 'นำเข้า CSV' และรายการเครื่องที่บอกลูกค้าที่ผูกและว่าผูก LINE แล้วหรือยัง]

_EN: Record sold units — Register the units you sold against the customer — they can check the warranty themselves, and repairs attach to the unit._

_- Register a unit for a customer — attached to their record at once → type "ลงทะเบียนสินค้า SN12345678 แอร์ ให้ลูกค้า สมชาย"_
_- An unknown name is asked about, never registered silently · a unit can be registered with no customer_
_- The customer types this S/N in the customer LINE to see the warranty_
_- Someone who registers a unit joins the customer list as a customer (not a lead) and the unit is linked to that record — the shop is told_
_- The purchase date can come later (no end date until it is known) → type "วันที่ซื้อ SN12345678 1 ก.ย. 2569"_
_- Or give it at once → type "ลงทะเบียนสินค้า SN12345678 แอร์ ซื้อวันที่ 1 ก.ย. 2569 ประกัน 2 ปี"_
_- The default period comes from the product (or the warranty field on the products page) → type "สินค้า FAN01 รับประกัน 2 ปี"_
_- Every unit, with its customer and whether they are linked on LINE → type "รายการประกัน"_
_- A job you open for a caller is linked to their registered unit, with buttons when they have several → type "เปิดงานให้ สมชาย แอร์ไม่เย็น"_

## 3. งานซ่อม: มอบหมาย

ลูกค้าแจ้งซ่อมแล้วคุณได้ LINE พร้อมชื่อเครื่องและสถานะประกัน — งานรอร้านก่อน ช่างยังไม่เห็นจนกว่าจะมอบหมายหรือเปิดให้รับ

- ดูคิวที่รอมอบหมาย (งานที่ลูกค้าแจ้งจะมีป้าย "ช่างยังไม่เห็น") → พิมพ์ "งานซ่อม"
- ดูงานทั้งหมด → พิมพ์ "รายการงาน"
- มอบหมายให้ทีม — ต้องมีชื่อ เบอร์ ที่อยู่ นัดครบ ระบบบอกถ้าขาด → พิมพ์ "มอบหมาย T-2026-0001 ให้ทีม แอร์"
- ไม่ต้องพิมพ์เลขเต็ม → พิมพ์ "มอบหมาย 0001 ให้ช่าง"
- หรือ reply ข้อความแจ้งซ่อมนั้นแล้วพิมพ์ — ระบบถามว่าช่างคนไหนพร้อมปุ่มเลือก → พิมพ์ "มอบหมายให้ช่าง"
- เปิดให้ช่างทุกคนเห็น ใครรับก่อนได้ (ต้องมีที่อยู่และนัดครบเหมือนมอบหมาย) → พิมพ์ "เปิดให้ช่างรับ T-2026-0001"
- พอลูกค้ากรอกที่อยู่/นัดครบ คุณได้ LINE อีกครั้งพร้อมอาการ

**ลูกค้าขอเลื่อนนัด**
- ยืนยันด้วยคำพูดปกติ — ระบบเลื่อนตามเวลาที่ลูกค้าขอและแจ้งลูกค้าให้ มีหลายงานค้างจะถามว่างานไหน → พิมพ์ "เลื่อนได้ / ยืนยันเป็นวันที่ตามนั้นได้"
- หรือ reply ข้อความแจ้งเตือนนั้นแล้วพิมพ์ "เลื่อนได้"
- กำหนดเวลาเอง → พิมพ์ "เลื่อนนัด T-2026-0001 พรุ่งนี้ 10 โมง"

**จบได้เองไม่ต้องใช้ช่าง**
- ปิดได้เลยไม่ต้องเช็คอิน → พิมพ์ "ปิดงาน T-2026-0001"
- บันทึกสาเหตุและวิธีแก้ด้วยก็ได้ → พิมพ์ "ปิดงาน T-2026-0001 สาเหตุ ฟิวส์ขาด แก้ไข เปลี่ยนฟิวส์"
- หรือทำบนแดชบอร์ด > งานซ่อม

**กฎมอบหมายอัตโนมัติ**
- ดูกฎที่ใช้อยู่ (หน้าข้อมูลบริษัทก็แสดงและแก้ได้) → พิมพ์ "ดูกฎมอบหมาย"
- ปิดกฎ — ระบบถามยืนยันก่อน แล้วงานใหม่ต้องมอบหมายเอง → พิมพ์ "ปิดกฎมอบหมาย"

พิมพ์: `มอบหมาย T-2026-0001 ให้ทีม แอร์`

[IMAGE: sales-dispatch — หน้าจอ 'งานซ่อม' บนแดชบอร์ด การ์ดงาน T-2026-0001 มีช่อง 'มอบหมายให้…' เลือกทีมแอร์ และปุ่มมอบหมายสีเขียว ด้านล่างเป็นงานอื่นในร้านพร้อมป้ายสถานะ]

_EN: Repairs: dispatch — You hear when a customer reports, with the machine and its warranty state — the job waits for the shop; technicians do not see it until it is assigned or opened._

_- The queue waiting to be dispatched → type "งานซ่อม"_
_- Every job → type "รายการงาน"_
_- Assign to a team — name, phone, address and appointment required; it says what is missing → type "มอบหมาย T-2026-0001 ให้ทีม แอร์"_
_- The short number works → type "มอบหมาย 0001 ให้ช่าง"_
_- Or reply to the report and type — it asks which technician, with buttons → type "มอบหมายให้ช่าง"_
_- Open it to every technician; the first to accept takes it (address and appointment required) → type "เปิดให้ช่างรับ T-2026-0001"_
_- When the customer completes the address/appointment you hear again, with the fault_

**A customer asks to move the visit**
_- Confirm in plain words — the visit moves to the time they asked and the customer is told → type "เลื่อนได้ / ยืนยันเป็นวันที่ตามนั้นได้"_
_- Or reply to that notice with "ok to move"_
_- Set your own time → type "เลื่อนนัด T-2026-0001 พรุ่งนี้ 10 โมง"_

**Settled without a technician**
_- Close it, no check-in → type "ปิดงาน T-2026-0001"_
_- Record cause and fix too → type "ปิดงาน T-2026-0001 สาเหตุ ฟิวส์ขาด แก้ไข เปลี่ยนฟิวส์"_
_- Or dashboard > tickets_

**The automatic rule**
_- See the active rule (the company page shows and edits it too) → type "ดูกฎมอบหมาย"_
_- Switch it off — confirmed first; new work is then assigned by hand → type "ปิดกฎมอบหมาย"_

## 4. แชทลูกค้า

ลูกค้าที่กด "คุยกับร้าน" ใน LINE บริการลูกค้า จะขึ้นที่ หน้าจอ > แชทลูกค้า — ตอบที่หน้านั้นเท่านั้น ตอบใน LINE ร้านไม่ถึงลูกค้า

- LINE แจ้งทุกคนแค่ตอนเปิดแชทใหม่พร้อมข้อความแรก ที่เหลืออ่านและตอบบนหน้าแชทลูกค้า · คนแรกที่ตอบเป็นเจ้าของการสนทนา
- ปุ่ม "ข้อมูลลูกค้า" บนหัวแชท เปิดดูดีล งานซ่อม และบันทึกของคนนี้ กดไปที่เรคอร์ดได้เลย
- ส่งรูปได้ — กดปุ่มแนบรูปข้างช่องพิมพ์ เลือกรูป พิมพ์คำอธิบายถ้าต้องการ แล้วกดส่งรูป ลูกค้าเห็นเป็นรูปใน LINE · รูปที่ลูกค้าส่งมาระหว่างคุยขึ้นในแชทนี้เช่นกัน
- ร้านเริ่มคุยเองก็ได้ — ลูกค้าต้องผูก LINE กับร้านแล้ว → พิมพ์ "คุยกับลูกค้า สมชาย"
- หรือจากงาน → พิมพ์ "คุยกับลูกค้า T-2026-0001"
- ใส่ข้อความแรกต่อท้ายได้ → พิมพ์ "คุยกับลูกค้า สมชาย: พรุ่งนี้ช่างไปได้ไหม"
- หรือกดปุ่ม คุยกับลูกค้า บนหน้างานซ่อม/รายชื่อลูกค้า
- ปล่อยเกินเวลาตอบ (ค่าเริ่มต้น 15 นาที) ระบบแจ้งลูกค้าว่าจะติดต่อกลับและพักการสนทนา — ตอบทีหลังได้ ลูกค้าจะได้รับคำเชิญให้เปิดแชทต่อ
- ลูกค้าเงียบ 1 ชั่วโมงปิดให้เอง
- ตั้งเวลาเอง (หรือหน้าข้อมูลบริษัท) → พิมพ์ "ตั้งค่าแชท"

พิมพ์: `หน้าจอ > แชทลูกค้า`

[IMAGE: sales-chats — หน้าจอแดชบอร์ดสีเขียว รายการแชทลูกค้า 2 รายการ รายการแรกมีป้าย 'ลูกค้ารอคำตอบ' ด้านขวาเป็นบทสนทนา มีรูปที่ร้านส่งและรูปที่ลูกค้าส่ง ด้านล่างมีปุ่มแนบรูป ช่องพิมพ์คำตอบ และปุ่ม 'ส่ง']

_EN: Customer chats — A customer who taps "talk to the shop" appears under home > Customer chats — answer there only; a reply in the shop's LINE never reaches them._

_- LINE announces a NEW conversation with the first thing they said; the rest is read and answered on the chats page · the first to answer owns it_
_- The "Customer" button on the thread opens their deals, jobs and notes, each a link to the record_
_- Pictures go too — tap the attach button beside the box, pick one, add a caption if you like, then send; the customer sees it as a picture in LINE · a picture the customer sends while talking shows here as well_
_- The shop can open the conversation — the customer must be linked on LINE → type "คุยกับลูกค้า สมชาย"_
_- Or from a job → type "คุยกับลูกค้า T-2026-0001"_
_- A first line may follow a colon → type "คุยกับลูกค้า สมชาย: พรุ่งนี้ช่างไปได้ไหม"_
_- Or the Chat with customer button on a job or the customer list_
_- Past the reply time (default 15 min) the customer is told you will get back and the chat is paused — answer later and they are invited to reopen it_
_- An hour of customer silence closes it_
_- Set both times (or on the company page) → type "ตั้งค่าแชท"_

## 5. งานซ่อม: ตรวจรายงาน

ช่างปิดงานแล้วคุณได้ LINE — ตรวจรายงาน อนุมัติหรือตีกลับ ผ่านครบแล้วลูกค้าได้แบบประเมินและ PDF อัตโนมัติ

- ดูรายงานที่รอ → พิมพ์ "รายการรออนุมัติ"
- อนุมัติ → พิมพ์ "อนุมัติ SR-2026-0001"
- ตีกลับพร้อมเหตุผล — เหตุผลส่งถึงช่างเท่านั้น ลูกค้าจะรู้ว่าช่างต้องกลับไปดูอีกครั้ง → พิมพ์ "ตีกลับ SR-2026-0001 เหตุผล"
- เจ้าของร้าน (หรือคนที่ตั้งกฎอนุมัติได้) อนุมัติแทนได้ทุกขั้น แม้ขั้นนั้นรอคนอื่นอยู่ — รายงานจึงไม่ค้าง
- ตั้งขั้นตอนการอนุมัติ → พิมพ์ "ตั้งการอนุมัติ"
- ลูกค้ารู้ตั้งแต่ช่างปิดงานว่างานเสร็จและช่างทำอะไรไป — PDF ส่งให้หลังผ่านครบ

พิมพ์: `อนุมัติ SR-2026-0001`

[IMAGE: sales-approve — การ์ดรายงาน SR-2026-0001: ปัญหาที่พบ / สิ่งที่แก้ไข พร้อมปุ่ม 'อนุมัติ' สีเขียว และ 'ตีกลับ' ด้านล่างเป็นคิวที่เหลือพร้อมป้าย รอตรวจ / อนุมัติแล้ว]

_EN: Repairs: review reports — When a technician closes a job you hear — review the report, approve or reject; once every step passes the customer gets the survey and the PDF._

_- Reports waiting → type "รายการรออนุมัติ"_
_- Approve → type "อนุมัติ SR-2026-0001"_
_- Reject with a reason — the reason goes only to the technician; the customer hears the technician is coming back → type "ตีกลับ SR-2026-0001 เหตุผล"_
_- The owner (or anyone who may manage the approval rules) can act on every step, even one waiting on someone else — nothing stays stuck_
_- Change the approval flow → type "ตั้งการอนุมัติ"_
_- The customer already heard at check-out that the job was finished and what was done — the PDF follows once every step passes_

## 6. ลูกค้า ดีล ใบเสนอราคา

รายชื่อลูกค้า ดีล ใบเสนอราคา นัดหมาย และบันทึก — พิมพ์ตามที่พูด ชื่อซ้ำระบบให้เลือก ไม่เดาให้


**ลูกค้า**
- ดูรายชื่อ → พิมพ์ "รายชื่อลูกค้า"
- เพิ่มลูกค้า → พิมพ์ "สร้างลูกค้า สมชาย ใจดี 0812345678"
- เพิ่มหลายคนในข้อความเดียว — วางรายชื่อทีละบรรทัด "ชื่อ นามสกุล เบอร์ อีเมล" คนที่ไม่มีเบอร์ระบบถามทีละคน → พิมพ์ "เพิ่มลูกค้าหลายคน"
- บนหน้ารายชื่อลูกค้า: ปุ่ม "เพิ่มลูกค้าหลายคน" และ "นำเข้า CSV" (มีไฟล์ตัวอย่าง) อยู่ที่หัวรายการ
- ยืนยันหลายคนพร้อมกัน → พิมพ์ "เปลี่ยน สมชาย สมหญิง สมศรี เป็นลูกค้ายืนยัน"
- ลบออกจากรายชื่อ — ระบบถามยืนยันก่อน (เก็บถาวร ไม่ลบทิ้ง) หลายคนก็ได้ "ลบลูกค้า สมชาย กับ สมหญิง" → พิมพ์ "ลบ Lead สมชาย"
- บนหน้าจอ: ปุ่ม "เลือกหลายรายการ" ที่หัวรายการ แล้วติ๊กแถวที่ต้องการ แถบด้านล่างมี ยืนยันเป็นลูกค้า / ลบออกจากรายชื่อ
- เพิ่มซ้ำเบอร์/อีเมลเดิม ระบบบอกว่าเป็นใครและให้เลือก ใช้เดิม / อัปเดต / ยกเลิก
- เบอร์โทรต้องเป็นตัวเลข ระบบไม่บันทึกเบอร์ที่มีตัวอักษรและบอกเหตุผล
- ดูสิ่งที่ทำได้ทีละหมวด → พิมพ์ "ทำอะไรกับ Lead ได้บ้าง"

**ดีล**
- เปิดดีล → พิมพ์ "สร้างดีลให้ สมชาย"
- ใส่มูลค่าและวันคาดว่าจะปิดในประโยคเดียว → พิมพ์ "สร้างดีลให้ อาทิตย์ มูลค่า 250,000 ปิดสิ้นเดือนนี้"
- หลังเพิ่มลูกค้า บอกว่าสนใจอะไร ระบบเสนอเปิดดีลพร้อมสินค้าให้ → พิมพ์ "ลูกค้าสนใจอยากได้พัดลม 1 ตัว"
- ดีลแพ้ — ระบบถามเหตุผล พิมพ์สั้น ๆ หรือ "ข้าม" (เหตุผลแสดงใน "ดีลที่แพ้") → พิมพ์ "ดีลนี้แพ้"
- เปลี่ยนสถานะหลายดีลพร้อมกัน → พิมพ์ "ย้ายดีล D-2026-0001 กับ D-2026-0002 ไปชนะ"
- เก็บถาวรหลายดีล — ถามยืนยันครั้งเดียว → พิมพ์ "ลบดีล D-2026-0001 D-2026-0002"
- ดูดีลที่เพิ่งทำ → พิมพ์ "ดีลล่าสุด / สินค้าในดีล"

**สินค้าในดีลและใบเสนอราคา**
- เพิ่มสินค้าตามที่พูด — ไม่บอกราคา ระบบดูจากรายการสินค้า ไม่มีก็ถามราคาแล้วจำรายการไว้ ตอบแค่ "1500" ได้เลย → พิมพ์ "เพิ่มสินค้า พัดลม 2 ตัว ราคา 1500"
- หลายอย่างในประโยคเดียว (ใช้ได้ทั้งดีล ใบเสนอราคา และเพิ่มสินค้าเข้าคลัง) → พิมพ์ "เพิ่มพัดลม 2 ตัว และ แอร์ 1 ตัว"
- บวกเพิ่มจากเดิม → พิมพ์ "เพิ่มพัดลมอีก 3 ตัว"
- ตั้งจำนวนใหม่ / ลดจำนวน / เอาออก / ปรับราคา — ทุกครั้งระบบบอกยอดรวมดีล → พิมพ์ "แก้พัดลมเป็น 3 ตัว · ลดพัดลม 1 ตัว · ลบสินค้าพัดลมออก · ปรับราคาพัดลมเป็น 2000"
- ออกเอกสารใบเสนอราคา → พิมพ์ "ออกเอกสาร Q-2026-0001"
- ค้นรายการสินค้า → พิมพ์ "ค้นหาสินค้า พัดลม"

**ใบแจ้งหนี้และใบเสร็จ**
- ออกใบแจ้งหนี้จากใบเสนอราคาที่ส่งแล้ว (หรือจากดีล) — ได้เลขที่ INV ยอดรวม กำหนดชำระ 30 วัน และลิงก์ PDF ในคำตอบเดียว → พิมพ์ "ออกใบแจ้งหนี้ Q-2026-0001 · สร้างใบแจ้งหนี้ให้ดีล D-2026-0001"
- บอกแค่ชื่อลูกค้าก็ได้ — ระบบออกจากดีลของเขา (หลายดีลมีปุ่มให้เลือก ไม่มีดีลบอกให้สร้างก่อน เพราะใบแจ้งหนี้ผูกกับดีลเสมอ) · บนจอ: ปุ่ม "ออกใบแจ้งหนี้" ปุ่มเดียวกันทุกหน้า — หน้าใบแจ้งหนี้ (ลูกค้า → ดีล → ใบเสนอราคา) หน้าใบเสนอราคา ดีล และลูกค้า → พิมพ์ "ออกใบแจ้งหนี้ให้ สมชาย"
- ดูใบแจ้งหนี้ / รายการ / ยอดค้างชำระ → พิมพ์ "ใบแจ้งหนี้ INV-2026-0001 · รายการใบแจ้งหนี้ · ยอดค้างชำระ"
- บันทึกรับชำระ: จำนวนเงินกับช่องทาง (เงินสด/โอน/พร้อมเพย์/บัตร) มัดจำก็ได้ ไม่บอกจำนวนระบบถาม ตอบ "5000" หรือ "ครบ" ได้เลย → พิมพ์ "รับชำระ INV-2026-0001 5000 โอน · มัดจำ INV-2026-0001 2000 เงินสด · รับชำระ INV-2026-0001 ครบ"
- ออกใบเสร็จเมื่อชำระครบ — ระบบส่งลิงก์ให้ลูกค้าทาง LINE ให้ด้วย → พิมพ์ "ออกใบเสร็จ INV-2026-0001"
- ยกเลิกใบแจ้งหนี้ (ถามยืนยันก่อน ยกเลิกได้เฉพาะใบที่ยังไม่มีการรับชำระ) → พิมพ์ "ยกเลิกใบแจ้งหนี้ INV-2026-0001"
- แก้ใบแจ้งหนี้ได้จนกว่าจะมีการรับชำระ — จำนวน ราคา กำหนดชำระ ยอดรวมและ VAT คิดใหม่ให้ · แก้จำนวน/ราคาระบบโชว์ยอดใหม่ให้กด "ยืนยันแก้" ก่อน · ออกเอกสารไปแล้วต้องออกใหม่ก่อนส่งลูกค้า → พิมพ์ "เปลี่ยนจำนวนแอร์ในใบแจ้งหนี้ INV-2026-0001 เป็น 3 ตัว · ใบแจ้งหนี้ INV-2026-0001 เปลี่ยนกำหนดชำระเป็นสิ้นเดือน"
- เพิ่ม/ตัดรายการทำในแผงใบแจ้งหนี้บนจอ · รับชำระครบแล้วดีลปิดสำเร็จ รายการเป็นตามใบนี้ มูลค่าดีลเป็นยอดหลังส่วนลดก่อน VAT → พิมพ์ "รับชำระ INV-2026-0001 ครบ"
- ส่งใบเสนอราคา/ใบแจ้งหนี้/ใบเสร็จให้ลูกค้าทางไลน์ (ลูกค้าต้องผูกไลน์แล้ว ไม่งั้นระบบบอกเหตุผล) · บนจอ: ปุ่ม "ส่งให้ลูกค้าทางไลน์" ในหน้าใบเสนอราคาและแผงใบแจ้งหนี้ → พิมพ์ "ส่งใบแจ้งหนี้ INV-2026-0001 ให้ลูกค้า · ส่งใบเสนอราคา Q-2026-0001 ให้ลูกค้า · ส่งให้ลูกค้า (ต่อจากออกเอกสาร)"

**นัดหมายและบันทึก**
- ดูสิ่งที่ต้องทำวันนี้ → พิมพ์ "งานวันนี้"
- ตั้งเตือน → พิมพ์ "เตือน D-2026-0001 พรุ่งนี้"
- แก้นัดเดิม (ยังเป็นนัดเดิม ไม่ได้ตั้งใหม่) → พิมพ์ "เลื่อนนัด C-2026-0001 เป็นศุกร์ บ่าย 2"
- ยกเลิกแต่เก็บประวัติไว้ → พิมพ์ "ยกเลิกนัด สมชาย"
- บนหน้าลูกค้า/ดีล/ใบเสนอราคา มีปุ่ม แก้ไขนัด และ ลบนัด (ลบมีกล่องยืนยัน)
- กำลังเพิ่มลูกค้าอยู่แล้วพิมพ์คำสั่งอื่น ระบบถามก่อนว่าจะเปลี่ยนไปทำสิ่งนั้นหรือทำต่อ (ถ้าเปลี่ยน ระบบยังจำลูกค้าที่ค้างไว้ให้เปิดดีลได้)

**โอนให้พนักงานคนอื่น**
- โอนลูกค้าหรือดีลให้เพื่อนร่วมงานดูแล — คนที่รับจะได้รับแจ้งใน LINE (ต้องมีสิทธิ์โอนงาน · ชื่อซ้ำจะมีปุ่มให้เลือก) → พิมพ์ "โอนลูกค้า สมชาย ให้ สมหญิง · โอนดีล D-2026-0001 ให้ สมหญิง"
- ทั้งหมดของคนหนึ่งไปอีกคน — ระบบแจงรายชื่อแล้วถามยืนยัน · บนหน้าจอ: ช่อง "เจ้าของ" ในหน้าลูกค้า/ดีล หรือเลือกหลายรายการแล้ว "เปลี่ยนเจ้าของ" → พิมพ์ "โอนลูกค้าทั้งหมดของ สมชาย ให้ สมหญิง"

พิมพ์: `งานวันนี้`

[IMAGE: sales-crm — แดชบอร์ดขายธีมเขียว: เมนูด้านซ้ายจัดกลุ่มเป็น งานขาย (แชทลูกค้า ลูกค้า ดีล ใบเสนอราคา ใบแจ้งหนี้ นัดหมาย สินค้า) / งานบริการ / เอกสารและรายงาน / จัดการร้าน ด้านขวาเป็นมูลค่าไปป์ไลน์ ทางลัด 'เริ่มงานต่อ' และดีลที่ต้องตามวันนี้]

_EN: Customers, deals, quotes — Customers, deals, quotes, appointments and notes — type it as you would say it; a duplicate name gets a choice, never a guess._


**Customers**
_- The list → type "รายชื่อลูกค้า"_
_- Add one → type "สร้างลูกค้า สมชาย ใจดี 0812345678"_
_- Several at once — one person per line "first last phone email"; a row without a phone is asked for one → type "เพิ่มลูกค้าหลายคน"_
_- On the customers page the "Add several" and "Import CSV" buttons sit at the head of the list (sample file provided)_
_- Confirm several at once → type "เปลี่ยน สมชาย สมหญิง สมศรี เป็นลูกค้ายืนยัน"_
_- Remove — asks first (archived, not erased); several works too → type "ลบ Lead สมชาย"_
_- On the dashboard: "Select several" at the head of the list, tick the rows, and the bar at the bottom offers confirm / remove_
_- A duplicate phone/email is named, with use / update / cancel_
_- Phone numbers are digits; one with letters is refused and the reason said_
_- What can be done, by area → type "ทำอะไรกับ Lead ได้บ้าง"_

**Deals**
_- Open a deal → type "สร้างดีลให้ สมชาย"_
_- Value and expected close in one sentence → type "สร้างดีลให้ อาทิตย์ มูลค่า 250,000 ปิดสิ้นเดือนนี้"_
_- Right after adding a customer, say what they want and a deal with that line is offered → type "ลูกค้าสนใจอยากได้พัดลม 1 ตัว"_
_- A lost deal — the reason is asked; type it or "skip" → type "ดีลนี้แพ้"_
_- Move several deals at once → type "ย้ายดีล D-2026-0001 กับ D-2026-0002 ไปชนะ"_
_- Archive several — one confirmation → type "ลบดีล D-2026-0001 D-2026-0002"_
_- The deal in play → type "ดีลล่าสุด / สินค้าในดีล"_

**Lines on a deal or quote**
_- Add a line as you would say it — no price: the catalogue is checked, else the price is asked and "1500" is enough → type "เพิ่มสินค้า พัดลม 2 ตัว ราคา 1500"_
_- Several in one sentence (a deal, a quote, or the catalogue) → type "เพิ่มพัดลม 2 ตัว และ แอร์ 1 ตัว"_
_- Add to the quantity → type "เพิ่มพัดลมอีก 3 ตัว"_
_- Set / reduce / remove / reprice — every reply states the deal total → type "แก้พัดลมเป็น 3 ตัว · ลดพัดลม 1 ตัว · ลบสินค้าพัดลมออก · ปรับราคาพัดลมเป็น 2000"_
_- Issue the quotation → type "ออกเอกสาร Q-2026-0001"_
_- Search the catalogue → type "ค้นหาสินค้า พัดลม"_

**Invoices and receipts**
_- Invoice a sent quote (or a deal) — the INV number, total, 30-day due date and PDF link come back in one reply → type "ออกใบแจ้งหนี้ Q-2026-0001 · สร้างใบแจ้งหนี้ให้ดีล D-2026-0001"_
_- The customer's name is enough — the bill comes from their deal (several: buttons to choose; none: create one first, an invoice always belongs to a deal) · on screen: the same "ออกใบแจ้งหนี้" button on every page — invoices (customer → deal → quote), quote, deal and customer → type "ออกใบแจ้งหนี้ให้ สมชาย"_
_- One invoice, the list, what is owed → type "ใบแจ้งหนี้ INV-2026-0001 · รายการใบแจ้งหนี้ · ยอดค้างชำระ"_
_- Record a payment: amount and method (cash/transfer/PromptPay/card), a deposit is fine; with no amount it asks — answer "5000" or "ครบ" → type "รับชำระ INV-2026-0001 5000 โอน · มัดจำ INV-2026-0001 2000 เงินสด · รับชำระ INV-2026-0001 ครบ"_
_- Issue the receipt once paid in full — the customer gets the link on LINE → type "ออกใบเสร็จ INV-2026-0001"_
_- Void an invoice (asks first; only one with no payments) → type "ยกเลิกใบแจ้งหนี้ INV-2026-0001"_
_- Correct an invoice until paid — quantity, price, due date; totals and VAT recalculated · a quantity/price change shows the new total and waits for "ยืนยันแก้" · re-issue an issued one before sending → type "เปลี่ยนจำนวนแอร์ในใบแจ้งหนี้ INV-2026-0001 เป็น 3 ตัว · ใบแจ้งหนี้ INV-2026-0001 เปลี่ยนกำหนดชำระเป็นสิ้นเดือน"_
_- Add or cut lines on the invoice sheet on screen · once paid in full the deal is won with this invoice's lines, valued after discount and before VAT → type "รับชำระ INV-2026-0001 ครบ"_
_- Send a quote, invoice or receipt to the customer on LINE (they must be linked; otherwise the reason is said) · on screen: the "Send to customer on LINE" button on the quote page and the invoice sheet → type "ส่งใบแจ้งหนี้ INV-2026-0001 ให้ลูกค้า · ส่งใบเสนอราคา Q-2026-0001 ให้ลูกค้า · ส่งให้ลูกค้า (ต่อจากออกเอกสาร)"_

**Appointments and notes**
_- Today's list → type "งานวันนี้"_
_- Set a reminder → type "เตือน D-2026-0001 พรุ่งนี้"_
_- Move the appointment you already have → type "เลื่อนนัด C-2026-0001 เป็นศุกร์ บ่าย 2"_
_- Cancel, history kept → type "ยกเลิกนัด สมชาย"_
_- The customer, deal and quote pages have Edit appointment and Delete appointment (delete asks first)_
_- Typing another command while a customer is half-added asks whether to switch or continue (the half-added customer is still offered for the deal)_

**Handing over to a colleague**
_- Hand a customer or a deal to a colleague; they are told on LINE (needs the reassign permission; a duplicate name gets buttons) → type "โอนลูกค้า สมชาย ให้ สมหญิง · โอนดีล D-2026-0001 ให้ สมหญิง"_
_- Everyone one person holds, to another — listed, then confirmed · on screen: the "Owner" field on a customer/deal, or select rows and "Change owner" → type "โอนลูกค้าทั้งหมดของ สมชาย ให้ สมหญิง"_

## 7. ถามรายงานด้วย AI

ห้ารายงานที่ใช้บ่อยกดได้เลย ระบบคำนวณเอง ไม่ใช้เครดิต · อยากรู้อย่างอื่นก็พิมพ์เป็นภาษาคน


**ห้ารายงานที่ถูกเสมอ**
- มูลค่าดีลทั้งหมด — ทุกดีลที่ยังไม่ถูกลบ แยกตามขั้น → พิมพ์ "ยอดมูลค่าดีลทั้งหมด"
- ยอดปิดสำเร็จเดือนนี้ เทียบเดือนที่แล้ว (ตามวันที่ปิดจริง) → พิมพ์ "เดือนนี้ปิดได้เท่าไหร่"
- งานซ่อมค้างแยกตามช่าง — เปิดอยู่ มอบหมายแล้ว กำลังทำ → พิมพ์ "งานซ่อมค้างแยกตามช่าง"
- ยอดค้างชำระ แยกเลยกำหนดกับยังไม่ถึงกำหนด → พิมพ์ "ยอดค้างชำระ"
- คะแนนความพึงพอใจเฉลี่ย เฉพาะใบที่ลูกค้าตอบแล้ว → พิมพ์ "คะแนนความพึงพอใจเฉลี่ย"
- ห้าอันนี้ไม่ใช้เครดิต AI และตอบเหมือนกันทุกครั้ง · กดปุ่มใต้คำตอบเพื่อดูอันอื่นหรือ "ดูเป็นรูป"

**ถามอย่างอื่น**
- ถามเป็นประโยค → พิมพ์ "ยอดดีลปิดสำเร็จ 3 เดือนล่าสุด / ลูกค้าใหม่เดือนนี้แยกตามผู้ดูแล"
- คำถามอื่นที่ AI ตอบ หรือรูปที่ AI ออกแบบ ใช้เครดิตรายงาน AI ของร้านครั้งละ 1 (ค่าเริ่มต้น 30 ครั้งต่อเดือน) · ใช้ครบแล้วยังได้คำตอบเป็นตัวเลขตามปกติ แค่ไม่มีรูป · ถามตรงกับห้ารายงานข้างบนฟรีเสมอ ทั้งในไลน์และบนแดชบอร์ด
- ระบบถามกลับเมื่อคำถามกว้าง (เช่น "เทียบตามเจ้าของ หรือ แยกตามช่วงเวลา") ตอบสั้น ๆ ได้เลย ระบบจำคำถามเดิมไว้

**กราฟ**
- ห้ากราฟสำเร็จรูป ใช้ได้ไม่จำกัด (หรือกดปุ่ม "ดูเป็นกราฟ" ใต้คำตอบยอดขาย) → พิมพ์ "ขอกราฟยอดขาย · กราฟยอดขายรายเดือน · กราฟสินค้าขายดี · กราฟยอดขายรายคน · กราฟดีลแต่ละสถานะ"
- กราฟแบบสั่งเอง — ระบบส่งรูปมาในแชทพร้อมสรุปสั้น ๆ → พิมพ์ "สร้างรายงานด้วย AI: ยอดขายแยกตามช่าง 3 เดือน"
- รายงานที่ออกมาเป็นตัวเลขเดียวก็ได้รูป เป็นการ์ดตัวเลขใหญ่
- ใช้ครบแล้ว ตัวเลข ตาราง และไฟล์ยังโหลดได้ตามปกติ หายแค่รูป

**บนหน้าจอ**
- เมนู "รายงาน AI" มีการ์ดห้ารายงานอยู่บนสุด (รายการยาวกด "ดูทั้งหมด") ใต้การ์ดเป็นกล่องถาม คำตอบเป็นตารางพร้อมกราฟแท่งและปุ่มดาวน์โหลด CSV/PDF
- ใต้ตารางมีปุ่ม "ปรับรายงานนี้" เปลี่ยนช่วงเวลา/การแยกกลุ่ม/ตัวเลข แล้วกด "ดูใหม่ตามที่ปรับ" ไม่ต้องพิมพ์ใหม่
- ต้องมีสิทธิ์ "ดูรายงาน" (Sale มีตั้งแต่ต้น CS ต้องให้เจ้าของเปิด) กราฟยอดขายตามสถานะดีลใช้สิทธิ์ "ดูดีล"
- AI ไม่แตะฐานข้อมูลเอง — มันแปลคำถามเป็นรายการที่ระบบอนุญาต แล้วนับจากข้อมูลของร้านคุณเท่านั้น
- คะแนนที่ลูกค้าให้หลังงานซ่อม — เฉลี่ย จำนวนที่ตอบ แยกตามช่าง (เมนู "ความพึงพอใจ" บนหน้าจอ) → พิมพ์ "คะแนนความพึงพอใจ · ความพึงพอใจของช่าง สมศักดิ์"

พิมพ์: `ยอดมูลค่าดีลทั้งหมด`

[IMAGE: sales-ai-report — หน้าแดชบอร์ด 'รายงาน AI' ธีมเขียว: หัวข้อ 'รายงานที่ใช้บ่อย' กับการ์ดห้าใบเรียงสามคอลัมน์ — มูลค่าดีลทั้งหมด (สามแถวแรกแยกตามขั้น กับปุ่ม 'ดูทั้งหมด (4)'), ยอดปิดสำเร็จเดือนนี้ (เทียบเดือนที่แล้ว), งานซ่อมค้างแยกตามช่าง (กางครบทุกคน มีแถว 'ยังไม่มอบหมาย' และปุ่ม 'ย่อ'), ยอดค้างชำระ (เลยกำหนด/ยังไม่ถึงกำหนด), คะแนนความพึงพอใจเฉลี่ย — แต่ละใบมีชื่อ คำอธิบายสั้น ตัวเลขใหญ่ชิดขวา แถวแยกย่อย และหมายเหตุตัวเล็ก ใต้การ์ดเป็นกล่อง 'อยากดูรายงานอะไร' กับปุ่มเขียว 'สร้างรายงาน' และตัวอย่างคำถาม]

_EN: Ask for a report — Five everyday reports are one tap away and cost nothing; type anything else in plain words._


**The five fixed reports**
_- Pipeline value — every live deal, by stage → type "ยอดมูลค่าดีลทั้งหมด"_
_- Won this month against last month, by real close date → type "เดือนนี้ปิดได้เท่าไหร่"_
_- Open jobs by technician — open, assigned, in progress → type "งานซ่อมค้างแยกตามช่าง"_
_- Outstanding invoices, overdue split out → type "ยอดค้างชำระ"_
_- Average satisfaction, answered forms only → type "คะแนนความพึงพอใจเฉลี่ย"_
_- These five cost no AI credit and answer the same every time; the buttons under the answer reach the others, or a picture_

**Anything else**
_- Ask in a sentence → type "ยอดดีลปิดสำเร็จ 3 เดือนล่าสุด / ลูกค้าใหม่เดือนนี้แยกตามผู้ดูแล"_
_- Other questions the AI answers, or a picture it designs, use one of the shop's monthly AI report credits (30 by default); when they run out the answer still comes in numbers, just without a picture. A question that is one of the five above is always free, in LINE and on the dashboard_
_- A broad question is asked back ("by owner, or by period?") — answer in a word; the original request is remembered_

**Charts**
_- Five ready-made charts, unlimited (or the "View as chart" button under a sales summary) → type "ขอกราฟยอดขาย · กราฟยอดขายรายเดือน · กราฟสินค้าขายดี · กราฟยอดขายรายคน · กราฟดีลแต่ละสถานะ"_
_- A made-to-order chart — the picture comes back in the chat with a one-line summary → type "สร้างรายงานด้วย AI: ยอดขายแยกตามช่าง 3 เดือน"_
_- A single-number report gets a picture too, as a big-number card_
_- Past the allowance the numbers, table and files still come; only the picture does not_

**On the dashboard**
_- "AI reports" shows the five as cards at the top (a long list opens with "View all"); the question box sits beneath, and answers come as a table with bars and CSV/PDF downloads_
_- "Adjust this report" under the table changes the period/grouping/measure without retyping_
_- Needs the "View reports" permission (Sales has it; the owner grants it to CS); the pipeline chart uses "View deals"_
_- The AI never touches the database — it turns the question into an allowed query, counted over your shop's data only_
_- What customers scored after a repair — average, answered, per technician (the "Satisfaction" page on screen) → type "คะแนนความพึงพอใจ · ความพึงพอใจของช่าง สมศักดิ์"_

## 8. ติดขัด

ไม่รู้จะพิมพ์อะไร หรือคุยกันจนงง — มีทางออกสามอย่าง

- ดูตัวอย่างที่คุณมีสิทธิ์ → พิมพ์ "วิธีใช้"
- ดูสิทธิ์ทั้งหมด → พิมพ์ "ทำอะไรได้บ้าง"
- ล้างเฉพาะบทสนทนา ข้อมูลไม่หาย → พิมพ์ "เริ่มใหม่"
- สิทธิ์ขอได้จากเจ้าของร้าน (แดชบอร์ด > จัดการสิทธิ์และบทบาท)
- สลับภาษา → พิมพ์ "เปลี่ยนภาษาเป็นอังกฤษ"

พิมพ์: `วิธีใช้`

[IMAGE: sales-help — หน้าจอ 'จัดการสิทธิ์และบทบาท' ธีมเขียว: รายละเอียดบทบาท ขาย เป็นหมวดสิทธิ์ที่พับไว้พร้อมตัวเลข 5/6 4/4 หมวดหนึ่งกางอยู่เห็นรายการสิทธิ์ ด้านล่างเป็นการ์ด 'ติดขัด?']

_EN: Stuck — Not sure what to type, or talking in circles — three ways out._

_- Examples you may use → type "วิธีใช้"_
_- Everything you may do → type "ทำอะไรได้บ้าง"_
_- Clears the conversation only, never your data → type "เริ่มใหม่"_
_- Ask the owner for permissions (dashboard > roles)_
_- Switch language → type "เปลี่ยนภาษาเป็นอังกฤษ"_

## 9. สมาชิกในร้าน

หน้าจอ > สมาชิกในร้าน (ข้างบทบาท): ใครผูกกับบริษัทใน LINE ไหน — คนเดียวกันอยู่ได้ทั้ง LINE ทีมขาย/CS และ LINE ช่าง คนละบทบาท


**บนหน้าจอ**
- เปลี่ยนบทบาท · นำออก / กลับมาใช้งาน (นำช่างออก = ถอดจากทีมและคืนงานที่ค้างเข้าคิว)
- หน้าจอ > บทบาท: แต่ละบทบาทเป็นแถวสั้น ๆ บอกจำนวนสิทธิ์และกลุ่ม แตะ "รายละเอียด" ถึงเห็นรายการสิทธิ์ · สร้าง/แก้ในแผงเดียว กางทีละกลุ่ม เลือกทั้งกลุ่มได้
- รีเซ็ตการลงทะเบียน เมื่อแชทของคนนั้นค้าง
- เจ้าของร้านนำออกไม่ได้ ต้องโอนความเป็นเจ้าของก่อน

**เพิ่มคน**
- ขอรหัสเชิญแล้วเลือกว่าช่างหรือทีมขาย — รหัสช่างพิมพ์ใน LINE ช่าง รหัสทีมขาย/CS พิมพ์ใน LINE ฝ่ายขาย → พิมพ์ "ขอรหัสเชิญ"
- ดูรหัสที่ออกไปแล้ว → พิมพ์ "ดูรหัสเชิญ"
- รหัสหลุด ยกเลิกได้ (หรือปุ่มบนหน้าสมาชิก) → พิมพ์ "ยกเลิกรหัสเชิญ <รหัส>"
- แต่ละ LINE ลงทะเบียนแยกกัน ใช้ร่วมกันเฉพาะข้อมูลส่วนตัว

**จากแชท**
- เปลี่ยนบทบาท → พิมพ์ "เปลี่ยนบทบาทสมชายเป็นแอดมิน"
- เอาคนออก / รับกลับ → พิมพ์ "เอาสมศักดิ์ออกจากร้าน · ให้สมศักดิ์กลับมาใช้งานได้"
- ดูบทบาททั้งหมด → พิมพ์ "มีบทบาทอะไรบ้าง"
- เพิ่มสิทธิ์ให้บทบาท (เพิ่ม ไม่ทับของเดิม) → พิมพ์ "ให้บทบาท cs ดูใบเสนอราคาได้ด้วย"

**ใครทำอะไรไปบ้าง**
- ดูประวัติ หรือถามตรง ๆ "ใครลบลูกค้ารายนี้" — ทั้งหมดอยู่ที่ หน้าจอ > ประวัติการใช้งาน (ต้องมีสิทธิ์ "ดูประวัติการใช้งาน") → พิมพ์ "ประวัติการใช้งาน"

พิมพ์: `หน้าจอ > สมาชิกในร้าน`

[IMAGE: sales-members — หน้าจอแดชบอร์ดสีเขียว 'สมาชิกในร้าน' ตารางชื่อ / LINE (ทีมขาย·ช่าง) / บทบาท / สถานะ ปุ่ม เปลี่ยนบทบาท นำออก รีเซ็ต แถวเจ้าของมีป้าย 'เจ้าของ']

_EN: Members — Home > Members (next to roles): who is linked to the company on which LINE — one person can be on both LINEs with different roles._


**On the dashboard**
_- Change a role · remove / reactivate (removing a technician takes them off teams and returns their open jobs to the queue)_
_- Home > Roles: each role is one short row with its permission count and groups; "Details" shows the list · create/edit in one sheet, one collapsed group at a time, whole-group select_
_- Reset onboarding when someone's chat is stuck_
_- The owner cannot be removed; transfer ownership first_

**Adding people**
_- An invite code, technician or sales — typed in the matching LINE → type "ขอรหัสเชิญ"_
_- Codes still valid → type "ดูรหัสเชิญ"_
_- Cancel one that leaked (or the button on the members page) → type "ยกเลิกรหัสเชิญ <รหัส>"_
_- Each LINE is a separate registration; only personal details are shared_

**From chat**
_- Change a role → type "เปลี่ยนบทบาทสมชายเป็นแอดมิน"_
_- Remove / bring back → type "เอาสมศักดิ์ออกจากร้าน · ให้สมศักดิ์กลับมาใช้งานได้"_
_- Every role → type "มีบทบาทอะไรบ้าง"_
_- Add a permission to a role (added, never substituted) → type "ให้บทบาท cs ดูใบเสนอราคาได้ด้วย"_

**Who did what**
_- The log, or ask directly ("who deleted this customer") — all of it under Home > Activity, behind the "view audit log" permission → type "ประวัติการใช้งาน"_

## 10. เชื่อมต่อระบบภายนอก (API)

ให้โปรแกรมบัญชี ERP หรือระบบอื่นอ่านและเขียนข้อมูลร้านได้ผ่าน API — เจ้าของร้านสร้าง key แล้วส่งให้ผู้พัฒนาระบบนั้น key ทำงานในนามร้าน

- แดชบอร์ด > จัดการร้าน > API > "สร้าง key" ตั้งชื่อตามระบบที่จะใช้ — key แสดงครั้งเดียว คัดลอกเก็บทันที
- ส่ง key และลิงก์เอกสาร API (ในหน้าเดียวกัน) ให้ผู้พัฒนาระบบภายนอก
- ดูว่ามี key อะไรบ้างและใช้ล่าสุดเมื่อไหร่ → พิมพ์ "รายการ API key"
- เพิกถอนเมื่อเลิกใช้หรือ key หลุด — ระบบถามยืนยันก่อน ระบบภายนอกจะเรียกไม่ได้ทันที → พิมพ์ "เพิกถอน API key ระบบบัญชี"
- สร้าง key ทำได้บนหน้าจอเท่านั้น (ในแชทจะได้ปุ่มเปิดหน้า) — key ไม่ควรอยู่ในแชท → พิมพ์ "สร้าง API key"
- เฉพาะเจ้าของร้าน · จำกัด 600 คำขอ/นาที/key · ร้านที่ถูกระงับ key อ่านได้แต่เขียนไม่ได้

พิมพ์: `รายการ API key`

[IMAGE: sales-api — หน้าจอ 'API สำหรับระบบภายนอก' ธีมเขียว: รายการ key สองแถว (ชื่อ · chann_live_ab12… · ใช้ล่าสุด) ปุ่ม 'สร้าง key' และแผงที่แสดง key เต็มครั้งเดียวพร้อมปุ่มคัดลอกและประโยค 'จะไม่แสดงอีก']

_EN: Connect an outside system (API) — Let an accounting program, an ERP or another system read and write the shop's data through the API — the owner makes a key and hands it to that system's developer; the key acts as the shop._

_- Dashboard > Shop > API > "Create key", named after the system — shown once, copy it at once_
_- Give the key and the API docs link (same page) to the outside developer_
_- See which keys exist and when each was last used → type "รายการ API key"_
_- Revoke when no longer used or leaked — confirmed first; the outside system stops at once → type "เพิกถอน API key ระบบบัญชี"_
_- Keys are made on the screen only (chat hands you the button) — a key should never sit in a chat → type "สร้าง API key"_
_- Owner only · 600 requests/min/key · a suspended shop's key reads but cannot write_
