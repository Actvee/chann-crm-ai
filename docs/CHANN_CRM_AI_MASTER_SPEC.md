# Chann CRM AI — Implementation Context

> **AUTHORITATIVE PRODUCT SOURCE OF TRUTH**
>
> เอกสารนี้รวม SCOPE_LOCKED, Architecture Decisions, Requirements, Phase 16.5/17.5 addendum และ Permission Keys audit ฉบับแก้ไขไว้แล้วในไฟล์เดียว
> ไม่ต้องหาไฟล์ต้นฉบับเหล่านั้นแยก และห้าม merge/addendum ซ้ำ
>
> Implementation ใหม่เป็น **greenfield application บน existing GCP infrastructure**: ไม่ต้อง preserve source code, API, schema, seed หรือ business compatibility จาก Chann1 CRM เดิม
> Logical architecture ของ Chann CRM AI ยังคงล็อกเป็น **Presentation -> Application -> Data -> Database**; supporting services เช่น Redis, GCS, Zoho Catalyst SmartBrowz, LINE, OpenRouter และ payment provider ไม่ถือเป็น tier เพิ่ม
>
> ก่อนเริ่ม Phase 1 ให้อ่าน `00_START_HERE.md` และ `00_INFRASTRUCTURE_HANDOFF.md` และรัน `scripts/infra-preflight.sh`

---

นี่คือเอกสารทั้งหมดครับ รวมทุกการตัดสินใจที่เราคุยกันมา

---

# 1. SCOPE_LOCKED.md

```markdown
# Chann CRM AI — Locked Scope v1.0

**วันที่ lock:** 14 สิงหาคม 2026
**สถานะ:** 🔒 LOCKED v1.0 — พร้อมเริ่ม Phase 1
**พื้นฐาน:** Chann1 reference architecture + scope v7 + คำตอบ A1-A8 + B1-B7 + C1-C5 + D1-D2 + E1-E3

---

## 1. ธรรมชาติของระบบ

Multi-tenant SaaS CRM + Field Service Platform บน LINE ที่มีโครงสร้างคล้าย marketplace (Lazada-style) สำหรับธุรกิจไทย โดย:

- **Chat คือหน้าด่านหลัก** — ทุก role คุยกับ AI เป็นภาษาธรรมชาติ ฟอร์ม/LIFF เป็นทางเลือกเสริม
- **LINE OA 3 ตัวแยกกัน (platform-level):**
  - Customer OA — ลูกค้า: ดูสินค้า (storefront) → เลือกร้าน → แชท → แจ้งซ่อม → ลงทะเบียนสินค้า → ทำแบบประเมิน
  - Sales OA — Sales/CS/Owner/Admin: คุยกับ AI ทำงานประจำ + Rich Menu → เข้า Dashboard ตอบลูกค้า
  - Technician OA — ช่าง: คุยกับ AI ทำงานประจำ + Rich Menu → เข้า Dashboard
- **Platform Owner (ฝั่ง Chai)** ดูแล SaaS platform มี Platform Admin Dashboard สำหรับ tenant management + break-glass

---

## 2. หลักการบังคับ (Cross-cutting) — 22 ข้อ

| # | หลักการ | ที่มา |
|---|---|---|
| 1 | Chat-first — ทุก role คุยกับ AI เป็นภาษาธรรมชาติ ฟอร์ม/Dashboard เป็นทางเลือก | v7 |
| 2 | Prompt-driven config — AI แปลง policy → JSON → บันทึก → runtime อ่านค่านั้น (ยกเว้น Assignment Engine ที่เป็น rule engine deterministic) | v7 + A2 |
| 3 | AI ไม่แตะ DB — แปลง intent → JSON เท่านั้น ทุก action ผ่าน domain service ที่ validate + permission check | v7 |
| 4 | Multi-tenant isolation เข้มงวด + cross-tenant lookup ต้อง audit ทุกครั้ง | v7 + A6 |
| 5 | Auth 2 ชั้น: (1) LIFF ID Token สำหรับ LINE user (2) username/password สำหรับ Platform Admin | v7 + A3 + C4 + D2 |
| 6 | owner_member_id ทุก record — ใครรับผิดชอบ record นั้น | v7 |
| 7 | i18n TH/EN ทุกหน้า + ภาษาที่บอทตอบ (ค่าที่เลือกส่งเข้า AI prompt) | v7 |
| 8 | Slot-filling ทุก flow — ข้อมูลไม่ครบ AI บอกชัดว่าขาดอะไร | v7 |
| 9 | Reply-to-Entity ทุก channel ทุก entity — reply ข้อความยืนยันเดิม = แก้ไข entity นั้น | v7 |
| 10 | แนะนำสิ่งที่ทำได้แทนเมื่อไม่เข้าใจคำสั่ง — ดึงจาก Permission Matrix ตามสิทธิ์จริง | v7 |
| 11 | Mandatory automated test: multi-tenant isolation + permission gate + assignment race condition | v7 |
| 12 | 4-tier architecture + boundary tests บังคับ (Presentation → Application → Data → Database) | Chann1 |
| 13 | 3 env (DEV/Stage/Prod) + build-once promote-exact-artifact | Chann1 + A4 |
| 14 | Release Manifest ทุก release (version, commit, artifact identity, migration head, evidence) | Chann1 |
| 15 | Environment init order: migration → reference seed → fixture → runtime → acceptance | Chann1 |
| 16 | Cache-aside (Redis) + fail-secure — cache outage ต้องไม่ broaden permission | Chann1 + A7 |
| 17 | Audit emission ต้อง PROVEN ไม่ใช่แค่ schema มีตาราง | Chann1 |
| 18 | Definition of Done = runtime business acceptance ใน target env ไม่ใช่ CI green | Chann1 |
| 19 | LIFF เป็นทางเลือก — ทุก action (ลงทะเบียน, แจ้งซ่อม, ใบรับประกัน, profile) ทำผ่านแชทกับ AI ได้ | C5 |
| 20 | Document Engine = template-once/versioned pattern — ผู้ใช้อัปโหลด DOCX, AI ช่วย analyze/map/compile ตอน authoring เท่านั้น; runtime render deterministic ผ่าน Zoho Catalyst SmartBrowz | C5 + D1 |
| 21 | Notification = dual delivery (LINE push + Dashboard badge) — cross-cutting ทุก phase ใช้ | E3 + คำตอบข้อ 4 |
| 22 | CS role = หลังบาง (ticket, service report, approval, คุยลูกค้าใน Dashboard) แยกจาก Sales ที่เป็นหน้าบาน (customer, deal, quote) | คำตอบข้อ 8 |

---

## 3. สถาปัตยกรรม Tier

```
LINE (3 OA platform-level: Customer/Sales/Technician)
    ↓ webhook + LIFF ID Token
Presentation Tier (Next.js)
    ├── LIFF pages (ทางเลือก — ลงทะเบียน, แจ้งซ่อม, ใบรับประกัน, profile)
    └── Platform Dashboard (Live Chat, CRM, Admin — login แยกตาม role)
    ↓ Application API (/api/v1)
Application Tier (FastAPI)
    ├── LINE Webhook handler (3 OA)
    ├── AI Intent Engine (Qwen — แปลงภาษาไทย → JSON, thinking off)
    ├── Prompt-config Engine (policy → structured config)
    ├── Domain Services (validate + permission check)
    ├── Notification Service (LINE push + Dashboard badge)
    └── Internal HTTP client → Data + external adapters
    ↓
Data Tier
    ├── PostgreSQL
    ├── Redis cache-aside
    └── Authorization scope

Supporting integrations (ไม่ใช่ tier เพิ่ม):
    Application → Zoho Catalyst SmartBrowz (deterministic PDF rendering)
    Application → OpenRouter / LINE / payment provider
    Data → GCS (DOCX source, compiled template, PDF, image, signature)
```

### AI ใน Application Tier — หน้าที่ชัด

| หน้าที่ AI | Model | เมื่อไหร่ | Thinking mode |
|---|---|---|---|
| แปลง intent จากข้อความไทย → JSON | Qwen (qwen3.6-35b-a3b) | ทุกครั้งที่มี chat message | off |
| แปลง policy prompt → structured config | Qwen | ตอน config เท่านั้น | off |
| แปลง ad-hoc report request → query spec JSON | DeepSeek (deepseek-v4-pro) | ตอนขอ report เท่านั้น | on |
| Assignment decision | **ไม่ใช้ AI runtime** — deterministic rule engine | runtime (อ่านจาก config) | — |
| Document template authoring assistant | Qwen | ตอน upload/edit DOCX template เพื่อ analyze/map/compile เท่านั้น | off |

---

## 4. Authentication & Authorization

### Authentication (2 path)

| Path | กลุ่มผู้ใช้ | กลไก |
|---|---|---|
| LINE path | Customer, Sales, CS, Technician, Tenant Owner/Admin | LIFF ID Token ทุกหน้า ไม่มี URL เปล่า |
| Platform Admin path | ฝั่ง Chai (Platform Admin) | username/password (argon2 hash) — เข้าผ่านหน้า web แยก |

### Authorization

- **Chann Identity (global):** `CHN-C-000123` / `CHN-S-000045` / `CHN-T-000012` ผูก LINE user ID เดียวกัน ใช้ข้ามบริษัทได้ แต่ไม่เปิดเผย cross-tenant
- **Custom Roles:** บริษัทสร้าง role อิสระผ่าน prompt → AI แปลงเป็น permission set
  - Owner เป็น role พิเศษ แก้/ลบไม่ได้ มีสิทธิ์เต็ม ต้องมีอย่างน้อย 1 คนเสมอ
  - Role เริ่มต้น 4 อัน (Owner/Admin/Member/CS) เป็น template แก้/เพิ่มทับได้
- **Permission keys:** `customer.read`, `deal.reopen`, `reassign_records`, `view_reports` ฯลฯ — โค้ดเช็คผ่าน permission key เท่านั้น ห้าม hardcode role name
- **Transfer Ownership:** flow 2 ชั้น confirm (Owner เดิม + คนใหม่รับ) + break-glass ผ่าน Platform Admin Dashboard
- **reassign_records:** permission key ควบคุมสิทธิ์โอน record ให้คนอื่นในทีม

### CS vs Sales — บทบาทชัด

| | Sales | CS |
|---|---|---|
| หน้าบาน | customer, deal, quote | — |
| หลังบาน | — | ticket, service report, approval, คุยลูกค้าใน Dashboard |
| รายงาน | มี default (`view_reports`) | ปิด default (เปิดถ้า Owner/Admin อนุญาต) |

---

## 5. Phase ทั้งหมด (20 Phase)

| Phase | ชื่อ | ตารางใหม่ | Deps | MVP |
|---|---|---|---|---|
| 1 | Architecture & Security Foundation | `chann_identities`, `platform_admins`, `licenses`, `license_members` | — | 1 |
| 2 | Permission Matrix & license_settings | `custom_roles`, `role_permissions`, `license_settings` | 1 | 1 |
| 3 | Audit Log | `audit_log` | 1, 2 | 1 |
| 4 | AI Infrastructure (Qwen + DeepSeek config) | — | 1 | 1 |
| 5 | i18n Framework (UI + bot language) | — | 1 | 1 |
| 6 | Chat Foundation + Notification + Follow-up | `line_message_entity_map`, `notifications`, `follow_ups` | 1, 2, 4, 5 | 1 |
| 7 | Master Data & Organization | `products`, `sales_groups`, `sales_group_members`, `technician_teams`, `technician_team_members` | 1, 2 | 1 |
| 7.5 | Warranty | `warranties` | 2, 7, 8 | 1 |
| 8 | Profiles (Tech + Customer, chat-first) | — | 1, 6 | 1 |
| 9 | CRM Core: Lead→Contact→Deal + Storefront (Lazada-style) | `deals`, `deal_products` | 2, 6, 7, 8 | 1 |
| 10 | Quote + Versioned Document Template + PDF (SmartBrowz) | `quotes`, `document_templates`, `document_template_versions`, `generated_documents` | 9 | 1 |
| 11 | Assignment Engine (Deterministic) | `assignment_rules` | 7, 9 | 2 |
| 12 | Ticket Visibility + Dispatch Gate (chat-first) | เพิ่ม column `service_tickets` | 7, 11 | 2 |
| 13 | Field Service Execution (Check-in/out + GPS + Photos + Service Report + Signature) | `service_reports`, `ticket_photos` + shared `document_templates`/versions | 12 | 2 |
| 14 | Approval Workflow + Satisfaction Survey | `approval_workflows`, `approval_steps`, `satisfaction_surveys` | 13 | 2 |
| 15 | Live Chat Marketplace (Customer OA → Dashboard) + SLA | `chat_sessions`, `chat_messages` | 6, 9, 11 | 3 |
| 16 | Cross-company Serial Routing + Display Preferences | `user_display_preferences` | 1, 8, 7.5 | 3 |
| 17 | Ad-hoc AI Report Engine (Sales/CS only) | — | 2, 9, 12 | 3 |
| 18 | Platform Admin Dashboard (tenant mgmt + break-glass + audit) | (ใช้ `platform_admins` จาก Phase 1) | 1, 2, 3 | 3 |
| 19 | Rich Menu (ธีมสี + structure) | — | ทั้งหมด | 3 |
| 20 | Polish & Final i18n + UX | — | ทั้งหมด | 3 |

### MVP grouping

| MVP | Phase | เป้าหมาย |
|---|---|---|
| **MVP 1: Foundation + CRM** | 1-10 | ระบบทำงานได้: auth, chat, CRM, quote, PDF, warranty |
| **MVP 2: Field Service** | 11-14 | ส่วนช่าง + approval + survey |
| **MVP 3: Marketplace + Advanced** | 15-20 | Live chat, cross-company, report, admin, rich menu, polish |

---

## 6. บทบาท OA แต่ละตัว

### Customer OA — ลูกค้า

| กิจกรรม | ผ่านแชท | ผ่าน LIFF | Phase |
|---|---|---|---|
| ดูสินค้า (storefront, Lazada-style cross-tenant) | ✅ | ✅ | 9 |
| เลือกร้าน → แชทกับร้าน | ✅ | — | 15 |
| สนใจสินค้า → สร้าง Lead อัตโนมัติ | ✅ (auto) | — | 9 |
| แจ้งซ่อม (สร้าง ticket) | ✅ | ✅ (ทางเลือก) | 12 |
| ลงทะเบียนสินค้า (warranty) | ✅ | ✅ (ทางเลือก) | 7.5 |
| ดูใบรับประกัน | ✅ | ✅ | 7.5 |
| ลงทะเบียน profile ตัวเอง | ✅ | ✅ (ทางเลือก) | 8 |
| ทำแบบประเมินความพึงพอใจ | ✅ (quick reply) | — | 14 |
| ค้น serial ข้ามบริษัท | ✅ | — | 16 |

### Sales OA — Sales/CS/Owner/Admin

**Mode 1: คุยกับ AI ใน LINE (งานประจำ)**

| กิจกรรม | ใครทำได้ | Phase |
|---|---|---|
| สร้าง/แก้/ดู customer | Sales, CS, Admin, Owner | 8, 9 |
| สร้าง/แก้/ดู deal | Sales, Admin, Owner | 9 |
| สร้าง quote | Sales, Admin, Owner | 10 |
| โอน record ให้คนอื่น (`reassign_records`) | ทุก role (default) | 2 |
| ดู report มาตรฐาน | Sales, CS (ถ้ามี `view_reports`) | 17 |
| ขอ ad-hoc report | Sales, CS (ถ้ามี `view_reports`) | 17 |
| ตั้งค่าผ่าน prompt | Admin, Owner | 2, 11, 14, 15 |
| สร้าง/แก้ role | Admin, Owner (`role.manage`) | 2 |
| เชิญ/ลบสมาชิก | Admin, Owner (`member.manage`) | 2 |
| โอนสิทธิ์ Owner | Owner เท่านั้น | 2 |
| อนุมัติ/ปฏิเสธ | ตาม role/user ที่กำหนด | 14 |
| จัดการ follow-up/reminder | Sales, CS | 6 |

**Mode 2: เข้า Dashboard ผ่าน Rich Menu**

| กิจกรรม | ใครทำได้ | Phase |
|---|---|---|
| ตอบลูกค้า (Live Chat) | Sales, CS | 15 |
| ดู/จัดการ CRM | Sales, CS, Admin, Owner | 9, 10 |
| ดูรายการรออนุมัติ | ผู้อนุมัติ | 14 |
| ดู audit log | Admin, Owner | 3 |
| จัดการ role/permission | Admin, Owner | 2 |
| ดู report/dashboard | Sales, CS, Admin, Owner | 17 |

**สำคัญ:** Sales/CS ไม่ตอบลูกค้าใน LINE ตรง ๆ — ตอบใน Dashboard แล้วระบบ push ไป Customer OA

### Technician OA — ช่าง

**Mode 1: คุยกับ AI ใน LINE**

| กิจกรรม | Phase |
|---|---|
| รับ ticket (self-claim, public) | 12 |
| ดูตารางงานที่ได้รับมอบหมาย | 12 |
| check-in (GPS) | 13 |
| check-out (GPS + ภาพ + service report) | 13 |
| แจ้งสถานะงาน | 12 |
| ลงทะเบียน profile ตัวเอง | 8 |

**Mode 2: เข้า Dashboard ผ่าน Rich Menu**

| กิจกรรม | Phase |
|---|---|
| ดูตารางงาน | 12 |
| ดู ticket ที่ได้รับมอบหมาย | 12 |
| กรอก service report | 13 |

---

## 7. Rich Menu Structure

### Customer OA — หน้าที่ 1: หลัก (🟠 ส้ม)

```
┌─────────────┬─────────────┐
│  🔍 ค้นหา   │  🛒 สินค้า    │
│  สินค้า     │  ทั้งหมด      │
├─────────────┼─────────────┤
│  📱 แจ้ง    │  📋 ใบ       │
│  ซ่อม       │  รับประกัน    │
├─────────────┼─────────────┤
│  💬 คุยกับ   │  👤 โปรไฟล์  │
│  ร้านค้า    │  ของฉัน      │
└─────────────┴─────────────┘
```

### Customer OA — หน้าที่ 2: ประวัติ (🟠 ส้ม)

```
┌─────────────┬─────────────┐
│  📞 งาน     │  🔧 ใบ       │
│  ซ่อมของฉัน  │  รับประกัน   │
│             │  ทั้งหมด      │
├─────────────┼─────────────┤
│  ⭐ ประเมิน │  🗂 คำสั่ง   │
│  ความพึงพอใจ│  ซื้อ/ประวัติ│
├─────────────┼─────────────┤
│  🌐 EN/TH   │  ⬅ กลับ     │
│  สลับภาษา   │  หน้าหลัก    │
└─────────────┴─────────────┘
```

### Sales OA — หน้าที่ 1: งานประจำ (🟢 เขียว)

```
┌─────────────┬─────────────┐
│  📊 Dashboard│  📋 รายการ  │
│  (Live Chat,│  รออนุมัติ   │
│   CRM, Rpt) │             │
├─────────────┼─────────────┤
│  👥 ลูกค้า   │  💼 ดีล      │
│  (Customer) │  (Deal)     │
├─────────────┼─────────────┤
│  📝 ใบเสนอ  │  📈 รายงาน   │
│  ราคา       │             │
└─────────────┴─────────────┘
```

### Sales OA — หน้าที่ 2: จัดการ (🟢 เขียว, Admin/Owner เท่านั้น)

```
┌─────────────┬─────────────┐
│  ⚙ ตั้งค่า   │  👥 ทีม      │
│  (Policy)   │  (Members)  │
├─────────────┼─────────────┤
│  🔐 สิทธิ์    │  📜 Audit   │
│  (Roles)    │  Log        │
├─────────────┼─────────────┤
│  🌐 EN/TH   │  ⬅ กลับ     │
│  สลับภาษา   │  หน้าหลัก    │
└─────────────┴─────────────┘
```

### Technician OA — หน้าที่ 1: งาน (🔵 น้ำเงิน)

```
┌─────────────┬─────────────┐
│  📅 ตาราง   │  🔧 งาน     │
│  งานของฉัน  │  ที่รับ      │
├─────────────┼─────────────┤
│  ✅ Check   │  📝 Service │
│  -in/out    │  Report    │
├─────────────┼─────────────┤
│  📸 ภาพ     │  📋 ประวัติ │
│  หลักฐาน    │  งาน        │
└─────────────┴─────────────┘
```

### Technician OA — หน้าที่ 2: โปรไฟล์ (🔵 น้ำเงิน)

```
┌─────────────┬─────────────┐
│  👤 โปรไฟล์ │  🏢 บริษัท   │
│  ของฉัน     │  ที่สังกัด    │
├─────────────┼─────────────┤
│  ⭐ ประเมิน │  🌐 EN/TH   │
│  (ถ้ามี)    │  สลับภาษา   │
├─────────────┼─────────────┤
│  ⬅ กลับ     │  🏠 หน้า     │
│  หน้าหลัก   │  แรก        │
└─────────────┴─────────────┘
```

---

## 8. Storefront (Lazada-style) — จาก C3

```
ลูกค้าเข้า Customer OA
    → ค้นหาสินค้า
    → เห็นสินค้าจากหลายร้าน (cross-tenant product listing — แสดงเฉพาะ product info ไม่เปิดเผยข้อมูลร้านอื่น)
    → เลือกสินค้า → เลือกร้าน
    → กดสนใจ → ระบบสร้าง Lead ให้เอง (auto-create record)
    → แชทกับร้าน (Live Chat — Phase 15)
```

**Privacy rule:** product listing ข้ามร้านได้ แต่ห้ามเปิดเผยว่าลูกค้าคนนี้คุยกับร้านไหนบ้างให้ร้านอื่นเห็น

---

## 9. Notification Flow

| เหตุการณ์ | ผู้รับ | LINE push | Dashboard badge | Phase |
|---|---|---|---|---|
| Live Chat session ใหม่ | Sales, CS ทุกคน (จนกว่าจะ assign) | ✅ | ✅ | 15 |
| Ticket มอบหมายให้ช่าง | ช่าง | ✅ | ✅ | 12 |
| Service report ส่งจากช่าง | CS (เจ้าของ ticket) | ✅ | ✅ | 13 |
| Approval ส่งต่อขั้นถัดไป | ผู้อนุมัติขั้นถัดไป | ✅ | ✅ | 14 |
| Transfer Ownership request | คนใหม่ | ✅ | ✅ | 2 |
| SLA ใกล้เกิน | Sales/CS ที่รับผิดชอบ | ✅ | ✅ | 15 |
| Follow-up due ภายใน 1 วัน | owner ของ follow-up | ✅ | ✅ | 6 |
| Warranty ใกล้หมด | ลูกค้า + Sales | ✅ (ลูกค้า) | ✅ (Sales) | 7.5 |
| Survey ส่งให้ลูกค้า | ลูกค้า | ✅ (quick reply) | — | 14 |
| Deal stage เปลี่ยน | owner ของ deal | — | ✅ | 9 |

---

## 10. Cloud Target (GCP)

| ส่วน | Service |
|---|---|
| Presentation | Cloud Run |
| Application | Cloud Run |
| Data | Cloud Run |
| Document Renderer | Zoho Catalyst SmartBrowz (external supporting service) |
| Database | Cloud SQL (PostgreSQL) |
| Cache | Memorystore (Redis) |
| File storage | GCS (PDF, ภาพหลักฐาน, signature) |
| Container registry | Artifact Registry |
| CI/CD | GitHub Actions |
| IaC | Terraform |

---

## 11. Evidence State Model

ทุก capability ใช้สถานะนี้ตั้งแต่ต้น:

| State | ความหมาย |
|---|---|
| `PROVEN` / `PASS` | มี executable test หรือ runtime evidence |
| `PROVEN_WITH_LIMITATIONS` | ทำงานได้แต่มีข้อจำกัดที่ระบุชัด |
| `NOT_VERIFIED` | ออกแบบแล้วแต่ยังไม่มี evidence |
| `NOT_PROVEN_DEFERRED` | เลื่อนทำโดยเจตนา |
| `REQUIRES_EXPLICIT_APPROVAL` | ต้อง approve ก่อน execute |
```

---

# 2. ARCHITECTURE_DECISIONS.md

```markdown
# Architecture Decision Records — Chann CRM AI

**วันที่:** 14 สิงหาคม 2026
**สถานะ:** ใช้งาน ตั้งแต่ Phase 1

---

## ADR-001: ใช้ 4-tier architecture ตาม Chann1 reference

**สถานะ:** ACCEPTED
**ที่มา:** Chann1 reference + A1

### Decision
Presentation → Application → Data → Database แยกกันชัด มี boundary tests บังคับ

### Rationale
- แยกความรับผิดชอบชัด — Presentation ไม่ access DB, Application ไม่ access DB ตรง
- รองรับ independent deployability — เปลี่ยน tier หนึ่งไม่ต้อง rebuild ทุก tier
- มี executable boundary tests บังคับจริง ไม่ใช่แค่ doc

### Consequences
- ต้องสร้าง boundary tests ตั้งแต่ Phase 1
- Application และ Data แยกเป็น service คนละตัว (Cloud Run คนละ service)

---

## ADR-002: AI อยู่ใน Application Tier ไม่ใช่ tier ใหม่

**สถานะ:** ACCEPTED
**ที่มา:** A1

### Decision
AI (Qwen/DeepSeek) เป็นส่วนหนึ่งของ Application tier ไม่ใช่ tier แยก

### Rationale
- scope หลักการข้อ 3 "AI แปลง intent → JSON แล้วส่งต่อ domain service" คือแนวคิด Application tier อยู่แล้ว
- ไม่ต้องสร้าง service เพิ่ม
- AI ไม่มี state ของตัวเอง — เป็น stateless transformation เท่านั้น

### Consequences
- Application tier รับผิดชอบ: LINE webhook → AI intent → domain service → Data API
- ต้องมี timeout + retry + fallback สำหรับ OpenRouter API call
- ไม่มี "AI tier" ใน deployment topology

---

## ADR-003: Assignment Engine เป็น deterministic rule engine ไม่ใช่ AI runtime

**สถานะ:** ACCEPTED
**ที่มา:** A2

### Decision
Assignment Engine รันจาก config (ที่ AI แปลงจาก policy prompt ตอน config) ไม่ใช่ AI ตัดสินใจ runtime

### Rationale
- หลักการข้อ 2: "runtime อ่านค่าที่บันทึกไว้ตรง ๆ เร็ว เสถียร"
- หลีกเลี่ยง latency 2-3 วิ + cost + nondeterministic ใน hot path
- Assignment ต้อง reproducible สำหรับ audit

### Consequences
- Phase 11 ลดความซับซ้อน
- AI (Qwen) ใช้แค่ตอน config: แปลง policy prompt → rule JSON
- Rule engine เป็น code deterministic ที่อ่าน rule JSON
- Fallback: Round Robin ในกลุ่ม/ทีม
- ต้องมี capacity constraint + lock ระดับ DB สำหรับ race condition

---

## ADR-004: LINE OA 3 ตัว platform-level ไม่ใช่ per-tenant

**สถานะ:** ACCEPTED
**ที่มา:** B2 + C1

### Decision
Customer OA, Sales OA, Technician OA เป็น platform-level (OA เดียวทั้ง platform) ไม่ใช่ per-tenant

### Rationale
- onboarding tenant ใหม่เร็ว (ไม่ต้องสร้าง OA)
- Chann Identity lookup จาก LINE user ID อยู่แล้ว
- Customer OA เป็น marketplace อยู่แล้วต้อง platform-level
- ดูแลศูนย์กลางง่ายกว่า

### Consequences
- Webhook routing ต้องอาศัย Chann Identity lookup ทุกครั้ง
- ต้องมี tenant selection flow ถ้า user อยู่หลายบริษัท
- ถ้า tenant อยากมี OA ของตัวเองทีหลัง สามารถเพิ่มได้ (future scope)

---

## ADR-005: Authentication 2 path — LIFF สำหรับ LINE user, username/password สำหรับ Platform Admin

**สถานะ:** ACCEPTED
**ที่มา:** A3 + C4 + D2

### Decision
- LINE user (Customer/Sales/CS/Technician/Tenant Owner/Admin): LIFF ID Token ทุกหน้า
- Platform Admin (ฝั่ง Chai): username/password (argon2 hash) ผ่านหน้า web แยก

### Rationale
- LINE คือช่องทางหลัก ใช้ LIFF ID Token เป็น authentication จริง (LINE เป็น IdP)
- Platform Admin อาจไม่ได้ใช้ LINE เป็นหลัก ต้องมี auth path แยก
- ไม่มี MFA ใน phase นี้ (สามารถเพิ่มทีหลัง)

### Consequences
- `platform_admins` table เก็บ username + password_hash (argon2)
- Platform Admin login ผ่านหน้า web แยก ไม่ใช่ LIFF
- Session ใช้ JWT
- หน้า Platform Admin Dashboard ไม่ขึ้นกับ LINE

---

## ADR-006: ใช้ Redis cache-aside + fail-secure

**สถานะ:** ACCEPTED
**ที่มา:** A7 + Chann1

### Decision
Redis cache-aside สำหรับ authorization context, Chann Identity lookup, reference data

### Rationale
- Multi-tenant SaaS มี lookup ซ้ำๆ (permission, identity, reference data)
- Cache-aside ลด DB load
- Fail-secure: cache down → fallback DB แต่ห้าม broaden permission

### Consequences
- ทุก cached object ต้อง define: key, TTL, invalidation, source of truth, failure behavior
- Authorization cache: cache miss → DB fallback (ช้าแต่ปลอดภัย)
- ห้าม cache เป็น authoritative source

---

## ADR-007: Document Engine = AI-assisted DOCX authoring + Zoho Catalyst SmartBrowz deterministic rendering

**สถานะ:** ACCEPTED
**ที่มา:** C5 + D1 + final document-engine decision

### Decision

ใช้ **Zoho Catalyst SmartBrowz** เป็น external/supporting PDF rendering engine โดยคงหลัก template-once/versioned pattern และแยก authoring ออกจาก runtime อย่างเด็ดขาด

Authoring flow:

`DOCX upload -> AI analyze/map -> Intermediate Template Model -> compiled HTML/CSS/Liquid-compatible template -> preview -> user approval -> immutable published template version`

Runtime flow:

`business data -> deterministic JSON snapshot -> published template version -> deterministic HTML render -> SmartBrowz PDF -> GCS`

### Rationale

- ผู้ใช้ธุรกิจสามารถเริ่มจาก Word/DOCX ที่คุ้นเคย
- AI ช่วยเฉพาะงาน authoring ที่ต้องตีความ layout/field mapping ไม่อยู่ใน PDF hot path
- SmartBrowz รองรับ PDF จาก HTML และรองรับ predefined templates + dynamic JSON; template design รองรับ HTML/CSS/JavaScript/LiquidJS
- business amount/status/permission ทุกค่าเป็นผลจาก deterministic domain logic ไม่ให้ AI คำนวณหรือแก้ค่าในขณะสร้าง PDF
- Chann CRM เป็นเจ้าของ template metadata/version/data snapshot เพื่อ audit และไม่ผูก business versioning กับ provider console

### Rendering adapter rule

Baseline v1 คือ `html_convert`:

1. Chann CRM เก็บ compiled template source/version เอง
2. deterministic template renderer merge business JSON เป็น final HTML
3. Application เรียก SmartBrowz HTML-to-PDF

เหตุผล: core user flow ต้องไม่พึ่งการสร้าง/แก้ SmartBrowz Template ด้วยมือใน Catalyst console ทุกครั้ง

`predefined_template` mode ใช้ได้ภายหลังเมื่อ supported programmatic management/adoption path ถูกพิสูจน์แล้ว โดยเก็บ `smartbrowz_template_id` ใน template version และยังต้องรักษา Chann CRM version/audit contract เดิม

### Consequences

- ไม่มี Zoho Catalyst SmartBrowz container/Cloud Run PDF service
- `.docx` เป็น **authoring source**, ไม่ใช่ runtime template
- ต้องมี Intermediate Template Model เพื่อไม่ lock AI compiler กับ renderer provider
- Published template version immutable; การแก้ไขสร้าง version ใหม่
- ทุก generated document ต้องเก็บ `template_version_id`, source entity, deterministic `data_snapshot`, output path, SHA-256, generated_by/at
- ต้องมี Preview -> Approve -> Publish gate
- Runtime PDF generation ห้ามเรียก LLM
- SmartBrowz/Catalyst configuration เป็น external integration readiness gate ของ Phase 10

---

## ADR-008: 3 environment (DEV/Stage/Prod) + build-once promote-exact-artifact

**สถานะ:** ACCEPTED
**ที่มา:** A4 + Chann1

### Decision
3 environment จริง ทุก phase ต้อง PROVEN ใน Stage ก่อน promote Production

### Rationale
- Chann1 พิสูจน์ว่า Stage gate สำคัญ — ทดสอบใน DEV พอไม่ได้แปลว่าพร้อม Production
- build-once ลดความเสี่ยง "ใน Stage ใช้ artifact หนึ่ง ใน Prod ใช้อีกอัน"

### Consequences
- ทุก phase ต้องมี Stage evidence ก่อน Production
- Release Manifest ทุก release
- ไม่มี source variant ต่อ env

---

## ADR-009: โปรเจกต์ใหม่ — ออกแบบ schema ครั้งเดียว ไม่ต้อง Expand/Migrate/Contract ตอนนี้

**สถานะ:** ACCEPTED
**ที่มา:** A5

### Decision
ออกแบบ schema ให้ถูกตั้งแต่ต้น ไม่ต้องวาง compatibility window ตอนนี้ แต่วางหลักการไว้สำหรับการเปลี่ยนแปลงในอนาคต

### Rationale
- โปรเจกต์ใหม่ ไม่มี production data ต้อง migrate
- ออกแบบให้ถูกตั้งแต่ต้นง่ายกว่า

### Consequences
- schema migration ใช้ Alembic ตั้งแต่ต้น (สำหรับอนาคต)
- เมื่อมี production data แล้ว การเปลี่ยน schema ต้องใช้ Expand/Migrate/Contract
- ต้องมี reference seed ที่ idempotent ตั้งแต่ต้น

---

## ADR-010: Production promotion — ทุกคน promote ได้ มี Stage gate

**สถานะ:** ACCEPTED
**ที่มา:** A8

### Decision
ไม่บังคับ explicit approval gate ที่เด็ดขาด แต่มี Stage gate เป็น quality bar

### Rationale
- ทีมเล็ก + ใช้ AI coding agent
- Stage gate พอเป็น quality bar

### Consequences
- Stage PASS → สามารถ promote Production ได้
- ต้องบันทึก evidence ทุกครั้ง
- ยังมี Rollback rehearsal สำหรับ material release

---

## ADR-011: Cross-tenant lookup ต้อง audit ทุกครั้ง

**สถานะ:** ACCEPTED
**ที่มา:** A6

### Decision
ทุกการ access ข้อมูลข้าม license_id ต้องมี audit row ใน `audit_log`

### Rationale
- Multi-tenant SaaS มีจุดที่ข้าม tenant ได้ (Chann Identity, cross-company routing, product listing)
- ต้องย้อนดูได้ว่าใครเข้าถึงข้อมูล cross-tenant เมื่อไหร่

### Consequences
- `audit_log` มี `cross_tenant: true` flag
- ทุก cross-tenant query ต้องผ่าน service function เดียวกันที่ audit อัตโนมัติ
- Platform Admin Dashboard (Phase 18) แสดง cross-tenant audit log

---

## ADR-012: LIFF เป็นทางเลือก — chat-first เป็นหลัก

**สถานะ:** ACCEPTED
**ที่มา:** C5

### Decision
ทุก action (ลงทะเบียน, แจ้งซ่อม, ใบรับประกัน, profile) ทำผ่านแชทกับ AI ได้ LIFF เป็น fallback

### Rationale
- chat-first เป็นหลักการข้อ 1 อยู่แล้ว
- ลูกค้า/ช่างบางคนไม่อยากเปิด LIFF อยากพิมพ์แชทเลย
- LIFF มีประโยชน์สำหรับ form ที่ซับซ้อน (เช่น กรอกที่อยู่ + เลือกวันที่) แต่ไม่บังคับ

### Consequences
- ทุก flow ต้องรองรับทั้ง chat path และ LIFF path
- AI slot-filling ต้องครอบคลุมทุก flow ไม่ใช่แค่บาง flow
- LIFF และ chat ต้องได้ผลลัพธ์เหมือนกัน (same domain service function)

---

## ADR-013: Storefront = Lazada-style cross-tenant product listing

**สถานะ:** ACCEPTED
**ที่มา:** C3

### Decision
ลูกค้าค้นหาสินค้า → เห็นสินค้าจากหลายร้าน → เลือกร้าน → แชท + สร้าง Lead อัตโนมัติ

### Rationale
- คล้าย Lazada จริง — เปรียบเทียบสินค้าข้ามร้านได้
- สนใจสินค้า = Lead อัตโนมัติ (ไม่ต้องรอ Sales สร้าง)

### Consequences
- product listing เป็น cross-tenant query (แต่แสดงเฉพาะ product info ไม่เปิดเผยข้อมูลร้านอื่น)
- เมื่อลูกค้าเลือกร้าน → สร้าง Lead ใน tenant นั้น
- ห้ามร้าน A เห็นว่าลูกค้าคนนี้สนใจสินค้าร้าน B
- Cross-tenant audit สำหรับ product listing query

---

## ADR-014: AI Model Fallback Criteria

**สถานะ:** ACCEPTED
**ที่มา:** scope v7 Phase 4 + คำตอบข้อ 6

### Decision
Qwen เป็น default สำหรับ tier แชท (thinking off) แต่ถ้า latency จริงใน production ช้ากว่าเกณฑ์ ให้สลับกลับ Gemini

### Fallback rule
1. **วัดจริงใน production** (ไม่ใช่ benchmark) — ใช้ p95 latency ของ intent parsing ต่อ 100 message
2. **เกณฑ์ latency:** ถ้า Qwen p95 > 1.5 วิ และ Gemini 2.5 Flash p95 < 0.8 วิ → สลับกลับ Gemini
3. **เกณฑ์ความแม่นยำ:** ถ้า Qwen intent parse error rate > 5% ใน 100 message → สลับกลับ Gemini
4. **กลไกสลับ:** เปลี่ยนค่า `OPENROUTER_MODEL` ใน environment variable ไม่กระทบโครงสร้างโค้ด

### Rationale
- Qwen เป็นผู้นำ SEA-HELM ภาษาไทย แต่ถ้าช้าจริงใน production ต้องสลับได้
- การสลับเป็น config change ไม่ใช่ code change

### Consequences
- ต้องมี monitoring วัด p95 latency + error rate ของ AI intent parsing
- ต้องมี alert เมื่อเกินเกณฑ์
- ต้องบันทึก model change ใน Release Manifest

---

## ADR-015: OpenRouter Provider Preference

**สถานะ:** ACCEPTED
**ที่มา:** scope v7 Phase 4 + คำตอบข้อ 7

### Decision
เลือก OpenRouter provider ที่มีสถิติเสถียรภาพ/uptime ดีที่สุดก่อนเสมอ แม้จะแพงกว่าหรือช้ากว่าตัวถูกสุดเล็กน้อย

### Config
```yaml
# config/openrouter.yaml
provider_preference:
  qwen:
    - provider: "fireworks"      # ลำดับ 1 (uptime ดีสุด)
      fallback: true
    - provider: "together"       # ลำดับ 2
      fallback: true
    - provider: "deepseek_official"  # ลำดับ 3
  deepseek:
    - provider: "deepseek_official"
      fallback: true
    - provider: "fireworks"
      fallback: true
```

### Rationale
- เสถียรภาพสำคัญกว่าราคาถูกเล็กน้อย
- ผู้ใช้ไม่สนใจว่าใช้ provider ไหน สนแค่ตอบเร็ว + แม่น
- OpenRouter มีหลาย provider ต่อ model เดียวกัน ใช้ feature นี้

### Consequences
- ต้อง monitor provider uptime + latency
- ต้องมี fallback chain (provider 1 down → provider 2)
- บันทึก provider preference ใน config ไม่ใช่ hardcode

---

## ADR-016: Notification = dual delivery (LINE push + Dashboard badge)

**สถานะ:** ACCEPTED
**ที่มา:** E3 + คำตอบข้อ 4

### Decision
Notification ส่งทั้ง LINE push message และ Dashboard badge (polling ก่อน, WebSocket ทีหลังได้)

### Rationale
- ผู้ใช้บางคนอยู่ใน LINE ตลอด → LINE push เร็วสุด
- ผู้ใช้บางคนเปิด Dashboard อยู่ → badge แสดงได้ทันที
- ส่งคู่กันเพื่อไม่ให้พลาด

### Consequences
- `notifications` table มี `delivery_line` + `delivery_dashboard` flag
- LINE push ใช้ LINE Messaging API
- Dashboard badge ใช้ polling ใน Phase 6 (สามารถอัปเกรดเป็น WebSocket ทีหลัง)
- ทุก notification ต้องมี test ว่าส่งถูกคน

---

## ADR-017: CS role scope (หลังบาน, แยกจาก Sales)

**สถานะ:** ACCEPTED
**ที่มา:** คำตอบข้อ 8

### Decision
CS ดูแลหลังบาน: ticket, service report, approval, คุยลูกค้าใน Dashboard
Sales ดูแลหน้าบาน: customer, deal, quote

### CS หน้าที่ชัด
- รับ ticket จากลูกค้า (ลูกค้าแจ้งซ่อม → CS รับ/จัดการ)
- มอบหมายงานจาก ticket → ช่าง (CS assign ticket ให้ช่าง/ทีม)
- ตรวจ service report จากช่าง → ผ่าน/ไม่ผ่าน
- ประสานคุยกับลูกค้าใน Dashboard

### CS default permission
```python
CS_DEFAULT = {
    "ticket.read", "ticket.create", "ticket.update", "ticket.assign", "ticket.close",
    "service_report.read", "service_report.create", "service_report.update",
    "approval.view", "approval.approve", "approval.reject",
    "customer.read", "customer.update",
    "contact.read", "contact.create", "contact.update",
    # ไม่มี deal.*, quote.*, view_reports (default)
    # ไม่มี role.manage, member.manage, setting.manage
}
```

### Consequences
- Role template แยกชัด
- Permission test ต้องครอบ CS vs Sales
- การ assign ticket ให้ช่างใช้ ticket เดียว (ไม่แยก work_orders — ADR-018)

---

## ADR-018: Ticket = single entity (ไม่แยก work_orders)

**สถานะ:** ACCEPTED
**ที่มา:** E2

### Decision
ใช้ `service_tickets` ตัวเดียว ไม่แยก `work_orders` — 1 ticket = 1 งานช่าง

### Rationale
- scope v7 เดิมใช้ ticket เดียว
- ทีมเล็ก + AI coding agent ไม่ต้องเพิ่ม complexity
- ถ้าต้องการหลายงานต่อ ticket ทีหลัง สามารถเพิ่มได้

### Consequences
- `service_tickets` มี column ครบ: assigned_target_type, accept_status, service_address, scheduled_date, scheduled_time
- CS assign ticket ให้ช่าง = เปลี่ยน assigned_target_type + assigned_to_ref
- ไม่มี `work_orders` table

---

## ADR-019: Contract ตัดออกจาก scope ปัจจุบัน

**สถานะ:** ACCEPTED
**ที่มา:** E1

### Decision
ไม่ทำ Contract entity ใน scope นี้ — เลื่อนทีหลัง

### Rationale
- Contract มาจาก Deal ที่ปิดสำเร็จ แต่ถ้าทำก่อน Deal จะไม่มีอะไรผูก
- ลด scope MVP 1 ให้เล็กลง

### Consequences
- ไม่มี `contracts` table
- Warranty ไม่ผูก contract (warranty อิสระ)
- ถ้าต้องการ Contract ทีหลัง สามารถเพิ่มเป็น phase ใหม่ได้
```

---

# 3. REQUIREMENTS.md

```markdown
# Requirements — Chann CRM AI

**สถานะ:** LOCKED v1.0 — requirements รวม Phase 1-20 + 7.5/16.5/17.5 พร้อมเริ่ม
**Evidence model:** ทุก capability ตั้ง `NOT_VERIFIED` ตั้งแต่ต้น อัปเดตเป็น `PROVEN` เมื่อมี evidence

---

## Phase 1 — Architecture & Security Foundation

### 1.1 เป้าหมาย

วางรากฐานทั้งหมดที่ phase ถัดไปพึ่งพา:
- 4-tier architecture (Presentation/Application/Data/Database) พร้อม boundary tests
- 3 environment (DEV/Stage/Prod) + CI/CD + Release Manifest framework
- LINE OA 3 ตัว (platform-level) + webhook routing
- LIFF ID Token authentication
- Chann Identity (global, cross-tenant)
- Platform Admin authentication (username/password)
- Multi-tenant data model พื้นฐาน (licenses, license_members)
- Redis cache-aside infrastructure
- GCP infrastructure (Terraform)

### 1.2 Tier impact

| Tier | งาน | รายละเอียด |
|---|---|---|
| Presentation | สร้าง Next.js project + LIFF shell + Platform Admin login page | LIFF ID Token verify, dashboard shell (empty), platform admin login form |
| Application | สร้าง FastAPI + LINE webhook handler + AI intent stub + domain service stub | webhook จาก 3 OA, LIFF token verify, Chann Identity lookup, permission stub |
| Data | สร้าง FastAPI + SQLAlchemy + Alembic + Redis client | schema พื้นฐาน, authorization scope stub, cache-aside stub |
| Database | สร้าง schema พื้นฐาน + reference seed | `licenses`, `license_members`, `chann_identities`, `platform_admins` |
| Infrastructure | Terraform adoption + Cloud Run + reuse existing Cloud SQL/Memorystore/VPC/Artifact Registry + GCS (create only if missing) | 3 env; resolve existing-resource ↔ Terraform state before apply; no IAM/Service Account work in current scope |

### 1.3 ตารางใหม่

#### `licenses`
```sql
id UUID PK
license_code VARCHAR UNIQUE NOT NULL  -- รหัสบริษัท เช่น ACME001
company_name VARCHAR NOT NULL
auto_accept_new_customers BOOLEAN DEFAULT false  -- จาก Phase 16 แต่วาง column ไว้ตั้งแต่ต้น
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
```

#### `license_members`
```sql
id UUID PK
license_id UUID FK -> licenses.id
chann_uid VARCHAR FK -> chann_identities.chann_uid  -- ผูกกับ Chann Identity
role VARCHAR NOT NULL DEFAULT 'member'  -- จะเปลี่ยนเป็น free-text ใน Phase 2
status VARCHAR DEFAULT 'active'  -- active / disabled
joined_at TIMESTAMPTZ
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
UNIQUE(license_id, chann_uid)
```

#### `chann_identities` (GLOBAL — cross-tenant)
```sql
chann_uid VARCHAR PK  -- CHN-C-000123 / CHN-S-000045 / CHN-T-000012
line_user_id VARCHAR UNIQUE NOT NULL
primary_role VARCHAR NOT NULL  -- customer / sales / technician
display_name VARCHAR  -- จาก LINE Profile API
signature_url VARCHAR  -- สำหรับ Phase 13 (วาง column ไว้ตั้งแต่ต้น)
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
```

#### `platform_admins` (GLOBAL — ฝั่ง Chai)
```sql
id UUID PK
username VARCHAR UNIQUE NOT NULL
password_hash VARCHAR NOT NULL  -- argon2
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
```

### 1.4 Reference seed

```python
# scripts/seed_reference.py (idempotent by business key)

# Default platform admin (เปลี่ยนรหัสผ่านทันทีหลัง deploy)
DEFAULT_PLATFORM_ADMIN = {
    "username": "admin",
    "password": "changeme123"  # hash ด้วย argon2 ตอน seed
}
```

### 1.5 Permission keys (stub — จะขยายใน Phase 2)

- `platform.admin.access` — Platform Admin เข้า Dashboard ได้
- `tenant.member` — เป็นสมาชิกของ tenant (ตรวจจาก `license_members`)

### 1.6 LINE OA + Webhook routing

```
LINE Webhook (3 OA) → Application
    → รับ message + LINE user ID
    → lookup chann_identities โดย line_user_id
    → ถ้าไม่เจบ → สร้าง Chann Identity ใหม่ (primary_role ตาม OA ที่ทักเข้ามา)
    → lookup license_members โดย chann_uid
    → ถ้าเจอหลาย tenant → ถามเลือก tenant (ในอนาคต Phase 16)
    → ถ้าเจอ 1 tenant → ใช้ tenant นั้น
    → ถ้าไม่เจอ → แนะนำขั้นตอนลงทะเบียน (Phase 8)
```

### 1.7 Mandatory automated tests

#### Multi-tenant isolation test (บังคับ — หลักการข้อ 11)
```
test_tenant_isolation:
  - สร้าง license A + license B
  - สร้าง member ใน A และ B
  - เรียก Data API ด้วย license A → ต้องไม่เห็นข้อมูล B
  - เรียก Data API ด้วย license B → ต้องไม่เห็นข้อมูล A
  - แม้ใน query ตรง ๆ ก็ต้อง filter license_id เสมอ
```

#### Boundary test (บังคับ — หลักการข้อ 12)
```
test_boundary_presentation_not_import_data:
  - Presentation ต้องไม่ import Data tier module
  - ต้องไม่ import SQLAlchemy / psycopg / redis

test_boundary_application_not_access_db:
  - Application ต้องไม่ import SQLAlchemy / psycopg / redis
  - ต้องเรียก Data ผ่าน internal HTTP API เท่านั้น

test_boundary_data_owns_db:
  - Data เป็น tier เดียวที่ import SQLAlchemy / psycopg / redis
```

#### Authentication test
```
test_liff_token_required:
  - เรียก API โดยไม่มี LIFF ID Token → 401
  - เรียก API ด้วย token ไม่ถูกต้อง → 401
  - เรียก API ด้วย token ถูกต้อง → 200

test_platform_admin_login:
  - login ด้วย username/password ถูกต้อง → JWT
  - login ด้วย password ผิด → 401
  - เข้าหน้า Dashboard โดยไม่มี JWT → redirect login
```

#### Chann Identity test
```
test_chann_identity_lookup:
  - LINE user ID ใหม่ → สร้าง Chann Identity อัตโนมัติ
  - LINE user ID เดิม → ใช้ Chann Identity ที่มี
  - Chann Identity เดียวกันใช้ข้าม tenant ได้
  - ห้ามเห็นว่า Chann Identity นี้อยู่ใน tenant ไหนบ้าง (ยกเว้น tenant ตัวเอง)
```

### 1.8 Cache contract (Redis)

| Object | Key | TTL | Source | Invalidation | Failure behavior |
|---|---|---|---|---|---|
| Chann Identity | `chann_id:{line_user_id}` | 1h | DB | update profile | Fallback DB (fail-secure) |
| License member | `license_member:{license_id}:{chann_uid}` | 30m | DB | role change / status change | Fallback DB (fail-secure) |
| Platform Admin session | `admin_session:{session_id}` | 24h | DB (JWT) | logout | ห้าม fallback ถ้า cache down ให้ login ใหม่ |

### 1.9 Acceptance criteria

- [ ] 4-tier architecture ทำงานได้ — Presentation → Application → Data → Database
- [ ] Boundary tests PASS — ไม่มี tier ข้าม boundary
- [ ] 3 OA webhook รับ message ได้ — Customer/Sales/Technician
- [ ] LIFF ID Token authentication ทำงานได้
- [ ] Platform Admin login (username/password) ทำงานได้
- [ ] Chann Identity lookup ทำงานได้ — สร้างอัตโนมัติถ้าไม่มี
- [ ] Multi-tenant isolation test PASS
- [ ] Redis cache-aside ทำงานได้ — fail-secure เมื่อ cache down
- [ ] CI/CD pipeline ทำงานได้ — 3 env, build-once
- [ ] Release Manifest framework สร้างได้
- [ ] Infrastructure preflight + Terraform state/adoption gate PASS; deploy Cloud Run x3; reuse existing Cloud SQL/Memorystore/VPC/Artifact Registry; create GCS only if required and absent
- [ ] DEV runtime: ส่ง message จาก LINE → ได้ response กลับ (stub)
- [ ] Stage runtime: เหมือน DEV + readiness PASS
- [ ] Production runtime: promote Stage artifact โดยไม่ rebuild

### 1.10 Evidence state target

| Environment | Target state | เกณฑ์ |
|---|---|---|
| DEV | PROVEN | ส่ง message จาก LINE → ได้ response |
| Stage | NOT_VERIFIED → PROVEN | readiness PASS + functional smoke PASS |
| Production | NOT_VERIFIED → PROVEN | promote Stage artifact โดยไม่ rebuild + smoke PASS |

### 1.11 Dependencies

- **depends-on:** — (phase แรก)
- **blocks:** Phase 2, 3, 4, 5, 6, 7, 8, 9 (ทั้งหมดพึ่ง Phase 1)

### 1.12 Release Manifest template

```json
{
  "platform_version": "1.0.0",
  "phase": 1,
  "git_commit": "<sha>",
  "presentation_artifact": "<gcr.io/.../presentation@sha256:...>",
  "application_artifact": "<gcr.io/.../application@sha256:...>",
  "data_artifact": "<gcr.io/.../data@sha256:...>",
  "document_renderer": "zoho-catalyst-smartbrowz",
  "document_template_contract_version": "<version>",
  "database_migration_head": "<alembic revision>",
  "environment": "dev|stage|production",
  "verification_status": {
    "boundary_tests": "PASS|FAIL",
    "multi_tenant_isolation": "PASS|FAIL",
    "auth_tests": "PASS|FAIL",
    "runtime_smoke": "PASS|FAIL"
  },
  "known_limitations": [],
  "security_mode": "PRODUCTION_PROOF_REDUCED_SECURITY|SECURE"
}
```

---

## Phase 2 — Permission Matrix & license_settings

### 2.1 เป้าหมาย

เปลี่ยนจาก enum role ตายตัว → custom role ต่อบริษัท ผ่าน prompt → AI แปลงเป็น permission set + สร้างตารางกลางสำหรับ config ต่อบริษัท

### 2.2 Tier impact

| Tier | งาน |
|---|---|
| Presentation | หน้า role management (Dashboard) — สร้าง/แก้/ลบ role, ดู permission matrix |
| Application | prompt-config endpoint สำหรับ role creation, permission check refactor ทั้งระบบ |
| Data | permission lookup + cache, license_settings CRUD |
| Database | `custom_roles`, `role_permissions`, `license_settings` |

### 2.3 ตารางใหม่

#### `custom_roles`
```sql
id UUID PK
license_id UUID FK -> licenses.id
role_name VARCHAR NOT NULL  -- free-text, ตั้งโดย tenant
is_owner BOOLEAN DEFAULT false  -- Owner เป็น role พิเศษ แก้/ลบไม่ได้
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
UNIQUE(license_id, role_name)
```

#### `role_permissions`
```sql
id UUID PK
license_id UUID FK -> licenses.id
role VARCHAR NOT NULL  -- อ้าง custom_roles.role_name
permission_key VARCHAR NOT NULL  -- เช่น contact.read, deal.reopen, reassign_records
allowed BOOLEAN DEFAULT true
created_at TIMESTAMPTZ
UNIQUE(license_id, role, permission_key)
```

#### `license_settings`
```sql
id UUID PK
license_id UUID FK -> licenses.id
setting_key VARCHAR NOT NULL  -- เช่น session_timeout, chat_sla, auto_accept_new_customers
setting_value JSONB NOT NULL
updated_at TIMESTAMPTZ
UNIQUE(license_id, setting_key)
```

### 2.4 Reference seed (idempotent)

```python
# Default permission keys (global reference)
DEFAULT_PERMISSION_KEYS = [
    "contact.read", "contact.create", "contact.update", "contact.archive",
    "deal.read", "deal.create", "deal.update", "deal.archive", "deal.reopen",
    "note.read", "note.create", "note.update",
    "followup.read", "followup.create", "followup.update",
    "ticket.read", "ticket.create", "ticket.update", "ticket.assign", "ticket.close",
    "quote.read", "quote.create", "quote.update",
    "service_report.read", "service_report.create", "service_report.update",
    "approval.view", "approval.approve", "approval.reject",
    "reassign_records",
    "view_reports",
    "role.manage", "member.manage", "setting.manage",
    "warranty.read", "warranty.create", "warranty.update",
]

# Default role templates (สร้างใหม่ทุก tenant ตอน onboard)
DEFAULT_ROLE_TEMPLATES = {
    "owner": {"all_permissions": True, "is_owner": True},
    "admin": {"all_except": ["role.manage_owner"]},
    "member": {"subset": ["contact.*", "deal.*", "note.*", "followup.*", "warranty.*"]},
    "cs": {
        "subset": [
            "ticket.*", "service_report.*", "approval.*",
            "customer.read", "customer.update",
            "contact.read", "contact.create", "contact.update",
        ]
    }
}
```

### 2.5 Permission keys ที่เพิ่มใน Phase 2

| Key | ความหมาย | Default role |
|---|---|---|
| `reassign_records` | โอน record ให้คนอื่นในทีม | ทุก role (ยกเว้นตั้งจำกัด) |
| `view_reports` | ดู ad-hoc report (Phase 17) | Sales, CS (ปิด default ถ้าไม่เปิด) |
| `role.manage` | สร้าง/แก้/ลบ role | Admin, Owner |
| `member.manage` | เชิญ/ลบสมาชิก | Admin, Owner |
| `setting.manage` | แก้ license_settings | Admin, Owner |
| `warranty.read/create/update` | จัดการ warranty | Member, Sales |

### 2.6 Mandatory automated tests

#### Permission gate test (บังคับ — หลักการข้อ 11)
```
test_permission_gate:
  - สร้าง custom role "หัวหน้าทีมขาย" ที่ทำได้ทุกอย่างยกเว้นลบ license
  - ยืนยันว่า role นี้ทำ deal.create ได้
  - ยืนยันว่า role นี้ทำ license.delete ไม่ได้
  - ยืนยันว่า permission check ผ่าน permission key ไม่ใช่ role name
  - ยืนยันว่า Owner ทำได้ทุกอย่างแม้จะไม่มี row ใน role_permissions
```

#### Multi-tenant isolation test
```
test_role_isolation:
  - role "หัวหน้าทีมขาย" ใน tenant A ไม่ปรากฏใน tenant B
  - permission ใน tenant A ไม่ครอบคลุม tenant B
```

#### Race condition test (สำหรับ role creation)
```
test_role_creation_race:
  - สร้าง role ชื่อเดียวกันพร้อมกัน → ต้องมีแค่ 1 ที่สำเร็จ (UNIQUE constraint)
```

#### CS vs Sales permission test
```
test_cs_vs_sales_permission:
  - CS ทำ ticket.assign ได้, Sales ทำไม่ได้ (default)
  - Sales ทำ deal.create ได้, CS ทำไม่ได้ (default)
  - CS ทำ approval.approve ได้, Sales ทำไม่ได้ (default)
```

### 2.7 Transfer Ownership flow

```
1. Owner เดิมพิมพ์ "โอนสิทธิ์ Owner ให้สมหญิง"
   → AI แปลง intent → domain service
   → ตรวจว่าผู้สั่งเป็น Owner จริง
   → สร้าง pending transfer (รอคนใหม่ยืนยัน)

2. คนใหม่ได้รับการแจ้งเตือน "ได้รับการโอนสิทธิ์ Owner จาก... ยืนยันรับไหม?"
   → คนใหม่พิมพ์ "ยืนยัน"
   → AI แปลง intent → domain service
   → ตรวจว่าเป็นคนใหม่จริง
   → โอน Owner: คนใหม่เป็น Owner, คนเดิมเป็น Admin
   → audit log

3. Break-glass (Platform Admin):
   → Platform Admin เข้า Dashboard → force transfer Owner
   → audit log (cross-tenant)
```

### 2.8 Acceptance criteria

- [ ] Custom role สร้าง/แก้/ลบ ผ่าน prompt ได้
- [ ] Permission check ทั้งระบบ refactor เป็น permission key (ไม่มี hardcode role name)
- [ ] Owner role แก้/ลบไม่ได้ มีสิทธิ์เต็ม
- [ ] ต้องมี Owner อย่างน้อย 1 คนเสมอ
- [ ] Transfer Ownership flow 2 ชั้น confirm ทำงานได้
- [ ] Break-glass (Platform Admin force transfer) ทำงานได้
- [ ] `license_settings` CRUD ได้ (key-value)
- [ ] Permission gate test PASS
- [ ] Multi-tenant role isolation test PASS
- [ ] CS vs Sales permission test PASS
- [ ] runtime: สร้าง role ใหม่ผ่านแชท → ใช้งานได้จริง

### 2.9 Evidence state target

| Environment | Target state |
|---|---|
| DEV | PROVEN |
| Stage | NOT_VERIFIED → PROVEN |
| Production | NOT_VERIFIED → PROVEN |

### 2.10 Dependencies

- **depends-on:** Phase 1
- **blocks:** Phase 3, 6, 9, 12 (audit + chat + CRM + ticket ต้องเช็ค permission)

---

## Phase 3 — Audit Log

### 3.1 เป้าหมาย

สร้าง audit log ทุก entity + บันทึก `ai_reasoning` เมื่อ actor เป็น AI + cross-tenant audit flag

### 3.2 Tier impact

| Tier | งาน |
|---|---|
| Application | audit emit ทุก domain service action |
| Data | audit_log CRUD + cross-tenant flag |
| Database | `audit_log` |

### 3.3 ตารางใหม่

#### `audit_log`
```sql
id UUID PK
license_id UUID FK -> licenses.id
entity_type VARCHAR NOT NULL  -- customer, deal, ticket, quote, warranty, ...
entity_id UUID NOT NULL
actor_type VARCHAR NOT NULL  -- user, ai, system, platform_admin
actor_id VARCHAR  -- chann_uid หรือ platform_admin_id
action VARCHAR NOT NULL  -- create, update, delete, assign, transfer, cross_tenant_lookup
field_changes JSONB  -- {field: {old, new}}
ai_reasoning TEXT  -- เมื่อ actor_type = 'ai' เท่านั้น
cross_tenant BOOLEAN DEFAULT false  -- flag สำหรับ cross-tenant access
created_at TIMESTAMPTZ
-- เก็บไว้ตลอดไป ไม่ลบ
```

### 3.4 Audit emission rules

| เหตุการณ์ | actor_type | field_changes | ai_reasoning | cross_tenant |
|---|---|---|---|---|
| User สร้าง customer | user | ทุก field ที่สร้าง | — | false |
| AI แปลง intent แล้วสร้าง customer | ai | ทุก field | "ผู้ใช้พิมพ์ 'เพิ่มลูกค้าชื่อสมชาย' → แปลงเป็น create customer intent" | false |
| Assignment Engine assign ticket | ai | assigned_to | "match rule: category=AC → team A → round robin → ช่าง X (capacity 3/5)" | false |
| Cross-company serial lookup | system | — | — | true |
| Platform Admin force transfer Owner | platform_admin | owner_member_id | — | true |

### 3.5 Mandatory automated tests

#### Audit emission test (บังคับ — Chann1 lesson)
```
test_audit_emission:
  - สร้าง customer → ต้องมี audit row (action=create)
  - แก้ customer → ต้องมี audit row (action=update, field_changes มี old+new)
  - ลบ customer → ต้องมี audit row (action=delete)
  - AI สร้าง customer → ต้องมี audit row (actor_type=ai, ai_reasoning ไม่ว่าง)
  - ไม่มี audit row สำหรับ read (ยกเว้น cross-tenant)
```

#### Cross-tenant audit test
```
test_cross_tenant_audit:
  - cross-company serial lookup → ต้องมี audit row (cross_tenant=true)
  - Platform Admin force transfer → ต้องมี audit row (cross_tenant=true)
  - ไม่มี cross_tenant=true สำหรับ action ใน tenant เดียวกัน
```

### 3.6 Acceptance criteria

- [ ] ทุก material change มี audit row จริง (ไม่ใช่แค่ schema มีตาราง)
- [ ] `ai_reasoning` บันทึกเมื่อ actor เป็น AI
- [ ] `cross_tenant` flag ถูกต้อง
- [ ] Audit log ไม่ลบ (append-only)
- [ ] Audit emission test PASS
- [ ] Cross-tenant audit test PASS
- [ ] runtime: สร้าง customer ผ่านแชท → ดู audit log ได้

### 3.7 Dependencies

- **depends-on:** Phase 1, 2
- **blocks:** Phase 6 ขึ้นไป (ทุก action ต้อง audit)

---

## Phase 4 — AI Infrastructure

### 4.1 เป้าหมาย

วาง AI infrastructure ที่ Application tier ใช้เรียก OpenRouter พร้อม:
- tier แบ่งตามหน้าที่ (Qwen แปลง intent/config, DeepSeek ad-hoc report)
- thinking mode ปิดสำหรับ tier แชท
- provider preference + fallback chain
- fallback criteria (วัดจริง, สลับได้)
- monitoring (latency, error rate, cost)

### 4.2 Tier impact

| Tier | งาน |
|---|---|
| Application | OpenRouter client, model selection, prompt template, timeout/retry, provider fallback, monitoring hook |
| Data | — (ไม่แตะ) |
| Database | — (ไม่แตะ) |

### 4.3 Model assignment

| หน้าที่ | Model | Thinking | Config key | ใช้เมื่อ |
|---|---|---|---|---|
| Intent parsing (แชท) | `qwen/qwen3.6-35b-a3b` | off | `OPENROUTER_MODEL` | ทุก chat message |
| Prompt-config (policy → JSON) | `qwen/qwen3.6-35b-a3b` | off | `OPENROUTER_MODEL` | ตอน config เท่านั้น |
| Ad-hoc report query spec | `deepseek/deepseek-v4-pro` | on | `OPENROUTER_MODEL_REASONING` | ตอนขอ report (Phase 17) |
| PDF template validation | `qwen/qwen3.6-35b-a3b` | off | `OPENROUTER_MODEL` | ตอนอัปโหลด template (Phase 10) |

### 4.4 Provider preference (ADR-015)

```yaml
# config/openrouter.yaml
provider_preference:
  qwen:
    - provider: "fireworks"
      fallback: true
    - provider: "together"
      fallback: true
  deepseek:
    - provider: "deepseek_official"
      fallback: true
    - provider: "fireworks"
      fallback: true
```

### 4.5 Fallback criteria (ADR-014)

| เกณฑ์ | ค่า | การกระทำ |
|---|---|---|
| Qwen p95 latency | > 1.5 วิ | สลับกลับ Gemini 2.5 Flash |
| Gemini p95 latency | < 0.8 วิ | ใช้ Gemini |
| Qwen intent error rate | > 5% ใน 100 msg | สลับกลับ Gemini |

กลไกสลับ: เปลี่ยน `OPENROUTER_MODEL` env var (ไม่กระทบโค้ด)

### 4.6 Prompt template structure

```python
# Application tier — prompt template pattern

INTENT_SYSTEM_PROMPT = """
You are an intent parser for a CRM system.
Convert the user's Thai message into a JSON action.
Available actions depend on the user's role and permissions.

User context:
- chann_uid: {chann_uid}
- role: {role}
- license_id: {license_id}
- language: {language}  # from Phase 16, default 'th'

Available permission keys: {permission_keys}

Return ONLY JSON: {"action": "...", "entity": "...", "fields": {...}, "missing": [...]}
If information is incomplete, list missing fields in "missing".
If the action is not allowed, return {"action": "suggest", "suggestions": [...]}.
"""
```

### 4.7 Mandatory automated tests

```
test_ai_intent_parsing:
  - ส่ง "เพิ่มลูกค้าชื่อสมชาย" → ได้ JSON action=create, entity=customer, fields.name=สมชาย
  - ส่ง "เพิ่มลูกค้า" → ได้ JSON missing=[name]
  - ส่งคำสั่งที่ไม่มีสิทธิ์ → ได้ JSON action=suggest
  - ทดสอบ timeout: OpenRouter ไม่ตอบใน 10 วิ → fallback provider
  - ทดสอบทุก provider ล้ม → ตอบ "ขออภัย ระบบไม่พร้อม" (ไม่ hang)

test_thinking_mode_off:
  - ส่งคำสั่งง่าย → ตอบกลับภายใน 3 วิ (ไม่ใช่ 80+ วิ)
  - ตรวจ response ว่าไม่มี thinking trace ใน output
```

### 4.8 Acceptance criteria

- [ ] Qwen แปลง intent ไทย → JSON ได้ถูกต้อง
- [ ] Thinking mode ปิดสำหรับ tier แชท
- [ ] Provider fallback chain ทำงานได้
- [ ] Timeout + retry ทำงานได้
- [ ] Monitoring hook วัด latency + error rate
- [ ] Intent parsing test PASS
- [ ] Thinking mode test PASS
- [ ] runtime: ส่งข้อความไทย → ได้ JSON ภายใน 3 วิ

### 4.9 Dependencies

- **depends-on:** Phase 1
- **blocks:** Phase 6, 10, 17 (chat + PDF template + report)

---

## Phase 5 — i18n Framework

### 5.1 เป้าหมาย

วาง i18n framework ที่ครอบคลุม:
- UI text (ทุกหน้า)
- ภาษาที่บอทตอบ (ส่งเข้า AI prompt)
- สลับภาษา + จำค่าที่เลือก

### 5.2 Tier impact

| Tier | งาน |
|---|---|
| Presentation | i18n dictionary TH/EN, ปุ่มสลับภาษา, จำ preference ใน localStorage |
| Application | ส่ง `language` เข้า AI prompt, ตอบเป็นภาษาที่เลือก |
| Data | — |

### 5.3 i18n dictionary structure

```typescript
// presentation/src/i18n/th.ts
export const th = {
  common: { save: "บันทึก", cancel: "ยกเลิง", confirm: "ยืนยัน" },
  customer: { title: "ลูกค้า", addNew: "เพิ่มลูกค้าใหม่" },
  deal: { title: "ดีล", stage: { new: "ใหม่", proposed: "เสนอราคา", won: "ปิดสำเร็จ", lost: "ปิดไม่สำเร็จ" } },
  // ...
}

// presentation/src/i18n/en.ts
export const en = {
  common: { save: "Save", cancel: "Cancel", confirm: "Confirm" },
  customer: { title: "Customer", addNew: "Add New Customer" },
  deal: { title: "Deal", stage: { new: "New", proposed: "Proposed", won: "Won", lost: "Lost" } },
  // ...
}
```

### 5.4 Bot language integration

```python
# Application tier — ส่ง language เข้า AI prompt
def build_intent_prompt(user_message, user_context):
    language = user_context.get("language", "th")  # จาก Phase 16 user_display_preferences
    return INTENT_SYSTEM_PROMPT.format(
        language=language,
        ...
    )

# AI ตอบเป็นภาษาที่เลือก
def format_ai_response(json_result, language):
    if language == "en":
        return translate_response_to_en(json_result)
    return format_thai_response(json_result)
```

### 5.5 Mandatory automated tests

```
test_i18n_ui_switch:
  - สลับภาษา EN → UI แสดงภาษาอังกฤษ
  - สลับภาษา TH → UI แสดงภาษาไทย
  - refresh หน้า → จำภาษาที่เลือก

test_i18n_bot_language:
  - ตั้งภาษา EN → ส่งข้อความ → AI ตอบเป็นอังกฤษ
  - ตั้งภาษา TH → ส่งข้อความ → AI ตอบเป็นไทย
  - ถ้า AI ตอบผิดภาษา → test fail

test_i18n_dictionary_complete:
  - ทุก key ใน th.ts ต้องมีใน en.ts
  - ทุก key ใน en.ts ต้องมีใน th.ts
```

### 5.6 Acceptance criteria

- [ ] i18n dictionary TH/EN ครบ
- [ ] ปุ่มสลับภาษาทำงานได้
- [ ] จำค่าที่เลือกได้
- [ ] AI ตอบเป็นภาษาที่เลือก
- [ ] i18n test PASS

### 5.7 Dependencies

- **depends-on:** Phase 1
- **blocks:** Phase 6 ขึ้นไป (ทุกหน้าต้อง i18n)

---

## Phase 6 — Chat Foundation + Notification + Follow-up

### 6.1 เป้าหมาย

วาง chat foundation ที่ทุก phase ใช้:
- Slot-filling pattern (ทุก flow)
- Reply-to-Entity (ทุก channel ทุก entity)
- Personalized Greeting
- Suggest-what-you-can-do (ดึงจาก Permission Matrix)
- Chat-first action (ทุก action ทำผ่านแชทได้)
- Notification system (LINE push + Dashboard badge)
- Follow-up/Reminder

### 6.2 Tier impact

| Tier | งาน |
|---|---|
| Presentation | Dashboard notification badge (polling), notification list |
| Application | Slot-filling engine, reply-to-entity handler, greeting, suggest engine, notification sender (LINE push + Dashboard) |
| Data | `line_message_entity_map`, `notifications`, `follow_ups` CRUD |
| Database | 3 ตารางใหม่ |

### 6.3 ตารางใหม่

#### `line_message_entity_map`
```sql
id UUID PK
message_id VARCHAR UNIQUE NOT NULL  -- LINE message ID
entity_type VARCHAR NOT NULL  -- customer, deal, ticket, ...
entity_id UUID NOT NULL
license_id UUID FK -> licenses.id
created_at TIMESTAMPTZ
```

#### `notifications`
```sql
id UUID PK
license_id UUID FK -> licenses.id  -- NULL สำหรับ platform-level noti
target_chann_uid VARCHAR FK -> chann_identities.chann_uid
type VARCHAR NOT NULL  -- chat_session_new, approval_pending, transfer_request, sla_warning, followup_due, warranty_expiring, ...
entity_type VARCHAR  -- ticket, deal, chat_session, ...
entity_id UUID
message TEXT NOT NULL  -- ข้อความที่แสดง (TH)
message_en TEXT  -- ภาษาอังกฤษ (i18n)
delivery_line BOOLEAN DEFAULT true  -- ส่ง LINE push ด้วยไหม
delivery_dashboard BOOLEAN DEFAULT true  -- แสดงใน Dashboard ด้วยไหม
read_at TIMESTAMPTZ  -- NULL = ยังไม่อ่าน
created_at TIMESTAMPTZ
```

#### `follow_ups`
```sql
id UUID PK
license_id UUID FK -> licenses.id
entity_type VARCHAR NOT NULL  -- customer, deal, ticket, chat_session, ...
entity_id UUID NOT NULL
due_date DATE NOT NULL
status VARCHAR DEFAULT 'pending'  -- pending, completed, cancelled
owner_member_id UUID FK -> license_members.id
notes TEXT
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
```

### 6.4 Slot-filling pattern

```python
# Application tier — slot-filling pattern (ใช้ทุก flow)

async def handle_chat_message(message, user_context):
    intent = await parse_intent(message, user_context)
    
    if intent.missing_fields:
        # ข้อมูลไม่ครบ → ถามข้อมูลที่ขาด
        return ask_for_missing(intent.missing_fields)
    
    if intent.action == "suggest":
        # ไม่มีสิทธิ์ → แนะนำสิ่งที่ทำได้
        return suggest_what_you_can_do(user_context)
    
    # ข้อมูลครบ → ส่ง domain service
    result = await domain_service.execute(intent, user_context)
    return format_response(result, user_context.language)
```

### 6.5 Reply-to-Entity pattern

```python
# Application tier — reply-to-entity

async def handle_reply(message_id, reply_text, user_context):
    # ค้น entity ที่ผูกกับ message_id
    mapping = await data.get_message_entity_map(message_id)
    if not mapping:
        return "ไม่พบข้อความต้นฉบับที่ตอบกลับ"
    
    # แปลง reply_text เป็น action สำหรับ entity นั้น
    intent = await parse_intent_for_entity(reply_text, mapping.entity_type, user_context)
    result = await domain_service.execute_on_entity(
        mapping.entity_type, mapping.entity_id, intent, user_context
    )
    return format_response(result, user_context.language)
```

### 6.6 Suggest-what-you-can-do

```python
# Application tier — ดึงจาก Permission Matrix

async def suggest_what_you_can_do(user_context):
    permissions = await get_user_permissions(user_context)
    available_actions = []
    for perm in permissions:
        if perm.allowed:
            available_actions.append(PERMISSION_DESCRIPTIONS[perm.key])
    return format_suggestions(available_actions, user_context.language)
```

### 6.7 Personalized Greeting

```python
# Application tier

async def get_greeting(chann_uid, license_id):
    member = await data.get_license_member(license_id, chann_uid)
    if member and member.registered_name:
        return f"สวัสดี {member.registered_name}"  # หลังลงทะเบียน
    else:
        identity = await data.get_chann_identity(chann_uid)
        return f"สวัสดี {identity.display_name}"  # ก่อนลงทะเบียน (LINE Display Name)
```

### 6.8 Notification sender

```python
# Application tier — dual delivery

async def send_notification(license_id, target_chann_uid, type, entity_type, entity_id, message, message_en):
    # 1. บันทึกใน DB
    noti = await data.create_notification(
        license_id, target_chann_uid, type, entity_type, entity_id, message, message_en
    )
    
    # 2. ส่ง LINE push (ถ้า delivery_line = true)
    if noti.delivery_line:
        await line_push_message(target_chann_uid, message)
    
    # 3. Dashboard badge แสดงจาก polling (ไม่ต้องส่งแยก)
    # Dashboard จะ poll /api/v1/notifications/unread_count
```

### 6.9 Mandatory automated tests

```
test_slot_filling:
  - ส่ง "เพิ่มลูกค้า" (ไม่มีชื่อ) → AI ถาม "กรุณาระบุชื่อลูกค้า"
  - ส่ง "เพิ่มลูกค้าชื่อสมชาย" → สร้าง customer สำเร็จ
  - ทดสอบทุก flow: customer, deal, ticket, warranty, follow-up

test_reply_to_entity:
  - reply ข้อความยืนยันเดิมด้วย "แก้ชื่อเป็นสมหญิง" → แก้ entity นั้น
  - reply ข้อความที่ไม่ใช่ entity → ตอบ "ไม่พบข้อความต้นฉบับ"

test_suggest_what_you_can_do:
  - Sales พิมพ์คำสั่งที่ไม่มี → แนะนำเฉพาะ Sales permission
  - CS พิมพ์คำสั่งที่ไม่มี → แนะนำเฉพาะ CS permission
  - ไม่แนะนำ permission ที่ไม่มีสิทธิ์

test_notification:
  - สร้าง notification → ต้องมี LINE push + Dashboard badge
  - อ่าน notification → read_at ไม่เป็น NULL
  - ส่งถูกคน (target_chann_uid ถูกต้อง)

test_follow_up:
  - สร้าง follow-up → แจ้งเตือนภายใน 1 วันก่อน due_date
  - ทำเสร็จ → status = completed
  - ยกเลิก → status = cancelled

test_greeting:
  - ก่อนลงทะเบียน → ทักด้วย LINE Display Name
  - หลังลงทะเบียน → ทักด้วยชื่อจริง
```

### 6.10 Acceptance criteria

- [ ] Slot-filling ทำงานทุก flow
- [ ] Reply-to-Entity ทำงานทุก channel
- [ ] Suggest ดึงจาก Permission Matrix จริง
- [ ] Greeting เปลี่ยนตามสถานะลงทะเบียน
- [ ] Notification ส่ง LINE + Dashboard badge
- [ ] Follow-up แจ้งเตือนก่อน due_date
- [ ] ทุก test PASS
- [ ] runtime: ส่งข้อความไม่ครบ → AI ถามข้อมูลที่ขาด

### 6.11 Dependencies

- **depends-on:** Phase 1, 2, 4, 5
- **blocks:** Phase 8 ขึ้นไป (ทุก phase ใช้ chat foundation)

---

## Phase 7 — Master Data & Organization

### 7.1 เป้าหมาย

สร้าง master data ที่ CRM + Assignment + Ticket ต้องใช้:
- Product Master Data (เพิ่มได้ 2 ทาง: ทีละตัว หรือ CSV)
- Sales Groups (1 Sales อยู่ได้หลายกลุ่ม)
- Technician Teams (1 ช่างอยู่ได้หลายทีม + มีหัวหน้าทีม)

### 7.2 Tier impact

| Tier | งาน |
|---|---|
| Presentation | หน้า product management, หน้า group/team management, CSV upload |
| Application | product CRUD (chat + API), group/team CRUD (chat + API), CSV parser |
| Data | 5 ตารางใหม่ + cache |
| Database | `products`, `sales_groups`, `sales_group_members`, `technician_teams`, `technician_team_members` |

### 7.3 ตารางใหม่

#### `products`
```sql
id UUID PK
product_id VARCHAR UNIQUE NOT NULL  -- รหัสสินค้า แยกต่อบริษัท
license_id UUID FK -> licenses.id
product_name VARCHAR NOT NULL
sku VARCHAR
category VARCHAR  -- ใช้ใน Assignment Rule
unit_price NUMERIC(18,2)
description TEXT
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
UNIQUE(license_id, product_id)
```

#### `sales_groups`
```sql
id UUID PK
license_id UUID FK -> licenses.id
group_name VARCHAR NOT NULL
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
UNIQUE(license_id, group_name)
```

#### `sales_group_members`
```sql
id UUID PK
license_id UUID FK -> licenses.id
group_id UUID FK -> sales_groups.id
member_id UUID FK -> license_members.id
created_at TIMESTAMPTZ
UNIQUE(group_id, member_id)  -- 1 Sales อยู่ได้หลายกลุ่ม
```

#### `technician_teams`
```sql
id UUID PK
license_id UUID FK -> licenses.id
team_name VARCHAR NOT NULL
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
UNIQUE(license_id, team_name)
```

#### `technician_team_members`
```sql
id UUID PK
license_id UUID FK -> licenses.id
team_id UUID FK -> technician_teams.id
member_id UUID FK -> license_members.id
is_lead BOOLEAN DEFAULT false  -- หัวหน้าทีม
created_at TIMESTAMPTZ
UNIQUE(team_id, member_id)  -- 1 ช่างอยู่ได้หลายทีม
```

### 7.4 CSV upload

```python
# Application tier — CSV upload for products

CSV_TEMPLATE = """
product_id,product_name,sku,category,unit_price,description
P001,แอร์ LG 12000 BTU,AC-LG-12K,AIR_CONDITIONER,25000,แอร์ LG 12000 BTU
P002,ฟิลเตอร์ HEPA,FILTER-HEPA-01,AIR_FILTER,1500,ฟิลเตอร์ HEPA สำหรับแอร์
"""

async def upload_products_csv(license_id, file_content, user_context):
    products = parse_csv(file_content)
    for product in products:
        # idempotent by product_id (business key)
        await data.upsert_product(license_id, product)
    return f"นำเข้า {len(products)} สินค้าสำเร็จ"
```

### 7.5 Mandatory automated tests

```
test_product_crud:
  - สร้าง product ผ่านแชท → สำเร็จ
  - สร้าง product ผ่าน CSV → สำเร็จ
  - สร้าง product_id ซ้ำ → upsert (ไม่ error)
  - ลบ product → ไม่ใช่ hard delete (archive)

test_sales_group:
  - 1 Sales อยู่ได้หลายกลุ่ม
  - ลบกลุ่ม → ไม่ลบ member (แค่ลบ group)

test_technician_team:
  - 1 ช่างอยู่ได้หลายทีม
  - ตั้ง is_lead ได้
  - 1 ทีมมีได้หลาย lead

test_multi_tenant_product:
  - product ใน tenant A ไม่ปรากฏใน tenant B
  - product_id ซ้ำข้าม tenant ได้ (UNIQUE แยกตาม license_id)
```

### 7.6 Acceptance criteria

- [ ] Product CRUD ผ่านแชท + CSV ได้
- [ ] Sales Groups 1 คนหลายกลุ่มได้
- [ ] Technician Teams 1 คนหลายทีม + is_lead ได้
- [ ] Multi-tenant isolation test PASS
- [ ] runtime: เพิ่มสินค้าผ่านแชท → ใช้ใน Deal ได้

### 7.7 Dependencies

- **depends-on:** Phase 1, 2
- **blocks:** Phase 7.5 (warranty), 9 (deal), 11 (assignment), 12 (ticket)

---

## Phase 7.5 — Warranty

### 7.5.1 เป้าหมาย

ลูกค้าลงทะเบียนสินค้า → สร้าง warranty → ดูใบรับประกัน PDF

### 7.5.2 Tier impact

| Tier | งาน |
|---|---|
| Presentation | หน้าลงทะเบียนสินค้า (LIFF หรือแชท), หน้าดูใบรับประกัน |
| Application | warranty CRUD (chat + API), PDF generation through shared SmartBrowz document engine |
| Data | `warranties` CRUD |
| Database | `warranties` |

### 7.5.3 ตารางใหม่

#### `warranties`
```sql
id UUID PK
license_id UUID FK -> licenses.id
warranty_number VARCHAR UNIQUE NOT NULL  -- W-YYYY-NNNN แยกต่อบริษัท
customer_chann_uid VARCHAR FK -> chann_identities.chann_uid
product_id UUID FK -> products.id
serial_number VARCHAR NOT NULL
warranty_start DATE NOT NULL
warranty_end DATE NOT NULL
status VARCHAR DEFAULT 'active'  -- active, expired, void
pdf_path VARCHAR  -- ใบรับประกัน PDF (GCS)
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
```

### 7.5.4 Flow

```
ลูกค้าแจ้ง "ลงทะเบียนสินค้า"
    → AI ถาม: serial number, รหัสสินค้า/ชื่อสินค้า, วันที่ซื้อ
    → ค้น product ใน tenant (จาก serial หรือ product_id)
    → ถ้าไม่เจอ → แจ้ง "ไม่พบสินค้า กรุณาติดต่อร้านค้า"
    → ถ้าเจอ → คำนวณ warranty_end (จาก product.category หรือ default)
    → สร้าง warranty row
    → gen PDF ผ่าน shared deterministic SmartBrowz document engine → เก็บใน GCS
    → ส่ง PDF link กลับให้ลูกค้า
    → audit log
```

### 7.5.5 Mandatory automated tests

```
test_warranty_create:
  - ลงทะเบียนสินค้าผ่านแชท → สร้าง warranty สำเร็จ
  - ลงทะเบียนสินค้าผ่าน LIFF → สร้าง warranty สำเร็จ
  - serial ไม่มี → แจ้ง "ไม่พบสินค้า"
  - ข้อมูลไม่ครบ → AI ถามข้อมูลที่ขาด (slot-filling)

test_warranty_pdf:
  - สร้าง warranty → ต้องมี PDF ใน GCS
  - PDF รองรับภาษาไทย

test_warranty_expiry:
  - warranty_end ผ่านแล้ว → status = expired
  - ใกล้หมด → ส่ง notification

test_multi_tenant_warranty:
  - warranty ใน tenant A ไม่ปรากฏใน tenant B
```

### 7.5.6 Acceptance criteria

- [ ] ลงทะเบียนสินค้าผ่านแชท + LIFF ได้
- [ ] สร้าง warranty + PDF ได้
- [ ] ดูใบรับประกันได้
- [ ] Notification ใกล้หมดอายุ
- [ ] Multi-tenant isolation test PASS
- [ ] runtime: ลูกค้าลงทะเบียนสินค้า → ได้ใบรับประกัน PDF

### 7.5.7 Dependencies

- **depends-on:** Phase 1, 2, 7 (products), 8 (customer profile)
- **blocks:** Phase 16 (cross-company serial routing)

---

## Phase 8 — Profiles (Tech + Customer)

### 8.1 เป้าหมาย

ช่าง + ลูกค้ากรอกข้อมูลส่วนตัวเองได้ (chat-first, LIFF เป็นทางเลือก)
Sales/CS กรอกแทนลูกค้าได้ผ่าน Dashboard Chat

### 8.2 Tier impact

| Tier | งาน |
|---|---|
| Presentation | หน้า profile (LIFF หรือแชท) |
| Application | profile CRUD (chat + API) |
| Data | เพิ่ม column ใน `chann_identities` หรือตาราง profile แยก |

### 8.3 Profile fields

```sql
-- เพิ่มใน chann_identities หรือสร้างตาราง profile แยก

ALTER TABLE chann_identities ADD COLUMN
  first_name VARCHAR,
  last_name VARCHAR,
  phone VARCHAR,
  email VARCHAR,
  address TEXT,
  registered BOOLEAN DEFAULT false,
  registered_at TIMESTAMPTZ;
```

### 8.4 Flow

```
ช่าง/ลูกค้าแจ้ง "แก้โปรไฟล์"
    → AI ถามข้อมูลที่ต้องการแก้
    → แก้ → audit log

Sales/CS กรอกแทนลูกค้า
    → ใน Dashboard Chat: "แก้ลูกค้าชื่อสมชาย เบอร์ 08x-xxx-xxxx"
    → AI แปลง intent → domain service
    → ตรวจ permission (customer.update)
    → แก้ → audit log
```

### 8.5 Mandatory automated tests

```
test_profile_self_edit:
  - ช่างแก้โปรไฟล์ตัวเองได้
  - ลูกค้าแก้โปรไฟล์ตัวเองได้
  - แก้โปรไฟล์คนอื่นไม่ได้ (ยกเว้นมี customer.update)

test_profile_chat_vs_liff:
  - แก้ผ่านแชท → ผลเหมือนแก้ผ่าน LIFF
  - ทั้งคู่ใช้ domain service function เดียวกัน
```

### 8.6 Acceptance criteria

- [ ] ช่าง/ลูกค้าแก้ profile ตัวเองได้ (chat + LIFF)
- [ ] Sales/CS กรอกแทนลูกค้าได้ (ผ่าน Dashboard Chat)
- [ ] แก้คนอื่นไม่ได้ (ยกเว้นมี permission)
- [ ] runtime: ลูกค้าพิมพ์ "แก้เบอร์เป็น 08x-xxx-xxxx" → เปลี่ยนสำเร็จ

### 8.7 Dependencies

- **depends-on:** Phase 1, 6
- **blocks:** Phase 7.5 (warranty ต้องมี customer), 9 (deal ต้องมี customer), 12 (ticket ต้องมี customer)

---

## Phase 9 — CRM Core: Lead→Contact→Deal + Storefront

### 9.1 เป้าหมาย

- รวม Lead + Contact เป็น `customers` (stage = lead | contact)
- สร้าง Deal + Deal Products
- Storefront Lazada-style (cross-tenant product listing)
- ลูกค้าสนใจสินค้า → สร้าง Lead อัตโนมัติ

### 9.2 Tier impact

| Tier | งาน |
|---|---|
| Presentation | หน้า storefront (LIFF), หน้า customer/deal management (Dashboard) |
| Application | customer CRUD, deal CRUD, storefront search (cross-tenant), auto-create lead |
| Data | `customers` (stage column), `deals`, `deal_products` |
| Database | 3 ตารางใหม่ |

### 9.3 ตารางใหม่

#### `customers`
```sql
id UUID PK
license_id UUID FK -> licenses.id
customer_chann_uid VARCHAR FK -> chann_identities.chann_uid
stage VARCHAR DEFAULT 'lead'  -- lead, contact
owner_member_id UUID FK -> license_members.id
first_name VARCHAR
last_name VARCHAR
phone VARCHAR
email VARCHAR
address TEXT
notes TEXT
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
UNIQUE(license_id, customer_chann_uid)  -- 1 customer ต่อ tenant
```

#### `deals`
```sql
id UUID PK
license_id UUID FK -> licenses.id
deal_id VARCHAR UNIQUE NOT NULL  -- D-YYYY-NNNN
contact_id UUID FK -> customers.id
stage VARCHAR DEFAULT 'new'  -- new, proposed, won, lost
owner_member_id UUID FK -> license_members.id
notes TEXT
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
```

#### `deal_products`
```sql
id UUID PK
deal_id UUID FK -> deals.id
product_id UUID FK -> products.id  -- NULL ได้ถ้าเป็นสินค้านอก list
product_name VARCHAR NOT NULL
quoted_unit_price NUMERIC(18,2) NOT NULL
qty INT NOT NULL DEFAULT 1
notes TEXT
created_at TIMESTAMPTZ
```

### 9.4 Storefront flow (Lazada-style)

```
ลูกค้าเข้า Customer OA → ค้นหาสินค้า
    → Application ค้น products ข้ามทุก tenant (cross-tenant)
    → แสดงสินค้าจากหลายร้าน (เฉพาะ product info ไม่เปิดเผยข้อมูลร้าน)
    → ลูกค้าเลือกสินค้า → เลือกร้าน
    → กดสนใจ → สร้าง Lead ใน tenant ของร้านนั้น
    → audit log (cross_tenant=true สำหรับ product listing query)
    → แชทกับร้าน (Phase 15)
```

### 9.5 Lead → Contact promotion

```
Sales ยืนยัน Lead → Contact
    → พิมพ์ "ยืนยันลูกค้าสมชาย" หรือกดใน Dashboard
    → customers.stage = 'contact'
    → audit log
```

### 9.6 Deal stage transition

```
new → proposed (สร้าง quote)
proposed → won (ปิดสำเร็จ)
proposed → lost (ปิดไม่สำเร็จ)
won → new (reopen — ต้องมี deal.reopen permission)
```

### 9.7 Mandatory automated tests

```
test_customer_crud:
  - สร้าง customer ผ่านแชท → สำเร็จ
  - สร้าง customer ผ่าน Dashboard → สำเร็จ
  - 1 customer ต่อ tenant (UNIQUE constraint)
  - Lead → Contact promotion

test_deal_crud:
  - สร้าง deal ผ่านแชท → สำเร็จ
  - เพิ่ม product ใน deal → สำเร็จ
  - stage transition: new → proposed → won
  - reopen: won → new (ต้องมี deal.reopen)

test_storefront_cross_tenant:
  - ค้นสินค้า → เห็นสินค้าจากหลาย tenant
  - เลือกร้าน → สร้าง Lead ใน tenant นั้น
  - ร้าน A ไม่เห็นว่าลูกค้าคนนี้สนใจสินค้าร้าน B
  - product listing query มี cross_tenant audit

test_multi_tenant_customer:
  - customer ใน tenant A ไม่ปรากฏใน tenant B
  - deal ใน tenant A ไม่ปรากฏใน tenant B
```

### 9.8 Acceptance criteria

- [ ] Customer CRUD (chat + Dashboard)
- [ ] Deal CRUD + Deal Products (chat + Dashboard)
- [ ] Lead → Contact promotion
- [ ] Deal stage transition + reopen
- [ ] Storefront cross-tenant product listing
- [ ] Auto-create Lead เมื่อลูกค้าสนใจสินค้า
- [ ] Privacy: ร้าน A ไม่เห็นข้อมูลร้าน B
- [ ] Multi-tenant isolation test PASS
- [ ] runtime: ลูกค้าค้นสินค้า → สร้าง Lead → Sales เห็นใน Dashboard

### 9.9 Dependencies

- **depends-on:** Phase 2, 6, 7, 8
- **blocks:** Phase 10 (quote), 11 (assignment), 15 (live chat), 17 (report)

---

## Phase 10 — Quote + AI-assisted Document Template + PDF

### 10.1 เป้าหมาย

สร้าง Quote จาก Deal และมี Document Template Engine ที่ผู้ใช้สามารถอัปโหลด `.docx` เป็นแบบตั้งต้น จากนั้น AI ช่วย analyze/map/compile เป็น versioned template ที่ระบบ render PDF แบบ deterministic ผ่าน Zoho Catalyst SmartBrowz

หลักสำคัญ:

- Word/DOCX = authoring input
- AI = authoring assistant เท่านั้น
- SmartBrowz = external/supporting PDF renderer
- runtime PDF = AI-free และ deterministic
- template ทุกประเภทใช้ generic versioning model เดียวกัน เพื่อ reuse กับ Warranty, Service Report, PDPA Export และ Invoice

### 10.2 Tier impact

| Tier | งาน |
|---|---|
| Presentation | quote management, DOCX upload, detected-field mapping UI, preview, approve/publish, version history |
| Application | quote CRUD, DOCX analysis orchestration, AI template assistant, compiler, deterministic dataset builder, SmartBrowz adapter |
| Data | template metadata/version/generated-document CRUD, GCS object access |
| Database | `quotes`, `document_templates`, `document_template_versions`, `generated_documents` |
| Supporting integration | Zoho Catalyst SmartBrowz PDF rendering |
| GCS | original DOCX, compiled template assets, preview/final PDF, images/signatures |

### 10.3 ตารางใหม่

#### `quotes`
```sql
id UUID PK
license_id UUID FK -> licenses.id
quote_id VARCHAR UNIQUE NOT NULL  -- Q-YYYY-NNNN แยกต่อบริษัท
deal_id UUID FK -> deals.id
status VARCHAR DEFAULT 'draft'  -- draft, sent, accepted, rejected, expired
generated_document_id UUID NULL FK -> generated_documents.id
owner_member_id UUID FK -> license_members.id
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
```

#### `document_templates`
```sql
id UUID PK
license_id UUID FK -> licenses.id
document_type VARCHAR NOT NULL  -- quote, warranty, service_report, pdpa_export, invoice, ...
template_code VARCHAR NOT NULL
template_name VARCHAR NOT NULL
is_active BOOLEAN DEFAULT true
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
UNIQUE(license_id, template_code)
```

#### `document_template_versions`
```sql
id UUID PK
template_id UUID FK -> document_templates.id
version INTEGER NOT NULL
status VARCHAR NOT NULL  -- draft, published, archived
source_docx_path VARCHAR NOT NULL  -- GCS source file
intermediate_model JSONB NOT NULL  -- layout/fields/tables/header/footer/style abstraction
mapping_schema JSONB NOT NULL
compiled_template_path VARCHAR NOT NULL  -- GCS HTML/CSS/template source
renderer VARCHAR NOT NULL DEFAULT 'smartbrowz'
renderer_mode VARCHAR NOT NULL DEFAULT 'html_convert'  -- html_convert | predefined_template
smartbrowz_template_id VARCHAR NULL
created_by UUID FK -> license_members.id
created_at TIMESTAMPTZ
published_at TIMESTAMPTZ NULL
UNIQUE(template_id, version)
```

#### `generated_documents`
```sql
id UUID PK
license_id UUID FK -> licenses.id
document_type VARCHAR NOT NULL
source_entity_type VARCHAR NOT NULL
source_entity_id UUID NOT NULL
template_version_id UUID FK -> document_template_versions.id
data_snapshot JSONB NOT NULL
output_path VARCHAR NOT NULL  -- GCS
sha256 VARCHAR(64) NOT NULL
renderer VARCHAR NOT NULL DEFAULT 'smartbrowz'
generated_by UUID NULL FK -> license_members.id
generated_at TIMESTAMPTZ NOT NULL
```

### 10.4 Authoring + runtime pattern

```text
AUTHORING (AI allowed)
1. Owner/Admin uploads .docx
   -> Data stores original in GCS
   -> Application extracts document structure/text/assets
   -> Qwen proposes fields, loops, mappings and layout interpretation
   -> create Intermediate Template Model
   -> compile to HTML/CSS/Liquid-compatible template source
   -> store DRAFT template version

2. Preview
   -> use explicit sample/business data
   -> deterministic merge to final HTML
   -> SmartBrowz converts HTML to PDF
   -> user reviews mapping/layout

3. Publish
   -> user approves
   -> DRAFT becomes immutable PUBLISHED version
   -> later edits create version N+1, never overwrite historical published version

RUNTIME (AI forbidden)
4. Generate Quote PDF
   -> Application loads deal + deal_products + customer
   -> deterministic business/domain logic calculates authoritative values
   -> create JSON data_snapshot
   -> load published template version
   -> deterministic template merge -> final HTML
   -> SmartBrowz HTML-to-PDF
   -> GCS output + SHA-256
   -> generated_documents row + URL
```

### 10.5 Intermediate Template Model requirement

AI must not compile DOCX straight into provider-specific opaque state only. Persist a provider-neutral model at minimum containing:

```json
{
  "document_type": "quote",
  "paper": {"size": "A4", "orientation": "portrait"},
  "fields": [],
  "tables": [],
  "images": [],
  "header": {},
  "footer": {},
  "styles": {},
  "mapping_schema_version": 1
}
```

This model is the stable boundary between AI authoring and the SmartBrowz adapter.

### 10.6 SmartBrowz integration rule

- v1 baseline: Chann CRM owns compiled template source and calls SmartBrowz HTML-to-PDF.
- SmartBrowz predefined-template mode is optional, not required for MVP.
- If predefined-template mode is adopted, prove the supported creation/update/publish management path first; do not make operators manually copy templates for every tenant as part of normal product runtime.
- Application runs on GCP, therefore Phase 10 must verify the current supported Catalyst SDK/REST authentication path from the deployed Application environment before claiming readiness.
- Provider outage must return a clear render failure and must never cause AI to fabricate a document.

### 10.7 Mandatory automated tests

```text
test_quote_create:
  - create quote from deal -> success
  - quote number increments per tenant
  - authoritative price comes from domain data, not AI

test_template_authoring:
  - upload .docx -> source stored
  - AI proposes field mapping -> saved as draft
  - intermediate model generated
  - compiled template generated
  - ambiguous mapping requires confirmation rather than silent guess

test_template_versioning:
  - preview does not publish
  - publish requires explicit approval
  - published version is immutable
  - editing published version creates N+1 draft
  - old generated document still references old version

test_smartbrowz_preview:
  - deterministic sample JSON -> PDF output
  - Thai text/font renders acceptably
  - provider error is surfaced explicitly

test_pdf_render:
  - create quote -> deterministic data snapshot -> SmartBrowz -> GCS
  - download PDF
  - PDF content matches authoritative data
  - generated_documents contains template version + snapshot + sha256
  - no LLM call occurs in runtime render path

test_multi_tenant_quote:
  - quote in tenant A is invisible in tenant B
  - tenant A cannot use tenant B template/version
  - generated document isolation is enforced
```

### 10.8 Acceptance criteria

- [ ] สร้าง quote จาก deal (chat + Dashboard)
- [ ] อัปโหลด `.docx` template ได้
- [ ] AI-assisted field/mapping/layout proposal ทำงานได้
- [ ] ผู้ใช้แก้ mapping ได้ก่อน publish
- [ ] Intermediate Template Model ถูกบันทึก
- [ ] Preview PDF ผ่าน SmartBrowz ได้
- [ ] Preview -> Approve -> Publish workflow ทำงาน
- [ ] Published template version immutable
- [ ] Runtime PDF ไม่มี LLM call
- [ ] PDF ภาษาไทยผ่าน acceptance
- [ ] generated document trace ได้ถึง template version + data snapshot + SHA-256
- [ ] Multi-tenant isolation test PASS
- [ ] runtime: create quote -> download deterministic PDF successfully

### 10.9 Dependencies

- **depends-on:** Phase 9 (deal), Phase 4 (AI authoring assistance), GCS/file infrastructure, SmartBrowz configuration
- **blocks:** later document types reuse this engine (Warranty, Service Report, PDPA Export, Invoice)

---

## Phase 11 — Assignment Engine (Deterministic)

### 11.1 เป้าหมาย

สร้าง assignment rule engine ที่:
- Owner/Admin พิมพ์ policy ผ่าน prompt → AI แปลงเป็น rule JSON → บันทึก
- Runtime ใช้ deterministic rule engine อ่าน rule JSON (ไม่ใช้ AI runtime)
- รองรับ Sales assignment + Technician assignment
- Capacity constraint (Hard block / Soft warn)
- Race condition ป้องกันด้วย DB lock
- Fallback: Round Robin ในกลุ่ม/ทีมที่เกี่ยวข้อง
- ไม่มีใคร active เลย → assign ให้ Owner/Admin อย่างน้อย 1 คน

### 11.2 Tier impact

| Tier | งาน |
|---|---|
| Presentation | หน้า assignment rule config (Dashboard) |
| Application | prompt-config endpoint (policy → rule JSON), rule engine executor |
| Data | `assignment_rules` CRUD, capacity check + lock |
| Database | `assignment_rules` |

### 11.3 ตารางใหม่

#### `assignment_rules`
```sql
id UUID PK
license_id UUID FK -> licenses.id
scope VARCHAR NOT NULL  -- sales, technician
rules_json JSONB NOT NULL  -- structured rule from AI
is_active BOOLEAN DEFAULT true
updated_at TIMESTAMPTZ
updated_by UUID FK -> license_members.id
created_at TIMESTAMPTZ
```

### 11.4 Rule JSON structure (จาก AI แปลง policy prompt)

```json
{
  "version": 1,
  "scope": "technician",
  "match_criteria": [
    {
      "field": "product.category",
      "operator": "equals",
      "value": "AIR_CONDITIONER",
      "assign_to_team": "AC Team"
    },
    {
      "field": "product.category",
      "operator": "equals",
      "value": "REFRIGERATOR",
      "assign_to_team": "Cooling Team"
    }
  ],
  "selection_strategy": "least_load",
  "capacity_constraint": {
    "max_per_day": 5,
    "mode": "hard_block"
  },
  "fallback": "round_robin_in_team",
  "no_active_fallback": "assign_to_owner_or_admin"
}
```

### 11.5 Deterministic rule engine (ไม่ใช้ AI runtime)

```python
# Application tier — deterministic rule engine

async def execute_assignment(license_id, scope, context, user_context):
    # 1. ดึง rule จาก DB (cache ได้)
    rule = await data.get_assignment_rule(license_id, scope)
    
    # 2. match criteria
    matched_team = match_criteria(rule.match_criteria, context)
    if not matched_team:
        return assign_fallback(rule, license_id, scope)
    
    # 3. ดึง candidates ใน team
    candidates = await data.get_team_members(matched_team, status='active')
    if not candidates:
        return assign_fallback(rule, license_id, scope)
    
    # 4. selection strategy
    if rule.selection_strategy == "least_load":
        selected = await select_least_load(candidates, rule.capacity_constraint)
    elif rule.selection_strategy == "round_robin":
        selected = await select_round_robin(candidates)
    else:
        selected = candidates[0]
    
    # 5. capacity check + lock (race condition)
    async with db_lock(f"assignment:{license_id}:{scope}"):
        if rule.capacity_constraint.mode == "hard_block":
            current_load = await data.get_current_load(selected.id, date.today())
            if current_load >= rule.capacity_constraint.max_per_day:
                return await select_next_candidate(candidates, rule)
        
        await data.assign_record(entity_id, selected.id)
        # audit log (actor_type=ai, ai_reasoning="match rule: ...")
    
    return selected
```

### 11.6 Prompt-config flow

```
Owner/Admin พิมพ์: "ช่างที่รับผิดชอบแอร์ ให้ทีม AC ไม่เกินวันละ 5 งาน"
    → AI (Qwen) แปลงเป็น rule JSON
    → โชว์สรุปให้ปรับจนพอใจ
    → ยืนยัน → บันทึกใน assignment_rules.rules_json
    → audit log (actor_type=ai, ai_reasoning="...")
```

### 11.7 Mandatory automated tests

#### Race condition test (บังคับ — หลักการข้อ 11)
```
test_assignment_race_condition:
  - จำลอง 10 ticket เข้าพร้อมกัน ที่ match ทีมเดียวกัน
  - capacity = 5 งาน/วัน
  - ผล: 5 งานแรก assign สำเร็จ, 5 งานหลัง fallback หรือ assign ช่างอื่น
  - ไม่ทะลุ capacity constraint
  - DB lock ทำงานถูกต้อง
```

#### Rule matching test
```
test_rule_matching:
  - category=AC → assign ทีม AC
  - category=REF → assign ทีม Cooling
  - ไม่ match อะไร → fallback round_robin
  - ไม่มี active ในทีม → assign Owner/Admin
```

#### Capacity constraint test
```
test_capacity_hard_block:
  - ช่างมีงาน 5 อยู่แล้ว → assign ไม่ได้ (hard_block)
  - เลือกช่างอื่นแทน

test_capacity_soft_warn:
  - ช่างมีงาน 5 อยู่แล้ว → assign ได้ (soft_warn) แต่มี warning
```

#### Multi-tenant isolation test
```
test_assignment_rule_isolation:
  - rule ใน tenant A ไม่ใช้ใน tenant B
  - ช่างใน tenant A ไม่ถูก assign ใน tenant B
```

### 11.8 Acceptance criteria

- [ ] Owner/Admin พิมพ์ policy → AI แปลงเป็น rule JSON → บันทึก
- [ ] Runtime ใช้ deterministic rule engine (ไม่ใช้ AI)
- [ ] Capacity constraint (Hard block / Soft warn) ทำงานได้
- [ ] Race condition ป้องกันด้วย DB lock
- [ ] Fallback Round Robin ทำงานได้
- [ ] ไม่มี active → assign Owner/Admin
- [ ] Race condition test PASS
- [ ] Multi-tenant isolation test PASS
- [ ] runtime: พิมพ์ policy → สร้าง ticket → ถูก assign ตาม rule

### 11.9 Evidence state target

| Environment | Target state |
|---|---|
| DEV | PROVEN |
| Stage | NOT_VERIFIED → PROVEN |
| Production | NOT_VERIFIED → PROVEN |

### 11.10 Dependencies

- **depends-on:** Phase 7 (teams), Phase 9 (deal — สำหรับ sales assignment)
- **blocks:** Phase 12 (ticket dispatch), Phase 15 (live chat assignment)

---

## Phase 12 — Ticket Visibility + Dispatch Gate

### 12.1 เป้าหมาย

- Ticket CRUD + visibility (public/private)
- CS มอบหมาย ticket ให้ช่าง/ทีมช่าง (ไม่แยก work_orders)
- Dispatch Gate: ก่อน assign ต้องมีชื่อ-เบอร์ลูกค้า + ที่อยู่ + วันเวลานัดครบ
- ช่างกดรับเอง (public) หรือหัวหน้าทีมกดรับ (private → team)
- Chat-first: แจ้งซ่อมผ่านแชทเป็นหลัก

### 12.2 Tier impact

| Tier | งาน |
|---|---|
| Presentation | หน้า ticket management (Dashboard), หน้า ticket list (LIFF/ช่าง) |
| Application | ticket CRUD (chat + API), dispatch gate validation, assign flow |
| Data | `service_tickets` เพิ่ม column, assignment integration |
| Database | เพิ่ม column ใน `service_tickets` (table สร้างใน Phase 1 stub หรือ Phase 12) |

### 12.3 ตาราง / Column ใหม่

#### `service_tickets` (สร้างใหม่ใน Phase 12 หรือเพิ่ม column ถ้ามี stub)
```sql
id UUID PK
license_id UUID FK -> licenses.id
ticket_number VARCHAR UNIQUE NOT NULL  -- T-YYYY-NNNN
customer_chann_uid VARCHAR FK -> chann_identities.chann_uid
product_id UUID FK -> products.id  -- สินค้าที่ซ่อม (ถ้ามี)
serial_number VARCHAR
issue_description TEXT NOT NULL
status VARCHAR DEFAULT 'open'  -- open, assigned, in_progress, completed, cancelled
visibility VARCHAR DEFAULT 'public'  -- public, private
assigned_target_type VARCHAR  -- technician, technician_team
assigned_to_ref UUID  -- member_id หรือ team_id
accept_status VARCHAR DEFAULT 'pending'  -- pending, accepted, rejected
service_address TEXT
scheduled_date DATE
scheduled_time TIME
owner_member_id UUID FK -> license_members.id  -- CS ที่รับผิดชอบ
created_by UUID FK -> license_members.id
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
```

### 12.4 Flow

```
ลูกค้าแจ้งซ่อม (แชทหรือ LIFF)
    → AI slot-filling: ชื่อ, เบอร์, ปัญหา, ที่อยู่, วันเวลานัด
    → ข้อมูลครบ → สร้าง ticket (status=open, owner_member_id=CS ที่รับ)
    → แจ้งเตือน CS

CS มอบหมายให้ช่าง
    → CS พิมพ์ "มอบหมาย ticket T-2026-0001 ให้ทีม AC"
    → Dispatch Gate ตรวจ: ชื่อ-เบอร์ลูกค้า + ที่อยู่ + วันเวลานัด ครบไหม
    → ครบ → assign (ผ่าน Assignment Engine Phase 11 หรือเลือกเอง)
    → ไม่ครบ → แจ้ง "ข้อมูลไม่ครบ: กรุณาระบุ ..."

ช่างรับงาน
    → Public: ช่างทุกคนกดรับเอง (self-claim)
    → Private → คนเดียว: ไม่รับ → แจ้งกลับผู้มอบหมาย (ไม่ auto-reassign)
    → Private → ทีม: หัวหน้าทีมกด "รับ" = ทีมรับ → เปิด public ในทีม → สมาชิกกดรับ
    → แจ้งเตือนช่าง (LINE + Dashboard)
```

### 12.5 Dispatch Gate

```python
# Application tier — dispatch gate

async def assign_ticket(ticket_id, target_type, target_ref, user_context):
    ticket = await data.get_ticket(ticket_id)
    
    # Dispatch Gate: ตรวจข้อมูลครบก่อน assign
    missing = []
    if not ticket.customer_name: missing.append("ชื่อลูกค้า")
    if not ticket.customer_phone: missing.append("เบอร์ลูกค้า")
    if not ticket.service_address: missing.append("ที่อยู่")
    if not ticket.scheduled_date: missing.append("วันนัด")
    if not ticket.scheduled_time: missing.append("เวลานัด")
    
    if missing:
        return f"ไม่สามารถมอบหมายได้ ข้อมูลไม่ครบ: {', '.join(missing)}"
    
    # ผ่าน gate → assign
    if target_type == "technician_team":
        # ใช้ Assignment Engine (Phase 11) เลือกคนในทีม หรือเปิดให้ทีมรับ
        await assignment_engine.assign(ticket_id, "technician", context, user_context)
    else:
        await data.assign_ticket(ticket_id, target_type, target_ref)
    
    # audit + notification
    await send_notification(...)
```

### 12.6 Mandatory automated tests

```
test_ticket_crud:
  - แจ้งซ่อมผ่านแชท → สร้าง ticket สำเร็จ
  - แจ้งซ่อมผ่าน LIFF → สร้าง ticket สำเร็จ
  - ข้อมูลไม่ครบ → AI ถาม (slot-filling)

test_dispatch_gate:
  - ข้อมูลครบ → assign ได้
  - ข้อมูลไม่ครบ → ไม่ให้ assign + บอกขาดอะไร
  - ข้อมูลครบหลังแก้ → assign ได้

test_ticket_visibility:
  - Public: ช่างทุกคนเห็น + กดรับได้
  - Private → คนเดียว: คนนั้นเท่านั้นเห็น + รับ/ปฏิเสธได้
  - Private → ทีม: ทีมเท่านั้นเห็น + หัวหน้ากดรับ → เปิดในทีม

test_cs_assign_to_technician:
  - CS assign ให้ทีม → ทีมรับ → ช่างกดรับ
  - CS assign ให้ช่างเจาะจง → ช่างรับ/ปฏิเสธ
  - ปฏิเสธ → แจ้งกลับ CS (ไม่ auto-reassign)

test_multi_tenant_ticket:
  - ticket ใน tenant A ไม่ปรากฏใน tenant B
  - ช่างใน tenant A ไม่เห็น ticket tenant B
```

### 12.7 Acceptance criteria

- [ ] Ticket CRUD (chat + LIFF + Dashboard)
- [ ] Dispatch Gate ป้องกัน assign โดยข้อมูลไม่ครบ
- [ ] Visibility public/private ทำงานได้
- [ ] CS assign ให้ช่าง/ทีมได้
- [ ] ช่าง self-claim (public) และ team-claim (private→team) ทำงานได้
- [ ] Notification ส่งถูกคน
- [ ] Multi-tenant isolation test PASS
- [ ] runtime: ลูกค้าแจ้งซ่อม → CS มอบหมาย → ช่างรับ

### 12.8 Dependencies

- **depends-on:** Phase 7 (teams), Phase 11 (assignment), Phase 8 (customer)
- **blocks:** Phase 13 (field service), Phase 15 (live chat — เพราะ ticket จากแชท)

---

## Phase 13 — Field Service Execution

### 13.1 เป้าหมาย

- Check-in/Check-out บันทึก GPS
- แนบภาพหลักฐาน (GCS)
- ก่อน check-out ต้องกรอก Service Report ให้ครบ (gate)
- Check-out สำเร็จ → สร้าง Service Report PDF ผ่าน shared SmartBrowz document engine
- เก็บรูปลายเซ็นของผู้ approve (ผูก Chann Identity)
- Chat-first: ช่างส่งข้อมูลผ่านแชทได้

### 13.2 Tier impact

| Tier | งาน |
|---|---|
| Presentation | หน้า check-in/out (LIFF/แชท), หน้า service report |
| Application | check-in/out handler, photo upload → GCS, service report gen PDF |
| Data | `service_reports`, `ticket_photos` CRUD |
| Database | 3 ตารางใหม่ |
| Supporting integration | Zoho Catalyst SmartBrowz render Service Report PDF ผ่าน shared document engine |
| GCS | ภาพหลักฐาน + signature + Service Report PDF |

### 13.3 ตารางใหม่

#### `service_reports`
```sql
id UUID PK
license_id UUID FK -> licenses.id
report_id VARCHAR UNIQUE NOT NULL  -- SR-YYYY-NNNN
ticket_id UUID FK -> service_tickets.id
technician_member_id UUID FK -> license_members.id
report_data JSONB NOT NULL  -- ข้อมูลที่ช่างกรอก
pdf_path VARCHAR  -- GCS path
status VARCHAR DEFAULT 'draft'  -- draft, submitted, approved, rejected
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
```

#### Service Report template

ใช้ generic `document_templates` + `document_template_versions` จาก Phase 10 โดย `document_type = 'service_report'`; ห้ามสร้าง template-versioning model แยกซ้ำ

#### `ticket_photos`
```sql
id UUID PK
ticket_id UUID FK -> service_tickets.id
photo_url VARCHAR NOT NULL  -- GCS path
photo_type VARCHAR  -- checkin, checkout, evidence
taken_at TIMESTAMPTZ
gps_lat NUMERIC(10,7)
gps_lng NUMERIC(10,7)
uploaded_by UUID FK -> license_members.id
created_at TIMESTAMPTZ
```

### 13.4 Flow

```
ช่าง check-in
    → ส่ง GPS + ภาพ (ถ้ามี) → บันทึกใน ticket_photos
    → ticket.status = 'in_progress'

ช่าง check-out
    → Gate: ต้องกรอก service report ให้ครบก่อน
    → ข้อมูล service report: ปัญหาที่พบ, การแก้ไข, อะไรที่เปลี่ยน, ความคิดเห็น
    → ครบ → check-out (GPS + ภาพ)
    → สร้าง service_report row (status=submitted)
    → gen PDF ผ่าน shared SmartBrowz document engine → GCS
    → ส่งให้ CS ตรวจ (Phase 14 approval)
    → แจ้งเตือน CS
```

### 13.5 Signature integration

```python
# Application tier — signature สำหรับ approval (Phase 14)

# ตอน check-out หรือ approve ระบบดึง signature จาก Chann Identity
async def get_signature(chann_uid):
    identity = await data.get_chann_identity(chann_uid)
    return identity.signature_url  # GCS path

# ตอน gen Service Report PDF แปะลายเซ็นของผู้ approve
async def gen_service_report_pdf(report_id, approver_chann_uid):
    report = await data.get_service_report(report_id)
    signature_url = await get_signature(approver_chann_uid)
    template = await data.get_service_report_template(report.license_id)
    
    # ส่ง authoritative data + signature เข้า shared document engine
    pdf = await document_engine.render({
        "report": report.report_data,
        "signature": signature_url
    }, template.id)
    
    return pdf
```

### 13.6 Mandatory automated tests

```
test_check_in_out:
  - check-in พร้อม GPS → สำเร็จ
  - check-out โดยไม่กรอก service report → ไม่ให้ check-out (gate)
  - check-out หลังกรอกครบ → สำเร็จ + สร้าง service_report

test_photo_upload:
  - อัปโหลดภาพ → เก็บใน GCS
  - ภาพมี GPS metadata
  - ภาพผูกกับ ticket

test_service_report_pdf:
  - check-out → สร้าง PDF
  - PDF มีข้อมูลถูกต้อง + ลายเซ็น
  - PDF รองรับภาษาไทย

test_multi_tenant_service_report:
  - service_report ใน tenant A ไม่ปรากฏใน tenant B
  - ticket_photos ใน tenant A ไม่ปรากฏใน tenant B
```

### 13.7 Acceptance criteria

- [ ] Check-in/out พร้อม GPS ทำงานได้
- [ ] ภาพหลักฐานเก็บใน GCS ได้
- [ ] Gate: กรอก service report ครบก่อน check-out
- [ ] Service Report PDF สร้างได้ผ่าน shared SmartBrowz document engine
- [ ] ลายเซ็นผูก Chann Identity
- [ ] Multi-tenant isolation test PASS
- [ ] runtime: ช่าง check-in → ทำงาน → check-out → ได้ PDF

### 13.8 Dependencies

- **depends-on:** Phase 12 (ticket), Phase 10 (PDF pattern)
- **blocks:** Phase 14 (approval)

---

## Phase 14 — Approval Workflow + Satisfaction Survey

### 14.1 เป้าหมาย

- Approval Workflow Engine กลาง ใช้ได้กับ Service Report, Quote, ส่วนลด ฯลฯ
- Default: ช่าง check-out → ส่งให้ CS เจ้าของ ticket เช็คก่อน
- Owner/Admin พิมพ์ policy กำหนดลำดับอนุมัติ → AI แปลงเป็น workflow
- อนุมัติ/ปฏิเสธได้ทั้งแชทและ Dashboard
- อนุมัติครบทุกขั้น → ส่ง Survey ให้ลูกค้า

### 14.2 Tier impact

| Tier | งาน |
|---|---|
| Presentation | หน้า approval queue (Dashboard), หน้า approval config |
| Application | approval workflow executor, survey sender |
| Data | `approval_workflows`, `approval_steps`, `satisfaction_surveys` CRUD |
| Database | 3 ตารางใหม่ |

### 14.3 ตารางใหม่

#### `approval_workflows`
```sql
id UUID PK
license_id UUID FK -> licenses.id
entity_type VARCHAR NOT NULL  -- service_report, quote, discount, ...
rules_json JSONB NOT NULL  -- structured workflow from AI
is_active BOOLEAN DEFAULT true
updated_at TIMESTAMPTZ
updated_by UUID FK -> license_members.id
created_at TIMESTAMPTZ
```

#### `approval_steps`
```sql
id UUID PK
license_id UUID FK -> licenses.id
entity_type VARCHAR NOT NULL
entity_id UUID NOT NULL
workflow_id UUID FK -> approval_workflows.id
step_order INT NOT NULL
approver_type VARCHAR NOT NULL  -- role, user
approver_ref VARCHAR NOT NULL  -- role_name หรือ chann_uid
status VARCHAR DEFAULT 'pending'  -- pending, approved, rejected
acted_by UUID FK -> license_members.id
acted_at TIMESTAMPTZ
ai_reasoning TEXT  -- ถ้า AI ตัดสินใจ (ไม่ได้ใช้ใน Phase นี้ แต่วางไว้)
created_at TIMESTAMPTZ
UNIQUE(entity_type, entity_id, step_order)
```

#### `satisfaction_surveys`
```sql
id UUID PK
license_id UUID FK -> services.id
ticket_id UUID FK -> service_tickets.id
scale_config_json JSONB  -- {1: "ไม่ดี", 2: "พอใช้", 3: "ดีเยี่ยม"}
score INT
submitted_at TIMESTAMPTZ
created_at TIMESTAMPTZ
```

### 14.4 Workflow JSON structure

```json
{
  "version": 1,
  "entity_type": "service_report",
  "steps": [
    {
      "order": 1,
      "approver_type": "user",
      "approver_ref": "CS ที่เป็น owner_member_id ของ ticket"
    },
    {
      "order": 2,
      "approver_type": "role",
      "approver_ref": "admin"
    }
  ],
  "on_reject": "notify_submitter",
  "on_all_approved": "send_survey"
}
```

### 14.5 Flow

```
ช่าง check-out → service_report status=submitted
    → สร้าง approval_steps ตาม workflow
    → ส่งให้ approver ขั้นที่ 1 (CS เจ้าของ ticket)
    → แจ้งเตือน CS (LINE + Dashboard)

CS อนุมัติ/ปฏิเสธ
    → พิมพ์ "อนุมัติ SR-2026-0001" หรือกดใน Dashboard
    → ถ้า approved → ส่งขั้นถัดไป
    → ถ้า rejected → แจ้งช่าง + ให้แก้

อนุมัติครบทุกขั้น
    → service_report status=approved
    → แปะลายเซ็นลง PDF
    → ส่ง Survey ให้ลูกค้า (Customer OA quick reply)
```

### 14.6 Mandatory automated tests

```
test_approval_workflow:
  - สร้าง service_report → สร้าง approval_steps
  - อนุมัติขั้น 1 → ส่งขั้น 2
  - อนุมัติครบ → status=approved + ส่ง survey
  - ปฏิเสธ → แจ้งช่าง + status=rejected

test_approval_chat_vs_dashboard:
  - อนุมัติผ่านแชท → ผลเหมือนผ่าน Dashboard
  - ทั้งคู่ใช้ domain service เดียวกัน

test_survey:
  - อนุมัติครบ → ส่ง survey ให้ลูกค้า
  - ลูกค้าตอบ → บันทึก score
  - ไม่ตอบ → ไม่บังคับ (แค่ส่ง)

test_multi_tenant_approval:
  - approval ใน tenant A ไม่ปรากฏใน tenant B
  - approver ใน tenant A ไม่อนุมัติ entity tenant B ได้
```

### 14.7 Acceptance criteria

- [ ] Approval Workflow สร้าง/แก้ได้ผ่าน prompt
- [ ] Multi-step approval ทำงานได้
- [ ] อนุมัติ/ปฏิเสธผ่านแชท + Dashboard
- [ ] อนุมัติครบ → ส่ง Survey
- [ ] Survey บันทึก score ได้
- [ ] Multi-tenant isolation test PASS
- [ ] runtime: ช่าง check-out → CS อนุมัติ → ลูกค้าทำ survey

### 14.8 Dependencies

- **depends-on:** Phase 13 (service report), Phase 6 (notification)
- **blocks:** Phase 15 (live chat — ticket จากแชทต้องผ่าน approval)

---

## Phase 15 — Live Chat Marketplace + SLA

### 15.1 เป้าหมาย

- ลูกค้าเลือก "คุยกับร้าน" → session ใหม่ → แจ้งเตือน Sales/CS
- Sales/CS ทุกคนเห็นใน Dashboard จนกว่าจะ assign
- Sales/CS ตอบใน Dashboard (ไม่ตอบ LINE ตรง) → ระบบ push ไป Customer OA
- Customer OA ระหว่าง session: หยุด AI auto-create record เฉพาะ (ไม่หยุดทั้งหมด)
- SLA การตอบกลับ + escalate ถ้าใกล้เกิน
- Timeout ปิด session อัตโนมัติ

### 15.2 Tier impact

| Tier | งาน |
|---|---|
| Presentation | Live Chat Dashboard (Sales/CS), Customer Dashboard (storefront + chat) |
| Application | chat session handler, message routing, SLA monitor, auto-create pause |
| Data | `chat_sessions`, `chat_messages` CRUD |
| Database | 2 ตารางใหม่ |

### 15.3 ตารางใหม่

#### `chat_sessions`
```sql
id UUID PK
license_id UUID FK -> licenses.id
customer_chann_uid VARCHAR FK -> chann_identities.chann_uid
status VARCHAR DEFAULT 'open'  -- open, assigned, closed, timeout
assigned_to UUID FK -> license_members.id  -- Sales/CS ที่รับ
product_id UUID FK -> products.id  -- สินค้าที่ลูกค้าสนใจ (ถ้ามี)
sla_deadline TIMESTAMPTZ  -- ต้องตอบก่อนเวลานี้
timeout_at TIMESTAMPTZ  -- ปิดอัตโนมัติถ้าไม่มี activity
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
closed_at TIMESTAMPTZ
```

#### `chat_messages`
```sql
id UUID PK
session_id UUID FK -> chat_sessions.id
license_id UUID FK -> licenses.id
sender_type VARCHAR NOT NULL  -- customer, agent, ai, system
sender_chann_uid VARCHAR  -- NULL สำหรับ ai/system
content TEXT NOT NULL
content_en TEXT  -- i18n
is_read BOOLEAN DEFAULT false
created_at TIMESTAMPTZ
```

### 15.4 Flow

```
ลูกค้าเลือก "คุยกับร้าน" ใน Customer OA
    → Application สร้าง chat_session (status=open)
    → แจ้งเตือน Sales/CS ทุกคน (LINE + Dashboard)
    → SLA deadline = now + chat_sla (จาก license_settings)

Sales/CS ตอบ
    → พิมพ์ใน Dashboard (ไม่ตอบ LINE ตรง)
    → ระบบ push ข้อความไป Customer OA
    → session.assigned_to = คนที่ตอบ
    → SLA reset

ลูกค้าตอบกลับ
    → มาที่ Application → ส่งเข้า session
    → Sales/CS เห็นใน Dashboard

AI ระหว่าง session
    → ลูกค้ายังถามหาสินค้า/ดูข้อมูลทั่วไปได้
    → แต่ AI ไม่ auto-create ticket/warranty/deal (ป้องกันชนกับ Sales ที่กำลังคุย)

SLA ใกล้เกิน
    → AI พิจารณา escalate/แจ้งเตือน (อ่านจาก license_settings)
    → ส่ง notification ให้ Sales/CS ที่รับผิดชอบ

Timeout
    → ไม่มี activity เกิน timeout → ปิด session อัตโนมัติ
```

### 15.5 Mandatory automated tests

```
test_chat_session:
  - ลูกค้าเลือกคุย → สร้าง session
  - Sales ตอบ → assigned_to + SLA reset
  - ข้อความไป Customer OA
  - ลูกค้าตอบ → มาที่ session

test_sla_monitor:
  - SLA ใกล้เกิน → แจ้งเตือน
  - SLA เกิน → escalate

test_ai_pause_auto_create:
  - ระหว่าง session: AI ไม่ auto-create ticket/warranty/deal
  - ระหว่าง session: AI ยังค้นสินค้าได้
  - หลัง session ปิด: AI กลับมา auto-create ได้

test_timeout:
  - ไม่มี activity เกิน timeout → ปิด session
  - มี activity → timeout reset

test_multi_tenant_chat:
  - session ใน tenant A ไม่ปรากฏใน tenant B
  - Sales tenant A ไม่เห็น session tenant B
```

### 15.6 Acceptance criteria

- [ ] Live Chat session สร้างได้
- [ ] Sales/CS ตอบใน Dashboard → push ไป Customer OA
- [ ] AI หยุด auto-create record ระหว่าง session
- [ ] SLA monitor + escalate
- [ ] Timeout ปิด session อัตโนมัติ
- [ ] Multi-tenant isolation test PASS
- [ ] runtime: ลูกค้าคุย → Sales ตอบ → ลูกค้าเห็นใน LINE

### 15.7 Dependencies

- **depends-on:** Phase 6 (notification), Phase 9 (storefront/lead), Phase 11 (assignment)
- **blocks:** Phase 18 (platform admin — ดู chat session ทั้งหมด)

---

## Phase 16 — Cross-company Serial Routing + Display Preferences

### 16.1 เป้าหมาย

- ลูกค้าไม่ลงทะเบียน พิมพ์ serial + ชื่อร้าน → ค้นข้ามบริษัท
- ซ้ำหลายบริษัท + ชื่อร้านไม่ชัด → ถามลูกค้าเลือก
- `auto_accept_new_customers` → true + ข้อมูลครบ → auto สร้างลูกค้า
- ไม่เจอ → บอกลูกค้าตรง ๆ
- ทุก cross-tenant lookup ต้อง audit
- Personal Display Preferences (ต่อผู้ใช้ ผูก Chann Identity)

### 16.2 Tier impact

| Tier | งาน |
|---||
| Presentation | หน้าเลือกร้าน (LIFF), หน้าตั้งค่า display preference |
| Application | cross-tenant serial search, auto-create customer, display preference CRUD |
| Data | `user_display_preferences`, cross-tenant query (audit) |
| Database | 1 ตารางใหม่ |

### 16.3 ตารางใหม่

#### `user_display_preferences`
```sql
chann_uid VARCHAR PK FK -> chann_identities.chann_uid
date_format VARCHAR DEFAULT 'dd/mm/yyyy'  -- dd/mm/yyyy, mm/dd/yyyy, yyyy-mm-dd
language VARCHAR DEFAULT 'th'  -- th, en
timezone VARCHAR DEFAULT 'Asia/Bangkok'
updated_at TIMESTAMPTZ
```

### 16.4 Cross-company serial routing flow

```
ลูกค้าพิมพ์ "สอบถาม serial ABC123 ร้าน ACME"
    → Application ค้น warranties ข้ามทุก tenant (cross-tenant)
    → audit log (cross_tenant=true)

ผลลัพธ์:
    1. เจอ 1 บริษัท → ใช้บริษัทนั้น
    2. เจอหลายบริษัท + ชื่อร้านชัด → ใช้บริษัทที่ชื่อตรง
    3. เจอหลายบริษัท + ชื่อร้านไม่ชัด → ถามลูกค้าเลือก
    4. ไม่เจอ → บอกลูกค้าตรง ๆ

หลังเลือกบริษัท:
    → ถ้า auto_accept_new_customers=true + ข้อมูลครบ → auto สร้าง customer
    → ถ้า auto_accept_new_customers=false → ส่ง request ไป Sales channel อนุมัติ
    → ถ้ามี customer อยู่แล้ว → ใช้ customer เดิม
```

### 16.5 Display preference integration

```python
# Application tier — ดึง preference ส่งเข้า AI prompt

async def get_user_context(chann_uid, license_id):
    pref = await data.get_display_preferences(chann_uid)
    return {
        "language": pref.language,
        "date_format": pref.date_format,
        "timezone": pref.timezone,
        ...
    }

# AI ตอบเป็นภาษา + format ที่ผู้ใช้เลือก
```

### 16.6 Mandatory automated tests

```
test_cross_company_serial:
  - serial มีใน 1 บริษัท → ใช้บริษัทนั้น
  - serial มีในหลายบริษัท + ชื่อร้านชัด → ใช้บริษัทที่ตรง
  - serial มีในหลายบริษัท + ชื่อร้านไม่ชัด → ถามลูกค้า
  - serial ไม่มี → บอกลูกค้า

test_auto_accept:
  - auto_accept=true + ข้อมูลครบ → auto สร้าง customer
  - auto_accept=false → ส่ง request ไป Sales
  - customer มีอยู่แล้ว → ใช้เดิม

test_cross_tenant_audit:
  - ทุก cross-tenant lookup มี audit row (cross_tenant=true)
  - ไม่มีข้อมูลร้านอื่น leak ไป tenant อื่น

test_display_preference:
  - ตั้ง language=en → AI ตอบอังกฤษ
  - ตั้ง date_format=mm/dd/yyyy → AI ใช้ format นั้น
  - preference ติดตัวไปทุกบริษัท (ผูก Chann Identity)
```

### 16.7 Acceptance criteria

- [ ] Cross-company serial search ทำงานได้
- [ ] Auto-create customer ตาม `auto_accept_new_customers`
- [ ] ถามลูกค้าเลือกร้านเมื่อไม่ชัด
- [ ] ไม่เจอ → บอกลูกค้า
- [ ] Cross-tenant audit ทุกครั้ง
- [ ] Display preference ใช้ข้ามบริษัทได้
- [ ] runtime: ลูกค้าพิมพ์ serial → ระบบหาร้าน → auto สร้าง customer (ถ้าเปิด)

### 16.8 Dependencies

- **depends-on:** Phase 1 (Chann Identity), Phase 7.5 (warranty), Phase 8 (customer)
- **blocks:** —

---

## Phase 16.5 — PDPA Data Rights

### 16.5.1 เป้าหมาย

- รองรับสิทธิ์ตาม พ.ร.บ. คุ้มครองข้อมูลส่วนบุคคล (PDPA): ขอลบข้อมูล (erasure), ขอข้อมูลของตัวเอง (portability), บันทึกความยินยอม (consent)
- **Erasure = soft-delete/anonymize ไม่ใช่ hard delete** — ป้องกันบั๊กเดิมที่เคยเจอ (ลบแล้ว NULL ใส่ FK column ที่เป็น NOT NULL) และตรงกับหลักการ Chann1 ("archive/disable แทน destructive cascade delete")
- Erasure ต้องล้างข้อมูลข้าม tenant ที่ Chann Identity เดียวกันเคยผูกอยู่ทั้งหมด (ไม่ใช่แค่ tenant เดียว)
- Consent บันทึกตั้งแต่ตอนลงทะเบียน ผูกกับ Chann Identity (global)
- คำขอทำได้ทั้งผ่านแชท (AI) และ Platform Admin Dashboard (สำหรับกรณี dispute/escalate)

### 16.5.2 Tier impact

| Tier | งาน |
|---|---|
| Presentation | หน้าคำขอข้อมูล/ลบข้อมูลใน Dashboard (Platform Admin), ปุ่ม "ขอดู/ลบข้อมูลของฉัน" ใน LIFF profile |
| Application | erasure orchestrator (anonymize ข้าม tenant), export builder, consent capture ตอน register |
| Data | `data_subject_requests` CRUD, anonymize มid ทุก domain (customers, chann_identities), export query ข้าม tenant (audit) |
| Database | `data_subject_requests` ใหม่ + ALTER `chann_identities` เพิ่ม consent columns |

### 16.5.3 ตารางใหม่ / เปลี่ยนแปลง

#### `data_subject_requests`
```sql
id UUID PK
chann_uid VARCHAR NOT NULL FK -> chann_identities.chann_uid
request_type VARCHAR NOT NULL  -- erasure, export, consent_withdraw
status VARCHAR NOT NULL DEFAULT 'pending'  -- pending, processing, completed, rejected
requested_via VARCHAR NOT NULL  -- chat, liff, platform_admin
requested_at TIMESTAMPTZ NOT NULL
completed_at TIMESTAMPTZ
processed_by UUID FK -> platform_admins.id  -- null ถ้า auto-process
rejection_reason VARCHAR
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
```

#### `chann_identities` — เพิ่ม column (ALTER, ไม่กระทบของเดิม)
```sql
ALTER TABLE chann_identities ADD COLUMN consent_accepted_at TIMESTAMPTZ;
ALTER TABLE chann_identities ADD COLUMN consent_version VARCHAR;
ALTER TABLE chann_identities ADD COLUMN anonymized_at TIMESTAMPTZ;  -- null = ยังไม่ถูกลบ
```

### 16.5.4 Erasure flow (anonymize ข้าม tenant)

```
คำขอ erasure เข้ามา (chann_uid = X)
    → สร้าง data_subject_requests row (status=pending)
    → หา license_members ทุกแถวที่ chann_uid = X (ทุก tenant)
    → สำหรับแต่ละ tenant:
        - anonymize customers row ที่ผูกกับ chann_uid นี้ (name/phone/email/address → ค่า placeholder)
        - anonymize service_tickets contact info ที่เกี่ยวข้อง (ถ้ามีเก็บแยก)
        - ลบ/scrub ticket_photos + signature ที่ผูกกับ identity นี้ใน GCS (ตาม retention เดิม)
        - เก็บ row ไว้ (ไม่ลบจริง) — เพื่อ referential integrity กับ deals/tickets/audit_log เดิม
        - audit log (action=pdpa_erasure, cross_tenant=true)
    → anonymize chann_identities.display_name, signature_url → placeholder, ตั้ง anonymized_at = now()
    → line_user_id คงไว้ (ต้องใช้ dedup กันสร้าง identity ซ้ำถ้า user เดิมทักมาใหม่) แต่ระบบต้อง treat เป็น identity ใหม่ที่ต้อง consent ใหม่ถ้ามีปฏิสัมพันธ์อีก
    → data_subject_requests.status = completed
    → แจ้งผลกลับ (LINE push ถ้ายังทักได้ หรือ Dashboard)
```

### 16.5.5 Export flow

```python
# Application tier — รวมข้อมูลข้าม tenant สำหรับ chann_uid เดียว

async def export_my_data(chann_uid):
    identity = await data.get_chann_identity(chann_uid)
    memberships = await data.get_license_members_by_chann_uid(chann_uid)  # cross-tenant, audit
    bundle = {"identity": identity, "companies": []}
    for m in memberships:
        customer = await data.get_customer_by_chann_uid(m.license_id, chann_uid)
        tickets = await data.get_tickets_by_chann_uid(m.license_id, chann_uid)
        warranties = await data.get_warranties_by_chann_uid(m.license_id, chann_uid)
        bundle["companies"].append({
            "license_id": m.license_id, "customer": customer,
            "tickets": tickets, "warranties": warranties
        })
    # audit log (action=pdpa_export, cross_tenant=true)
    return render_pdf_or_json(bundle)  # ใช้ versioned SmartBrowz document-engine pattern (Phase 10)
```

### 16.5.6 Mandatory automated tests

```
test_erasure_anonymizes_not_deletes:
  - ขอ erasure → row ยัง exist ใน customers/chann_identities
  - field ที่เป็น PII ถูก scrub เป็น placeholder
  - FK ที่ผูกกับ deals/tickets/audit_log เดิมยังไม่ขาด (referential integrity คงอยู่)

test_erasure_cross_tenant:
  - identity เดียวกันเป็น customer ใน 2 tenant
  - ขอ erasure ครั้งเดียว → ทั้ง 2 tenant ถูก anonymize
  - audit log มี cross_tenant=true ทั้งคู่

test_erasure_isolation:
  - anonymize chann_uid A ไม่กระทบ chann_uid B เลย

test_consent_recorded:
  - ลงทะเบียนใหม่ → consent_accepted_at + consent_version ถูกบันทึก
  - ไม่ยอมรับ consent → ลงทะเบียนไม่สำเร็จ

test_data_export:
  - ขอ export → ได้ bundle ที่มีข้อมูลครบทุก tenant ที่เคยเป็นสมาชิก
  - ไม่มีข้อมูลของ chann_uid อื่นหลุดมาปน

test_pdpa_request_audit:
  - ทุก erasure/export request มี data_subject_requests row + audit_log ครบ
```

### 16.5.7 Acceptance criteria

- [ ] ขอ erasure ผ่านแชทหรือ Dashboard ได้
- [ ] Erasure = anonymize ไม่ hard delete, ข้าม tenant ครบ
- [ ] Consent บันทึกตอนลงทะเบียนทุกครั้ง
- [ ] Export ข้อมูลได้ครบทุก tenant ที่เคยเป็นสมาชิก
- [ ] Erasure/export test PASS ทั้งหมด
- [ ] runtime: ลูกค้าขอลบข้อมูล → ข้อมูลใน 2 tenant ถูก anonymize จริง, ระบบยังทำงานปกติ (ไม่มี FK error)

### 16.5.8 Dependencies

- **depends-on:** Phase 1 (Chann Identity, license_members), Phase 3 (audit_log), Phase 8 (profiles), Phase 9 (customers), Phase 10 (PDF template สำหรับ export)
- **blocks:** — (ไม่ block phase อื่น แต่ควรทำก่อน Phase 20 Polish เพื่อรวมอยู่ใน final i18n/UX check)

---

---

## Phase 17 — Ad-hoc AI Report Engine

### 17.1 เป้าหมาย

- Sales/CS ขอ report อิสระผ่านแชท (ไม่ต้องตรงกับ list ที่มี)
- AI แปลงคำขอเป็น query spec JSON (DeepSeek thinking on)
- โค้ดแปลง JSON → SQL parameterized (filter license_id เสมอ)
- AI ไม่เขียน SQL เอง — ผ่าน whitelist entity/field/aggregation
- Output 3 แบบ: ข้อความ, ตาราง/กราฟ, Excel/PDF
- ใช้ `view_reports` permission (Sales default, CS ปิด default)

### 17.2 Tier impact

| Tier | งาน |
|---|---|
| Presentation | หน้า report viewer (Dashboard), download link |
| Application | AI query spec generator (DeepSeek), query builder (parameterized), output formatter |
| Data | query execution (parameterized, license_id filter) |
| Database | — (ใช้ตารางเดิม) |

### 17.3 Query Specification Pattern

```python
# Application tier — AI แปลงคำขอเป็น query spec JSON

async def generate_report(user_message, user_context):
    # 1. AI (DeepSeek) แปลงเป็น query spec JSON
    query_spec = await ai.generate_query_spec(user_message, user_context)
    # query_spec = {
    #   "entity": "deals",
    #   "metric": "count",
    #   "filter": {"stage": "won"},
    #   "groupBy": "owner_member_id",
    #   "dateRange": "last_3_months"
    # }
    
    # 2. validate กับ whitelist
    if not validate_query_spec(query_spec):
        return "ไม่สามารถสร้าง report ได้ กรุณาลองใหม่"
    
    # 3. แปลงเป็น SQL parameterized (filter license_id เสมอ)
    sql, params = build_sql(query_spec, user_context.license_id)
    
    # 4. รัน
    result = await data.execute_query(sql, params)
    
    # 5. AI สรุปผล
    summary = await ai.summarize_report(result, user_context.language)
    
    return summary
```

### 17.4 Whitelist

```python
# Application tier — whitelist entity/field/aggregation

ALLOWED_ENTITIES = {
    "deals": ["stage", "owner_member_id", "created_at", "deal_id"],
    "customers": ["stage", "owner_member_id", "created_at"],
    "tickets": ["status", "assigned_to", "created_at", "scheduled_date"],
    "quotes": ["status", "owner_member_id", "created_at"],
    "warranties": ["status", "product_id", "warranty_end"],
}

ALLOWED_METRICS = ["count", "sum", "avg", "min", "max"]
ALLOWED_GROUP_BY = ["owner_member_id", "stage", "status", "product_id"]
ALLOWED_DATE_RANGES = ["today", "last_7_days", "last_30_days", "last_3_months", "last_year"]
```

### 17.5 Mandatory automated tests

```
test_query_spec_generation:
  - "ดูยอดดีลปิดสำเร็จ 3 เดือนล่าสุด" → query_spec ถูกต้อง
  - "สรุป ticket ค้าง แยกตามช่าง" → query_spec ถูกต้อง
  - คำขอไม่ชัด → AI ถามเพิ่ม

test_sql_injection_prevention:
  - พยายาม SQL injection → ถูก block
  - ทุก query filter license_id เสมอ
  - parameterized query เท่านั้น

test_whitelist:
  - entity นอก whitelist → reject
  - field นอก whitelist → reject
  - metric นอก whitelist → reject

test_report_output:
  - ข้อความสรุป → สำเร็จ
  - ตาราง/กราฟ → สำเร็จ
  - Excel/PDF download → สำเร็จ

test_permission:
  - Sales มี view_reports → ใช้ได้
  - CS ไม่มี view_reports → ไม่ใช้ได้ (default)
  - Owner เปิดให้ CS → CS ใช้ได้
```

### 17.6 Acceptance criteria

- [ ] AI แปลงคำขอเป็น query spec JSON ได้
- [ ] Whitelist บังคับ (entity/field/metric)
- [ ] SQL parameterized + filter license_id
- [ ] Output 3 แบบ: ข้อความ, ตาราง, Excel/PDF
- [ ] Permission `view_reports` บังคับ
- [ ] SQL injection test PASS
- [ ] runtime: Sales พิมพ์ "ดูยอดดีล 3 เดือนล่าสุด" → ได้ report

### 17.7 Dependencies

- **depends-on:** Phase 2 (permission), Phase 9 (deal), Phase 12 (ticket), Phase 10 (quote)
- **blocks:** —

---

## Phase 17.5 — Billing & Subscription

### 17.5.1 เป้าหมาย

- Trial → paid subscription lifecycle จริง (ไม่ใช่แค่ดู usage เฉยๆ แบบเดิมใน Phase 18)
- รองรับ payment gateway ไทย (เสนอ Omise หรือ 2C2P — ตัดสินใจจริงตอนเริ่ม Phase นี้)
- Invoice + PDF (ใช้ template-once pattern เดียวกับ Phase 10)
- Trial หมดอายุ → แจ้งเตือนล่วงหน้า → suspend ถ้าไม่จ่าย
- Payment webhook ต้อง idempotent (กัน event ซ้ำจาก provider)
- **หมายเหตุ:** ตาม decision ที่ล็อกไว้ — `max_ai_messages_per_month` ยัง**ไม่ enforce**บล็อกจริงใน phase นี้ แค่ใช้แสดงผล/billing metering เท่านั้น การ enforce จริงเป็นงานทีหลัง

### 17.5.2 Tier impact

| Tier | งาน |
|---|---|
| Presentation | หน้า billing/plan ใน Dashboard, checkout flow, invoice history |
| Application | subscription lifecycle logic, payment webhook handler, trial-expiry cron |
| Data | `subscription_plans`/`license_subscriptions`/`invoices`/`payment_events` CRUD |
| Database | 4 ตารางใหม่ |

### 17.5.3 ตารางใหม่

#### `subscription_plans`
```sql
id UUID PK
plan_code VARCHAR UNIQUE NOT NULL  -- TRIAL, STARTER, PRO, ENTERPRISE
name VARCHAR NOT NULL
price_monthly NUMERIC(18,2) NOT NULL
currency CHAR(3) NOT NULL DEFAULT 'THB'
max_customers INTEGER
max_ai_messages_per_month INTEGER
max_members INTEGER
features_json JSONB
is_active BOOLEAN DEFAULT true
created_at TIMESTAMPTZ
```

#### `license_subscriptions`
```sql
id UUID PK
license_id UUID UNIQUE FK -> licenses.id
plan_id UUID FK -> subscription_plans.id
status VARCHAR NOT NULL  -- trial, active, past_due, canceled, suspended
trial_ends_at TIMESTAMPTZ
current_period_start TIMESTAMPTZ
current_period_end TIMESTAMPTZ
payment_provider VARCHAR  -- omise, 2c2p
provider_subscription_id VARCHAR
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
```

#### `invoices`
```sql
id UUID PK
license_id UUID FK -> licenses.id
subscription_id UUID FK -> license_subscriptions.id
amount NUMERIC(18,2) NOT NULL
currency CHAR(3) NOT NULL DEFAULT 'THB'
status VARCHAR NOT NULL  -- pending, paid, failed, refunded
issued_at TIMESTAMPTZ
paid_at TIMESTAMPTZ
provider_invoice_id VARCHAR
pdf_url VARCHAR  -- GCS signed URL, generated by shared SmartBrowz document engine
created_at TIMESTAMPTZ
```

#### `payment_events` (webhook log — idempotency)
```sql
id UUID PK
provider VARCHAR NOT NULL
provider_event_id VARCHAR UNIQUE NOT NULL  -- กัน process ซ้ำ
event_type VARCHAR NOT NULL
license_id UUID FK -> licenses.id
invoice_id UUID FK -> invoices.id
raw_payload JSONB
received_at TIMESTAMPTZ
processed_at TIMESTAMPTZ
```

### 17.5.4 Trial-expiry cron (ต่อยอด cron pattern เดิมจาก Phase 6)

```
daily cron job:
    → หา license_subscriptions ที่ status=trial และ trial_ends_at ใกล้ถึง (3 วัน, 1 วัน)
        → ส่ง notification (dual delivery — LINE push + Dashboard badge, ตามหลักการข้อ 21)
    → หา license_subscriptions ที่ status=trial และ trial_ends_at ผ่านไปแล้ว + ไม่มี payment
        → status = suspended
        → บล็อก AI chat ใหม่ (ตอบ "กรุณาชำระเงินเพื่อใช้งานต่อ"), Dashboard เข้าได้แบบ read-only
```

### 17.5.5 Payment webhook handler

```python
# Application tier — idempotent webhook processing

async def handle_payment_webhook(provider, payload, signature):
    verify_webhook_signature(provider, payload, signature)  # กัน webhook ปลอม
    event_id = payload["event_id"]

    if await data.payment_event_exists(provider, event_id):
        return  # already processed — idempotent, ไม่ทำซ้ำ

    await data.record_payment_event(provider, event_id, payload)

    if payload["type"] == "payment.succeeded":
        invoice = await data.mark_invoice_paid(payload["invoice_ref"])
        await data.activate_subscription(invoice.license_id)
        await notification.send(invoice.license_id, "ชำระเงินสำเร็จ")
    elif payload["type"] == "payment.failed":
        await data.mark_subscription_past_due(payload["license_id"])
        await notification.send(payload["license_id"], "ชำระเงินไม่สำเร็จ กรุณาลองใหม่")
```

### 17.5.6 Mandatory automated tests

```
test_trial_expiry_notification:
  - trial_ends_at อีก 3 วัน → ส่ง notification
  - trial_ends_at อีก 1 วัน → ส่ง notification อีกครั้ง

test_trial_expiry_suspend:
  - trial_ends_at ผ่านไปแล้ว ไม่มี payment → status=suspended
  - suspended → AI chat ตอบปฏิเสธ, Dashboard read-only

test_webhook_idempotency:
  - ยิง webhook event เดิมซ้ำ 2 ครั้ง → process แค่ครั้งเดียว
  - provider_event_id UNIQUE constraint ป้องกัน duplicate

test_webhook_signature_verification:
  - webhook ไม่มี signature ถูกต้อง → reject

test_invoice_generation:
  - payment สำเร็จ → invoice สร้าง + PDF gen ผ่าน shared SmartBrowz document engine
  - invoice.status = paid, subscription เปลี่ยนเป็น active

test_multi_tenant_billing_isolation:
  - invoice ของ tenant A ไม่ปนกับ tenant B
```

### 17.5.7 Acceptance criteria

- [ ] Trial → active subscription lifecycle ทำงานได้
- [ ] Payment webhook idempotent + signature verify
- [ ] Invoice + PDF gen ได้
- [ ] Trial expiry notification + suspend ทำงานถูกต้อง
- [ ] Billing isolation ต่อ tenant
- [ ] runtime: license trial หมดอายุไม่จ่าย → suspend จริง, จ่ายแล้ว → active กลับมาใช้ได้

### 17.5.8 Dependencies

- **depends-on:** Phase 1 (licenses), Phase 2 (license_settings), Phase 6 (cron pattern, notification dual delivery), Phase 10 (PDF template-once pattern สำหรับ invoice)
- **blocks:** — (ไม่ block phase อื่น แต่ Phase 18 Platform Admin Dashboard ควรอัปเดตให้ดึงข้อมูลจากตารางชุดนี้แทนที่จะเป็นแค่ "ดู usage" เฉยๆ ตามเดิม)

---

---

## Phase 18 — Platform Admin Dashboard

### 18.1 เป้าหมาย

- Platform Admin (ฝั่ง Chai) เข้า Dashboard (username/password)
- ดู tenant ทั้งหมด (list, search, filter)
- ดู usage/billing ต่อ tenant (เช่น จำนวน member, ticket, deal)
- ปิด/ระงับ tenant
- ดู cross-tenant audit log
- Break-glass: force transfer Owner (กรณีฉุกเฉิน)

### 18.2 Tier impact

| Tier | งาน |
|---|---|
| Presentation | Platform Admin Dashboard (web แยก, ไม่ใช่ LIFF), login page |
| Application | platform admin API (filter by platform_admin_id), cross-tenant query (audit), break-glass endpoint |
| Data | cross-tenant query (audit), platform admin session |
| Database | — (ใช้ตารางเดิม) |

### 18.3 Platform Admin API

```python
# Application tier — Platform Admin API

# ทุก endpoint ตรวจ platform_admin session
@app.middleware
async def require_platform_admin(request):
    session = verify_jwt(request.headers["Authorization"])
    if not session.is_platform_admin:
        return 403

# Endpoints:
# GET /api/v1/platform/tenants — ดู tenant ทั้งหมด
# GET /api/v1/platform/tenants/{id} — ดู detail
# PATCH /api/v1/platform/tenants/{id} — ปิด/ระงับ
# GET /api/v1/platform/audit?cross_tenant=true — ดู cross-tenant audit
# POST /api/v1/platform/break-glass/transfer-owner — force transfer Owner
```

### 18.4 Break-glass flow

```
Platform Admin login
    → ดู tenant ที่มีปัญหา
    → กด "Force Transfer Owner"
    → เลือก tenant + คนใหม่
    → ยืนยัน
    → audit log (cross_tenant=true, actor_type=platform_admin)
    → แจ้ง tenant ที่ได้รับการ transfer
```

### 18.5 Mandatory automated tests

```
test_platform_admin_login:
  - login ถูก → JWT
  - login ผิด → 401
  - ไม่มี JWT → redirect login

test_tenant_management:
  - ดู tenant ทั้งหมด → สำเร็จ
  - ปิด tenant → tenant ไม่สามารถ login ได้
  - เปิด tenant กลับ → login ได้

test_cross_tenant_audit_view:
  - ดู audit log cross_tenant=true → สำเร็จ
  - filter ตาม tenant → สำเร็จ
  - filter ตาม actor_type → สำเร็จ

test_break_glass:
  - force transfer Owner → สำเร็จ + audit
  - ไม่สามารถ transfer โดยไม่มี break-glass permission
  - หลัง transfer → คนใหม่เป็น Owner, คนเดิมเป็น Admin

test_isolation:
  - Platform Admin ดูข้อมูล tenant ได้ แต่ไม่แก้ business data
  - Platform Admin ไม่สามารถ login ผ่าน LIFF path
```

### 18.6 Acceptance criteria

- [ ] Platform Admin login (username/password) ทำงานได้
- [ ] ดู tenant ทั้งหมดได้
- [ ] ปิด/ระงับ tenant ได้
- [ ] ดู cross-tenant audit log ได้
- [ ] Break-glass force transfer Owner ทำงานได้
- [ ] runtime: Platform Admin login → ดู tenant → force transfer

### 18.7 Dependencies

- **depends-on:** Phase 1 (platform_admins), Phase 2 (transfer ownership), Phase 3 (audit)
- **blocks:** —

---

## Phase 19 — Rich Menu

### 19.1 เป้าหมาย

สร้าง Rich Menu จริงใน LINE OA ทั้ง 3 ตัว ตามโครงที่ออกแบบไว้:
- Customer OA: 2 หน้า (หลัก + ประวัติ) — 🟠 ส้ม
- Sales OA: 2 หน้า (งานประจำ + จัดการ) — 🟢 เขียว
- Technician OA: 2 หน้า (งาน + โปรไฟล์) — 🔵 น้ำเงิน

### 19.2 Tier impact

| Tier | งาน |
|---|---|
| Presentation | — (Rich Menu เป็น LINE configuration ไม่ใช่โค้ด) |
| Application | Rich Menu setup script (LINE Messaging API), Rich Menu switch endpoint |
| Data | — |
| Database | — |

### 19.3 Rich Menu setup

```python
# scripts/setup_rich_menu.py

# สร้าง Rich Menu ผ่าน LINE Messaging API
# Customer OA: 2 หน้า
# Sales OA: 2 หน้า (หน้าจัดการมีเงื่อนไข Admin/Owner)
# Technician OA: 2 หน้า

# แต่ละปุ่มกำหนด action:
# - เปิด LIFF page
# - ส่งข้อความไปยัง bot (เช่น "ค้นหาสินค้า")
# - สลับ Rich Menu (เช่น ไปหน้า 2)
```

### 19.4 Rich Menu structure (จาก SCOPE_LOCKED.md)

<details>
<summary>Customer OA — หน้าที่ 1: หลัก (🟠 ส้ม)</summary>

```
┌─────────────┬─────────────┐
│  🔍 ค้นหา   │  🛒 สินค้า    │
│  สินค้า     │  ทั้งหมด      │
├─────────────┼─────────────┤
│  📱 แจ้ง    │  📋 ใบ       │
│  ซ่อม       │  รับประกัน    │
├─────────────┼─────────────┤
│  💬 คุยกับ   │  👤 โปรไฟล์  │
│  ร้านค้า    │  ของฉัน      │
└─────────────┴─────────────┘
```
- ค้นหาสินค้า → LIFF storefront (Phase 9) หรือพิมพ์ "ค้นหา [keyword]"
- สินค้าทั้งหมด → LIFF storefront list
- แจ้งซ่อม → แชท AI slot-filling หรือ LIFF form (Phase 12)
- ใบรับประกัน → LIFF warranty list หรือแชท (Phase 7.5)
- คุยกับร้านค้า → เลือกร้าน → เปิด chat session (Phase 15)
- โปรไฟล์ของฉัน → LIFF profile หรือแชท (Phase 8)
</details>

<details>
<summary>Customer OA — หน้าที่ 2: ประวัติ (🟠 ส้ม)</summary>

```
┌─────────────┬─────────────┐
│  📞 งาน     │  🔧 ใบ       │
│  ซ่อมของฉัน  │  รับประกัน   │
│             │  ทั้งหมด      │
├─────────────┼─────────────┤
│  ⭐ ประเมิน │  🗂 คำสั่ง   │
│  ความพึงพอใจ│  ซื้อ/ประวัติ│
├─────────────┼─────────────┤
│  🌐 EN/TH   │  ⬅ กลับ     │
│  สลับภาษา   │  หน้าหลัก    │
└─────────────┴─────────────┘
```
- งานซ่อมของฉัน → LIFF ticket list ของลูกค้า (Phase 12)
- ใบรับประกันทั้งหมด → LIFF warranty list (Phase 7.5)
- ประเมินความพึงพอใจ → เปิด survey quick reply (Phase 14)
- คำสั่งซื้อ/ประวัติ → LIFF deal history ของลูกค้า (Phase 9)
- EN/TH → สลับภาษา (Phase 5)
- กลับหน้าหลัก → สลับ Rich Menu
</details>

<details>
<summary>Sales OA — หน้าที่ 1: งานประจำ (🟢 เขียว)</summary>

```
┌─────────────┬─────────────┐
│  📊 Dashboard│  📋 รายการ  │
│  (Live Chat,│  รออนุมัติ   │
│   CRM, Rpt) │             │
├─────────────┼─────────────┤
│  👥 ลูกค้า   │  💼 ดีล      │
│  (Customer) │  (Deal)     │
├─────────────┼─────────────┤
│  📝 ใบเสนอ  │  📈 รายงาน   │
│  ราคา       │             │
└─────────────┴─────────────┘
```
- Dashboard → LIFF/เข้า web Dashboard (Phase 15)
- รายการรออนุมัติ → Dashboard approval queue (Phase 14)
- ลูกค้า → แชท AI สร้าง/ดู customer หรือเข้า Dashboard (Phase 8, 9)
- ดีล → แชท AI สร้าง/ดู deal หรือเข้า Dashboard (Phase 9)
- ใบเสนอราคา → แชท AI สร้าง quote หรือเข้า Dashboard (Phase 10)
- รายงาน → แชท AI ขอ report หรือเข้า Dashboard (Phase 17)
</details>

<details>
<summary>Sales OA — หน้าที่ 2: จัดการ (🟢 เขียว, Admin/Owner เท่านั้น)</summary>

```
┌─────────────┬─────────────┐
│  ⚙ ตั้งค่า   │  👥 ทีม      │
│  (Policy)   │  (Members)  │
├─────────────┼─────────────┤
│  🔐 สิทธิ์    │  📜 Audit   │
│  (Roles)    │  Log        │
├─────────────┼─────────────┤
│  🌐 EN/TH   │  ⬅ กลับ     │
│  สลับภาษา   │  หน้าหลัก    │
└─────────────┴─────────────┘
```
- ตั้งค่า → prompt-config ผ่านแชทหรือ Dashboard (Phase 2, 11, 14, 15)
- ทีม → จัดการ members (Phase 2, 7)
- สิทธิ์ → จัดการ roles (Phase 2)
- Audit Log → ดูใน Dashboard (Phase 3)
- EN/TH → สลับภาษา (Phase 5)
- กลับหน้าหลัก → สลับ Rich Menu
</details>

<details>
<summary>Technician OA — หน้าที่ 1: งาน (🔵 น้ำเงิน)</summary>

```
┌─────────────┬─────────────┐
│  📅 ตาราง   │  🔧 งาน     │
│  งานของฉัน  │  ที่รับ      │
├─────────────┼─────────────┤
│  ✅ Check   │  📝 Service │
│  -in/out    │  Report    │
├─────────────┼─────────────┤
│  📸 ภาพ     │  📋 ประวัติ │
│  หลักฐาน    │  งาน        │
└─────────────┴─────────────┘
```
- ตารางงานของฉัน → LIFF/แชท (Phase 12)
- งานที่รับ → ดู ticket ที่ assign มา (Phase 12)
- Check-in/out → LIFF หรือแชท (Phase 13)
- Service Report → กรอกผ่านแชทหรือ LIFF (Phase 13)
- ภาพหลักฐาน → ดู/อัปโหลด (Phase 13)
- ประวัติงาน → LIFF (Phase 13)
</details>

<details>
<summary>Technician OA — หน้าที่ 2: โปรไฟล์ (🔵 น้ำเงิน)</summary>

```
┌─────────────┬─────────────┐
│  👤 โปรไฟล์ │  🏢 บริษัท   │
│  ของฉัน     │  ที่สังกัด    │
├─────────────┼─────────────┤
│  ⭐ ประเมิน │  🌐 EN/TH   │
│  (ถ้ามี)    │  สลับภาษา   │
├─────────────┼─────────────┤
│  ⬅ กลับ     │  🏠 หน้า     │
│  หน้าหลัก   │  แรก        │
└─────────────┴─────────────┘
```
- โปรไฟล์ของฉัน → แก้ profile (Phase 8)
- บริษัทที่สังกัด → เลือก tenant ถ้าอยู่หลายบริษัท (Phase 16)
- ประเมิน (ถ้ามี) → เปิด survey (Phase 14)
- EN/TH → สลับภาษา (Phase 5)
- กลับ/หน้าแรก → สลับ Rich Menu
</details>

### 19.5 Mandatory automated tests

```
test_rich_menu_creation:
  - สร้าง Rich Menu ผ่าน LINE API → สำเร็จ
  - แต่ละปุ่มมี action ถูกต้อง (LIFF URL หรือ message)

test_rich_menu_switch:
  - กด "กลับหน้าหลัก" → สลับไปหน้า 1
  - กดปุ่มที่สลับหน้า → สลับไปหน้า 2

test_rich_menu_permission:
  - Sales/CS ไม่เห็นปุ่มจัดการ (ตั้งค่า/สิทธิ์/Audit) ใน Sales OA
  - Admin/Owner เห็นปุ่มจัดการ
```

### 19.6 Acceptance criteria

- [ ] Rich Menu ทั้ง 6 หน้า (3 OA × 2) สร้างใน LINE ได้
- [ ] แต่ละปุ่ม action ถูกต้อง
- [ ] สลับหน้าได้
- [ ] สีหลักตาม OA (ส้ม/เขียว/น้ำเงิน)
- [ ] runtime: กดปุ่ม Rich Menu → ไปหน้า/แชทที่ถูกต้อง

### 19.7 Dependencies

- **depends-on:** ทุก phase ก่อนหน้า (ต้องมีหน้า/แชทที่ปุ่มจะ link ไป)
- **blocks:** Phase 20

---

## Phase 20 — Polish & Final i18n + UX

### 20.1 เป้าหมาย

- Final i18n pass — ตรวจทุกหน้า + ทุกข้อความบอท ว่า TH/EN ครบ
- UX polish — ปรับ flow ที่ไม่ smooth, เพิ่ม loading state, error message
- Performance — optimize slow query, cache hit rate, AI response time
- Accessibility — basic WCAG (เช่น label, contrast)
- Documentation — ปิดท้ายด้วย evidence ว่าทุก phase PROVEN

### 20.2 Tier impact

| Tier | งาน |
|---|---|
| Presentation | UX polish, loading state, error message, accessibility, i18n check |
| Application | performance optimize, error handling, AI response time |
| Data | query optimize, cache hit rate |
| Database | index check |

### 20.3 Final i18n check

```
checklist:
  - ทุกหน้า LIFF → TH/EN ครบ
  - ทุกหน้า Dashboard → TH/EN ครบ
  - ทุกข้อความบอท → TH/EN ครบ
  - notification message → TH/EN ครบ
  - error message → TH/EN ครบ
  - Rich Menu label → TH/EN ครบ
```

### 20.4 Performance checklist

```
- AI intent parsing p95 < 1.5 วิ (จาก ADR-014)
- API response p95 < 500ms
- DB query p95 < 100ms
- Cache hit rate > 80%
- Page load < 3 วิ
```

### 20.5 Mandatory automated tests

```
test_i18n_complete:
  - ทุก key ใน th.ts มีใน en.ts
  - ทุก key ใน en.ts มีใน th.ts
  - ไม่มี hardcoded Thai ในโค้ด (ต้องผ่าน dictionary)

test_performance:
  - AI intent p95 < 1.5 วิ
  - API p95 < 500ms
  - DB p95 < 100ms

test_accessibility:
  - ทุก form มี label
  - contrast ratio > 4.5:1
  - keyboard navigation ได้
```

### 20.6 Acceptance criteria

- [ ] i18n TH/EN ครบทุกหน้า + ทุกข้อความ
- [ ] UX polish — ไม่มี flow ที่ดูครึ่ง ๆ กลาง ๆ
- [ ] Performance ผ่านเกณฑ์
- [ ] Basic accessibility ผ่าน
- [ ] Documentation: ทุก phase PROVEN
- [ ] runtime: ใช้ระบบจริง — ไม่มีจุดที่รู้สึกว่ายังไม่เสร็จ

### 20.7 Dependencies

- **depends-on:** ทุก phase
- **blocks:** — (phase สุดท้าย)
```

---

# สรุปทั้งหมด

เอกสารที่ผลิตแล้ว:

| เอกสาร | สถานะ |
|---|---|
| **SCOPE_LOCKED.md** | ✅ พร้อมใช้ — 22 หลักการ, 20 phase, 3 MVP, OA บทบาท, Rich Menu, Notification, Cloud, Evidence model |
| **ARCHITECTURE_DECISIONS.md** | ✅ พร้อมใช้ — 19 ADR ครอบคลุมทุกจุดที่ต่างจาก Chann1 |
| **REQUIREMENTS.md** | ✅ พร้อมใช้ — Phase 1-20 ละเอียดครบ + Permission keys appendix |

## สิ่งที่ได้จากการทำ requirement ครบ

1. **Schema รวมทั้งหมด** — รู้ทุกตารางตั้งแต่ Phase 1 ไม่ต้อง migrate ทีหลัง
2. **Permission keys รวม** — ใส่ใน Phase 2 seed ทีเดียว
3. **Dependency map** — รู้ว่า Phase N ต้องการอะไรจาก Phase ก่อนหน้า
4. **Cache contract รวม** — รู้ทุก object ที่ต้อง cache
5. **Test plan รวม** — รู้ทุก test ที่ต้องมี
6. **AI coding agent ทำงานได้ดีขึ้น** — รู้ภาพรวมตั้งแต่ต้น

## จุดที่อาจต้องเช็คก่อนเริ่ม

ถ้าจะเริ่ม Phase 1 จริง ผมแนะนำให้เช็คสิ่งนี้ก่อน:

1. **Schema รวม** — ว่าทุกตารางที่ Phase 2-20 ต้องการ วางไว้ใน Phase 1 หมดแล้ว (เช่น `customers`, `service_tickets` อาจต้องสร้างใน Phase 1 หรือรอ phase นั้น)
2. **Permission keys seed** — ว่า list ใน Phase 2 seed ครบทุก key ที่ Phase 3-20 ใช้
3. **AI coding agent** — ว่าจะใช้ agent ตัวไหน (Claude Code, Cursor, etc.) และจะให้มันอ่านเอกสารไหนก่อนเริ่ม

อยากให้ผมเช็คสิ่งนี้ให้ก่อนเริ่ม Phase 1 ไหมครับ หรือมีแก้ไขจุดไหนในเอกสารบอกได้
---

## (C) ปัญหาที่สำคัญที่สุด — ขัดกับหลักการตัวเอง

**Phase 19 (Rich Menu) test_rich_menu_permission เช็คด้วยชื่อ role ตรงๆ:**
> "Sales/CS ไม่เห็นปุ่มจัดการ... Admin/Owner เห็นปุ่มจัดการ"

นี่ hardcode role name ("Admin"/"Owner") ตรงๆ ขัดกับหลักการข้อ 10 ที่ทั้งเอกสารยึดถือทุกที่อื่น (permission key เท่านั้น ห้าม hardcode role name) — ควรแก้เป็นเช็คว่า user มี permission key กลุ่มจัดการอย่างน้อย 1 ตัว (เช่น `role.manage` OR `setting.manage` OR `platform.admin.access`) แทนการเช็คชื่อ role ตรงๆ เพื่อให้ custom role ที่ไม่ใช่ Admin/Owner แต่มีสิทธิ์จัดการ ก็เห็นปุ่มได้ถูกต้องด้วย

## (A) Naming mismatch ที่ต้องแก้ — contact vs customer

Appendix เดิมเขียน `contact.read/create/update/archive` (Phase 8,9) แต่ **ไม่มีตาราง `contacts` อยู่จริงในเอกสารเลย** — Phase 9 นิยาม entity เป็น `customers` table (stage = lead | contact) และ Phase 8 เช็ค permission จริงด้วยชื่อ `customer.update` (บรรทัด 1955 ของ REQUIREMENTS.md) ไม่ใช่ `contact.update`

→ **แก้ appendix เป็น `customer.read/create/update/archive`** ให้ตรงกับ entity/permission key ที่ใช้จริงในโค้ด

## (B) Permission key ที่ไม่เคยถูกนิยามเลย แต่ควรมี

| Phase | การกระทำที่ต้องมี gate | เดิมมี key ไหม |
|---|---|---|
| 7 | สร้าง/แก้ product (ทีละตัว + CSV upload) | ❌ ไม่มีเลย |
| 7 | จัดการ sales_groups / technician_teams (สมาชิก, หัวหน้าทีม) | ❌ ไม่มีเลย |
| 11 | ตั้ง policy prompt → assignment rule JSON | ❌ ไม่มีเลย |
| 15 | ดู chat session ที่เปิดอยู่ (ก่อน assign) | ❌ ไม่มีเลย |
| 15 | รับ (claim) chat session ให้ตัวเอง | ❌ ไม่มีเลย |
| 15 | ตอบใน chat session | ❌ ไม่มีเลย |
| 15 | โอน chat session ให้คนอื่น | ❌ ไม่มีเลย |
| 3/18 | Owner/Admin ดู audit log ของ tenant ตัวเอง (แยกจาก Platform Admin cross-tenant) | ❌ ไม่มีเลย |
| 18 | Force-transfer Owner (break-glass) — test อ้างถึง "break-glass permission" แต่ไม่เคยนิยาม key จริง | ❌ อ้างถึงในเทสต์แต่ไม่มี key |

---

## Appendix ฉบับแก้ไข/สมบูรณ์ (แทนที่ตารางเดิมทั้งหมด)

| Key | คำอธิบาย | Phase | สถานะ |
|---|---|---|---|
| `customer.read/create/update/archive` | จัดการ customer/lead/contact (ชื่อแก้จาก `contact.*`) | 8, 9 | 🔧 แก้ชื่อ |
| `deal.read/create/update/archive/reopen` | จัดการ deal + reopen | 9 | เดิม |
| `note.read/create/update` | จัดการ note | 9 | เดิม |
| `followup.read/create/update` | จัดการ follow-up | 6 | เดิม |
| `product.manage` | สร้าง/แก้/ลบ product (ทีละตัว + CSV) | 7 | 🆕 ใหม่ |
| `team.manage` | จัดการ sales_groups / technician_teams + สมาชิก | 7 | 🆕 ใหม่ |
| `assignment_rule.manage` | ตั้ง policy prompt → assignment rule | 11 | 🆕 ใหม่ |
| `ticket.read/create/update/assign/close` | จัดการ ticket + assign ให้ช่าง | 12 | เดิม |
| `quote.read/create/update` | จัดการ quote | 10 | เดิม |
| `service_report.read/create/update` | จัดการ service report | 13 | เดิม |
| `approval.view/approve/reject` | approval workflow | 14 | เดิม |
| `chat_session.view` | ดู chat session ที่เปิดอยู่ก่อน assign | 15 | 🆕 ใหม่ |
| `chat_session.claim` | รับ chat session เป็นของตัวเอง | 15 | 🆕 ใหม่ |
| `chat_session.reply` | ตอบใน chat session | 15 | 🆕 ใหม่ |
| `chat_session.transfer` | โอน chat session ให้คนอื่น | 15 | 🆕 ใหม่ |
| `reassign_records` | โอน record ให้คนอื่น | 2 | เดิม |
| `view_reports` | ดู ad-hoc report | 17 | เดิม |
| `role.manage` | จัดการ role | 2 | เดิม |
| `member.manage` | จัดการสมาชิก | 2 | เดิม |
| `setting.manage` | แก้ license_settings | 2 | เดิม |
| `warranty.read/create/update` | จัดการ warranty | 7.5 | เดิม |
| `audit_log.view` | ดู audit log ของ tenant ตัวเอง (ไม่ใช่ cross-tenant) | 3 | 🆕 ใหม่ |
| `platform.admin.access` | Platform Admin Dashboard | 18 | เดิม |
| `platform.admin.break_glass` | Force-transfer Owner แบบ break-glass | 18 | 🆕 ใหม่ (เดิมแค่พูดถึงในเทสต์ ไม่เคยนิยาม) |
| `pdpa.request.view` | ดูคำขอ erasure/export | 16.5 | 🆕 ใหม่ |
| `pdpa.request.process` | ดำเนินการ erasure/export | 16.5 | 🆕 ใหม่ |
| `billing.view` | ดู billing/invoice ของ tenant ตัวเอง | 17.5 | 🆕 ใหม่ |
| `billing.manage` | จัดการ payment/plan (Owner เท่านั้น) | 17.5 | 🆕 ใหม่ |

## แนะนำ default role template (Phase 2) ที่ต้องอัปเดตตาม key ใหม่

- **Owner**: ทุก key (ตามเดิม — fixed role)
- **Admin**: ทุก key ยกเว้น `platform.admin.*` (เดิม + เพิ่ม `product.manage`, `team.manage`, `assignment_rule.manage`, `chat_session.*`, `billing.manage`, `pdpa.request.*`)
- **Member (Sales/Tech ทั่วไป)**: `customer.*`, `deal.*`, `note.*`, `followup.*`, `quote.*`, `ticket.*`, `chat_session.view/claim/reply`, `view_reports`, `reassign_records`, `billing.view` — ไม่มี `*.manage`, ไม่มี `chat_session.transfer`
- **CS**: `ticket.*`, `service_report.*`, `approval.*`, `chat_session.*`, `audit_log.view` ไม่เปิด `view_reports` เป็น default (ตามหลักการข้อ 22)

---

# สรุปเอกสารทั้งชุด (รวม addendum แล้ว)

| เอกสาร | เนื้อหา | สถานะ |
|---|---|---|
| **SCOPE_LOCKED.md** | 22 หลักการ, 20 phase + Phase 16.5/17.5, 3 MVP, OA บทบาท, Rich Menu, Notification, Cloud, Evidence model | ✅ พร้อมใช้ |
| **ARCHITECTURE_DECISIONS.md** | 19 ADR ครอบคลุมทุกจุดที่ต่างจาก Chann1 | ✅ พร้อมใช้ |
| **REQUIREMENTS.md** | Phase 1-20 ละเอียดครบทุก Phase (รวม Phase 16.5 PDPA และ 17.5 Billing ที่แทรกใหม่) + Permission keys appendix ฉบับแก้ไข | ✅ พร้อมใช้ทั้งหมด |

**การเปลี่ยนแปลงจากฉบับเดิม:**
- เพิ่ม Phase 16.5 — PDPA Data Rights (หลัง Phase 16)
- เพิ่ม Phase 17.5 — Billing & Subscription (หลัง Phase 17)
- แก้ Permission Keys Appendix: เปลี่ยน `contact.*` → `customer.*` ให้ตรงกับ entity จริง, เพิ่ม 9 key ที่ไม่เคยนิยาม, ปรับ default role template
- แก้ Phase 19 Rich Menu ให้เช็ค permission key แทนการ hardcode ชื่อ role
- แก้ข้อความสรุปท้ายเอกสาร (เดิมบอกผิดว่า Phase 11-20 เป็นแค่ summary — ที่จริงละเอียดครบอยู่แล้ว)

**พร้อมเริ่ม Phase 1 ได้เลยครับ** ตามลำดับใน `CLAUDE.md`
