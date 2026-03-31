import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "kiseki - JV-Linkデータによる競馬予測指数・期待値分析",
  description:
    "JV-Linkデータを元にスピード指数・コース適性・騎手指数など14種の競馬指数を算出し、オッズとの期待値比較で合理的な馬券購入をサポートするシステム。",
  manifest: "/manifest.json",
  robots: { index: false, follow: false },
  openGraph: {
    type: "website",
    locale: "ja_JP",
    siteName: "kiseki",
    title: "kiseki - 競馬予測指数システム",
    description: "JV-Linkデータによる競馬指数・期待値分析",
    images: [{ url: "/images/logo.png", width: 512, height: 512 }],
  },
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
    title: "kiseki",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#1a5c38",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="ja"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        {/* スキップナビゲーション（キーボードユーザー向け） */}
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:px-4 focus:py-2 focus:bg-white focus:text-black focus:rounded focus:shadow-lg"
        >
          メインコンテンツへスキップ
        </a>
        {children}
      </body>
    </html>
  );
}
