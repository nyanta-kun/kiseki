"use client";

import { useEffect, useState, useTransition } from "react";
import { updatePaidMode } from "./actions";

type Setting = { key: string; value: string };

export function SettingsTab() {
  const [paidMode, setPaidMode] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [isPending, startTransition] = useTransition();

  async function loadSettings() {
    setLoading(true);
    try {
      const res = await fetch("/api/admin/settings");
      if (res.ok) {
        const data = (await res.json()) as { settings: Setting[] };
        const pm = data.settings.find((s) => s.key === "PAID_MODE");
        setPaidMode(pm?.value === "true");
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadSettings();
  }, []);

  function handleToggle() {
    if (paidMode === null) return;
    const newValue = !paidMode;
    startTransition(async () => {
      const result = await updatePaidMode(newValue);
      if (result.error) {
        alert(`更新に失敗しました: ${result.error}`);
      } else {
        setPaidMode(newValue);
      }
    });
  }

  if (loading) {
    return <div className="py-8 text-center text-gray-400 text-sm">読み込み中...</div>;
  }

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <h3 className="text-sm font-bold text-[#0d1f35] mb-1">有料モード (PAID_MODE)</h3>
        <p className="text-xs text-gray-500 mb-4">
          ONにするとペイウォールが有効になります。次回ページアクセス時から反映されます。
        </p>
        <div className="flex items-center gap-3">
          <button
            onClick={handleToggle}
            disabled={isPending || paidMode === null}
            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none disabled:opacity-40 ${
              paidMode ? "bg-[#1a5c38]" : "bg-gray-300"
            }`}
            role="switch"
            aria-checked={paidMode ?? false}
          >
            <span
              className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                paidMode ? "translate-x-6" : "translate-x-1"
              }`}
            />
          </button>
          <span className="text-sm text-gray-700">
            {paidMode ? "有効（ペイウォールON）" : "無効（ペイウォールOFF）"}
          </span>
        </div>
      </div>
    </div>
  );
}
