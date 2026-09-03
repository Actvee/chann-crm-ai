#!/usr/bin/env bash
# richmenu-apply.sh — สร้าง+อัปโหลด+ตั้ง rich menu ให้ 3 OA (2 หน้าต่อ OA — Phase 19)
#
# ใช้:
#   export LINE_SALES_TOKEN=... LINE_TECHNICIAN_TOKEN=... LINE_CUSTOMER_TOKEN=...
#   export LIFF_SALES=https://liff.line.me/xxxx-sales          # ใส่เท่าที่มี
#   export LIFF_TECHNICIAN=https://liff.line.me/xxxx-tech
#   export LIFF_TECHNICIAN_REPORTS=https://liff.line.me/xxxx-tech/reports
#   export LIFF_CUSTOMER=https://liff.line.me/xxxx-customer
#   bash scripts/richmenu/richmenu-apply.sh            # ทั้ง 3 OA
#   bash scripts/richmenu/richmenu-apply.sh technician # OA เดียว
#
# แต่ละ OA มี 2 เมนู: หน้าหลัก (default) และ เพิ่มเติม — แท็บบนหัวเมนูสลับกันด้วย
# rich menu alias (chann-<oa>-main / chann-<oa>-more) ตามที่ generate.py ใส่ไว้ใน json
#
# ปุ่มที่เป็น uri แต่ LIFF ยังไม่ตั้งค่า จะถูกแปลงเป็น message action
# อัตโนมัติ (ส่งข้อความชื่อเมนูแทน) — เมนูใช้ได้ทันทีวันนี้ และอัปเกรด
# เป็นลิงก์ได้ทีหลังโดยรันซ้ำ ไม่มีปุ่มตายให้ลูกค้ากดค้าง
# {LIFF_X}/path ก็ได้ — แทนเฉพาะส่วน {LIFF_X} แล้วต่อ path ให้
#
# รันซ้ำได้: ลบ alias และเมนูชื่อ chann-<oa>-* ทั้งหมดก่อนสร้างใหม่เสมอ

set -euo pipefail
cd "$(dirname "$0")"

die(){ printf 'HALT: %s\n' "$*" >&2; exit 1; }
info(){ printf '  %s\n' "$*"; }

command -v jq >/dev/null || die "ต้องมี jq (sudo apt-get install -y jq)"
[ -f out/richmenu-sales.png ] || die "ยังไม่มีรูป — รัน python3 generate.py ก่อน"
[ -f out/richmenu-sales-more.png ] || die "ยังไม่มีรูปหน้า 2 — รัน python3 generate.py (เวอร์ชัน 2 หน้า) ก่อน"

# แทนค่า {LIFF_*} ใน uri (รวม path ต่อท้าย); ตัวไหนไม่มีค่า → message action ชื่อเมนูแทน
_resolve_body() {
  jq -c '
    del(._alias) |
    .areas = [ .areas[] |
      if .action.type == "uri" then
        .action.uri as $u
        | ($u | capture("\\{(?<v>[A-Z_]+)\\}").v // "") as $var
        | if $var != "" then
            (env[$var] // "") as $val
            | if $val == "" then
                .action = {type:"message",
                           text: (env["FALLBACK_" + $var] // "เมนู")}
              else .action.uri = ($u | sub("\\{" + $var + "\\}"; $val)) end
          else . end
      else . end ]' "$1"
}

apply_one() {
  local oa="$1" token_var="LINE_$(echo "$1" | tr a-z A-Z)_TOKEN"
  local token="${!token_var:-}"
  [ -n "$token" ] || { info "ข้าม $oa — ไม่ได้ตั้ง \$$token_var"; return 0; }

  info "[$oa] ลบ alias เดิม (ถ้ามี)"
  for alias in "chann-${oa}-main" "chann-${oa}-more"; do
    curl -s -o /dev/null -X DELETE -H "Authorization: Bearer $token" \
      "https://api.line.me/v2/bot/richmenu/alias/$alias" || true
  done

  info "[$oa] ลบเมนูเดิมชื่อ chann-${oa}-* (ถ้ามี)"
  curl -fsS -H "Authorization: Bearer $token" \
    https://api.line.me/v2/bot/richmenu/list \
  | jq -r --arg p "chann-${oa}-" '.richmenus[] | select(.name | startswith($p)) | .richMenuId' \
  | while read -r rid; do
      curl -fsS -X DELETE -H "Authorization: Bearer $token" \
        "https://api.line.me/v2/bot/richmenu/$rid" >/dev/null
      info "[$oa] ลบ $rid"
    done

  local main_rid=""
  for page in main more; do
    local suffix=""; [ "$page" = "more" ] && suffix="-more"
    local json="out/richmenu-${oa}${suffix}.json" png="out/richmenu-${oa}${suffix}.png"
    [ -f "$json" ] || die "[$oa] ไม่มี $json — รัน python3 generate.py"
    local alias; alias=$(jq -r '._alias // empty' "$json")
    [ -n "$alias" ] || die "[$oa] $json ไม่มี _alias — generate.py เก่า"
    local body; body=$(_resolve_body "$json")

    info "[$oa] สร้างเมนู $page"
    local rid
    rid=$(curl -fsS -X POST https://api.line.me/v2/bot/richmenu \
      -H "Authorization: Bearer $token" -H "Content-Type: application/json" \
      -d "$body" | jq -r .richMenuId)
    [ -n "$rid" ] && [ "$rid" != "null" ] || die "[$oa] สร้างเมนู $page ไม่สำเร็จ"

    info "[$oa] อัปโหลดรูป $page"
    curl -fsS -X POST "https://api-data.line.me/v2/bot/richmenu/$rid/content" \
      -H "Authorization: Bearer $token" -H "Content-Type: image/png" \
      --data-binary "@$png" >/dev/null

    info "[$oa] ตั้ง alias $alias → $rid"
    curl -fsS -X POST https://api.line.me/v2/bot/richmenu/alias \
      -H "Authorization: Bearer $token" -H "Content-Type: application/json" \
      -d "{\"richMenuAliasId\":\"$alias\",\"richMenuId\":\"$rid\"}" >/dev/null

    [ "$page" = "main" ] && main_rid="$rid"
  done

  info "[$oa] ตั้งหน้าหลักเป็น default ของทุกคน"
  # LINE answers 411 to a bodiless POST: curl sends no Content-Length
  # without a body, so hand it an empty one (Content-Length: 0).
  curl -fsS -X POST "https://api.line.me/v2/bot/user/all/richmenu/$main_rid" \
    -H "Authorization: Bearer $token" -H "Content-Length: 0" >/dev/null

  info "[$oa] เสร็จ — main $main_rid"
}

# ปุ่ม uri ที่ยังไม่มี LIFF จะพิมพ์ข้อความเหล่านี้แทน (แชทรับมือได้อยู่แล้ว)
export FALLBACK_LIFF_SALES="เปิดแดชบอร์ด"
export FALLBACK_LIFF_TECHNICIAN="งานของฉัน"
export FALLBACK_LIFF_TECHNICIAN_REPORTS="รายงานของฉัน"
export FALLBACK_LIFF_CUSTOMER="แจ้งซ่อม"

if [ $# -ge 1 ]; then apply_one "$1"; else
  for oa in sales technician customer; do apply_one "$oa"; done
fi
echo "DONE — เปิดห้องแชทแต่ละ OA แล้วดูเมนูใหม่ (อาจต้องปิด-เปิดห้องแชท) แท็บ 'เพิ่มเติม' อยู่บนหัวเมนู"
