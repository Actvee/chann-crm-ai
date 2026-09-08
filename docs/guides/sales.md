# วิธีใช้ LINE ทีมขาย / CS

ทุกอย่างที่ทำได้บนแดชบอร์ด พิมพ์ในแชทได้เหมือนกัน ตั้งร้านให้พร้อมก่อน แล้วเดินงานซ่อมตามลำดับ · เมนูมี 2 หน้า แตะ "เพิ่มเติม" บนหัวเมนู: แชทลูกค้า ดีล สินค้า ทีมช่าง ข้อมูลบริษัท สลับภาษา

> รูปแต่ละขั้น: สร้างจาก prompt ในวงเล็บ แล้วใส่ URL ลง `application/chann_app/help_images.json` และ `presentation/lib/help-images.json` (key ตามชื่อ slot) ระบบจะส่งรูปในแชทและโชว์บนหน้าวิธีใช้เอง

## 1. ตั้งร้านให้พร้อม

"ข้อมูลร้าน" ดูรหัสร้าน (ให้ลูกค้าใช้ผูก) · "ขอรหัสเชิญช่าง" ให้ช่างเข้าร่วม · "สร้างทีมช่าง แอร์" แล้ว "เพิ่ม สมศักดิ์ เข้าทีม แอร์ เป็นหัวหน้า" · "ข้อมูลบริษัท" สำหรับเอกสาร · "ตั้งค่ารับลูกค้าใหม่อัตโนมัติ เปิด" ให้ลูกค้าที่ผูกร้านเข้ารายชื่อทันที

พิมพ์: `สร้างทีมช่าง แอร์`

[IMAGE: sales-setup — แผนผังร้าน: กล่อง 'ร้าน (รหัส ABCD01)' เชื่อมไป 'ทีมช่าง แอร์ (หัวหน้า สมศักดิ์)' และ 'ลูกค้า' ธีมเขียว]

_EN: Set the shop up — "shop info" (the code customers link with) · "invite technician" · "create technician team AC" then "add Somsak to team AC as lead" · "company profile" for documents_

## 2. บันทึกเครื่องที่ขาย

"ลงทะเบียนสินค้า SN12345678 แอร์ ให้ลูกค้า สมชาย" — ลูกค้าจึงพิมพ์ S/N นี้ผูกเครื่องได้ · "รายการประกัน" ดูทั้งหมด

พิมพ์: `ลงทะเบียนสินค้า SN12345678 แอร์ ให้ลูกค้า สมชาย`

[IMAGE: sales-units — ตารางเครื่องที่ลงทะเบียน: S/N, สินค้า, สถานะ 'ลูกค้าผูกแล้ว' / 'ยังไม่มีลูกค้าผูก' ธีมเขียว]

_EN: Record sold units — "register product SN12345678 aircon for Somchai" — the customer then attaches it by typing the S/N · "warranties" lists them_

## 3. งานซ่อม: มอบหมาย

ลูกค้าแจ้งซ่อมแล้วคุณได้ LINE · "รายการงาน" ดูคิว · "มอบหมาย T-2026-0001 ให้ทีม แอร์" (ต้องมีชื่อ เบอร์ ที่อยู่ นัดครบ ระบบบอกถ้าขาด) · หรือทำบนแดชบอร์ด > งานซ่อม

พิมพ์: `มอบหมาย T-2026-0001 ให้ทีม แอร์`

[IMAGE: sales-dispatch — หน้าจอ 'งานซ่อม' บนแดชบอร์ด การ์ดงาน T-2026-0001 มีช่อง 'มอบหมายให้…' เลือกทีมแอร์ และปุ่มมอบหมายสีเขียว]

_EN: Repairs: dispatch — You hear when a customer reports · "tickets" for the queue · "assign T-2026-0001 to team AC" (name, phone, address, appointment required — it tells you what is missing) · or dashboard > tickets_

## 4. แชทลูกค้า

ลูกค้าที่กด "คุยกับร้าน" ใน LINE บริการลูกค้า จะขึ้นที่ หน้าจอ > แชทลูกค้า และทุกคนได้ LINE แจ้ง ตอบที่หน้านั้นเท่านั้น (ตอบใน LINE ร้านไม่ถึงลูกค้า) คนแรกที่ตอบเป็นเจ้าของการสนทนา ถ้าปล่อยเกินเวลาตอบ (ค่าเริ่มต้น 15 นาที) ระบบแจ้งลูกค้าว่าจะติดต่อกลับและพักการสนทนา — ตอบทีหลังได้ ลูกค้าจะได้รับคำเชิญให้เปิดแชทต่อ · ลูกค้าเงียบ 1 ชั่วโมงปิดให้เอง · ตั้งเวลาเองได้ด้วย "ตั้งค่าแชท" หรือหน้าข้อมูลบริษัท

พิมพ์: `หน้าจอ > แชทลูกค้า`

[IMAGE: sales-chats — หน้าจอแดชบอร์ดสีเขียว รายการแชทลูกค้า 2 รายการ รายการแรกมีป้าย 'ลูกค้ารอคำตอบ' ด้านล่างเป็นบทสนทนาและช่องพิมพ์คำตอบ ปุ่ม 'ส่ง' และ 'ปิดการสนทนา']

_EN: Customer chats — A customer who taps "talk to the shop" appears under home > Customer chats, and everyone is pushed a LINE. Answer there only (a reply in the shop's LINE never reaches them). The first to answer owns it. Past the reply time (default 15 min) the customer is told you will get back and the chat is paused — answer later and they are invited to reopen it. An hour of customer silence closes it. Set both with "chat settings" or on the company page._

## 5. งานซ่อม: ตรวจรายงาน

ช่างปิดงานแล้วคุณได้ LINE · "รายการรออนุมัติ" · "อนุมัติ SR-2026-0001" หรือ "ตีกลับ SR-… เหตุผล" · ผ่านครบ → ลูกค้าได้แบบประเมิน + PDF รายงานออกอัตโนมัติ · ตั้งขั้นตอน: "ตั้งการอนุมัติ"

พิมพ์: `อนุมัติ SR-2026-0001`

[IMAGE: sales-approve — การ์ดรายงาน SR-2026-0001: ปัญหาที่พบ / สิ่งที่แก้ไข พร้อมปุ่ม 'อนุมัติ' สีเขียว และ 'ตีกลับ' สีเทา]

_EN: Repairs: review reports — "pending approvals" · "approve SR-2026-0001" or "reject SR-… reason" · all steps passed → the customer gets the survey and the PDF is produced · "approval policy" to change the flow_

## 6. ลูกค้า ดีล ใบเสนอราคา

"รายชื่อลูกค้า" · "สร้างลูกค้า สมชาย ใจดี 0812345678" · "สร้างดีลให้ สมชาย" · "ออกเอกสาร Q-2026-0001" · "งานวันนี้" ดูสิ่งที่ต้องทำ · "เตือน D-… พรุ่งนี้" · "สร้างดีลให้ อาทิตย์ มูลค่า 250,000 ปิดสิ้นเดือนนี้" ใส่มูลค่าและวันคาดว่าจะปิดในประโยคเดียว · "ลบ Lead สมชาย" ระบบถามยืนยันก่อน (เก็บถาวร ไม่ลบทิ้ง) · เพิ่มลูกค้าซ้ำเบอร์/อีเมลเดิม ระบบบอกว่าเป็นใครและให้เลือก ใช้เดิม / อัปเดต / ยกเลิก · "ทำอะไรกับ Lead ได้บ้าง" ดูสิ่งที่ทำได้ทีละหมวด · เพิ่มหลายคนในข้อความเดียว: วางรายชื่อทีละบรรทัด "ชื่อ นามสกุล เบอร์ อีเมล" (จะพิมพ์ "เพิ่มลูกค้าหลายคน" นำหน้าหรือคั่นด้วยจุลภาคก็ได้) คนที่ไม่มีเบอร์ระบบจะถามทีละคน · บนหน้ารายชื่อลูกค้าใช้กล่อง "เพิ่มลูกค้าหลายคนในครั้งเดียว" วางรายชื่อ หรือปุ่ม "นำเข้า CSV" (มีไฟล์ตัวอย่าง) · ถ้ากำลังเพิ่มลูกค้าอยู่แล้วพิมพ์คำสั่งอื่น เช่น "สร้างดีล" ระบบจะถามก่อนว่าจะเปลี่ยนไปทำสิ่งนั้นหรือทำต่อ (ถ้าเปลี่ยนไปแล้ว ระบบจะบอก และยังจำลูกค้าที่ค้างไว้ให้เปิดดีลได้) · เบอร์โทรต้องเป็นตัวเลข ระบบไม่บันทึกเบอร์ที่มีตัวอักษรและบอกเหตุผล · สินค้าในดีล พิมพ์ตามที่พูด: "เพิ่มสินค้า พัดลม 2 ตัว ราคา 1500" (ไม่บอกราคา ระบบดูจากรายการสินค้า ไม่มีก็ถามราคาแล้วจำรายการไว้ ตอบแค่ "1500" ได้เลย) · "เพิ่มพัดลมอีก 3 ตัว" / "เพิ่มอีก 1 ตัว" คือบวกเพิ่มจากเดิม · "แก้พัดลมเป็น 3 ตัว" คือตั้งจำนวนใหม่ · "ลดพัดลม 1 ตัว" ลดจำนวน · "ลบสินค้าพัดลมออก" เอาออกจากดีล · "ปรับราคาพัดลมเป็น 2000" · ทุกครั้งระบบบอกยอดรวมดีล · "ดีลล่าสุด" หรือ "สินค้าในดีล" ดูดีลที่เพิ่งทำ · "ลูกค้าสนใจอยากได้พัดลม 1 ตัว" หลังเพิ่มลูกค้า ระบบเสนอเปิดดีลพร้อมสินค้าให้ · "มีสินค้าอะไรบ้างที่เป็น พัดลม" หรือ "ค้นหาสินค้า พัดลม" ค้นรายการสินค้า

พิมพ์: `งานวันนี้`

[IMAGE: sales-crm — แดชบอร์ดขายธีมเขียว: tile ลูกค้า / ดีล / ใบเสนอราคา / งานซ่อม / รอการอนุมัติ / ทีมช่าง]

_EN: Customers, deals, quotes — "customers" · "create customer …" · "create deal for Somchai" · "issue quote Q-…" · "today" · "remind D-… tomorrow" · "create deal for Arthit worth 250,000, closing end of month" · "delete lead Somchai" (asks first) · a duplicate phone/email is named, with use / update / cancel · "what can I do with leads?" for that area in detail several at once: paste one person per line "first last phone email" (a leading "add customers" or commas also work); a row without a phone is asked for one at a time · on the customers page use the "Add several customers at once" box or "Import CSV" (sample file provided) · typing another command such as "create deal" while a customer is half-added asks whether to switch or continue (a switch is announced, and the half-added customer is still offered for the deal) · lines on a deal, as you would say it: "add product fan 2 at 1500" (no price: the catalogue is checked, else the price is asked and the item remembered — reply "1500") · "add 2 more fans" adds to the quantity, "set fan to 3" sets it, "remove 1 fan" takes off, "remove the fan" deletes the line · every reply states the deal total · "latest deal" / "items on the deal" show the deal in play · "customer wants 2 fans" right after adding a customer offers a deal with that line · "search products fan" searches the catalogue_

## 7. ถามรายงานด้วย AI

พิมพ์สิ่งที่อยากรู้เป็นภาษาคนในแชท เช่น "ยอดดีลปิดสำเร็จ 3 เดือนล่าสุด" หรือ "สรุปงานค้างแยกตามช่าง" ระบบสรุปเป็นตัวเลขทันที · อยากเห็นเป็นรูป ให้เติมคำว่า "เป็นกราฟ" เช่น "ขอกราฟยอดขาย" "กราฟยอดขายรายเดือน" "กราฟสินค้าขายดี" "กราฟยอดขายรายคน" "กราฟดีลแต่ละสถานะ" ระบบจะส่งรูปกราฟมาในแชทพร้อมสรุปสั้น ๆ (หรือกดปุ่ม "ดูเป็นกราฟ" ใต้คำตอบยอดขายก็ได้) · บนหน้าจอ เมนู "รายงาน AI" มีตารางพร้อมกราฟแท่งและปุ่มดาวน์โหลด CSV/PDF · ต้องมีสิทธิ์ "ดูรายงาน" (Sale มีตั้งแต่ต้น CS ต้องให้เจ้าของเปิด) กราฟยอดขายตามสถานะดีลใช้สิทธิ์ "ดูดีล" เท่ากับคำสั่ง "ยอดขาย" · AI ไม่แตะฐานข้อมูลเอง มันแค่แปลคำถามเป็นรายการที่ระบบอนุญาต แล้วนับจากข้อมูลของร้านคุณเท่านั้น

พิมพ์: `สรุปงานค้างแยกตามช่าง`

[IMAGE: sales-ai-report — แชท LINE ข้อความ 'สรุปงานค้างแยกตามช่าง' ตอบกลับเป็นรายการชื่อช่างกับตัวเลข ถัดมาข้อความ 'ขอกราฟยอดขาย' ตอบกลับเป็นรูปกราฟแท่งสีเขียว และหน้าจอตารางมีกราฟแท่งสีเขียว]

_EN: Ask for a report — Type what you want to know in the chat — "won deals in the last 3 months", "open tickets by technician" — and get the numbers at once. Add "as a chart" ("sales report as a chart", "monthly sales chart", "top products chart", "sales chart per person") and the picture comes back in the chat with a one-line summary; the "View as chart" button under a sales summary does the same. The dashboard's "AI reports" page adds a table with bars and CSV/PDF downloads. Needs the "View reports" permission (Sales has it; the owner grants it to CS); the pipeline chart uses "View deals", the same key as "sales"._

## 8. ติดขัด

"วิธีใช้" ดูตัวอย่างที่คุณมีสิทธิ์ · "ทำอะไรได้บ้าง" ดูสิทธิ์ทั้งหมด · สิทธิ์ขอได้จากเจ้าของร้าน (แดชบอร์ด > บทบาทและทีม) · "เปลี่ยนภาษาเป็นอังกฤษ"

พิมพ์: `วิธีใช้`

[IMAGE: sales-help — หน้าจอ 'บทบาทและทีม' แสดงรายชื่อสมาชิกและสิทธิ์เป็น toggle ธีมเขียว]

_EN: Stuck — "help" · "what can I do" · ask the owner for permissions (dashboard > roles) · "switch to English"_

## 9. สมาชิกในร้าน

หน้าจอ > สมาชิกในร้าน (ข้างบทบาท) ดูว่าใครผูกกับบริษัทใน LINE ไหน — LINE ทีมขาย/CS หรือ LINE ช่าง คนเดียวกันอาจอยู่ทั้งสองฝั่งคนละบทบาท · เปลี่ยนบทบาท · นำออก / กลับมาใช้งาน (นำช่างออก = ถอดจากทีมและคืนงานที่ค้างเข้าคิว) · รีเซ็ตการลงทะเบียน เมื่อแชทของคนนั้นค้าง · เจ้าของร้านนำออกไม่ได้ ต้องโอนความเป็นเจ้าของก่อน · เพิ่มคน: ช่างพิมพ์ "ขอรหัสเชิญช่าง" ให้เขาไปพิมพ์ใน LINE ช่าง ทีมขาย/CS ใช้รหัสเชิญของ LINE ทีมขาย · แต่ละ LINE ลงทะเบียนแยกกัน ใช้ร่วมกันเฉพาะข้อมูลส่วนตัว

พิมพ์: `หน้าจอ > สมาชิกในร้าน`

[IMAGE: sales-members — หน้าจอแดชบอร์ดสีเขียว 'สมาชิกในร้าน' ตารางชื่อ / LINE (ทีมขาย·ช่าง) / บทบาท / สถานะ ปุ่ม เปลี่ยนบทบาท นำออก รีเซ็ต แถวเจ้าของมีป้าย 'เจ้าของ']

_EN: Members — Home > Members (next to roles): who is linked to the company on which LINE — the sales/CS LINE or the technician LINE; one person can be on both with different roles · change a role · remove / reactivate (removing a technician also takes them off teams and returns their open jobs to the queue) · reset onboarding when someone's chat is stuck · the owner cannot be removed; transfer ownership first · to add people: "invite technician" gives a code they type in the technician LINE; sales/CS staff use a sales-LINE invite code · each LINE is a separate registration; only personal details are shared._
