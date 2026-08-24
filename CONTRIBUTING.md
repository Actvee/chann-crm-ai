# Contributing — Chann CRM AI

## 0. ข้อจำกัดข้อเดียวที่กำหนดทุกอย่างในเอกสารนี้

**โปรเจกต์นี้ตั้งใจไม่ใช้ Secret Manager** (ดู `CLAUDE.md` §5 — reduced security
posture) ความลับทั้งหมด — LINE channel secrets/tokens ทั้ง 3 OA, `admin_secret`,
`jwt_secret`, `database_password`, `openrouter_api_key` — อยู่รวมกันในไฟล์เดียวคือ
`infrastructure/terraform/envs/dev/terraform.tfvars` ซึ่งถูก gitignore ไว้

ผลที่ตามมาโดยตรง และเป็นสิ่งที่ตั้งใจ:

> **คนที่ deploy ได้ = คนที่ถือ `terraform.tfvars` เท่านั้น**
> การเพิ่มคน deploy หมายถึงการแจกความลับทั้งชุด ซึ่งไม่ทำ

ดังนั้นโครงสร้างทีมคือ **หลายคนเขียนโค้ด แต่คนเดียว deploy** ทุกอย่างข้างล่างนี้
ออกแบบให้คนที่ไม่มี secret ทำงานได้เต็มที่โดยไม่ต้องแตะ GCP เลย

---

## 1. ใครทำอะไรได้

| บทบาท | ทำได้ | ทำไม่ได้ |
|---|---|---|
| **Contributor** | เขียนโค้ดทุก tier, รันครบทุก test บนเครื่องตัวเอง, ส่ง PR | deploy, แตะ GCP, แตะ terraform state |
| **Release owner** (ถือ tfvars) | ทุกอย่าง + merge + deploy | — |

Contributor **ไม่จำเป็นต้องมี** GCP account, gcloud, terraform, หรือ secret ใดๆ

---

## 2. ตั้งเครื่องครั้งแรก

```bash
git clone https://github.com/Actvee/chann-crm-ai.git
cd chann-crm-ai
docker compose up --build
```

ได้ครบทั้ง 4 tier บนเครื่องตัวเอง พร้อม Postgres + Redis:

| Tier | URL |
|---|---|
| Presentation | http://localhost:3000 |
| Application | http://localhost:8080 |
| Data | http://localhost:8081 |
| Postgres | localhost:5432 (`chann`/`chann`) |
| Redis | localhost:6379 |

`docker-compose.yml` ใส่ค่า dev ปลอมไว้ให้แล้ว **ห้ามเอา secret จริงมาใส่ในนั้น**
— ไฟล์นี้ commit ขึ้น git

ตั้ง schema ครั้งแรก:

```bash
cd database
pip install -r requirements.txt -r ../data/requirements.txt
DATABASE_URL="postgresql+psycopg://chann:chann@localhost:5432/chann_crm_ai" alembic upgrade head
DATABASE_URL="postgresql+psycopg://chann:chann@localhost:5432/chann_crm_ai" python3 scripts/seed_reference.py
```

> `alembic/env.py` import `chann_data.db` จึงต้องลง `data/requirements.txt` ด้วย
> ไม่ใช่แค่ `database/requirements.txt` — จุดนี้พลาดกันบ่อย

### สิ่งที่ทำไม่ได้บนเครื่องตัวเอง

- **LINE webhook จริง** — ต้องมี channel secret จริง ทดสอบด้วย unit test แทน
- **OpenRouter จริง** — ถ้าจะทดสอบ AI จริง ใช้ key ของตัวเองใส่ผ่าน env var
  ชั่วคราว **ห้าม commit**
- **LIFF** — ต้องมี LIFF ID จริงและเปิดผ่านแอป LINE

---

## 3. จองงานก่อนเริ่ม — สำคัญมาก

Phase หลังๆ แก้ไฟล์เดียวกันหมด (`data/chann_data/models.py`,
`data/chann_data/routers/internal.py`, `data/chann_data/schemas.py`) ถ้าสองคน
ทำคนละ phase พร้อมกันจะ conflict หนัก

**กติกา:**

1. เปิด GitHub Issue ชื่อ `Phase N — <ชื่อ>` ก่อนเริ่มเขียนโค้ด
2. assign ตัวเอง — **หนึ่ง phase หนึ่งคน**
3. เช็ค `depends-on` ใน `docs/CHANN_CRM_AI_MASTER_SPEC.md` ว่า phase ที่ต้องมาก่อน
   เสร็จแล้วจริง (สเปคระบุไว้ทุก phase)
4. ถ้าจะแตะไฟล์กลางที่ระบุข้างบน บอกในอิสชูก่อน

**Phase ที่ทำขนานกันได้ปลอดภัย** คือ phase ที่แตะคนละ tier เช่นงาน Presentation
ล้วน กับงาน Data ล้วน — boundary test บังคับไม่ให้ล้ำเส้นกันอยู่แล้ว

---

## 4. Workflow

```bash
git checkout -b phase-N-short-name
# เขียนโค้ด
./scripts/phase2-source-verify.sh          # ต้อง PASS
git add -A                                  # -A เสมอ ไม่ใช่รายชื่อไฟล์
git status                                  # ต้องสะอาด ไม่มีไฟล์ตกหล่น
git commit
git push origin phase-N-short-name
# เปิด PR
```

### `git add -A` ไม่ใช่รายชื่อไฟล์ — ทำไมถึงย้ำ

โปรเจกต์นี้เคยเสียหายจากเรื่องนี้ **3 ครั้ง**: ไฟล์ใหม่ตกหล่นจาก commit ทำให้
`main` import โมดูลที่ไม่มีใน repo (clone ใหม่แล้ว service boot ไม่ขึ้น) และครั้ง
หนึ่งทั้ง feature หายไปทั้ง phase โดยที่ DEV ยังทำงานปกติ เพราะ image ถูก build
จาก working tree ที่มีไฟล์ครบ — **ซึ่งเป็นสิ่งที่ปิดบังปัญหาไว้**

`git status` ต้องสะอาดหลัง commit เสมอ ถ้าเหลือ untracked แปลว่ายังไม่ครบ

---

## 5. Definition of Done (ก่อนเปิด PR)

- [ ] `./scripts/phase2-source-verify.sh` ผ่าน — แนบ
      `phase*-source-verification-*.txt` ใน PR
- [ ] มี test ตาม "Mandatory automated tests" ของ phase นั้นใน MASTER_SPEC ครบ
- [ ] `git status` สะอาด
- [ ] ไม่มี secret ในโค้ด — `git diff origin/main | grep -iE 'sk-or-|CHANNEL_ACCESS|password.*='`
      ต้องไม่เจออะไร
- [ ] ถ้ามี migration ใหม่ — รัน `alembic upgrade head` แล้ว `alembic downgrade -1`
      บนเครื่องตัวเองได้จริง

**ห้ามเขียนว่าเสร็จถ้ายังไม่ได้รันจริง** — โปรเจกต์นี้ยึด "boot ของจริงแล้ว assert
กับ DB จริง" ไม่ใช่ "โค้ดดูถูกต้องแล้ว"

---

## 6. Deploy — Release owner เท่านั้น

```bash
# 1. merge PR
# 2. build + push image (เฉพาะ tier ที่เปลี่ยน)
# 3. อัปเดต image_digests + git_commit ใน envs/dev/terraform.tfvars
# 4. GODEBUG=netdns=go APP_ENV=dev CHANN_ALLOW_DEV_TERRAFORM_PLAN=YES ./scripts/dev-infra-plan.sh
# 5. GODEBUG=netdns=go terraform apply "<plan file>"
# 6. migration (ถ้ามี) รันแยกด้วยมือ
```

**DEV มีชุดเดียว** — ห้าม deploy ทับกัน ประกาศในทีมก่อน deploy ทุกครั้ง

รายละเอียดเต็มอยู่ใน `CLAUDE.md` §7 และ §10

---

## 7. กติกาสำหรับ AI coding agent

ทุกคนในทีมใช้ AI ช่วยเขียน — `CLAUDE.md` + `docs/CHANN_CRM_AI_MASTER_SPEC.md`
คือ shared context ที่ทำให้ AI ของทุกคนเล่นตามกติกาเดียวกัน **ให้ agent อ่าน 2
ไฟล์นี้ก่อนเสมอ**

เพิ่มเติมสำหรับกรณีหลายคน/หลาย agent:

- **agent ห้ามรัน `terraform apply` เอง** — หยุดที่ plan เสมอ ให้คนตัดสินใจ
- **agent ห้ามรัน `gcloud` ที่เปลี่ยนสถานะ** — รวมถึงการตอบ `y` ให้ prompt
  "enable this API?" (เคยเกิดมาแล้วจากสคริปต์ที่โฆษณาว่า read-only)
- **agent ห้ามแตะไฟล์นอก tier ที่กำลังทำ** โดยไม่บอก
- ถ้า agent จะสร้าง patch ให้ validate ด้วยการ apply บน clone สดก่อนส่ง
- ให้ agent เขียน test ที่ **ยิงของจริง** (Postgres จริงผ่าน docker compose)
  ไม่ใช่ mock ทุกชั้น — bug 2 ตัวที่เจอในโปรเจกต์นี้ถูกจับได้เพราะทดสอบกับ
  runtime จริงเท่านั้น

---

## 8. ห้ามเด็ดขาด

- ❌ commit `terraform.tfvars`, `*.tfvars.bak-*`, `.env`, `backend.hcl`
- ❌ ใส่ secret จริงใน `docker-compose.yml`
- ❌ แตะ IAM / Service Account / Secret Manager (`CLAUDE.md` §5, §14)
- ❌ `terraform apply` โดยไม่ได้เป็น release owner
- ❌ แก้ migration ที่ push ไปแล้ว — เขียนตัวใหม่แทนเสมอ
- ❌ hardcode ชื่อ role (`"owner"`, `"admin"`) — ใช้ permission key เท่านั้น
      (Principle #10) เคยเป็นบั๊กที่ทำให้ user ถูกล็อกออกจากบริษัทตัวเองมาแล้ว

---

## 9. ติดปัญหาบ่อยๆ

| อาการ | สาเหตุ |
|---|---|
| `ModuleNotFoundError: chann_data` ตอนรัน pytest | ต้องรันจาก repo root ให้ `pytest.ini` ทำงาน หรือใช้ `phase2-source-verify.sh` ที่สร้าง venv สะอาดเอง |
| `alembic` import error | ยังไม่ได้ลง `data/requirements.txt` |
| Docker build ล้ม | build context คือโฟลเดอร์ของ tier เอง (`docker build data`) ไม่ใช่ repo root |
| test ผ่านบนเครื่องแต่ CI ไม่ผ่าน | เครื่องคุณมี package ค้างจากงานอื่น — เชื่อ `phase2-source-verify.sh` เท่านั้น |
