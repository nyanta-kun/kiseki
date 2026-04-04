import { fetchDisplaySettings } from "@/app/actions/yoso";
import { DisplaySettingsClient } from "./DisplaySettingsClient";
import type { DisplaySetting } from "@/lib/api";

export default async function YosoSettingsPage() {
  const settings = (await fetchDisplaySettings()) as DisplaySetting[];

  return (
    <div className="space-y-4">
      <h1 className="text-sm font-semibold text-gray-700">表示設定</h1>
      <p className="text-xs text-gray-500">
        他のユーザーの印・指数を予想一覧に表示するかを設定します。
        指数の表示は相手ユーザーが指数投入権限を持つ場合のみ有効です。
      </p>

      {settings.length === 0 ? (
        <div className="text-center py-12 text-gray-400 text-sm">
          他のユーザーがいません
        </div>
      ) : (
        <DisplaySettingsClient initialSettings={settings} />
      )}
    </div>
  );
}
