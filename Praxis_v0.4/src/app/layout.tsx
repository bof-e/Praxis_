import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Praxis",
  description: "Système de travail intelligent personnel",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="fr">
      <body>
        <div className="container">
          <div className="header">
            <div>
              <h1>
                <Link href="/" style={{ color: "inherit", textDecoration: "none" }}>
                  Praxis
                </Link>
              </h1>
              <div className="subtitle">Atelier de travail intelligent — v0.4</div>
            </div>
            <Link href="/knowledge" className="nav-link">
              Base de connaissances →
            </Link>
          </div>
          {children}
        </div>
      </body>
    </html>
  );
}
