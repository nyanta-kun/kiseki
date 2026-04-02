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
  title: "GallopLab - 競馬AI指数・期待値分析",
  description:
    "JRA競馬のAI指数・期待値を提供する競馬予測サービス。スピード指数・コース適性・騎手指数など14種の指数で合理的な馬券購入をサポート。",
  manifest: "/manifest.json",
  robots: { index: false, follow: false },
  icons: {
    icon: [
      { url: "/images/favicon/favicon-16x16.png", sizes: "16x16", type: "image/png" },
      { url: "/images/favicon/favicon-32x32.png", sizes: "32x32", type: "image/png" },
      { url: "/images/favicon/favicon-48x48.png", sizes: "48x48", type: "image/png" },
      { url: "/images/favicon/favicon-64x64.png", sizes: "64x64", type: "image/png" },
      { url: "/images/favicon/favicon-128x128.png", sizes: "128x128", type: "image/png" },
      { url: "/images/favicon/favicon-192x192.png", sizes: "192x192", type: "image/png" },
      { url: "/images/favicon/favicon-256x256.png", sizes: "256x256", type: "image/png" },
      { url: "/images/favicon/favicon-512x512.png", sizes: "512x512", type: "image/png" },
    ],
    shortcut: "/images/favicon/favicon.ico",
    apple: { url: "/images/favicon/favicon-180x180.png", sizes: "180x180", type: "image/png" },
  },
  openGraph: {
    type: "website",
    locale: "ja_JP",
    siteName: "GallopLab",
    title: "GallopLab - 競馬AI指数・期待値分析",
    description: "JRA競馬のAI指数・期待値を提供する競馬予測サービス。スピード指数・コース適性・騎手指数など14種の指数で合理的な馬券購入をサポート。",
    images: [{ url: "/images/logo.png", width: 512, height: 512 }],
  },
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
    title: "GallopLab",
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
