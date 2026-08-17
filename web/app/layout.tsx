import type { Metadata } from "next";
import Link from "next/link";
import UserMenu from "@/components/UserMenu";
import { SessionProvider } from "@/lib/session";
import "./globals.css";

export const metadata: Metadata = {
  title: "sentHire",
  description: "Yapay zekâ destekli CV tarama — yükleyin, anlatın, sıralansın.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="tr">
      <body>
        <SessionProvider>
        <div className="shell">
          <header className="topbar">
            <Link href="/" className="brand">
              sent<span>Hire</span>
            </Link>
            <nav>
              <Link href="/">İlanlar</Link>
            </nav>
            <div className="spacer" />
            <UserMenu />
          </header>
          {children}
        </div>
        </SessionProvider>
      </body>
    </html>
  );
}
