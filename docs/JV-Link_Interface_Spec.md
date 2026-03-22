# JRA-VAN Data Lab. SDK - JV-Link インターフェース仕様書

> 第4.9.0.1版 (2024年8月7日)

---

## 1. プロパティ

| プロパティ | 型 | 説明 |
|-----------|-----|------|
| `m_saveflag` | Integer | ダウンロードファイルを`m_savepath`に保存するかどうかのフラグ。`0`=保存しない、`1`=保存する |
| `m_savepath` | String | JV-Data を保存するディレクトリパス。JVInit 呼び出し時にレジストリから取得。デフォルト: `%InstallPath%`。`data` / `cache` サブフォルダに保存される |
| `m_servicekey` | String | JRA-VAN DataLab. サービス利用キー（17桁）。JVInit 呼び出し時にレジストリから取得 |
| `m_JVLinkVersion` | String | JV-Link バージョン（4桁数字、例: `0100`）。読み取り専用 |
| `m_TotalReadFilesize` | Long | JVOpen 後にセットされる読み込み対象の総データサイズ（KB単位、0の場合は1）。読み取り専用。プログレスバー表示に利用可能 |
| `m_CurrentReadFilesize` | Long | JVRead/JVGets で現在読み込み中のファイルサイズ。読み取り専用 |
| `m_CurrentFileTimestamp` | String | JVRead/JVGets で現在読み込み中のファイルのタイムスタンプ。読み取り専用 |
| `ParentHWnd` | Long | JV-Link が表示するダイアログのオーナーウィンドウ。JVOpen/JVRTOpen 呼出前に設定（Ver2.0.0以降必須） |
| `m_payflag` | Integer | 払戻ダイアログ表示フラグ。`0`=表示する、`1`=表示しない |

---

## 2. メソッド一覧

| メソッド | 処理内容 |
|---------|---------|
| `JVInit` | JV-Link の初期化 |
| `JVSetUIProperties` | JV-Link 設定変更（ダイアログ版） |
| `JVSetServiceKey` | JV-Link 設定変更（利用キー） |
| `JVSetSaveFlag` | JV-Link 設定変更（保存フラグ） |
| `JVSetSavePath` | JV-Link 設定変更（保存パス） |
| `JVOpen` | 蓄積系データの取得要求 |
| `JVRTOpen` | リアルタイム系データの取得要求 |
| `JVStatus` | ダウンロード進捗情報の取得 |
| `JVRead` | JV-Data の読み込み |
| `JVGets` | JV-Data の読み込み（バイト配列版） |
| `JVSkip` | JV-Data の読みとばし |
| `JVCancel` | ダウンロードスレッドの停止 |
| `JVClose` | JV-Data 読み込み処理の終了 |
| `JVFiledelete` | ダウンロードしたファイルの削除 |
| `JVFukuFile` | 勝負服画像情報要求（ファイル出力） |
| `JVFuku` | 勝負服画像情報要求（バイナリ） |
| `JVMVCheck` | レース映像公開チェック要求 |
| `JVMVCheckWithType` | 映像公開チェック要求（種別指定） |
| `JVMVPlay` | レース映像再生要求 |
| `JVMVPlayWithType` | 映像再生要求（種別指定） |
| `JVMVOpen` | 動画リストの取得要求 |
| `JVMVRead` | 動画リストの読み込み |
| `JVCourseFile` | コース図取得要求（説明付き） |
| `JVCourseFile2` | コース図取得要求（ファイル保存） |
| `JVWatchEvent` | 確定・変更情報イベント通知開始 |
| `JVWatchEventClose` | イベント通知終了 |

---

## 3. メソッド詳細

### JVInit - JV-Link の初期化

```
Long JVInit(String sid)
```

**パラメータ:**
- `sid` - ソフトウェアID（最大64バイト、半角英数字・スペース・`_`・`.`・`/`のみ）。デフォルト `"UNKNOWN"` 使用可

**戻り値:** `0`=正常、負の値=エラーコード

**解説:** アプリケーション初期化時に**必ず最初に**呼び出す。JVOpen/JVRTOpen の都度呼び出す必要はない。

---

### JVSetUIProperties - 設定変更（ダイアログ版）

```
Long JVSetUIProperties()
```

**パラメータ:** なし

**戻り値:** `0`=正常またはキャンセル、`-100`=エラー

**設定可能プロパティ:**
- `m_saveflag`（保存フラグ）
- `m_savepath`（保存パス）
- `m_servicekey`（利用キー）※設定済の場合は変更不可
- `m_payflag`（払戻ダイアログ表示フラグ）

---

### JVSetServiceKey - 設定変更（利用キー）

```
Long JVSetServiceKey(String servicekey)
```

**パラメータ:**
- `servicekey` - 利用キー（17桁の英数字）

**戻り値:** `0`=正常、`-100`=エラー

---

### JVSetSaveFlag - 設定変更（保存フラグ）

```
Long JVSetSaveFlag(Long saveflag)
```

**パラメータ:**
- `saveflag` - `0`=保存しない、`1`=保存する

**戻り値:** `0`=正常、`-100`=エラー

---

### JVSetSavePath - 設定変更（保存パス）

```
Long JVSetSavePath(String savepath)
```

**パラメータ:**
- `savepath` - 実際に存在するパスを指定。`cache`/`data` サブフォルダが自動作成される

**戻り値:** `0`=正常、`-100`=エラー

---

### JVOpen - 蓄積系データの取得要求

```
Long JVOpen(String dataspec, String fromtime, Long option,
            Long readcount, Long downloadcount, String lastfiletimestamp)
```

**パラメータ:**

| パラメータ | 説明 |
|-----------|------|
| `dataspec` | データ種別ID（4桁固定、複数連結可）。詳細は「JV-Data仕様書」参照 |
| `fromtime` | 読み出し開始ポイント時刻 `YYYYMMDDhhmmss`、または範囲指定 `YYYYMMDDhhmmss-YYYYMMDDhhmmss` |
| `option` | `1`=通常データ、`2`=今週データ、`3`=セットアップデータ、`4`=ダイアログ無しセットアップ |
| `readcount` | （出力）条件に該当する全ファイル数 |
| `downloadcount` | （出力）ダウンロードが必要なファイル数 |
| `lastfiletimestamp` | （出力）最新ファイルのタイムスタンプ `YYYYMMDDhhmmss`。次回JVOpenのfromtimeに使用 |

**option と dataspec の組み合わせ:**

| option | 指定可能な dataspec |
|--------|-------------------|
| `1` | `TOKU`, `RACE`, `DIFF`, `BLOD`, `SNAP`, `SLOP`, `WOOD`, `YSCH`, `HOSE`, `HOYU`, `DIFN`, `BLDN`, `SNPN`, `HOSN` |
| `2` | `TOKU`, `RACE`, `TCOV`, `RCOV`, `SNAP`, `TCVN`, `RCVN`, `SNPN` |
| `3`, `4` | `TOKU`, `RACE`, `DIFF`, `BLOD`, `SNAP`, `SLOP`, `WOOD`, `YSCH`, `HOSE`, `HOYU`, `COMM`, `MING`, `DIFN`, `BLDN`, `SNPN`, `HOSN` |

**読み出し終了ポイント時刻を指定できないデータ種別ID:**
`TOKU`, `DIFF`, `DIFN`, `HOSE`, `HOSN`, `HOYU`, `COMM`（指定時は戻り値 `-1`）

**戻り値:** `0`=正常、負の値=エラーコード

**JVRead/JVGets の中断・再開:**
- 通常データ: `m_CurrentFileTimestamp` を保持し、再開時の `fromtime` に指定
- セットアップデータ: 最後に読込んだファイル名を保持し、JVSkip で該当ファイルまでスキップ

> **既知の障害:** dataspec を複数指定するとJVReadの処理時間が遅くなる場合がある。個別指定を推奨。

---

### JVRTOpen - リアルタイム系データの取得要求

```
Long JVRTOpen(String dataspec, String key)
```

**パラメータ:**

| パラメータ | 説明 |
|-----------|------|
| `dataspec` | データ種別ID（4桁固定、1つのみ） |
| `key` | 要求キー |

**key の指定形式:**

| 提供単位 | key 形式 |
|---------|---------|
| レース毎 | `YYYYMMDDJJKKHHRR` または `YYYYMMDDJJRR` |
| 開催日単位 | `YYYYMMDD` |
| 変更情報単位 | JVWatchEvent から返されるパラメータ |

**イベント用 dataspec:**

| 種類 | dataspec |
|-----|---------|
| 払戻確定 | `0B12` |
| 騎手変更 | `0B16` |
| 天候馬場状態変更 | `0B16` |
| コース変更 | `0B16` |
| 出走取消・競走除外 | `0B16` |
| 発走時刻変更 | `0B16` |
| 馬体重発表 | `0B11` |

**戻り値:** `0`=正常、負の値=エラーコード

---

### JVStatus - ダウンロード進捗情報の取得

```
Long JVStatus()
```

**パラメータ:** なし

**戻り値:** ダウンロード完了ファイル数（Long）。`downloadcount` と一致したらダウンロード完了。エラー時は負の値

> JVRead/JVGets の前にダウンロード完了を確認すること。

---

### JVRead - JV-Data の読み込み

```
Long JVRead(String buff, Long size, String filename)
```

**パラメータ:**

| パラメータ | 説明 |
|-----------|------|
| `buff` | データ格納バッファ（改行コード含むレコードサイズ + 1） |
| `size` | バッファにコピーするデータ長 |
| `filename` | （出力）現在読み込み中のファイル名 |

**戻り値:**
- `正の値` = 読み込んだバイト数
- `-1` = ファイル切り替わり（継続してください）
- `0` = 全ファイル読み込み終了（EOF）
- 負の値 = エラーコード

> JVRead では buff は JV-Link 内部で解放・再確保される。

---

### JVGets - JV-Data の読み込み（バイト配列版）

```
Long JVGets(Byte Array buff, Long size, String filename)
```

**パラメータ:**

| パラメータ | 説明 |
|-----------|------|
| `buff` | BYTE型配列ポインタ |
| `size` | コピーするデータ長 |
| `filename` | （出力）現在読み込み中のファイル名 |

**戻り値:** JVRead と同様

**JVGets の特徴:**
- JVRead より高パフォーマンス（SJIS→UNICODE変換なし）
- アプリケーション側でメモリ解放が必要（`Erase bytData`）
- JVRead と交互に呼び出し可能

```vb
' VB6 での使用例
Dim bytData() As Byte
ReturnCode = JVLink1.JVGets(bytData, BuffSize, BuffName)
Debug.Print bytData
Erase bytData  ' メモリ解放必須
```

---

### JVSkip - JV-Data の読みとばし

```
void JVSkip()
```

**パラメータ:** なし / **戻り値:** なし

**解説:** 現在読み込み中のファイルの残りレコードをスキップし、次のファイル先頭へ移動。不要なレコード種別の処理時間短縮に有効。

**注意点:**
- 複数回連続呼び出しでも1回と同じ動作
- JVOpen 直後に呼び出すと2ファイル目から読み込み
- JVRead/JVGets が `-1` を返した直後に呼び出すと次のファイルをスキップ

---

### JVCancel - ダウンロードスレッドの停止

```
void JVCancel()
```

**パラメータ:** なし / **戻り値:** なし

**解説:** JVOpen で起動されたダウンロードを中断。中断後に JVRead/JVGets を呼ぶとエラー。

---

### JVClose - JV-Data 読み込み処理の終了

```
Long JVClose()
```

**パラメータ:** なし

**戻り値:** `0`=正常

**解説:** 開いているファイルを全てクローズし、ダウンロードスレッドを中断、不要ファイルを削除。

---

### JVFiledelete - ダウンロードしたファイルの削除

```
Long JVFiledelete(String filename)
```

**パラメータ:**
- `filename` - 削除対象のファイル名

**戻り値:** `0`=正常、`-1`=エラー

---

### JVFukuFile - 勝負服画像情報要求（ファイル出力）

```
Long JVFukuFile(String pattern, String filepath)
```

**パラメータ:**
- `pattern` - 服色標示（最大全角30文字、例: `「水色，赤山形一本輪，水色袖」`）
- `filepath` - 出力ファイル名（フルパス）

**戻り値:** `0`=正常、`-1`=該当データ無し（No Image 出力）、負の値=エラー

**出力形式:** 50×50px、BMP 24ビット

---

### JVFuku - 勝負服画像情報要求（バイナリ）

```
Long JVFuku(String pattern, Byte Array buff)
```

**パラメータ:**
- `pattern` - 服色標示
- `buff` - （出力）画像データのバイト配列

**戻り値:** `0`=正常、`-1`=該当データ無し、負の値=エラー

---

### JVMVCheck / JVMVCheckWithType - 映像公開チェック

```
Long JVMVCheck(String key)
Long JVMVCheckWithType(String movietype, String key)
```

**movietype:**
| 値 | 種類 |
|----|------|
| `"00"` | レース映像（JVMVCheck と同等） |
| `"01"` | パドック映像 |
| `"02"` | マルチカメラ映像 |
| `"03"` | パトロール映像 |

**key 形式:** `YYYYMMDDJJRR` または `YYYYMMDDJJKKHHRR`

**戻り値:** `1`=公開あり、`0`=公開なし、`-1`=該当データ無し、負の値=エラー

> JVOpen/JVRTOpen/JVMVOpen 中は使用不可。先に JVClose を呼び出すこと。

---

### JVMVPlay / JVMVPlayWithType - 映像再生要求

```
Long JVMVPlay(String key)
Long JVMVPlayWithType(String movietype, String key)
```

**movietype と key:**

| movietype | 種類 | key 形式 |
|-----------|------|---------|
| `"00"` | レース映像 | `YYYYMMDDJJKKHHRRTT` または `YYYYMMDDJJRR[TT]` |
| `"01"` | パドック映像 | `YYYYMMDDJJKKHHRR` または `YYYYMMDDJJRR` |
| `"02"` | マルチカメラ映像 | `YYYYMMDDJJKKHHRR` または `YYYYMMDDJJRR` |
| `"03"` | パトロール映像 | `YYYYMMDDJJKKHHRR` または `YYYYMMDDJJRR` |
| `"11"`, `"12"`, `"13"` | 調教映像 | `YYYYMMDDNNNNNNNNNN`（血統登録番号） |

**TT（動画種別）:**
- `"01"` = 高解像度版優先
- `"02"` = 通常版優先
- 省略 = 高解像度版優先

**戻り値:** `0`=正常、`-1`=該当データ無し、負の値=エラー

> 利用には JRA レーシングビュアー連携機能利用申請が必要。開発時は JVInit の sid に `"SA000000/SD000004"` を指定。

---

### JVMVOpen - 動画リストの取得要求

```
Long JVMVOpen(String movietype, String searchkey)
```

| movietype | 種類 | searchkey |
|-----------|------|-----------|
| `"11"` | 調教映像（指定週全馬） | `YYYYMMDD` |
| `"12"` | 調教映像（指定週指定馬） | `YYYYMMDDNNNNNNNNNN` |
| `"13"` | 調教映像（指定馬全調教） | `NNNNNNNNNN`（血統登録番号） |

**戻り値:** `0`=正常、負の値=エラー

---

### JVMVRead - 動画リストの読み込み

```
Long JVMVRead(String buff, Long size)
```

**レコード形式:** `YYYYMMDDNNNNNNNNNN<改行>`（最大21バイト）

**戻り値:** バイト数（正常）、`0`=EOF、負の値=エラー

> JVRead と異なり、ファイル切り替わり時に `-1` を返さない。処理後は JVClose を呼ぶこと。

---

### JVCourseFile - コース図要求（説明付き）

```
Long JVCourseFile(String key, String filepath, String explanation)
```

**key 形式:** `YYYYMMDDJJKKKKTT`（最新取得: 開催年月日に `99999999`）

**パラメータ:**
- `filepath` - （出力）画像ファイルパス
- `explanation` - （出力）コース説明（最大6800バイト）

**出力形式:** 256×200px、GIF形式

**戻り値:** `0`=正常、負の値=エラー

---

### JVCourseFile2 - コース図要求（ファイル保存）

```
Long JVCourseFile2(String key, String filepath)
```

**パラメータ:**
- `key` - `YYYYMMDDJJKKKKTT`（最新取得: `99999999` 指定）
- `filepath` - 出力ファイル名（フルパス）

**戻り値:** `0`=正常、負の値=エラー

---

### JVWatchEvent - イベント通知開始

```
Long JVWatchEvent()
```

**パラメータ:** なし

**戻り値:** `0`=正常、負の値=エラー

**受信可能なイベント:**

| 種類 | イベントメソッド名 | bstr パラメータ形式 |
|------|-----------------|-------------------|
| 払戻確定 | `JVEvtPay` | `YYYYMMDDJJRR` |
| 馬体重発表 | `JVEvtWeight` | `YYYYMMDDJJRR` |
| 騎手変更 | `JVEvtJockeyChange` | `TTYYYYMMDDJJRRNNNNNNNNNNNNNN` |
| 天候馬場状態変更 | `JVEvtWeather` | 同上 |
| コース変更 | `JVEvtCourseChange` | 同上 |
| 出走取消・競走除外 | `JVEvtAvoid` | 同上 |
| 発走時刻変更 | `JVEvtTimeChange` | 同上 |

**VB6 使用例:**
```vb
' WithEvents 付きで宣言
Friend WithEvents InterfaceJVLink As JVDTLabLib.JVLink
InterfaceJVLink = New JVDTLabLib.JVLink
ReturnCode = InterfaceJVLink.JVWatchEvent()

' 払戻確定イベント
Private Sub InterfaceJVLink_JVEvtPay(ByVal bstr As String)
    Handles InterfaceJVLink.JVEvtPay
    ReturnCode = frmMain.JVLink1.JVRTOpen("0B12", bstr)
End Sub
```

---

### JVWatchEventClose - イベント通知終了

```
Long JVWatchEventClose()
```

**パラメータ:** なし

**戻り値:** `0`=正常、`-1`=エラー

---

## 4. コード表

### 共通エラーコード

| 戻り値 | 意味 |
|--------|------|
| `0` | 正常 |
| `-1` | 該当データ無し |
| `-2` | セットアップダイアログでキャンセル |
| `-100` | パラメータ不正またはレジストリ保存失敗 |
| `-101` | sid 未設定 / 既に利用キー登録済み |
| `-102` | sid が64バイト超 |
| `-103` | sid の1桁目がスペース |
| `-111` | dataspec / movietype パラメータ不正 |
| `-112` | fromtime（開始時刻）不正 |
| `-113` | fromtime（終了時刻）不正 |
| `-114` | key / searchkey パラメータ不正 |
| `-115` | option パラメータ不正 |
| `-116` | dataspec と option の組み合わせ不正 |
| `-118` | filepath パラメータ不正 |
| `-201` | JVInit が行われていない |
| `-202` | 前回の Open に対して JVClose が呼ばれていない |
| `-203` | JVOpen / JVMVOpen が行われていない |
| `-211` | レジストリ内容が不正 |
| `-301` | 認証エラー（利用キー不正または複数マシン使用） |
| `-302` | 利用キーの有効期限切れ |
| `-303` | 利用キーが未設定（空値） |
| `-304` | JRA レーシングビュアー連携機能の認証エラー |
| `-305` | 利用規約に未同意 |
| `-401` | JV-Link 内部エラー |
| `-402` | ダウンロードファイル異常（サイズ=0） |
| `-403` | ダウンロードファイル異常（データ内容） |
| `-411` | サーバーエラー（HTTP 404 NotFound） |
| `-412` | サーバーエラー（HTTP 403 Forbidden） |
| `-413` | サーバーエラー（HTTP その他） |
| `-421` | サーバーエラー（応答不正） |
| `-431` | サーバーエラー（アプリ内部エラー） |
| `-501` | スタートキット(CD/DVD-ROM)が無効 ※2022年3月提供終了 |
| `-502` | ダウンロード失敗（通信/ディスクエラー） |
| `-503` | ファイルが見つからない |
| `-504` | サーバーメンテナンス中 |

### JVRead / JVGets 固有コード

| 戻り値 | 意味 |
|--------|------|
| `正の値` | 正常（バッファにセットしたデータのバイト数） |
| `0` | 全ファイル読み込み終了（EOF） |
| `-1` | ファイル切り替わり（エラーではない） |
| `-3` | ファイルダウンロード中（少し待ってから再試行） |

### JVStatus 固有コード

| 戻り値 | 意味 |
|--------|------|
| `0以上` | 正常（ダウンロード済みファイル数） |

---

## 5. 典型的な使用フロー

### 蓄積系データ取得

```
JVInit(sid)
  ↓
JVOpen(dataspec, fromtime, option, readcount, downloadcount, lastfiletimestamp)
  ↓
while JVStatus() < downloadcount:
    wait
  ↓
loop:
  ret = JVRead(buff, size, filename)
  if ret == 0: break  (EOF)
  if ret == -1: continue  (ファイル切り替わり)
  if ret < -1: handle error
  process(buff)
  ↓
JVClose()
```

### リアルタイム系データ取得（イベント駆動）

```
JVInit(sid)
JVWatchEvent()
  ↓
イベント発生時:
  JVRTOpen(dataspec, bstr)
    ↓
  loop: JVRead / JVGets → 処理
    ↓
  JVClose()
  ↓
JVWatchEventClose()
```
