import Link from "next/link";

export const dynamic = "force-dynamic";

/**
 * The Sales dashboard index.
 *
 * This project has been chat-first through every phase, and the handful of
 * LIFF pages that exist were reachable only by knowing their URL. This page
 * is the hub each new phase adds a card to, so a page shipped in Phase 12
 * is discoverable without anyone having to remember it exists.
 *
 * Sections that are not built yet are listed deliberately, marked and
 * unlinked, rather than hidden: a visible "coming in Phase 12" tells a
 * tenant the capability is planned, and tells the next developer where the
 * page is expected to live.
 */

type Section = {
  href?: string;
  title: string;
  description: string;
  note?: string;
};

const SECTIONS: Section[] = [
  {
    href: "/liff/sales/company",
    title: "ข้อมูลบริษัท",
    description: "ชื่อนิติบุคคล เลขผู้เสียภาษี ที่อยู่ และภาษีมูลค่าเพิ่ม สำหรับพิมพ์บนเอกสาร",
  },
  {
    href: "/liff/sales/roles",
    title: "บทบาทและสิทธิ์",
    description: "จัดการ role ของทีม และการตั้งค่าบริษัท",
  },
  {
    href: "/liff/sales/quotes",
    title: "ใบเสนอราคา",
    description: "ดูรายการใบเสนอราคา และเปิดดูเอกสาร PDF เพื่อตรวจทาน",
  },
  {
    href: "/liff/sales/customers",
    title: "ลูกค้า",
    description: "ดูรายชื่อลูกค้าและลูกค้ามุ่งหวังทั้งหมด",
  },
  {
    href: "/liff/sales/deals",
    title: "ดีล",
    description: "ดูดีลทั้งหมดพร้อมสถานะและจำนวนรายการสินค้า",
  },
  {
    href: "/liff/sales/products",
    title: "สินค้า",
    description: "ดูรายการสินค้าและราคาที่ตั้งไว้",
  },
];

export default function SalesDashboardPage() {
  return (
    <main style={{ maxWidth: 720, margin: "0 auto", padding: 24 }}>
      <h1>Chann CRM AI — เมนูสำหรับทีมขาย</h1>
      <p style={{ color: "#666" }}>
        ทุกอย่างในนี้สั่งผ่านแชท LINE ได้เช่นกัน หน้านี้มีไว้สำหรับงานที่กรอกหลายช่องพร้อมกันจะสะดวกกว่า
      </p>

      <ul style={{ listStyle: "none", padding: 0, display: "grid", gap: 12 }}>
        {SECTIONS.map((section) => (
          <li
            key={section.title}
            style={{
              border: "1px solid #ddd",
              borderRadius: 6,
              padding: 16,
              opacity: section.href ? 1 : 0.6,
            }}
          >
            <h2 style={{ fontSize: 18, margin: "0 0 4px" }}>
              {section.href ? <Link href={section.href}>{section.title}</Link> : section.title}
            </h2>
            <p style={{ margin: 0, color: "#444" }}>{section.description}</p>
            {section.note && (
              <p style={{ margin: "6px 0 0", fontSize: 13, color: "#888" }}>{section.note}</p>
            )}
          </li>
        ))}
      </ul>
    </main>
  );
}
