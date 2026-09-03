# วิธีใช้ LINE บริการลูกค้า

พิมพ์คุยได้เลย ไม่ต้องจำคำสั่ง ทำตามลำดับนี้ครั้งแรกครั้งเดียว แล้วแจ้งซ่อมได้ทุกเมื่อ

> รูปแต่ละขั้น: สร้างจาก prompt ในวงเล็บ แล้วใส่ URL ลง `application/chann_app/help_images.json` และ `presentation/lib/help-images.json` (key ตามชื่อ slot) ระบบจะส่งรูปในแชทและโชว์บนหน้าวิธีใช้เอง

## 1. ผูกกับร้าน

พิมพ์หมายเลขเครื่อง (S/N บนสติกเกอร์) หรือชื่อร้านที่ซื้อ ระบบจะผูกบัญชีให้ ถ้ามีหลายร้านจะมีปุ่มให้เลือก

พิมพ์: `SN12345678`

[IMAGE: customer-link — โทรศัพท์เปิด LINE แชทกับร้าน มือถือสติกเกอร์ S/N ของเครื่องใช้ไฟฟ้า ลูกศรชี้จากสติกเกอร์ไปช่องพิมพ์ข้อความ]

_EN: Link to your shop — Type the serial number on the sticker, or the shop's name. Several shops → buttons to pick one._

## 2. ลงทะเบียนสินค้า (รับประกัน)

พิมพ์ "ลงทะเบียนสินค้า" แล้วตามด้วย S/N ที่ร้านบันทึกไว้ให้ เครื่องจะผูกกับคุณ ถ้าระบบยังไม่รู้จักหมายเลข ให้ติดต่อร้าน

พิมพ์: `ลงทะเบียนสินค้า SN12345678`

[IMAGE: customer-register — หน้าจอแชท: ลูกค้าพิมพ์ 'ลงทะเบียนสินค้า SN12345678' บอทตอบ 'ลงทะเบียน แอร์ (S/N …) เป็นของคุณแล้ว' พร้อมไอคอนโล่สีส้ม]

_EN: Register your product (warranty) — Type "register product" then the S/N the shop recorded. Unknown S/N → contact the shop._

## 3. แจ้งซ่อม

พิมพ์อาการที่เสียมาได้เลย เช่น "แอร์ไม่เย็น" ระบบจะเลือกเครื่องให้ (หรือให้กดเลือกถ้ามีหลายเครื่อง) แล้วถามที่อยู่และวันเวลานัด · ส่งรูปอาการมาในแชทได้ ระบบแนบกับงานให้ช่างดู

พิมพ์: `แอร์ไม่เย็น มีน้ำหยด`

[IMAGE: customer-report — แชท 3 ฟอง: ลูกค้า 'แอร์ไม่เย็น' → บอท 'รับแจ้งแล้ว เลขงาน T-2026-0001 ขอที่อยู่' → ลูกค้าพิมพ์ที่อยู่ → บอทถามวันนัด]

_EN: Report a fault — Describe what is wrong, e.g. "air con not cooling". The machine is picked for you, then address and appointment are asked._

## 4. ดูสถานะ / เลื่อนนัด / ยกเลิก

พิมพ์ "งานของฉัน" หรือ "สถานะการซ่อม" · เลื่อนนัด: "เลื่อนนัดวันศุกร์ บ่าย 2" · ยกเลิก: "ยกเลิกงาน"

พิมพ์: `งานของฉัน`

[IMAGE: customer-status — การ์ดสถานะงานซ่อม T-2026-0001 แสดงขั้น รอมอบหมาย → ช่างรับแล้ว → กำลังทำ → เสร็จ พร้อมไอคอนนาฬิกา]

_EN: Status, reschedule, cancel — "my jobs" / "repair status" · reschedule: "move it to Friday 2pm" · "cancel job"_

## 5. หลังซ่อมเสร็จ

เมื่อร้านตรวจงานผ่าน คุณจะได้ปุ่มให้คะแนน 1–3 กดได้เลย และดูประวัติทั้งหมดได้ที่ "เปิดหน้าจอลูกค้า" ในเมนู

พิมพ์: `ข้อมูลของฉัน`

[IMAGE: customer-after — ข้อความจากร้าน 'งาน T-… เสร็จแล้ว ช่วยให้คะแนน' พร้อมปุ่ม 1 ไม่ดี / 2 พอใช้ / 3 ดีเยี่ยม สีส้ม]

_EN: After the repair — When the shop approves the work you get 1–3 rating buttons. Everything is on the home screen (menu → Open the dashboard)._
