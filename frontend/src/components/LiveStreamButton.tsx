"use client";

import { useState, useEffect, useRef, useCallback } from "react";

const STREAMS = [
  {
    label: "中央競馬",
    source: "sp.gch.jp",
    href: "https://sp.gch.jp/jra",
    icon: "🏇",
  },
  {
    label: "地方競馬",
    source: "keiba.rakuten.co.jp",
    href: "https://keiba.rakuten.co.jp/livemovie?l-id=keiba_header_liveMovie",
    icon: "🐎",
  },
] as const;

export function LiveStreamButton() {
  const [isOpen, setIsOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  const close = useCallback(() => {
    setIsOpen(false);
    setTimeout(() => triggerRef.current?.focus(), 0);
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, close]);

  useEffect(() => {
    if (!isOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    setTimeout(() => dialogRef.current?.focus(), 0);
  }, [isOpen]);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setIsOpen(true)}
        aria-label="ライブ中継を開く"
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        className="flex items-center justify-center w-9 h-9 rounded-md hover:bg-white/10 transition-colors text-white"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <rect x="2" y="6" width="20" height="13" rx="2" ry="2" />
          <polyline points="8 21 12 17 16 21" />
        </svg>
      </button>

      {isOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: "rgba(0,0,0,0.6)" }}
          onClick={(e) => {
            if (e.target === e.currentTarget) close();
          }}
        >
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="live-stream-title"
            tabIndex={-1}
            className="w-full max-w-sm rounded-2xl shadow-2xl outline-none"
            style={{
              background: "#0d1f35",
              border: "1px solid rgba(255,255,255,0.12)",
            }}
          >
            <div
              className="flex items-center justify-between px-5 py-3"
              style={{ borderBottom: "1px solid rgba(255,255,255,0.1)" }}
            >
              <h2 id="live-stream-title" className="text-white text-base font-semibold">
                ライブ中継
              </h2>
              <button
                type="button"
                onClick={close}
                aria-label="閉じる"
                className="w-8 h-8 flex items-center justify-center rounded-md text-white/70 hover:text-white hover:bg-white/10 transition-colors"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  width="18"
                  height="18"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>

            <div className="p-4 flex flex-col gap-3">
              {STREAMS.map((s) => (
                <a
                  key={s.label}
                  href={s.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={close}
                  className="flex items-center gap-3 px-4 py-3 rounded-xl transition-colors hover:bg-white/10"
                  style={{ border: "1px solid rgba(255,255,255,0.15)" }}
                >
                  <span aria-hidden="true" className="text-2xl">{s.icon}</span>
                  <div className="flex-1 min-w-0">
                    <div className="text-white text-sm font-semibold">{s.label}</div>
                    <div className="text-white/50 text-xs truncate">{s.source}</div>
                  </div>
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                    className="text-white/60 flex-shrink-0"
                  >
                    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                    <polyline points="15 3 21 3 21 9" />
                    <line x1="10" y1="14" x2="21" y2="3" />
                  </svg>
                </a>
              ))}
              <p className="text-white/50 text-xs mt-1 text-center">
                ※ 外部サイトに遷移します
              </p>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
