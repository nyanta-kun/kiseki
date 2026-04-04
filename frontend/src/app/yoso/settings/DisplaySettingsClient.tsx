"use client";

import { useState, useTransition } from "react";
import { updateDisplaySettings } from "@/app/actions/yoso";
import type { DisplaySetting } from "@/lib/api";

type Props = {
  initialSettings: DisplaySetting[];
};

export function DisplaySettingsClient({ initialSettings }: Props) {
  const [settings, setSettings] = useState<DisplaySetting[]>(initialSettings);
  const [isPending, startTransition] = useTransition();
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const toggle = (userId: number, field: "show_mark" | "show_index") => {
    setSettings((prev) =>
      prev.map((s) => s.target_user_id === userId ? { ...s, [field]: !s[field] } : s)
    );
  };

  const handleSave = () => {
    startTransition(async () => {
      const result = await updateDisplaySettings(
        settings.map((s) => ({
          target_user_id: s.target_user_id,
          show_mark: s.show_mark,
          show_index: s.show_index,
        }))
      );
      if (result.ok) {
        setMessage({ ok: true, text: "設定を保存しました" });
      } else {
        setMessage({ ok: false, text: result.error ?? "保存に失敗しました" });
      }
      setTimeout(() => setMessage(null), 3000);
    });
  };

  return (
    <div className="space-y-3">
      {message && (
        <div className={`rounded-lg px-4 py-2.5 text-xs ${
          message.ok ? "bg-green-50 text-green-700 border border-green-200" : "bg-red-50 text-red-700 border border-red-200"
        }`}>
          {message.text}
        </div>
      )}

      <div className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-400 border-b border-gray-100 bg-gray-50">
              <th className="px-4 py-2.5 text-left">ユーザー</th>
              <th className="px-4 py-2.5 text-center w-20">印を表示</th>
              <th className="px-4 py-2.5 text-center w-20">指数を表示</th>
            </tr>
          </thead>
          <tbody>
            {settings.map((s) => (
              <tr key={s.target_user_id} className="border-b border-gray-50 last:border-0">
                <td className="px-4 py-3">
                  <div className="text-gray-800 font-medium truncate max-w-[180px]">
                    {s.target_user_name ?? s.target_user_email}
                  </div>
                  <div className="text-gray-400 text-xs truncate max-w-[180px]">
                    {s.target_user_name ? s.target_user_email : ""}
                    {s.target_can_input_index && (
                      <span className="ml-2 text-blue-400">指数あり</span>
                    )}
                  </div>
                </td>
                <td className="px-4 py-3 text-center">
                  <Toggle
                    checked={s.show_mark}
                    onChange={() => toggle(s.target_user_id, "show_mark")}
                    label="印を表示"
                  />
                </td>
                <td className="px-4 py-3 text-center">
                  <Toggle
                    checked={s.show_index}
                    onChange={() => toggle(s.target_user_id, "show_index")}
                    disabled={!s.target_can_input_index}
                    label="指数を表示"
                    title={!s.target_can_input_index ? "相手ユーザーが指数投入権限を持っていません" : undefined}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <button
        onClick={handleSave}
        disabled={isPending}
        className="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white text-sm font-medium rounded-xl transition-colors"
      >
        {isPending ? "保存中..." : "設定を保存"}
      </button>
    </div>
  );
}

function Toggle({
  checked,
  onChange,
  disabled = false,
  label,
  title,
}: {
  checked: boolean;
  onChange: () => void;
  disabled?: boolean;
  label: string;
  title?: string;
}) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={onChange}
      disabled={disabled}
      title={title}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-blue-400 focus:ring-offset-1 ${
        disabled ? "opacity-30 cursor-not-allowed" :
        checked ? "bg-blue-500" : "bg-gray-200"
      }`}
    >
      <span
        className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transform transition-transform ${
          checked ? "translate-x-4.5" : "translate-x-0.5"
        }`}
      />
    </button>
  );
}
