# วิธีใช้ LINE บริการลูกค้า

พิมพ์คุยได้เลย ไม่ต้องจำคำสั่ง ทำตามลำดับนี้ครั้งแรกครั้งเดียว แล้วแจ้งซ่อมได้ทุกเมื่อ · เมนูด้านล่างมี 2 หน้า แตะ "เพิ่มเติม" บนหัวเมนูเพื่อดูสินค้า ประวัติการซื้อ ประกัน โปรไฟล์ และสลับภาษา

> รูปแต่ละขั้น: สร้างจาก prompt ในวงเล็บ แล้วใส่ URL ลง `application/chann_app/help_images.json` และ `presentation/lib/help-images.json` (key ตามชื่อ slot) ระบบจะส่งรูปในแชทและโชว์บนหน้าวิธีใช้เอง

## 1. ผูกกับร้าน

พิมพ์หมายเลขเครื่อง (S/N บนสติกเกอร์) หรือชื่อร้านที่ซื้อ ระบบจะผูกบัญชีให้ ถ้ามีหลายร้านจะมีปุ่มให้เลือก · อยู่กับหลายร้านได้ คุยทีละร้าน — ถามว่า "อยู่กับร้านไหนบ้าง" เพื่อดูรายชื่อ (มีบอกว่ากำลังคุยกับร้านไหน) พิมพ์ "เปลี่ยนร้าน" เพื่อสลับ · ถ้าพิมพ์เลขงานหรือ S/N ของอีกร้าน ระบบจะสลับให้เองแล้วบอกว่าสลับไปร้านไหน

พิมพ์: `SN12345678`

[IMAGE: customer-link — โทรศัพท์เปิด LINE แชทกับร้าน มือถือสติกเกอร์ S/N ของเครื่องใช้ไฟฟ้า ลูกศรชี้จากสติกเกอร์ไปช่องพิมพ์ข้อความ]

_EN: Link to your shop — Type the serial number on the sticker, or the shop's name. Several shops → buttons to pick one. You can be with several shops and talk to one at a time: ask "which shops am I with" for the list (it marks the one you are talking to) and say "change shop" to switch. Naming a job or serial that belongs to another of your shops switches you there and says so._

## 2. ดูสินค้า / สนใจสินค้า

พิมพ์ "สินค้าทั้งหมด" หรือ "ค้นหา" ตามด้วยชื่อสินค้า เช่น "ค้นหา พัดลม" จะเห็นสินค้าจากทุกร้าน พิมพ์เลขข้อที่สนใจ ร้านนั้นจะได้รับแจ้งและติดต่อกลับ · บนหน้าจอลูกค้ามีช่องค้นหาและปุ่ม "สนใจ" เหมือนกัน

พิมพ์: `ค้นหา พัดลม`

[IMAGE: customer-shop — หน้าจอแชท: ลูกค้าพิมพ์ 'ค้นหา พัดลม' บอทตอบรายการ 1. พัดลมไอเย็น (ร้านเย็นสบาย) — 3500 2. … ลูกค้าพิมพ์ '1' บอทตอบ 'ร้านเย็นสบาย จะติดต่อกลับ' พร้อมไอคอนตะกร้าสีส้ม]

_EN: Browse products / show interest — Type "all products" or "search" and a product name, e.g. "search fan". Products from every shop appear; type the number you like and that shop is told and gets back to you. The home screen has the same search and an "interested" button._

## 3. คุยกับร้าน

ระหว่างคุยกับร้าน ข้อความทุกอย่างจะถึงร้าน เมนูอื่นจะพักไว้ก่อน — พิมพ์ "จบการสนทนา" เมื่อคุยเสร็จแล้วค่อยใช้เมนูต่อ · พิมพ์ "คุยกับร้าน" (ต่อด้วยคำถามได้เลย เช่น "คุยกับร้าน ราคาแอร์ 12000 BTU") เจ้าหน้าที่ของร้านจะตอบกลับในแชทนี้ ระหว่างคุย ข้อความที่พิมพ์จะส่งถึงร้านทั้งหมด (ไม่มีข้อความยืนยันทุกครั้ง ถ้าส่งไม่ได้ระบบจะบอก) ไม่เปิดเป็นงานซ่อม กลับมาคุยใหม่ก็ต่อจากแชทเดิม · พิมพ์ "จบการสนทนา" เมื่อเสร็จ ไม่มีข้อความสักพักระบบปิดให้เอง · บนหน้าจอลูกค้ามีช่องแชทเดียวกัน

พิมพ์: `คุยกับร้าน ราคาแอร์ 12000 BTU เท่าไหร่`

[IMAGE: customer-chat — หน้าจอแชท LINE: ลูกค้าพิมพ์ 'คุยกับร้าน ราคาแอร์ 12000 BTU' บอทตอบ 'เปิดการสนทนากับ ร้านเย็นสบาย แล้ว' แล้วมีข้อความจากร้าน '💬 ร้านเย็นสบาย: 15,900 บาทครับ' ไอคอนคนสวมหูฟังสีส้ม]

_EN: While a conversation with the shop is open every message reaches them and the other menus stay out of the way — type "end chat" when you are done. Talk to the shop — Type "talk to the shop" (a question may follow, e.g. "talk to the shop price of a 12000 BTU air con"). A person at the shop answers here. While talking, what you type goes to the shop (no confirmation each time; a failure is reported) and does not open a repair job. Coming back later continues the same conversation. "end chat" when done; it closes itself after a quiet while. The home screen has the same chat box._

## 4. ลงทะเบียนสินค้า (รับประกัน)

แก้ข้อมูลของตัวเอง: พิมพ์ "แก้ไขข้อมูลส่วนตัว" แล้วพิมพ์สิ่งที่จะแก้ หลายช่องพร้อมกันได้ เช่น "ชื่อ สมชาย ใจดี ที่อยู่ 99/1 เบอร์โทร 0891234567" · พิมพ์ "ลงทะเบียนสินค้า" แล้วตามด้วย S/N ที่ร้านบันทึกไว้ให้ เครื่องจะผูกกับคุณ ถ้าระบบยังไม่รู้จักหมายเลข ให้ติดต่อร้าน · บอกวันที่ซื้อได้ด้วยถ้าร้านยังไม่ได้ใส่: "ลงทะเบียนสินค้า SN12345678 ซื้อเมื่อ 1 ก.ย. 2569"

พิมพ์: `ลงทะเบียนสินค้า SN12345678`

[IMAGE: customer-register — หน้าจอแชท: ลูกค้าพิมพ์ 'ลงทะเบียนสินค้า SN12345678' บอทตอบ 'ลงทะเบียน แอร์ (S/N …) เป็นของคุณแล้ว' พร้อมไอคอนโล่สีส้ม]

_EN: Register your product (warranty) — Your own details: type "edit my profile", then what to change — several at once is fine, e.g. "name Somchai Jaidee address 99/1 phone 0891234567". Type "register product" then the S/N the shop recorded. Unknown S/N → contact the shop. Add the purchase date if the shop did not: "register product SN12345678 bought 2026-09-01"._

## 5. แจ้งซ่อม

พิมพ์อาการที่เสียมาได้เลย เช่น "แอร์ไม่เย็น" ระบบจะเลือกเครื่องให้ (หรือให้กดเลือกถ้ามีหลายเครื่อง) แล้วบอกกลับว่างานนี้ผูกกับเครื่องไหนและยังอยู่ในประกันหรือไม่ จากนั้นถามที่อยู่ (ถ้ามีที่อยู่ในข้อมูลส่วนตัวแล้ว จะถามว่าใช้ที่อยู่นั้นไหม ตอบ "ใช่" หรือพิมพ์ที่อยู่ใหม่) และวันเวลานัด · ถ้าไม่มีหมายเลขเครื่อง กด "ไม่มีหมายเลขเครื่อง" ได้ ระบบจะแจ้งว่างานนี้ยังไม่ได้ผูกกับเครื่องที่ลงทะเบียน · ส่งรูปอาการมาในแชทได้ ระบบแนบกับงานให้ช่างดู

พิมพ์: `แอร์ไม่เย็น มีน้ำหยด`

[IMAGE: customer-report — แชท 3 ฟอง: ลูกค้า 'แอร์ไม่เย็น' → บอท 'รับแจ้งแล้ว เลขงาน T-2026-0001 ขอที่อยู่' → ลูกค้าพิมพ์ที่อยู่ → บอทถามวันนัด]

_EN: Report a fault — Describe what is wrong, e.g. "air con not cooling". The machine is picked for you (or you tap which one), the reply names it and says whether it is still under warranty, then the address (if your profile has one it is offered — answer "yes" or type another) and the appointment are asked. With no serial, the job is filed and says so._

## 6. ดูสถานะ / ขอเลื่อนนัด / ยกเลิก

พิมพ์ "งานของฉัน" หรือ "สถานะการซ่อม" · ขอเลื่อนนัด: "ขอเลื่อนนัดวันศุกร์ บ่าย 2" หรือบอกแค่เวลา "เลื่อนนัดเป็น 9 โมงเช้า" (วันเดิม) — ร้านจะเช็คคิวช่างแล้วยืนยันกลับ นัดเดิมยังอยู่จนกว่าร้านจะยืนยัน · ยกเลิก: "ยกเลิกงาน" (ยกเลิกได้เลย)

พิมพ์: `งานของฉัน`

[IMAGE: customer-status — การ์ดสถานะงานซ่อม T-2026-0001 แสดงขั้น รอมอบหมาย → ช่างรับแล้ว → กำลังทำ → เสร็จ พร้อมไอคอนนาฬิกา]

_EN: Status, ask to move, cancel — "my jobs" / "repair status" · ask to move it: "move it to Friday 2pm", or just a time "move it to 9am" (same day) — the shop checks the technicians' schedule and confirms; the existing appointment stands until they do · "cancel job" (cancels straight away)_

## 7. หลังซ่อมเสร็จ

พอช่างปิดงาน คุณจะได้ข้อความทันทีว่างานเลขไหนเสร็จแล้วและช่างทำอะไรไปบ้าง (ยังไม่ต้องรอร้านตรวจ) · จากนั้นเมื่อร้านตรวจงานผ่าน คุณจะได้ปุ่มให้คะแนน 1–3 กดได้เลย · ถ้าร้านตรวจแล้วขอให้ช่างกลับไปดูอีกครั้ง ระบบจะแจ้งคุณด้วย · ดูประวัติทั้งหมดได้ที่ "เปิดหน้าจอลูกค้า" ในเมนู

พิมพ์: `ข้อมูลของฉัน`

[IMAGE: customer-after — ข้อความจากร้าน 'งาน T-… เสร็จแล้ว ช่วยให้คะแนน' พร้อมปุ่ม 1 ไม่ดี / 2 พอใช้ / 3 ดีเยี่ยม สีส้ม]

_EN: After the repair — The moment the technician closes the job you are told which job is finished and what was done — you do not wait for the shop's review. When the shop then approves it you get 1–3 rating buttons; if the shop sends the technician back instead, you are told that too. Everything is on the home screen (menu → Open the dashboard)._

## 8. ข้อมูลส่วนตัว (PDPA)

ครั้งแรกระบบจะขอความยินยอมก่อนผูกร้าน (ตอบ "ยอมรับ") · ขอสำเนาข้อมูลทั้งหมดได้ด้วย "ขอข้อมูลของฉัน" จะได้ลิงก์หน้าสรุปใช้ได้ 24 ชั่วโมง · ขอลบด้วย "ขอลบข้อมูล" แล้วยืนยัน ชื่อ เบอร์ ที่อยู่ แชท และรูปจะถูกลบจากทุกร้าน (ประวัติงานยังอยู่แต่ไม่มีชื่อคุณ) · บนหน้าจอลูกค้า ส่วน "โปรไฟล์" มีปุ่มเดียวกัน

พิมพ์: `ขอข้อมูลของฉัน`

[IMAGE: customer-pdpa — แชท LINE ข้อความ 'ขอข้อมูลของฉัน' ตอบกลับเป็นลิงก์หน้าสรุปข้อมูล และปุ่ม 'ยืนยันลบข้อมูล' สีส้ม ไอคอนโล่ PDPA]

_EN: Your personal data (PDPA) — The first time, consent is asked before linking a shop (answer "accept"). "my data" gives a 24-hour link to a page with everything; "delete my data" plus a confirmation erases your name, phone, address, chat lines and pictures from every shop (job history stays, without your name). The profile section on the home screen has the same buttons._

## 9. ใบแจ้งหนี้และใบเสร็จ

พิมพ์ "ใบแจ้งหนี้ของฉัน" หรือ "ยอดค้าง" เพื่อดูใบแจ้งหนี้ของคุณกับร้านนี้ ยอดที่ค้าง และวันครบกำหนด · พิมพ์ "ขอใบเสร็จ" — ถ้าชำระครบแล้วจะได้ลิงก์ใบเสร็จ ถ้ายังค้างระบบบอกยอดค้างและทางร้านจะติดต่อเรื่องช่องทางชำระ · เมื่อร้านออกใบเสร็จ ระบบส่งลิงก์มาให้ในแชทนี้เอง · บนหน้าจอลูกค้ามีส่วน "ใบแจ้งหนี้และใบเสร็จ" พร้อมปุ่มเปิด PDF · ร้านส่งใบเสนอราคาและใบแจ้งหนี้มาให้ในแชทนี้ได้ พร้อมลิงก์เปิด PDF · ถ้าร้านแก้ใบแจ้งหนี้ก่อนคุณชำระ ยอดที่แชทบอกเป็นยอดล่าสุดเสมอ และร้านจะส่ง PDF ใบใหม่ให้หลังออกเอกสารใหม่

พิมพ์: `ขอใบเสร็จ`

[IMAGE: customer-invoices — หน้าจอแชท: ลูกค้าพิมพ์ 'ขอใบเสร็จ' บอทตอบ 'ใบเสร็จของ INV-2026-0001 (ยอด 32,100.00 บาท):' พร้อมลิงก์ ไอคอนใบเสร็จสีส้ม]

_EN: Invoices and receipts — Type "my invoices" or "outstanding" to see your invoices with this shop, what is owed and when it is due. "receipt": the receipt link once paid in full; otherwise what is outstanding, and the shop will contact you about how to pay. When the shop issues a receipt the link arrives in this chat. The home screen has the same list with PDF buttons. The shop can send you a quote or an invoice in this chat with a PDF link; if the shop corrects an invoice before you pay, the amount this chat tells you is always the latest, and the shop sends the new PDF once it is issued again._
