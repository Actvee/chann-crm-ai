# API สำหรับระบบภายนอก (External API) — `/api/ext/v1`

> รอบ 21B (23 ก.ย. 2569) เขียนจากโค้ดจริงที่ deploy:
> `application/chann_app/routers_ext.py` (29 route) และ
> `application/chann_app/auth/api_key.py`. ผู้ใช้หลักคือระบบบัญชี/ERP ของ
> ลูกค้า ไม่ใช่มนุษย์ — ยังไม่มี webhook ขาออก (ดู "สิ่งที่ยังไม่มี" ด้านล่าง)
> Interactive docs (ไม่ต้อง login): `<application>/api/ext/v1/docs`

## 1. ขอ key

Key ออกได้จาก**เจ้าของร้านเท่านั้น** ที่แดชบอร์ด **จัดการร้าน > API**
(พนักงานที่มีสิทธิ์ตั้งค่าร้านแต่ไม่ใช่เจ้าของ เปิด URL ตรงจะเห็นข้อความ
"สำหรับเจ้าของร้านเท่านั้น" — เมนูนี้ไม่ปรากฏใน rail ให้เลย) กด "สร้าง key"
ตั้งชื่อ key แล้วระบบโชว์ key เต็ม (`chann_live_…`) **ครั้งเดียว** พร้อมปุ่ม
คัดลอก — ถ้าทำหาย ต้องสร้างใหม่แล้วเพิกถอนอันเดิม รายการหลังจากนั้นแสดงแค่
`name`, `key_prefix` (เช่น `chann_live_ab12…`), created date และ
last-used — ไม่มีทางเห็น key เต็มอีก การเพิกถอนเป็นปุ่มแดงแยกต่างหาก +
ยืนยัน กดแล้ว key นั้นตอบ 401 ทันทีในคำขอถัดไป

ฝั่งแชท (Sale OA เท่านั้น) ทำได้แค่ *อ่านรายการ* กับ *เพิกถอน* — `สร้าง
API key` ทางแชทตอบกลับเป็นปุ่มเปิดหน้าจอเสมอ ไม่เคยสร้างให้ในแชท เพราะ
secret ห้ามอยู่ใน LINE thread

## 2. การยืนยันตัว

ทุกคำขอส่ง header:

```
Authorization: Bearer chann_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Key ถูก hash ด้วย SHA-256 ก่อนเทียบกับฐานข้อมูล (ไม่เก็บ plaintext) การ
เพิกถอนมีผลทันทีเพราะทุกคำขอ resolve key ใหม่ทุกครั้ง ไม่มีการ cache
สิทธิ์ของ key เท่ากับสิทธิ์เต็ม (admin) ของบัญชีเจ้าของร้านในทรัพยากรที่
API เปิดให้ (ดูตารางด้านล่าง) — **ไม่มี scope ต่อ key ในรอบนี้**

## 3. รูปแบบคำตอบ / ข้อผิดพลาด

รายการ (list) ทุกชนิดตอบ:

```json
{"items": [...], "total": 37, "limit": 50, "offset": 0}
```

พร้อม header `X-Total-Count` เท่ากับ `total` เสมอ (เผื่อ client ที่อ่าน
header อย่างเดียว) `limit` สูงสุด 200 ค่าเริ่มต้น 50, `offset` เริ่มต้น 0

ข้อผิดพลาดเป็นรูปเดียวกันทุกที่:

```json
{"error": {"code": "not_found", "message": "customer not found"}}
```

| HTTP | `code` | ความหมาย |
|---|---|---|
| 401 | `missing_or_malformed_key` | ไม่มี header หรือรูปแบบ key ผิด |
| 401 | `unknown_or_revoked_key` | key ไม่รู้จักหรือถูกเพิกถอนแล้ว |
| 429 | `rate_limited` | เกิน 600 คำขอ/นาที/key — มี header `Retry-After` (วินาที) |
| 423 | `tenant_suspended` | ร้านถูกระงับ/ลบแบบ soft — คำขอที่ไม่ใช่ GET ถูกปฏิเสธ (อ่านได้ปกติ) |
| 403 | `forbidden` | key นี้ไม่มี permission key ที่ route ต้องการ |
| 403 | `plan_required` | แพ็กเกจของร้านไม่มี API ภายนอก (มีตั้งแต่ Enterprise ขึ้นไป) — ทุก route รวม `/me` · key ยังอยู่ ใช้ได้อีกเมื่อร้านอัปเกรด |
| 404 | `not_found` | ไม่พบเรคอร์ด — รวมถึง path ที่ไม่มีอยู่จริง |
| 405 | `method_not_allowed` | path ถูกแต่ method ผิด (เช่น `DELETE /customers`) |
| 409 | `conflict` | สถานะขัดกัน (ทั่วไป) — บาง route ตอบ `code` เจาะจงกว่านี้ เช่น `invoice_state` |
| 422 | `validation_error` | body/query ไม่ผ่าน validation |
| 422 | `payment_invalid` | จำนวนเงินที่จ่ายไม่ถูกต้อง (เช่น เกินยอดคงเหลือ) |
| 409 | `invoice_state` | ใบแจ้งหนี้อยู่ในสถานะที่ทำสิ่งนี้ไม่ได้ (เช่น ยังไม่ผูกสินค้า) |
| 404 | `not_issued` | ยังไม่เคยออกเอกสาร (PDF) ให้เรคอร์ดนี้ |

โค้ดในตารางนี้คือชุดที่ตั้งใจให้ ERP ฝั่งนอก branch ตาม — โค้ดอื่นที่ไม่อยู่
ในตาราง (เช่น `bad_request`, `upstream_error`, `unavailable`) เป็น fallback
ทั่วไปตาม HTTP status ให้ตีความแบบ generic ได้

## 4. การแบ่งหน้า

ทุก list รับ `limit` (1–200, ค่าเริ่มต้น 50) และ `offset` (≥0) ตอบ
`{"items","total","limit","offset"}` + header `X-Total-Count`

## 5. การ sync ด้วย `updated_since`

รองรับเฉพาะ **ลูกค้า, ดีล, งานซ่อม (tickets), ใบแจ้งหนี้** เท่านั้น —
ใบเสนอราคา สินค้า ประกัน **ไม่มี** พารามิเตอร์นี้

**เขตเวลา:** ค่าที่**ไม่มี offset** (เช่น `updated_since=2026-09-23T00:00:00`)
ถูกอ่านเป็น **UTC** เสมอ ไม่ใช่เวลาท้องถิ่นของเครื่องที่เรียก ถ้า ERP ทำงานที่
เวลาไทย (UTC+7) ให้ส่ง offset มาด้วยตรง ๆ (`2026-09-23T00:00:00+07:00`) หรือ
แปลงเป็น UTC ก่อนส่ง (`2026-09-22T17:00:00Z`) — ไม่งั้นจะอ่านซ้ำหรือข้ามแถว
เป็นเวลา 7 ชั่วโมงทุกรอบ

วิธี sync ที่แนะนำ: เก็บ timestamp ล่าสุดที่ sync สำเร็จไว้ฝั่ง ERP แล้ว
poll เป็นรอบ (เช่นทุก 5–15 นาที) ด้วย `updated_since=<timestamp ล่าสุด>`
โดย**ทับซ้อนย้อนหลัง 1 นาที** (ส่ง timestamp ลบ 60 วินาทีจากที่บันทึกไว้จริง)
กัน race ระหว่างคำขอกับการเขียนที่เพิ่งเกิดในนาทีเดียวกัน แล้วอัปเดต
timestamp ที่บันทึกไว้เป็นเวลาที่เริ่มคำขอรอบนี้ (ไม่ใช่เวลาที่ตอบกลับมา)

## 6. Rate limit

600 คำขอ/นาที/key นับแบบ fixed window (นาทีปฏิทิน UTC) ทุกคำตอบมี header
`X-RateLimit-Limit: 600` และ `X-RateLimit-Remaining: <n>` เกินโควตาตอบ
429 `rate_limited` พร้อม `Retry-After` (วินาทีจนถึงนาทีถัดไป) **ถ้า Redis
ล่ม การนับจะไม่ทำงาน — คำขอผ่านหมดโดยไม่จำกัด (ไม่ใช่ปฏิเสธ 500)** เพราะ
ตัวนับนี้เป็นการป้องกัน client ที่ยิงรัว ไม่ใช่กลไก authority

คำตอบ **429 มี `X-RateLimit-Limit` และ `X-RateLimit-Remaining: 0` ด้วย** —
คำตอบที่ควรบอกขนาดหน้าต่างมากที่สุดเคยเป็นคำตอบเดียวที่ไม่มี header นี้
(รอบ 21B review I2) · ส่วนคำตอบ **401 ไม่มี header `X-RateLimit-*` เลย**
(ทั้ง `missing_or_malformed_key` และ `unknown_or_revoked_key`) เพราะยังไม่รู้ว่า
เป็น key ดวงไหน จึงไม่มีหน้าต่างให้รายงาน — อย่าเขียน client ให้พึ่ง header นี้
ในการตัดสินว่าโดนจำกัดหรือไม่ ให้ดู HTTP status

## 7. ตารางทรัพยากร (ทุก route ใน `routers_ext.py`)

ไม่มี CORS (ออกแบบให้เรียกจาก server ต่อ server) ทรัพยากรที่**ไม่มี**ใน
API นี้: platform, member, role, setting, chat session, signature, PDPA,
guide, approval, service report — เจตนา ไม่ใช่ตกหล่น

| Method | Path | Permission | Body / query |
|---|---|---|---|
| GET | `/me` | *(ไม่ต้องมี permission key ใด ๆ — key ทุกดวงเรียกได้)* | — |
| GET | `/customers` | `customer.read` | `q`, `stage`, `updated_since`, `limit`, `offset` |
| GET | `/customers/{customer_id}` | `customer.read` | — |
| POST | `/customers` | `customer.create` | `first_name`*, `last_name`, `phone`, `email`, `address`, `notes`, `stage` (`"lead"` \| `"contact"`, default `"contact"`) |
| PATCH | `/customers/{customer_id}` | `customer.update` | `first_name`, `last_name`, `phone`, `email`, `address`, `notes` (ทุกช่อง optional, ส่งเฉพาะที่จะแก้) |
| GET | `/deals` | `deal.read` | `q`, `stage`, `updated_since`, `limit`, `offset` |
| GET | `/deals/{deal_id}` | `deal.read` | — (ตอบพร้อม `items` = รายการสินค้าของดีล) |
| POST | `/deals` | `deal.create` | `customer_id`*, `amount`, `currency`, `expected_close_date`, `notes` |
| PATCH | `/deals/{deal_id}` | `deal.update` | `amount`, `currency`, `expected_close_date`, `notes` |
| POST | `/deals/{deal_id}/stage` | `deal.update` | `stage`* (`new`\|`proposed`\|`won`\|`lost`), `lost_reason` (optional — **แนะนำ**ให้ส่งเมื่อ `stage="lost"` แต่ไม่บังคับ ทั้งแชทและจอก็ไม่บังคับเช่นกัน) |
| GET | `/quotes` | `quote.read` | `deal_id`, `status`, `q`, `limit`, `offset` *(ไม่มี `updated_since`)* |
| GET | `/quotes/{quote_id}` | `quote.read` | — (ตอบ `quote`, `deal`, `customer`, `items`) |
| GET | `/quotes/{quote_id}/pdf` | `quote.read` | — (ตอบ `{"url","expires_at"}`) |
| GET | `/invoices` | `invoice.read` | `status`, `deal_id`, `quote_id`, `overdue`, `updated_since`, `limit`, `offset` |
| GET | `/invoices/{invoice_id}` | `invoice.read` | — |
| POST | `/invoices` | `invoice.create` (+ `invoice.update` ถ้า `issue: true`) | `deal_id`, `quote_id`, `note`, `issue` (bool, default `false`) |
| POST | `/invoices/{invoice_id}/payments` | `invoice.update` | `amount` **หรือ** `full: true`, `method`, `reference`, `note`, `paid_at` |
| GET | `/invoices/{invoice_id}/pdf` | `invoice.read` | — |
| GET | `/invoices/{invoice_id}/receipt-pdf` | `invoice.read` | — |
| GET | `/tickets` | `ticket.read` | `status`, `customer_id`, `q`, `updated_since`, `limit`, `offset` |
| GET | `/tickets/{ticket_id}` | `ticket.read` | — |
| POST | `/tickets` | `ticket.create` | `customer_id`, `problem`*, `address`, `appointment_at` |
| GET | `/warranties` | `warranty.read` | `q`, `status`, `limit`, `offset` *(ไม่มี `updated_since`)* |
| GET | `/warranties/{warranty_id}` | `warranty.read` | — *(สแกนลิสต์ ดูข้อจำกัดข้อ 9)* |
| POST | `/warranties` | `warranty.create` | `serial_number`*, `product_id`, `customer_id`, `purchase_date`, `warranty_months` |
| GET | `/products` | `product.read` หรือ `product.manage` | `q`, `category`, `include_archived`, `limit`, `offset` |
| GET | `/products/{product_id}` | `product.read` หรือ `product.manage` | — *(สแกนลิสต์ ดูข้อจำกัดข้อ 9)* |
| POST | `/products` | `product.manage` | `name`*, `product_id`, `sku`, `category`, `unit_price`, `description`, `warranty_months` — **ถ้ารหัสซ้ำกับสินค้าที่มีอยู่แล้วตอบ 409 `conflict`** (ดูใต้ตาราง) |
| PATCH | `/products/{product_id}` | `product.manage` | `name`, `sku`, `category`, `unit_price`, `description`, `warranty_months` |

`*` = ต้องส่ง ที่เหลือ optional รหัสที่มนุษย์อ่านได้ (`C-2026-0001`,
`D-…`, `Q-…`, `INV-…`) และ uuid ใช้แทนกันได้ในทุก `{id}` path

**`POST /products` สร้างอย่างเดียว ไม่ทับของเดิม** — รหัสสินค้าคือ `product_id`
ที่ส่งมา (ถ้าไม่ส่งใช้ `sku` ถ้าไม่มีทั้งคู่ระบบสร้างให้) ถ้ารหัสนั้นมีสินค้าอยู่แล้ว
ตอบ **409 `conflict`** พร้อมข้อความบอกรหัสที่ชนและให้ไปใช้
`PATCH /products/{product_id}` แทน (รอบ 21B review I3: ของเดิมเป็น upsert เงียบ ๆ
แล้วตอบ 201 — ERP ที่ยิงแคตตาล็อกซ้ำจึงเขียนทับชื่อและราคาโดยไม่มีใครรู้)
การแก้ไขสินค้าใช้ `PATCH` เสมอ

`GET /me` ตอบ:

```json
{
  "shop": {"license_id": "...", "license_code": null, "company_name": "ร้านทดสอบ", "status": "active"},
  "key": {"id": "...", "name": "ระบบบัญชี", "key_prefix": "chann_live_ab12"},
  "permissions": ["customer.read", "customer.create", "..."]
}
```

`shop.license_code` เป็น `null` เสมอในตอนนี้ — ยังไม่มี call ฝั่ง Data ที่คืน
license code ระดับ tenant มาให้ ห้ามสัญญากับ ERP ว่าจะได้ค่านี้

## 8. ตัวอย่าง curl

**`/me`**

```bash
curl -sS -H "Authorization: Bearer chann_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  https://<application-url>/api/ext/v1/me
```

**ลูกค้าที่แก้ไขหลังเวลาหนึ่ง**

```bash
curl -sS -H "Authorization: Bearer chann_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  "https://<application-url>/api/ext/v1/customers?updated_since=2026-09-23T09:00:00Z&limit=1"
```

**ออกใบแจ้งหนี้จากดีลแล้วออกเอกสารทันที**

```bash
curl -sS -X POST -H "Authorization: Bearer chann_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"deal_id": "D-2026-0012", "issue": true}' \
  https://<application-url>/api/ext/v1/invoices
```

(ต้องมี **ทั้ง** `invoice.create` **และ** `invoice.update` — ขาดอย่างใด
อย่างหนึ่งตอบ 403 `forbidden` ก่อนเขียนอะไรลงระบบ)

**บันทึกรับชำระเต็มจำนวน**

```bash
curl -sS -X POST -H "Authorization: Bearer chann_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"full": true, "method": "transfer"}' \
  https://<application-url>/api/ext/v1/invoices/<invoice_id>/payments
```

## 9. ข้อจำกัดที่รู้อยู่แล้ว (ตั้งใจ ไม่ใช่บั๊ก)

- **ลิงก์ PDF (`/quotes/{id}/pdf`, `/invoices/{id}/pdf`, `/invoices/{id}/receipt-pdf`)**
  เป็นลิงก์เซ็นชื่อ (signed) **หมดอายุใน 7 วัน** หลังขอ ต้องเรียก endpoint
  ใหม่เพื่อได้ลิงก์ใหม่ ไม่ควรเก็บลิงก์ไว้ใช้ระยะยาว
- **`GET /warranties/{id}` และ `GET /products/{id}`** ไม่มี query ฝั่ง Data
  ที่ดึงรายการเดียวโดยตรง — ทั้งสอง route จึงดึงลิสต์ (สูงสุด 1,000 แถว)
  แล้วหาแถวที่ id ตรงกัน ถ้าร้านมีประกัน/สินค้าเกิน 1,000 แถว รายการที่
  เกินจะหาไม่เจอผ่าน endpoint นี้ (รู้อยู่แล้ว ไม่ใช่ scope ของรอบนี้)
- ลูกค้าที่สร้างผ่าน API คือ **customer** จริง ไม่ใช่ lead ที่รอยืนยัน —
  ค่าเริ่มต้นของ `stage` คือ `"contact"` (ส่ง `"lead"` ถ้าต้องการให้เป็นสถานะ
  รอยืนยันแทน)
- `updated_since` มีเฉพาะลูกค้า/ดีล/งานซ่อม/ใบแจ้งหนี้ (ข้อ 5)

## 10. สิ่งที่ยังไม่มี (ตั้งใจ)

- **Webhook ขาออก** — ยังไม่มีการ push เหตุการณ์ออกจากระบบ ต้อง poll ด้วย
  `updated_since` เท่านั้น (แผนไว้รอบถัดไป)
- **`Idempotency-Key`** — ยิง `POST` ซ้ำ (เช่น timeout แล้ว retry) อาจสร้าง
  เรคอร์ดซ้ำ ฝั่งเรียกต้องกันเองในตอนนี้
- **Key หมดอายุ (expiry)** — key มีอายุไม่จำกัดจนกว่าจะถูกเพิกถอนเอง
- **Scope ต่อ key** — ทุก key มีสิทธิ์เท่ากับสิทธิ์เต็ม (admin) ของบัญชี
  ไม่สามารถจำกัดเฉพาะบางทรัพยากรต่อ key ได้
- **IP allowlist** — ไม่มีการจำกัดว่าคำขอต้องมาจาก IP ใด

## 11. Security posture (โครงการนี้จงใจใช้ reduced-security)

Key ถูก hash ที่ฐานข้อมูลและแสดงเต็มเพียงครั้งเดียว การเพิกถอนมีผลทันที
เพราะทุกคำขอ resolve key ใหม่เสมอ ร้านที่ถูกระงับ/ลบแบบ soft ใช้ key เขียน
อะไรไม่ได้ (อ่านได้อย่างเดียว) จำกัดอัตราต่อ key ทุกการเขียนถูกบันทึกลง
audit log แบบเดียวกับที่ route ฝั่ง Data ทำอยู่แล้ว (`actor_id="api:<key
id>"` — คำนำหน้า `api:` คือสิ่งที่แยก key ออกจากคนในบันทึก) พื้นผิว API
นี้เข้าถึง platform, member, role หรือ setting route ไม่ได้เลย ไม่มี CORS
(ออกแบบให้เรียกจาก server ต่อ server) ส่งผ่าน Cloud Run HTTPS อย่างไรก็ตาม
**นี่ไม่ใช่โมเดล "secure-production"**: ไม่มีตารางหมุนเวียน key (key
rotation), ไม่มี IP allowlist, ไม่มี scope ต่อ key — ต้องระบุข้อจำกัดนี้ให้
ชัดเจนใน release notes ทุกครั้ง
