#!/usr/bin/env bash
# richmenu-apply.sh — สร้าง+อัปโหลด+ตั้ง rich menu ให้ 3 OA
#
# ใช้:
#   export LINE_SALES_TOKEN=...        # channel access token ของแต่ละ OA
#   export LINE_TECHNICIAN_TOKEN=...
#   export LINE_CUSTOMER_TOKEN=...
#   export LIFF_SALES=https://liff.line.me/xxxx-sales          # ใส่เท่าที่มี
#   export LIFF_TECHNICIAN=https://liff.line.me/xxxx-tech
#   export LIFF_TECHNICIAN_REPORTS=https://liff.line.me/xxxx-tech/reports
#   export LIFF_CUSTOMER=https://liff.line.me/xxxx-customer
#   bash scripts/richmenu/richmenu-apply.sh            # ทั้ง 3 OA
#   bash scripts/richmenu/richmenu-apply.sh technician # OA เดียว
#
# ปุ่มที่เป็น uri แต่ LIFF ยังไม่ตั้งค่า จะถูกแปลงเป็น message action
# อัตโนมัติ (ส่งข้อความชื่อเมนูแทน) — เมนูใช้ได้ทันทีวันนี้ และอัปเกรด
# เป็นลิงก์ได้ทีหลังโดยรันซ้ำ ไม่มีปุ่มตายให้ลูกค้ากดค้าง
#
# รันซ้ำได้: ลบเมนูชื่อเดียวกัน (chann-<oa>-v1) ก่อนสร้างใหม่เสมอ

set -euo pipefail
cd "$(dirname "$0")"

die(){ printf 'HALT: %s\n' "$*" >&2; exit 1; }
info(){ printf '  %s\n' "$*"; }

command -v jq >/dev/null || die "ต้องมี jq (sudo apt-get install -y jq)"
[ -f out/richmenu-sales.png ] || die "ยังไม่มีรูป — รัน python3 generate.py ก่อน"

apply_one() {
  local oa="$1" token_var="LINE_$(echo "$1" | tr a-z A-Z)_TOKEN"
  local token="${!token_var:-}"
  [ -n "$token" ] || { info "ข้าม $oa — ไม่ได้ตั้ง \$$token_var"; return 0; }

  local json="out/richmenu-${oa}.json" png="out/richmenu-${oa}.png"
  local name; name=$(jq -r .name "$json")

  # แทนค่า {LIFF_*}; ตัวไหนไม่มีค่า → message action ชื่อเมนูแทน ปุ่มไม่ตาย
  local body
  body=$(jq -c '
    .areas = [ .areas[] |
      if .action.type == "uri" then
        .action.uri as $u
        | ($u | capture("\\{(?<v>[A-Z_]+)\\}").v // empty) as $var
        | if $var != "" then
            (env[$var] // "") as $val
            | if $val == "" then
                .action = {type:"message",
                           text: (env["FALLBACK_" + $var] // "เมนู")}
              else .action.uri = $val end
          else . end
      else . end ]' "$json")

  info "[$oa] ลบเมนูเดิมชื่อ $name (ถ้ามี)"
  curl -fsS -H "Authorization: Bearer $token" \
    https://api.line.me/v2/bot/richmenu/list \
  | jq -r --arg n "$name" '.richmenus[] | select(.name==$n) | .richMenuId' \
  | while read -r rid; do
      curl -fsS -X DELETE -H "Authorization: Bearer $token" \
        "https://api.line.me/v2/bot/richmenu/$rid" >/dev/null
      info "[$oa] ลบ $rid"
    done

  info "[$oa] สร้างเมนู"
  local rid
  rid=$(curl -fsS -X POST https://api.line.me/v2/bot/richmenu \
    -H "Authorization: Bearer $token" -H "Content-Type: application/json" \
    -d "$body" | jq -r .richMenuId)
  [ -n "$rid" ] && [ "$rid" != "null" ] || die "[$oa] สร้างเมนูไม่สำเร็จ"

  info "[$oa] อัปโหลดรูป"
  curl -fsS -X POST "https://api-data.line.me/v2/bot/richmenu/$rid/content" \
    -H "Authorization: Bearer $token" -H "Content-Type: image/png" \
    --data-binary "@$png" >/dev/null

  info "[$oa] ตั้งเป็น default ของทุกคน"
  # LINE answers 411 to a bodiless POST: curl sends no Content-Length
  # without a body, so hand it an empty one (Content-Length: 0).
  # Seen on the first live run, 3 Sep, after create + upload had passed.
  curl -fsS -X POST "https://api.line.me/v2/bot/user/all/richmenu/$rid" \
    -H "Authorization: Bearer $token" -H "Content-Length: 0" >/dev/null

  info "[$oa] เสร็จ — $rid"
}

# ปุ่ม uri ที่ยังไม่มี LIFF จะพิมพ์ข้อความเหล่านี้แทน (แชทรับมือได้อยู่แล้ว)
export FALLBACK_LIFF_SALES="เปิดแดชบอร์ด"
export FALLBACK_LIFF_TECHNICIAN="งานของฉัน"
export FALLBACK_LIFF_TECHNICIAN_REPORTS="รายงานของฉัน"
export FALLBACK_LIFF_CUSTOMER="แจ้งซ่อม"

if [ $# -ge 1 ]; then apply_one "$1"; else
  for oa in sales technician customer; do apply_one "$oa"; done
fi
echo "DONE — เปิดห้องแชทแต่ละ OA แล้วดูเมนูใหม่ (อาจต้องปิด-เปิดห้องแชท)"
