"use client";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

/**
 * Sales-dashboard strings added by the 6 Sep 2026 review fixes.
 *
 * Kept beside the pages rather than in lib/i18n because that dictionary
 * is being edited concurrently for the customer and technician apps;
 * the shape is checked the same way — `en` is typed from `th`, so a key
 * missing in either language fails `tsc`.
 */
const th = {
  errors: {
    suspended: "ร้านถูกระงับการใช้งาน ทำรายการใหม่ไม่ได้",
    forbidden: "คุณไม่มีสิทธิ์ทำรายการนี้",
    notFound: "ไม่พบรายการนี้แล้ว",
    conflict: "ทำรายการไม่ได้ในสถานะปัจจุบัน",
    invalid: "ข้อมูลไม่ถูกต้อง ตรวจช่องที่กรอกอีกครั้ง",
    failed: "ทำรายการไม่สำเร็จ ({status})",
    readOnly: "ดูได้อย่างเดียว — ต้องมีสิทธิ์ \"{permission}\" จึงจะแก้ไขได้",
    showingLatest: "แสดง {count} รายการล่าสุด",
  },
  reasons: {
    deal_has_no_products: "ดีลนี้ยังไม่มีสินค้า เพิ่มสินค้าในดีลก่อน",
    quote_transition_not_allowed: "เปลี่ยนสถานะใบเสนอราคาแบบนี้ไม่ได้",
    company_incomplete: "ยังออกเอกสารไม่ได้ ต้องกรอกข้อมูลบริษัทก่อน: {fields}",
    already_issued: "ใบนี้ออกเอกสารไปแล้ว ถ้าต้องการออกใหม่ให้ยืนยันอีกครั้ง",
    transfer_already_pending: "มีคำขอโอนความเป็นเจ้าของค้างอยู่แล้ว",
    transfer_target_is_self: "เลือกสมาชิกคนอื่นเป็นเจ้าของคนใหม่",
    not_owner: "เฉพาะเจ้าของร้านคนปัจจุบันเท่านั้นที่โอนได้",
    not_nominee: "คำขอโอนนี้ไม่ได้ส่งถึงคุณ",
    transfer_not_pending: "คำขอโอนนี้ถูกใช้ไปแล้ว",
    member_not_active: "สมาชิกคนนี้ไม่ได้อยู่ในร้านแล้ว",
    already_exists: "มีชื่อนี้อยู่แล้ว",
    duplicate: "มีรายการนี้อยู่แล้ว ({code})",
    dispatch_blocked: "ยังมอบหมายไม่ได้ ขาด: {fields}",
    tenant_suspended: "ร้านถูกระงับการใช้งาน ทำรายการใหม่ไม่ได้",
    reason_required: "ต้องระบุเหตุผล",
    staff_only: "เฉพาะพนักงานของร้าน",
  },
  customers: {
    lastNameAndPhone: "ต้องมีนามสกุลและเบอร์โทร (ชื่อจะเว้นก็ได้)",
  },
  deals: {
    lostReasonTitle: "ปิดดีลไม่สำเร็จ — เพราะอะไร",
    lostReasonHint: "เว้นว่างได้ แต่เหตุผลช่วยให้รู้ว่าแพ้เพราะอะไรบ่อย",
    confirmLost: "บันทึกว่าปิดไม่สำเร็จ",
    needsQuoteCreate: "ต้องมีสิทธิ์สร้างใบเสนอราคา",
  },
  quotes: {
    markSent: "ส่งให้ลูกค้าแล้ว",
    markExpired: "หมดอายุแล้ว",
    issueBeforeSend: "ออกเอกสารก่อน แล้วค่อยบันทึกว่าส่งให้ลูกค้าแล้ว",
    final: "ใบเสนอราคานี้ปิดแล้ว เปลี่ยนสถานะไม่ได้อีก",
    needsUpdate: "ต้องมีสิทธิ์แก้ไขใบเสนอราคาจึงจะออกเอกสารหรือเปลี่ยนสถานะได้",
  },
  tickets: {
    latestOnly: "แสดงเฉพาะงานที่ยังไม่จบ (สูงสุด {count} รายการต่อสถานะ)",
  },
  warranties: {
    serialSearch: "ค้นหาตาม S/N",
    serialSearchHint: "พิมพ์หมายเลขเครื่องให้ครบแล้วกดค้นหา",
    search: "ค้นหา",
    clearSearch: "แสดงทั้งหมด",
    serialNotFound: "ไม่พบเครื่องหมายเลข {serial} ในร้านนี้",
  },
  products: {
    codeLocked: "รหัสสินค้าแก้ไม่ได้ — รหัสใหม่คือสินค้าใหม่ ให้เพิ่มสินค้าใหม่แทน",
    readOnly: "ดูรายการได้ ต้องมีสิทธิ์จัดการสินค้าจึงจะเพิ่มหรือแก้ไขได้",
  },
  company: {
    readOnly: "ดูได้อย่างเดียว ต้องมีสิทธิ์จัดการการตั้งค่าบริษัทจึงจะแก้ไขได้",
    transferTitle: "โอนความเป็นเจ้าของร้าน",
    transferIntro: "เลือกสมาชิกที่จะรับเป็นเจ้าของคนใหม่ เขาต้องกด \"รับโอน\" ในเมนูทีมขายก่อนจึงจะมีผล และคุณจะกลายเป็นสมาชิกธรรมดา",
    pickMember: "เลือกสมาชิก",
    noOtherMembers: "ยังไม่มีสมาชิกคนอื่นในร้านที่จะรับโอนได้",
    request: "ส่งคำขอโอน",
    requested: "ส่งคำขอโอนแล้ว รอ {name} ยืนยัน",
    pending: "รอ {name} กดรับโอน",
    confirm: "โอนความเป็นเจ้าของร้านให้ {name}? เมื่อเขายืนยัน คุณจะไม่ใช่เจ้าของอีกต่อไป",
  },
  menu: {
    transferOffer: "เจ้าของร้าน {shop} ขอโอนความเป็นเจ้าของร้านให้คุณ",
    accept: "รับโอน",
    accepted: "คุณเป็นเจ้าของร้านแล้ว — โหลดหน้าใหม่เพื่อเห็นสิทธิ์ทั้งหมด",
    confirmAccept: "รับเป็นเจ้าของร้าน {shop}? คุณจะได้สิทธิ์ทั้งหมดและเจ้าของเดิมจะกลายเป็นสมาชิกธรรมดา",
  },
  groups: {
    title: "กลุ่มเซลส์",
    intro: "จัดพนักงานขายเป็นกลุ่ม เพื่อดูยอดและแบ่งลูกค้าเป็นทีม (ในแชท: \"สร้างกลุ่มเซลส์ ทีมเหนือ\")",
    newGroup: "ชื่อกลุ่มใหม่",
    create: "สร้างกลุ่ม",
    created: "สร้างกลุ่มแล้ว",
    empty: "ยังไม่มีกลุ่มเซลส์",
    addMember: "เพิ่มสมาชิกเข้ากลุ่ม",
    pickMember: "เลือกสมาชิก",
    allIn: "สมาชิกทุกคนอยู่ในกลุ่มนี้แล้ว",
    deleteGroup: "ลบกลุ่ม",
    confirmDelete: "ลบกลุ่มนี้ใช่ไหม สมาชิกจะยังอยู่ในร้าน แค่ไม่อยู่ในกลุ่มนี้",
    confirmRemove: "เอาสมาชิกคนนี้ออกจากกลุ่มใช่ไหม",
  },
  related: {
    movedButOldKept: "สร้างนัดใหม่แล้ว แต่ยกเลิกนัดเดิมไม่สำเร็จ — กด \"ยกเลิกนัด\" ที่นัดเดิมด้วย",
    needsFollowupCreate: "ต้องมีสิทธิ์ตั้งรายการติดตาม",
    needsNoteCreate: "ต้องมีสิทธิ์เพิ่มบันทึก",
  },
  roles: {
    readOnly: "ดูได้อย่างเดียว ต้องมีสิทธิ์จัดการบทบาทจึงจะแก้ไขได้",
  },
};

const en: typeof th = {
  errors: {
    suspended: "The shop is suspended — nothing new can be saved",
    forbidden: "You do not have permission for this",
    notFound: "This record no longer exists",
    conflict: "Not possible in the current state",
    invalid: "Some of the data is not valid — check the fields",
    failed: "The action failed ({status})",
    readOnly: "Read-only — the \"{permission}\" permission is needed to edit",
    showingLatest: "Showing the latest {count}",
  },
  reasons: {
    deal_has_no_products: "This deal has no products yet — add one to the deal first",
    quote_transition_not_allowed: "The quote cannot move to that status from here",
    company_incomplete: "The document cannot be issued yet — fill in the company profile: {fields}",
    already_issued: "This quote already has a document; confirm again to issue a new one",
    transfer_already_pending: "An ownership transfer is already waiting",
    transfer_target_is_self: "Pick another member as the new owner",
    not_owner: "Only the current owner can transfer ownership",
    not_nominee: "This transfer was not addressed to you",
    transfer_not_pending: "This transfer has already been used",
    member_not_active: "That member is no longer in the shop",
    already_exists: "That name already exists",
    duplicate: "This already exists ({code})",
    dispatch_blocked: "Cannot dispatch yet — missing: {fields}",
    tenant_suspended: "The shop is suspended — nothing new can be saved",
    reason_required: "A reason is required",
    staff_only: "Shop staff only",
  },
  customers: {
    lastNameAndPhone: "A last name and a phone number are required (the first name is optional)",
  },
  deals: {
    lostReasonTitle: "Deal lost — why?",
    lostReasonHint: "Optional, but a reason shows what keeps losing deals",
    confirmLost: "Mark as lost",
    needsQuoteCreate: "Needs the create-quotes permission",
  },
  quotes: {
    markSent: "Sent to the customer",
    markExpired: "Expired",
    issueBeforeSend: "Issue the document first, then mark it as sent",
    final: "This quote is closed; its status can no longer change",
    needsUpdate: "Needs the edit-quotes permission to issue or change status",
  },
  tickets: {
    latestOnly: "Open jobs only (up to {count} per status)",
  },
  warranties: {
    serialSearch: "Search by serial",
    serialSearchHint: "Type the full serial number, then search",
    search: "Search",
    clearSearch: "Show all",
    serialNotFound: "No unit with serial {serial} at this shop",
  },
  products: {
    codeLocked: "The product code cannot be changed — a new code is a new product; add one instead",
    readOnly: "Read-only list; the manage-products permission is needed to add or edit",
  },
  company: {
    readOnly: "Read-only; the manage-company-settings permission is needed to edit",
    transferTitle: "Transfer shop ownership",
    transferIntro: "Pick the member who will become the new owner. It takes effect when they tap \"Accept\" on the sales menu, and you become an ordinary member.",
    pickMember: "Pick a member",
    noOtherMembers: "There is no other member in the shop to hand over to",
    request: "Send transfer request",
    requested: "Transfer requested — waiting for {name} to accept",
    pending: "Waiting for {name} to accept",
    confirm: "Transfer ownership of the shop to {name}? Once they accept, you are no longer the owner.",
  },
  menu: {
    transferOffer: "The owner of {shop} wants to hand ownership of the shop to you",
    accept: "Accept",
    accepted: "You are now the owner — reload to see every permission",
    confirmAccept: "Become the owner of {shop}? You get every permission and the previous owner becomes an ordinary member.",
  },
  groups: {
    title: "Sales groups",
    intro: "Group salespeople to compare figures and share customers as a team (in chat: \"create sales group North\")",
    newGroup: "New group name",
    create: "Create group",
    created: "Group created",
    empty: "No sales groups yet",
    addMember: "Add a member",
    pickMember: "Pick a member",
    allIn: "Every member is already in this group",
    deleteGroup: "Delete group",
    confirmDelete: "Delete this group? The members stay in the shop, just not in this group",
    confirmRemove: "Remove this member from the group?",
  },
  related: {
    movedButOldKept: "The new appointment was made, but the old one could not be cancelled — tap \"cancel\" on it",
    needsFollowupCreate: "Needs the create-follow-ups permission",
    needsNoteCreate: "Needs the add-notes permission",
  },
  roles: {
    readOnly: "Read-only; the manage-roles permission is needed to edit",
  },
};

export type SalesText = typeof th;

export function useSalesText(): SalesText {
  const { locale } = useLanguage();
  return locale === "en" ? en : th;
}
