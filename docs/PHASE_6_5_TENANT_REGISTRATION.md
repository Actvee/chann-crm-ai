## Phase 6.5 — Tenant Registration & Onboarding

> **ที่มา:** ช่องว่างของสเปคเดิม — ไม่มี phase ไหนครอบคลุมการสร้าง license หรือ
> เข้าร่วม license เลย บรรทัด 973 อ้างว่าเป็น "Phase 8" แต่ Phase 8 คือ profile
> ผลคือการเพิ่มสมาชิกต้อง `INSERT` ด้วย SQL มือเท่านั้น

### 6.5.1 เป้าหมาย

ปิดช่องว่างระหว่าง "คนแปลกหน้าทัก LINE มา" กับ "เป็นสมาชิกที่ใช้งานระบบได้"
โดยแยกเป็น 3 กลไกที่ไม่ปนกัน:

| กลไก | ใครใช้ | รหัส | ผลลัพธ์ |
|---|---|---|---|
| สร้าง license | คนที่อยากเปิดบริษัทใหม่ | — | เป็น `owner` ของ license ใหม่ |
| Invite code | Owner/Admin ชวนเพื่อนร่วมงาน | มีอายุ + จำกัดจำนวนใช้ | เป็น `license_members` |
| Company code | ลูกค้าปลายทางผูกกับร้าน | 8 ตัวอักษร ถาวร | ผูก identity ↔ license (**ไม่ใช่** member) |

ข้อสามสำคัญ: **ลูกค้าปลายทางไม่ใช่สมาชิกของ tenant** เขาแค่ต้องระบุได้ว่ากำลัง
คุยกับร้านไหน และให้ระบบจำร้านที่เคยติดต่อไว้เป็นตัวเลือกครั้งต่อไป การทำให้
ลูกค้าเป็น `license_members` จะทำให้เขาได้ permission ของ tenant ติดมาด้วย
ซึ่งผิดทั้งด้านความปลอดภัยและด้าน billing

### 6.5.2 Tier impact

| Tier | งาน |
|---|---|
| Presentation | หน้าเลือกร้าน (LIFF, ทางเลือก), หน้าจัดการ invite code ใน Dashboard |
| Application | registration flow ผ่านแชท (slot-filling), invite redeem, public shop search |
| Data | `license_invites` + `customer_license_links` CRUD, company_code lookup |
| Database | 2 ตารางใหม่ + 3 column ใน `licenses` |

### 6.5.3 Schema

```sql
ALTER TABLE licenses
  ADD COLUMN company_code VARCHAR(8) UNIQUE NOT NULL,
  ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'trial',  -- trial|active|suspended
  ADD COLUMN trial_expires_at TIMESTAMPTZ;

-- CHECK: status IN ('trial','active','suspended')

CREATE TABLE license_invites (
  id UUID PRIMARY KEY,
  license_id UUID NOT NULL REFERENCES licenses(id) ON DELETE RESTRICT,
  invite_code VARCHAR(16) UNIQUE NOT NULL,
  role VARCHAR(64) NOT NULL,          -- role ที่จะได้ตอน redeem
  max_uses INTEGER NOT NULL DEFAULT 1,
  used_count INTEGER NOT NULL DEFAULT 0,
  expires_at TIMESTAMPTZ,             -- NULL = ไม่หมดอายุ
  created_by_member_id UUID REFERENCES license_members(id) ON DELETE RESTRICT,
  revoked_at TIMESTAMPTZ,             -- NULL = ยังใช้ได้
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- CHECK: used_count <= max_uses
-- INDEX partial: (license_id) WHERE revoked_at IS NULL

CREATE TABLE customer_license_links (
  id UUID PRIMARY KEY,
  chann_uid VARCHAR(32) NOT NULL REFERENCES chann_identities(chann_uid) ON DELETE RESTRICT,
  license_id UUID NOT NULL REFERENCES licenses(id) ON DELETE RESTRICT,
  linked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (chann_uid, license_id)
);
```

**`company_code` ต้องอ่านออกเสียงและพิมพ์ได้** — ลูกค้าจะพิมพ์มันในแชท
ตัดอักขระที่สับสน (`0/O`, `1/I/l`) ออกจากชุดอักขระที่ใช้สุ่ม

### 6.5.4 Anti-abuse — 1 LINE = 1 บริษัท

Self-service เปิดช่องให้สร้าง tenant ขยะได้ไม่จำกัด กติกาที่ล็อกไว้:

- **1 `chann_uid` เป็น owner ได้มากสุด 1 license** — เช็คตอนสร้าง ไม่ใช่ตอน redeem
  (เป็น member ของหลาย license ได้ตามปกติ ข้อจำกัดนี้คุมเฉพาะการ *สร้าง*)
- license ใหม่เริ่มที่ `status='trial'`, `trial_expires_at = now() + 30 วัน`
- ข้อจำกัดนี้บังคับที่ระดับ **application logic + partial unique index** ไม่ใช่
  แค่ใน code path เดียว เพราะ webhook อาจถูกยิงซ้ำพร้อมกัน

```sql
-- กัน race: สองข้อความพร้อมกันต้องสร้างได้แค่ license เดียว
CREATE UNIQUE INDEX ux_one_owned_license_per_identity
  ON license_members (chann_uid)
  WHERE role = 'owner';
```

> ⚠️ index ข้างบนต้องทบทวนก่อนใช้จริง — มันห้าม 1 identity เป็น owner ของหลาย
> license **ตลอดกาล** รวมถึงกรณีรับโอน ownership มาจากบริษัทอื่น (Phase 2 มี
> ownership transfer อยู่แล้ว) ถ้ายอมให้รับโอนได้ ต้องใช้ column แยก เช่น
> `licenses.created_by_chann_uid` + unique index บน column นั้นแทน
> **ตัดสินใจตอนเริ่มเขียนจริง**

### 6.5.5 Trial หมดอายุแล้วเกิดอะไร

Phase 6.5 **ไม่ทำ billing** แค่วางสถานะไว้ให้ Phase 17.5 ต่อยอด:

- มี cron/sweep แจ้งเตือนล่วงหน้า (ใช้ `notifications` จาก Phase 6)
- หมดอายุ → `status='suspended'`
- `suspended` หมายถึง **read-only** ไม่ใช่ลบข้อมูล — ข้อมูลลูกค้าต้องไม่หายเพราะ
  ไม่จ่ายเงิน และการ reactivate ต้องได้ข้อมูลเดิมกลับครบ
- การบังคับ read-only จริงทำที่ permission layer: `status != 'active'` และ
  `status != 'trial'` → ตัด permission ที่เป็น write ออกจาก context

### 6.5.6 Flow (chat-first — Principle #1)

```
ทัก Sales OA ครั้งแรก → resolution = NONE
    → บอทเสนอ 2 ทาง: "เปิดบริษัทใหม่" | "มีรหัสเชิญ"

[เปิดบริษัทใหม่]
    → slot-filling: ชื่อบริษัท (บังคับ), เบอร์ติดต่อ (ไม่บังคับ)
    → เช็ค 1-LINE-1-บริษัท
    → สร้าง license (status=trial, trial_expires_at=+30d, company_code สุ่ม)
    → seed default role templates ทั้ง 4 (owner/admin/member/cs)
    → สร้าง license_members: คนนี้ = owner
    → audit log: entity=license action=create
    → ตอบกลับพร้อม company_code ให้เอาไปบอกลูกค้า

[มีรหัสเชิญ]
    → slot-filling: ขอรหัส
    → ตรวจ: มีจริง / ยังไม่ revoke / ยังไม่หมดอายุ / used_count < max_uses
    → สร้าง license_members ตาม role ที่รหัสระบุ + used_count += 1
    → audit log: entity=license_member action=create

[ลูกค้า — Customer OA]
    → พิมพ์ company_code หรือค้นชื่อร้าน
    → public search คืนเฉพาะ company_name + company_code
    → สร้าง customer_license_links
    → ครั้งต่อไป: มีร้านที่เคยผูกให้เลือก ไม่ต้องพิมพ์รหัสซ้ำ
```

### 6.5.7 Public shop search — ข้อยกเว้นของ TenantScope

`GET /internal/v1/public/shops?q=...` เป็น endpoint เดียวในระบบที่**ตั้งใจ**ไม่ผูก
กับ tenant ไหนเลย เพราะลูกค้าที่ยังไม่ผูกร้านไม่มี tenant ให้ scope

ข้อบังคับ:
- คืน **เฉพาะ** `company_name` + `company_code` เท่านั้น ห้ามคืน id, จำนวนสมาชิก,
  สถานะ, หรืออะไรที่บอกขนาด/สุขภาพธุรกิจของ tenant
- คืนเฉพาะ license ที่ `status != 'suspended'`
- ต้อง rate-limit — ไม่งั้นเป็นช่องให้ enumerate company_code ทั้งระบบ
- มี test ยืนยันว่า response ไม่มี field อื่นหลุดออกมา

### 6.5.8 Mandatory automated tests

```
test_license_self_registration:
  - สร้าง license ใหม่ → เป็น owner, status=trial, trial_expires_at ≈ +30d
  - company_code ถูกสร้าง ไม่ซ้ำกับใคร
  - default role templates ครบ 4 role
  - identity เดิมสร้าง license ที่สอง → ถูกปฏิเสธ
  - ยิงพร้อมกัน 2 ครั้ง → ได้ license เดียว (race)

test_invite_redeem:
  - รหัสถูกต้อง → เป็น member ตาม role ที่ระบุ
  - รหัสหมดอายุ → ปฏิเสธ
  - รหัสถูก revoke → ปฏิเสธ
  - used_count ถึง max_uses → ปฏิเสธ
  - รหัสเดิม redeem ซ้ำโดยคนเดิม → ไม่สร้าง member ซ้ำ, used_count ไม่เพิ่ม
  - รหัสของ license อื่น → ไม่ทำให้เข้า license ผิด

test_customer_license_link:
  - company_code ถูกต้อง → ผูกสำเร็จ
  - ผูกซ้ำ → idempotent ไม่สร้างแถวซ้ำ
  - ลูกค้าผูกหลายร้าน → my-shops คืนครบทุกร้าน
  - ลูกค้าที่ผูกแล้ว **ไม่** ได้ permission ของ tenant นั้น

test_public_shop_search:
  - ค้นเจอด้วยชื่อบางส่วน
  - response มีแค่ company_name + company_code (ยืนยันทุก key)
  - license ที่ suspended ไม่โผล่ในผลค้นหา

test_trial_expiry:
  - trial หมดอายุ → status=suspended
  - suspended → permission ที่เป็น write ถูกตัดออกจาก context
  - suspended → ข้อมูลเดิมยังอ่านได้ครบ ไม่มีอะไรถูกลบ
```

### 6.5.9 Acceptance criteria

- [ ] สร้างบริษัทใหม่ผ่านแชทได้ ไม่ต้องแตะ SQL
- [ ] 1 LINE account สร้างได้บริษัทเดียว (พิสูจน์ด้วย concurrency test)
- [ ] Owner สร้าง/เพิกถอน invite code ได้
- [ ] เข้าร่วมด้วย invite code ผ่านแชทได้
- [ ] ลูกค้าผูกร้านด้วย company_code หรือค้นชื่อได้ และจำร้านไว้ครั้งต่อไป
- [ ] ลูกค้าที่ผูกร้านไม่ได้ permission ของ tenant
- [ ] public search ไม่รั่วข้อมูลนอกเหนือ company_name + company_code
- [ ] ทุก test PASS
- [ ] runtime: LINE account ใหม่ทัก → เปิดบริษัท → ได้ company_code → เพื่อนใช้
      invite เข้าร่วม → ทั้งหมดผ่านแชท ไม่แตะ SQL สักคำสั่ง

### 6.5.10 Dependencies

- **depends-on:** Phase 1 (identity), 2 (role templates + permission), 6 (chat engine)
- **blocks:** Phase 8 (ต้องเป็นสมาชิกก่อนถึงแก้ profile ได้), และในทางปฏิบัติคือ
  ทุก phase ที่ต้องการ tenant จริงที่ไม่ได้สร้างด้วย SQL มือ
- **เกี่ยวข้องกับ:** Phase 17.5 (Billing) — 6.5 วางแค่ `status` + `trial_expires_at`
  ไม่ออกแบบ billing; 17.5 ต่อยอดจากตรงนี้

### 6.5.11 ของเดิมที่ยกมาใช้ได้ (โปรเจกต์ Chann CRM AI เวอร์ชันก่อน)

- `company_code` 8-char สร้างอัตโนมัติทุก license — แนวคิดเดียวกัน ยกมาตรงๆ
- `verify-company-code` แบบไม่ต้อง auth (เปิดเผยแค่ชื่อสาธารณะ) — ตรงกับ 6.5.7
- `my-companies` สำหรับ dropdown เลือกร้านเมื่อเป็นลูกค้าหลายที่ — ตรงกับ
  `customer_license_links`
- invite token สำหรับช่าง + regenerate — ขยายเป็น `license_invites` ที่รองรับ
  ทุก role ไม่ใช่เฉพาะช่าง

**ที่ยกมาไม่ได้:** ของเดิม license ถูกสร้างโดย Chann admin เท่านั้น
(`POST /api/admin/licenses` + `x-admin-secret`) — self-service เป็นของใหม่ทั้งหมด
ไม่มีของเดิมให้อ้างอิง
