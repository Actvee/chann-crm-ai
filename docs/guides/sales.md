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
- วันที่ซื้อใส่ทีหลังได้ (ยังไม่กำหนดวันหมดประกันจนกว่าจะรู้) → พิมพ์ "วันที่ซื้อ SN12345678 1 ก.ย. 2569"
- หรือใส่ตั้งแต่แรก → พิมพ์ "ลงทะเบียนสินค้า SN12345678 แอร์ ซื้อวันที่ 1 ก.ย. 2569 ประกัน 2 ปี"
- ระยะประกันเริ่มต้นมาจากสินค้าแต่ละตัว (หรือช่องระยะประกันในหน้าสินค้า) → พิมพ์ "สินค้า FAN01 รับประกัน 2 ปี"
- ดูเครื่องทั้งหมด — บอกลูกค้าที่ผูกและว่าผูก LINE แล้วหรือยัง → พิมพ์ "รายการประกัน"
- เปิดงานให้ลูกค้าที่โทรมา — ระบบผูกงานกับเครื่องที่คนนั้นลงทะเบียนไว้ให้เอง มีหลายเครื่องจะมีปุ่มให้เลือก → พิมพ์ "เปิดงานให้ สมชาย แอร์ไม่เย็น"

พิมพ์: `ลงทะเบียนสินค้า SN12345678 แอร์ ให้ลูกค้า สมชาย`

[IMAGE: sales-units — ตารางเครื่องที่ลงทะเบียน: S/N, สินค้า, สถานะ 'ลูกค้าผูกแล้ว' / 'ยังไม่มีลูกค้าผูก' ธีมเขียว]

_EN: Record sold units — Register the units you sold against the customer — they can check the warranty themselves, and repairs attach to the unit._

_- Register a unit for a customer — attached to their record at once → type "ลงทะเบียนสินค้า SN12345678 แอร์ ให้ลูกค้า สมชาย"_
_- An unknown name is asked about, never registered silently · a unit can be registered with no customer_
_- The customer types this S/N in the customer LINE to see the warranty_
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

พิมพ์: `มอบหมาย T-2026-0001 ให้ทีม แอร์`

[IMAGE: sales-dispatch — หน้าจอ 'งานซ่อม' บนแดชบอร์ด การ์ดงาน T-2026-0001 มีช่อง 'มอบหมายให้…' เลือกทีมแอร์ และปุ่มมอบหมายสีเขียว]

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

[IMAGE: sales-chats — หน้าจอแดชบอร์ดสีเขียว รายการแชทลูกค้า 2 รายการ รายการแรกมีป้าย 'ลูกค้ารอคำตอบ' ด้านล่างเป็นบทสนทนาและช่องพิมพ์คำตอบ ปุ่ม 'ส่ง' และ 'ปิดการสนทนา']

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

[IMAGE: sales-approve — การ์ดรายงาน SR-2026-0001: ปัญหาที่พบ / สิ่งที่แก้ไข พร้อมปุ่ม 'อนุมัติ' สีเขียว และ 'ตีกลับ' สีเทา]

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

**นัดหมายและบันทึก**
- ดูสิ่งที่ต้องทำวันนี้ → พิมพ์ "งานวันนี้"
- ตั้งเตือน → พิมพ์ "เตือน D-2026-0001 พรุ่งนี้"
- แก้นัดเดิม (ยังเป็นนัดเดิม ไม่ได้ตั้งใหม่) → พิมพ์ "เลื่อนนัด C-2026-0001 เป็นศุกร์ บ่าย 2"
- ยกเลิกแต่เก็บประวัติไว้ → พิมพ์ "ยกเลิกนัด สมชาย"
- บนหน้าลูกค้า/ดีล/ใบเสนอราคา มีปุ่ม แก้ไขนัด และ ลบนัด (ลบมีกล่องยืนยัน)
- กำลังเพิ่มลูกค้าอยู่แล้วพิมพ์คำสั่งอื่น ระบบถามก่อนว่าจะเปลี่ยนไปทำสิ่งนั้นหรือทำต่อ (ถ้าเปลี่ยน ระบบยังจำลูกค้าที่ค้างไว้ให้เปิดดีลได้)

พิมพ์: `งานวันนี้`

[IMAGE: sales-crm — แดชบอร์ดขายธีมเขียว: เมนู navigation ด้านซ้าย จัดกลุ่มเป็น งานขาย / งานบริการ / เอกสารและรายงาน / จัดการร้าน กำลังเปิดหน้ารายชื่อลูกค้า]

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

**Appointments and notes**
_- Today's list → type "งานวันนี้"_
_- Set a reminder → type "เตือน D-2026-0001 พรุ่งนี้"_
_- Move the appointment you already have → type "เลื่อนนัด C-2026-0001 เป็นศุกร์ บ่าย 2"_
_- Cancel, history kept → type "ยกเลิกนัด สมชาย"_
_- The customer, deal and quote pages have Edit appointment and Delete appointment (delete asks first)_
_- Typing another command while a customer is half-added asks whether to switch or continue (the half-added customer is still offered for the deal)_

## 7. ถามรายงานด้วย AI

พิมพ์สิ่งที่อยากรู้เป็นภาษาคน ระบบสรุปเป็นตัวเลขทันที อยากเห็นเป็นรูปก็ขอได้

- ถามเป็นประโยค → พิมพ์ "ยอดดีลปิดสำเร็จ 3 เดือนล่าสุด / สรุปงานค้างแยกตามช่าง"
- ระบบถามกลับเมื่อคำถามกว้าง (เช่น "เทียบตามเจ้าของ หรือ แยกตามช่วงเวลา") ตอบสั้น ๆ ได้เลย ระบบจำคำถามเดิมไว้

**กราฟ**
- ห้ากราฟสำเร็จรูป ใช้ได้ไม่จำกัด (หรือกดปุ่ม "ดูเป็นกราฟ" ใต้คำตอบยอดขาย) → พิมพ์ "ขอกราฟยอดขาย · กราฟยอดขายรายเดือน · กราฟสินค้าขายดี · กราฟยอดขายรายคน · กราฟดีลแต่ละสถานะ"
- กราฟแบบสั่งเอง — ระบบส่งรูปมาในแชทพร้อมสรุปสั้น ๆ → พิมพ์ "สร้างรายงานด้วย AI: ยอดขายแยกตามช่าง 3 เดือน"
- กราฟสั่งเองมีโควตาต่อเดือนของแต่ละร้าน (ค่าเริ่มต้น 30 ครั้ง ผู้ดูแล Chann ปรับให้ได้) นับเฉพาะครั้งที่ได้รูปจริง ทั้งในแชทและบนหน้าจอ
- รายงานที่ออกมาเป็นตัวเลขเดียวก็ได้รูป เป็นการ์ดตัวเลขใหญ่
- ใช้ครบแล้ว ตัวเลข ตาราง และไฟล์ยังโหลดได้ตามปกติ หายแค่รูป

**บนหน้าจอ**
- เมนู "รายงาน AI" มีตารางพร้อมกราฟแท่งและปุ่มดาวน์โหลด CSV/PDF
- ใต้ตารางมีปุ่ม "ปรับรายงานนี้" เปลี่ยนช่วงเวลา/การแยกกลุ่ม/ตัวเลข แล้วกด "ดูใหม่ตามที่ปรับ" ไม่ต้องพิมพ์ใหม่
- ต้องมีสิทธิ์ "ดูรายงาน" (Sale มีตั้งแต่ต้น CS ต้องให้เจ้าของเปิด) กราฟยอดขายตามสถานะดีลใช้สิทธิ์ "ดูดีล"
- AI ไม่แตะฐานข้อมูลเอง — มันแปลคำถามเป็นรายการที่ระบบอนุญาต แล้วนับจากข้อมูลของร้านคุณเท่านั้น

พิมพ์: `สรุปงานค้างแยกตามช่าง`

[IMAGE: sales-ai-report — แชท LINE ข้อความ 'สรุปงานค้างแยกตามช่าง' ตอบกลับเป็นรายการชื่อช่างกับตัวเลข ถัดมาข้อความ 'ขอกราฟยอดขาย' ตอบกลับเป็นรูปกราฟแท่งสีเขียว และหน้าจอตารางมีกราฟแท่งสีเขียว]

_EN: Ask for a report — Type what you want to know in plain words and get the numbers at once; ask for a picture when you want one._

_- Ask in a sentence → type "ยอดดีลปิดสำเร็จ 3 เดือนล่าสุด / สรุปงานค้างแยกตามช่าง"_
_- A broad question is asked back ("by owner, or by period?") — answer in a word; the original request is remembered_

**Charts**
_- Five ready-made charts, unlimited (or the "View as chart" button under a sales summary) → type "ขอกราฟยอดขาย · กราฟยอดขายรายเดือน · กราฟสินค้าขายดี · กราฟยอดขายรายคน · กราฟดีลแต่ละสถานะ"_
_- A made-to-order chart — the picture comes back in the chat with a one-line summary → type "สร้างรายงานด้วย AI: ยอดขายแยกตามช่าง 3 เดือน"_
_- Made-to-order charts have a monthly allowance per shop (30 by default, adjustable by Chann) — counted only when a picture is made, in chat and on the dashboard alike_
_- A single-number report gets a picture too, as a big-number card_
_- Past the allowance the numbers, table and files still come; only the picture does not_

**On the dashboard**
_- "AI reports" adds a table with bars and CSV/PDF downloads_
_- "Adjust this report" under the table changes the period/grouping/measure without retyping_
_- Needs the "View reports" permission (Sales has it; the owner grants it to CS); the pipeline chart uses "View deals"_
_- The AI never touches the database — it turns the question into an allowed query, counted over your shop's data only_

## 8. ติดขัด

ไม่รู้จะพิมพ์อะไร หรือคุยกันจนงง — มีทางออกสามอย่าง

- ดูตัวอย่างที่คุณมีสิทธิ์ → พิมพ์ "วิธีใช้"
- ดูสิทธิ์ทั้งหมด → พิมพ์ "ทำอะไรได้บ้าง"
- ล้างเฉพาะบทสนทนา ข้อมูลไม่หาย → พิมพ์ "เริ่มใหม่"
- สิทธิ์ขอได้จากเจ้าของร้าน (แดชบอร์ด > บทบาทและทีม)
- สลับภาษา → พิมพ์ "เปลี่ยนภาษาเป็นอังกฤษ"

พิมพ์: `วิธีใช้`

[IMAGE: sales-help — หน้าจอ 'บทบาทและทีม' แสดงรายชื่อสมาชิกและสิทธิ์เป็น toggle ธีมเขียว]

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
