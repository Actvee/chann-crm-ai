# Chann Rich Menu v3 — Design notes

## แนวคิดหลัก

- ใช้ **visual hierarchy** แทนกริด 6 ช่องที่มีน้ำหนักเท่ากัน: งานที่ใช้บ่อยที่สุดเป็นปุ่มใหญ่ และงานรองใช้การ์ด 2 ขนาด
- ใช้โลโก้ Chann แบบตัว **C + chat + connected nodes** เพื่อสื่อถึงการรวมช่องทางสื่อสารไว้ในระบบเดียว
- รักษาสีตามบทบาทเดิม: ลูกค้า = ส้ม, ฝ่ายขาย = เขียว, ช่าง = น้ำเงิน
- ใช้พื้นขาว/สีอ่อนเพื่อให้เป็นทางการ อ่านง่าย และลดความล้าทางสายตา
- คงข้อความ action เดิมทั้งหมดใน JSON; การเปลี่ยนขนาดและตำแหน่งการ์ดถูกสะท้อนใน `bounds` แล้ว

## Layout

- Canvas: 2500 × 1686 px (LINE large rich menu)
- Header: 300 px พร้อมโลโก้ ชื่อ workspace และแท็บ `หน้าหลัก` / `เพิ่มเติม`
- Primary card: 900 × 1338 px
- Secondary cards: ด้านขวา 2 ช่องใหญ่ด้านบน + 3 ช่องเล็กด้านล่าง
- ทุกการ์ดมี tap area เต็มพื้นที่ โดยเว้นช่องว่าง 24 px เพื่อลดการกดผิด

## แหล่งอ้างอิงและตัวอย่าง

- [LINE Developers — Rich menus overview](https://developers.line.biz/en/docs/messaging-api/rich-menus-overview/)
- [LINE Developers — Use rich menus](https://developers.line.biz/en/docs/messaging-api/using-rich-menus/)
- [LINE for Business — Rich Menu guide](https://lineforbusiness.com/files/LineOA_Level1.4_How%20to%20Create%20Shortcuts%20for%20Easier%20Access%20to%20Information%20with%20Rich%20Menu_compressed.pdf)
- ตัวอย่าง LINE OA ของธุรกิจบริการ/อสังหาริมทรัพย์ที่ใช้ hero action ใหญ่และปุ่มติดต่อรอง ถูกใช้เป็นแรงบันดาลใจด้านลำดับสายตา ไม่ได้คัดลอกงานภาพหรือแบรนด์

## วิธีสร้างไฟล์ใหม่

```bash
python3 generate.py
```

ผลลัพธ์จะอยู่ใน `out/` และพร้อมใช้กับ `richmenu-apply.sh`

## วิธีอัปโหลด

ตั้งค่า token/LIFF ตามคำอธิบายใน `richmenu-apply.sh` แล้วรัน:

```bash
bash richmenu-apply.sh
```

## สองภาษา (Phase 20)

- `generate.py` วาดทุกหน้า 2 ภาษา: ไทย (`richmenu-<oa>[-more].png`) และอังกฤษ
  (`richmenu-<oa>[-more]-en.png`) — การ์ดเดิม ตำแหน่งเดิม action เดิม สลับแค่ว่าภาษาไหน
  เป็นตัวใหญ่ แท็บ/ป้าย "แตะเพื่อเริ่ม"/ชื่อแถบแชท เปลี่ยนตามภาษา
- alias ภาษาอังกฤษต่อท้าย `-en` (`chann-<oa>-main-en`, `chann-<oa>-more-en`)
  `richmenu-apply.sh` สร้างทั้ง 4 เมนูต่อ OA และตั้งหน้าหลักภาษาไทยเป็นค่าเริ่มต้น
- แอปพลิเคชัน (`application/chann_app/services/richmenu.py`) ผูกเมนูตามภาษาที่คนเลือก
  ให้ผู้ใช้แต่ละคนหลังลงทะเบียนและเมื่อพิมพ์ "สลับภาษา"
- ไฟล์ใน `out/` ไม่ commit (gitignored) — วาดใหม่ด้วย `python3 scripts/richmenu/generate.py`
