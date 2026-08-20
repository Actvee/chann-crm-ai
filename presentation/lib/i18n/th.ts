/**
 * Thai dictionary — the source of truth for i18n keys (Master Spec 5.3).
 *
 * `Dictionary` is derived from this object, so every other locale is checked
 * against it at compile time. Adding a key here without adding it to en.ts
 * fails `npm run typecheck`, which the source-verification script already
 * runs — that is how Spec 5.5's test_i18n_dictionary_complete is enforced,
 * in both directions, without a separate runtime test.
 */
export const th = {
  common: {
    save: "บันทึก",
    cancel: "ยกเลิก",
    confirm: "ยืนยัน",
    delete: "ลบ",
    edit: "แก้ไข",
    close: "ปิด",
    loading: "กำลังโหลด…",
    error: "เกิดข้อผิดพลาด",
    retry: "ลองใหม่",
    language: "ภาษา",
  },
  liff: {
    starting: "กำลังเริ่ม LIFF…",
    noCompany: "ยังไม่พบบริษัทที่ผูกไว้",
    multipleCompanies: "คุณเป็นสมาชิกหลายบริษัท กรุณาเลือก",
    notConfigured: "ยังไม่ได้ตั้งค่า LIFF ID",
    initFailed: "เริ่ม LIFF ไม่สำเร็จ",
    sdkLoadFailed: "โหลด LIFF SDK ไม่สำเร็จ",
  },
  role: {
    title: "จัดการสิทธิ์และบทบาท",
    permissionMatrix: "ตารางสิทธิ์",
    createCustomRole: "สร้างบทบาทใหม่",
    roleName: "ชื่อบทบาท",
    permissionKeys: "รหัสสิทธิ์ (คั่นด้วยจุลภาค)",
    createButton: "สร้างบทบาท",
    protectedOwner: "บทบาทเจ้าของ — แก้ไขไม่ได้",
  },
  licenseSetting: {
    title: "ตั้งค่าบริษัท",
    settingKey: "รหัสการตั้งค่า",
    settingValue: "ค่า (JSON หรือข้อความ)",
    saveButton: "บันทึกการตั้งค่า",
  },
  notification: {
    title: "การแจ้งเตือน",
    empty: "ยังไม่มีการแจ้งเตือน",
    markRead: "ทำเครื่องหมายว่าอ่านแล้ว",
    unreadBadge: "ยังไม่ได้อ่าน",
    loadFailed: "โหลดการแจ้งเตือนไม่สำเร็จ",
  },
  customer: {
    title: "ลูกค้า",
    addNew: "เพิ่มลูกค้าใหม่",
  },
  deal: {
    title: "ดีล",
    stage: {
      new: "ใหม่",
      proposed: "เสนอราคา",
      won: "ปิดสำเร็จ",
      lost: "ปิดไม่สำเร็จ",
    },
  },
};

/**
 * Deliberately NOT `as const`: literal value types would force every other
 * locale to repeat the Thai strings verbatim. Widening the leaves to `string`
 * keeps what actually matters — the key structure — checked in both
 * directions, while letting translations be translations.
 */
/** Every locale must match this shape exactly — no missing, no extra keys. */
export type Dictionary = typeof th;
