"use client";

import { useState, useTransition } from "react";
import { updateMyPublicSetting } from "@/app/actions/yoso";
import type { MyPublicSetting } from "@/lib/api";

type Props = {
  initial: MyPublicSetting;
};

export function MyPublicSettingClient({ initial }: Props) {
  const [isPublic, setIsPublic] = useState(initial.is_yoso_public);
  const [yosoName, setYosoName] = useState(initial.yoso_name ?? "");
  const [isPending, startTransition] = useTransition();
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const handleSave = () => {
    if (isPublic && !yosoName.trim()) {
      setMessage({ ok: false, text: "公開する場合は予想名を入力してください" });
      return;
    }
    startTransition(async () => {
      const result = await updateMyPublicSetting({
        is_yoso_public: isPublic,
        yoso_name: isPublic ? yosoName.trim() : null,
      });
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
          message.ok
            ? "bg-green-50 text-green-700 border border-green-200"
            : "bg-red-50 text-red-700 border border-red-200"
        }`}>
          {message.text}
        </div>
      )}

      <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-xs font-medium text-gray-700">予想を公開する</div>
            <div className="text-xs text-gray-400 mt-0.5">
              ONにすると他ユーザーがあなたの予想を表示設定に追加できます
            </div>
          </div>
          <button
            role="switch"
            aria-checked={isPublic}
            onClick={() => setIsPublic((v) => !v)}
            className={`relative shrink-0 inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-blue-400 focus:ring-offset-1 ${
              isPublic ? "bg-blue-500" : "bg-gray-300"
            }`}
          >
            <span
              className={`inline-block h-4 w-4 rounded-full bg-white shadow transition-transform duration-200 ${
                isPublic ? "translate-x-6" : "translate-x-1"
              }`}
            />
          </button>
        </div>

        {isPublic && (
          <div className="space-y-1">
            <label className="text-xs font-medium text-gray-700">
              予想名 <span className="text-red-400">*</span>
            </label>
            <input
              type="text"
              value={yosoName}
              onChange={(e) => setYosoName(e.target.value)}
              placeholder="例: ベテランAI、東京巧者など"
              maxLength={50}
              className="w-full px-3 py-2 text-xs border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent"
            />
            <div className="text-xs text-gray-400">
              他ユーザーにはこの名前のみ表示されます（メールアドレス・アカウント名は非公開）
            </div>
          </div>
        )}
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
