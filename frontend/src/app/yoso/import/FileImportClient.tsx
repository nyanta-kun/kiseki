"use client";

import { useRef, useState, useTransition } from "react";
import { importTargetCsv } from "@/app/actions/yoso";
import type { ImportLog } from "@/lib/api";

type Props = {
  initialHistory: ImportLog[];
};

export function FileImportClient({ initialHistory }: Props) {
  const [history, setHistory] = useState<ImportLog[]>(initialHistory);
  const [isDragging, setIsDragging] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [isPending, startTransition] = useTransition();
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = (file: File) => {
    if (!file.name.endsWith(".csv")) {
      setResult({ ok: false, message: "CSVファイルのみ対応しています" });
      return;
    }
    setSelectedFile(file);
    setResult(null);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  };

  const handleImport = () => {
    if (!selectedFile) return;
    const fd = new FormData();
    fd.append("file", selectedFile);

    startTransition(async () => {
      const res = await importTargetCsv(fd);
      if (res.ok) {
        setResult({
          ok: true,
          message: `投入完了: ${res.saved}件保存、${res.errors}件エラー`,
        });
        setSelectedFile(null);
        // 履歴を再取得（簡易: 先頭に仮エントリ追加）
        setHistory((prev) => [
          {
            id: Date.now(),
            filename: selectedFile.name,
            race_date: "",
            total_count: (res.saved ?? 0) + (res.errors ?? 0),
            saved_count: res.saved ?? 0,
            error_count: res.errors ?? 0,
            created_at: new Date().toISOString(),
          },
          ...prev,
        ]);
      } else {
        setResult({ ok: false, message: res.error ?? "投入に失敗しました" });
      }
    });
  };

  return (
    <div className="space-y-4">
      {/* ドロップゾーン */}
      <div
        onDrop={handleDrop}
        onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
        onDragLeave={() => setIsDragging(false)}
        onClick={() => inputRef.current?.click()}
        className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors ${
          isDragging
            ? "border-blue-400 bg-blue-50"
            : "border-gray-300 hover:border-blue-300 hover:bg-blue-50/30"
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".csv"
          className="hidden"
          onChange={(e) => { if (e.target.files?.[0]) handleFile(e.target.files[0]); }}
        />
        <div className="text-3xl mb-2">📂</div>
        {selectedFile ? (
          <p className="text-sm text-blue-700 font-medium">{selectedFile.name}</p>
        ) : (
          <>
            <p className="text-sm text-gray-600">CSVファイルをドロップ</p>
            <p className="text-xs text-gray-400 mt-1">またはクリックして選択</p>
          </>
        )}
      </div>

      {/* 投入ボタン */}
      {selectedFile && (
        <button
          onClick={handleImport}
          disabled={isPending}
          className="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white text-sm font-medium rounded-xl transition-colors"
        >
          {isPending ? "投入中..." : "取り込む"}
        </button>
      )}

      {/* 結果メッセージ */}
      {result && (
        <div className={`rounded-lg px-4 py-3 text-sm ${
          result.ok ? "bg-green-50 text-green-700 border border-green-200" : "bg-red-50 text-red-700 border border-red-200"
        }`}>
          {result.message}
        </div>
      )}

      {/* 投入履歴 */}
      {history.length > 0 && (
        <div>
          <h2 className="text-xs font-semibold text-gray-500 mb-2">投入履歴</h2>
          <div className="bg-white rounded-xl border border-gray-100 overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-400 border-b border-gray-100">
                  <th className="px-3 py-2 text-left">ファイル名</th>
                  <th className="px-3 py-2 text-right">保存</th>
                  <th className="px-3 py-2 text-right">エラー</th>
                  <th className="px-3 py-2 text-right">日時</th>
                </tr>
              </thead>
              <tbody>
                {history.map((log) => (
                  <tr key={log.id} className="border-b border-gray-50 last:border-0">
                    <td className="px-3 py-2 text-gray-700 max-w-[180px] truncate">{log.filename}</td>
                    <td className="px-3 py-2 text-right text-green-600 font-mono">{log.saved_count}</td>
                    <td className="px-3 py-2 text-right font-mono">
                      <span className={log.error_count > 0 ? "text-red-500" : "text-gray-300"}>
                        {log.error_count}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right text-gray-400">
                      {new Date(log.created_at).toLocaleString("ja-JP", {
                        month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
                      })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
