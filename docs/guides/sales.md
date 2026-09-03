# วิธีใช้ LINE ทีมขาย / CS

ทุกอย่างที่ทำได้บนแดชบอร์ด พิมพ์ในแชทได้เหมือนกัน ตั้งร้านให้พร้อมก่อน แล้วเดินงานซ่อมตามลำดับ

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

## 4. งานซ่อม: ตรวจรายงาน

ช่างปิดงานแล้วคุณได้ LINE · "รายการรออนุมัติ" · "อนุมัติ SR-2026-0001" หรือ "ตีกลับ SR-… เหตุผล" · ผ่านครบ → ลูกค้าได้แบบประเมิน + PDF รายงานออกอัตโนมัติ · ตั้งขั้นตอน: "ตั้งการอนุมัติ"

พิมพ์: `อนุมัติ SR-2026-0001`

[IMAGE: sales-approve — การ์ดรายงาน SR-2026-0001: ปัญหาที่พบ / สิ่งที่แก้ไข พร้อมปุ่ม 'อนุมัติ' สีเขียว และ 'ตีกลับ' สีเทา]

_EN: Repairs: review reports — "pending approvals" · "approve SR-2026-0001" or "reject SR-… reason" · all steps passed → the customer gets the survey and the PDF is produced · "approval policy" to change the flow_

## 5. ลูกค้า ดีล ใบเสนอราคา

"รายชื่อลูกค้า" · "สร้างลูกค้า สมชาย ใจดี 0812345678" · "สร้างดีลให้ สมชาย" · "ออกเอกสาร Q-2026-0001" · "งานวันนี้" ดูสิ่งที่ต้องทำ · "เตือน D-… พรุ่งนี้"

พิมพ์: `งานวันนี้`

[IMAGE: sales-crm — แดชบอร์ดขายธีมเขียว: tile ลูกค้า / ดีล / ใบเสนอราคา / งานซ่อม / รอการอนุมัติ / ทีมช่าง]

_EN: Customers, deals, quotes — "customers" · "create customer …" · "create deal for Somchai" · "issue quote Q-…" · "today" · "remind D-… tomorrow"_

## 6. ติดขัด

"วิธีใช้" ดูตัวอย่างที่คุณมีสิทธิ์ · "ทำอะไรได้บ้าง" ดูสิทธิ์ทั้งหมด · สิทธิ์ขอได้จากเจ้าของร้าน (แดชบอร์ด > บทบาทและทีม) · "เปลี่ยนภาษาเป็นอังกฤษ"

พิมพ์: `วิธีใช้`

[IMAGE: sales-help — หน้าจอ 'บทบาทและทีม' แสดงรายชื่อสมาชิกและสิทธิ์เป็น toggle ธีมเขียว]

_EN: Stuck — "help" · "what can I do" · ask the owner for permissions (dashboard > roles) · "switch to English"_
