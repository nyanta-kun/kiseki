"use client";

import { useEffect, useState, useTransition } from "react";
import Link from "next/link";
import { ArrowLeft, Bike, Loader2 } from "lucide-react";
import { fetchNetkeirinSettings, type NetkeirinRankKey, type NetkeirinSetting } from "@/lib/api";
import { saveNetkeirinSettings } from "./actions";

// ---------------------------------------------------------------------------
// 定数
// ---------------------------------------------------------------------------

const RANK_ORDER: NetkeirinRankKey[] = ["S1", "7SS", "7S", "7A", "9SS", "9S", "9A"];

const RANK_LABEL: Record<NetkeirinRankKey, string> = {
  _global: "全体",
  S1: "S1（三連単2点流し）",
  "7SS": "7SS（7車・三連複2軸流し5点）",
  "7S": "7S（7車・三連複2軸流し5点）",
  "7A": "7A（7車・境界ランク）",
  "9SS": "9SS（9車・三連複2軸流し7点）",
  "9S": "9S（9車・三連複2軸流し7点）",
  "9A": "9A（9車・境界ランク）",
};

// プレビュー用のテンプレート変数置換（keirin側 netkeirin_submit_wt.py と同じ
// 固定辞書の逐次 str.replace 方式。未定義の{...}はそのまま素通しする）。
const PREVIEW_VARS: Record<string, string> = {
  "{venue}": "小倉",
  "{race_no}": "9",
  "{date}": "2026-07-28",
  "{axis1}": "1",
  "{axis2}": "2",
};

function applyPreview(template: string, rank: string): string {
  let s = template.replaceAll("{rank}", rank);
  for (const [k, v] of Object.entries(PREVIEW_VARS)) {
    s = s.replaceAll(k, v);
  }
  return s;
}

type EditState = Record<string, NetkeirinSetting>;

function emptyRow(rank_key: NetkeirinRankKey): NetkeirinSetting {
  return { rank_key, enabled: true, title_template: "", comment_template: "" };
}

// ---------------------------------------------------------------------------
// トグルスイッチ（frontend/src/app/my/KeirinSettings.tsx と同じマークアップ）
// ---------------------------------------------------------------------------

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 flex-shrink-0 ${
        checked ? "bg-blue-500" : "bg-gray-300"
      }`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
          checked ? "translate-x-6" : "translate-x-1"
        }`}
      />
    </button>
  );
}

// ---------------------------------------------------------------------------
// ページ
// ---------------------------------------------------------------------------

export default function NetkeirinSettingsPage() {
  const [rows, setRows] = useState<EditState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [isPending, startTransition] = useTransition();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchNetkeirinSettings();
        if (cancelled) return;
        const next: EditState = { _global: emptyRow("_global") };
        for (const rank of RANK_ORDER) next[rank] = emptyRow(rank);
        for (const row of data) next[row.rank_key] = row;
        setRows(next);
      } catch {
        if (!cancelled) setError("設定の取得に失敗しました");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const update = (rank: string, patch: Partial<NetkeirinSetting>) => {
    setRows((prev) => (prev ? { ...prev, [rank]: { ...prev[rank], ...patch } } : prev));
  };

  const handleSave = () => {
    if (!rows) return;
    setSaveMsg(null);
    startTransition(async () => {
      const result = await saveNetkeirinSettings(Object.values(rows));
      setSaveMsg({ ok: result.ok, text: result.message });
    });
  };

  return (
    <div className="w-full sm:max-w-3xl sm:mx-auto px-3 sm:px-4 py-4 space-y-5 pb-28">
      {/* ヘッダー */}
      <div className="flex items-center gap-3">
        <Link
          href="/keirin"
          className="flex items-center gap-1 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 transition-colors"
        >
          <ArrowLeft size={16} />
          戻る
        </Link>
        <div className="flex items-center gap-2 ml-1">
          <Bike size={20} className="text-blue-500" />
          <h1 className="text-lg font-extrabold tracking-widest text-gray-950 dark:text-white">KEIRIN</h1>
          <span className="text-sm font-semibold text-gray-500 dark:text-gray-400">入稿設定</span>
        </div>
      </div>

      <p className="text-xs sm:text-sm text-gray-600 dark:text-gray-300 leading-relaxed">
        netkeirin（ウマい車券）への下書き自動入稿を、予想ランクごとに ON/OFF・タイトル・
        コメントのテンプレートで制御します。コメント末尾には、そのレースの出走選手ごとの
        1着率・3着内率テーブルが自動的に追加されます。
        テンプレートには <code className="bg-gray-100 text-gray-700 px-1 rounded">{"{venue}"}</code>{" "}
        <code className="bg-gray-100 text-gray-700 px-1 rounded">{"{race_no}"}</code>{" "}
        <code className="bg-gray-100 text-gray-700 px-1 rounded">{"{rank}"}</code>{" "}
        <code className="bg-gray-100 text-gray-700 px-1 rounded">{"{date}"}</code>{" "}
        <code className="bg-gray-100 text-gray-700 px-1 rounded">{"{axis1}"}</code>{" "}
        <code className="bg-gray-100 text-gray-700 px-1 rounded">{"{axis2}"}</code>{" "}
        が使えます（S1は axis1=軸・axis2=相手1）。
      </p>

      {loading && (
        <div className="space-y-3 animate-pulse">
          {[1, 2, 3].map((i) => (
            <div key={i} className="bg-white rounded-xl border border-gray-100 h-24" />
          ))}
        </div>
      )}

      {error && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl px-3 py-3 text-sm text-amber-700">
          {error}
        </div>
      )}

      {rows && (
        <>
          {/* 全体ON/OFF */}
          <section className="bg-white rounded-xl border border-gray-100 shadow-sm p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-bold text-gray-800">全体の自動入稿</p>
                <p className="text-xs text-gray-400 mt-0.5">
                  OFFにすると、各ランクのON/OFFに関わらず自動入稿を停止します。
                </p>
              </div>
              <Toggle
                checked={rows._global.enabled}
                onChange={(v) => update("_global", { enabled: v })}
              />
            </div>
          </section>

          {/* ランク別カード */}
          {RANK_ORDER.map((rank) => {
            const row = rows[rank];
            return (
              <section
                key={rank}
                className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden"
              >
                <div className="flex items-center justify-between gap-3 px-4 py-3 bg-gray-50 border-b border-gray-100">
                  <span className="text-sm font-semibold text-gray-800">{RANK_LABEL[rank]}</span>
                  <Toggle checked={row.enabled} onChange={(v) => update(rank, { enabled: v })} />
                </div>
                <div className="px-4 py-3 space-y-3">
                  <div>
                    <label className="text-xs font-medium text-gray-500 mb-1 block">
                      タイトルテンプレート
                    </label>
                    <input
                      type="text"
                      value={row.title_template}
                      onChange={(e) => update(rank, { title_template: e.target.value })}
                      className="w-full px-3 py-2 rounded-lg text-sm border border-gray-200 focus:outline-none focus:ring-2 focus:ring-blue-300"
                      placeholder="{venue}{race_no}R 二軸探偵"
                    />
                    {row.title_template && (
                      <p className="text-xs text-gray-400 mt-1 truncate">
                        プレビュー: {applyPreview(row.title_template, rank)}
                      </p>
                    )}
                  </div>
                  <div>
                    <label className="text-xs font-medium text-gray-500 mb-1 block">
                      コメントテンプレート
                    </label>
                    <textarea
                      value={row.comment_template}
                      onChange={(e) => update(rank, { comment_template: e.target.value })}
                      rows={5}
                      className="w-full px-3 py-2 rounded-lg text-sm border border-gray-200 focus:outline-none focus:ring-2 focus:ring-blue-300 font-mono"
                      placeholder="本日の二軸をお届けします。"
                    />
                    <p className="text-xs text-gray-400 mt-1">
                      ※ このテキストの末尾に選手成績テーブル（車番・印・選手名・1着率・3着内率）が自動追加されます。
                    </p>
                  </div>
                </div>
              </section>
            );
          })}

          {/* 保存 */}
          <div className="sticky bottom-4 flex items-center gap-3 bg-white/90 backdrop-blur rounded-xl border border-gray-200 shadow-lg px-4 py-3">
            <button
              type="button"
              onClick={handleSave}
              disabled={isPending}
              className="px-4 py-2 rounded-lg text-sm font-semibold bg-blue-500 text-white hover:bg-blue-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
            >
              {isPending && <Loader2 size={14} className="animate-spin" />}
              {isPending ? "保存中..." : "保存"}
            </button>
            {saveMsg && (
              <p className={`text-sm ${saveMsg.ok ? "text-green-600" : "text-red-600"}`}>
                {saveMsg.text}
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
