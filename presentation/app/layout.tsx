import { LanguageProvider } from "@/lib/i18n/LanguageProvider";

export const metadata = {
  title: "Chann CRM AI",
  description: "Multi-tenant CRM and field service platform on LINE",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // lang is set to the default here and updated client-side by LanguageProvider
  // once the stored preference is known — see the hydration note in that file.
  return (
    <html lang="th">
      <body style={{ fontFamily: "system-ui, sans-serif", margin: 0 }}>
        <LanguageProvider>{children}</LanguageProvider>
      </body>
    </html>
  );
}
