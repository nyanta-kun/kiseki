import { fetchDisplaySettings, fetchMyPublicSetting } from "@/app/actions/yoso";
import { DisplaySettingsClient } from "./DisplaySettingsClient";
import { MyPublicSettingClient } from "./MyPublicSettingClient";
import type { DisplaySetting, MyPublicSetting } from "@/lib/api";

export default async function YosoSettingsPage() {
  const [myPublic, settings] = await Promise.all([
    fetchMyPublicSetting() as Promise<MyPublicSetting>,
    fetchDisplaySettings() as Promise<DisplaySetting[]>,
  ]);

  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-gray-700">自分の公開設定</h2>
        <MyPublicSettingClient initial={myPublic} />
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-gray-700">他ユーザーの表示設定</h2>
        <p className="text-xs text-gray-500">
          予想を公開しているユーザーの印・指数を予想一覧に表示するかを設定します。
          指数の表示は相手ユーザーが指数投入権限を持つ場合のみ有効です。
        </p>

        {settings.length === 0 ? (
          <div className="text-center py-12 text-gray-400 text-sm">
            公開中のユーザーがいません
          </div>
        ) : (
          <DisplaySettingsClient initialSettings={settings} />
        )}
      </section>
    </div>
  );
}
