import { auth } from "@/auth";
import { fetchImportHistory } from "@/app/actions/yoso";
import { FileImportClient } from "./FileImportClient";
import type { ImportLog } from "@/lib/api";

export default async function YosoImportPage() {
  const session = await auth();
  const canInputIndex = session?.user?.can_input_index ?? false;
  const history = (await fetchImportHistory()) as ImportLog[];

  return (
    <div className="space-y-6">
      <h1 className="text-sm font-semibold text-gray-700">データ投入</h1>

      {canInputIndex ? (
        <FileImportClient initialHistory={history} />
      ) : (
        <div className="bg-yellow-50 border border-yellow-200 rounded-xl p-6 text-center">
          <p className="text-sm text-yellow-800 font-medium">指数投入権限がありません</p>
          <p className="text-xs text-yellow-600 mt-1">管理者に権限付与を依頼してください</p>
        </div>
      )}

      {/* CSV仮フォーマット説明 */}
      <div className="bg-gray-50 rounded-xl border border-gray-100 p-4">
        <h2 className="text-xs font-semibold text-gray-600 mb-2">CSVフォーマット（仮: v1）</h2>
        <pre className="text-xs text-gray-500 font-mono leading-relaxed overflow-x-auto">
{`race_date,course_code,race_no,horse_no,index
20260405,05,1,1,78.5
20260405,05,1,2,65.3
20260405,06,1,1,72.0`}
        </pre>
        <ul className="mt-3 space-y-0.5 text-xs text-gray-500">
          <li>• race_date: YYYYMMDD 形式</li>
          <li>• course_code: JRA 2 桁（01=札幌 02=函館 03=福島 04=新潟 05=東京 06=中山 07=中京 08=京都 09=阪神 10=小倉）</li>
          <li>• race_no: レース番号（1〜12）</li>
          <li>• horse_no: 馬番</li>
          <li>• index: 指数値（小数可）</li>
        </ul>
        <p className="mt-2 text-xs text-orange-600">※ このフォーマットは仮仕様です。TARGET連携確定後に変更予定。</p>
      </div>
    </div>
  );
}
