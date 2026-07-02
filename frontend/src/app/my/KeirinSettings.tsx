"use client";

import { useEffect, useState } from "react";

const LS_KEY = "keirin:hideNoPickRows";

export function KeirinSettings() {
  const [hide, setHide] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setHide(localStorage.getItem(LS_KEY) === "true");
    setMounted(true);
  }, []);

  const toggle = () => {
    const next = !hide;
    setHide(next);
    localStorage.setItem(LS_KEY, String(next));
  };

  if (!mounted) return null;

  return (
    <section className="bg-white rounded-xl border border-gray-100 p-5 shadow-sm">
      <h2 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
        <span className="w-1 h-4 rounded inline-block" style={{ background: "var(--primary)" }} />
        競輪 表示設定
      </h2>
      <label className="flex items-center justify-between cursor-pointer gap-3">
        <span className="text-sm text-gray-700">推奨レース外を非表示</span>
        <button
          role="switch"
          aria-checked={hide}
          onClick={toggle}
          className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 ${
            hide ? "bg-blue-500" : "bg-gray-300"
          }`}
        >
          <span
            className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
              hide ? "translate-x-6" : "translate-x-1"
            }`}
          />
        </button>
      </label>
      <p className="text-xs text-gray-400 mt-2">
        オンにすると、条件を満たさないレース（推奨外）をKEIRINページで非表示にします。
      </p>
    </section>
  );
}
