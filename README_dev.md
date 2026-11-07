# 勞權雷達：開發與執行

以下指令在這個資料夾執行，Linux、macOS 或 WSL 都可以，需要先裝好 [uv](https://docs.astral.sh/uv/)。

## 執行

```bash
uv sync
wslport 8730 -n          # 選用：在 WSL 上先確認 port 是空的（結束碼 1 代表空）
uv run uvicorn labor_radar.web:app --port 8730
```

瀏覽器開 <http://127.0.0.1:8730>，按「載入範例（虛構的林小美）」再按「開始健檢」。

這樣啟動沒有 Gemini 金鑰，「讀取照片」按鈕會停用，其他功能都能用。要讀照片時，先在本機設定 `GEMINI_API_KEY` 環境變數再啟動。程式只從這個環境變數讀金鑰，不會印出來，也不會寫進檔案；金鑰不要放進這個資料夾，更不要提交到 Git：

```bash
export GEMINI_API_KEY=<你的金鑰>
uv run uvicorn labor_radar.web:app --port 8730
```

照片會傳到 Google 的 Gemini API。依 Gemini API 條款，免費額度送出的內容可能被拿去改進產品、由人工審閱，所以只用虛構照片示範；處理真實勞工的照片前，要改用付費額度（出處見 `labor_radar/rules/sources.md`「Gemini API 的資料使用」）。

示範照片是 `samples/schedule_lin_xiaomei.jpg`。要換模型，加上 `GEMINI_MODEL=<模型名稱>`，預設是 `gemini-2.5-flash`。

## 測試與範例

```bash
uv run pytest -q
uv run python -m labor_radar samples/lin_xiaomei.json          # Markdown 報告
uv run python -m labor_radar samples/lin_xiaomei.json --json   # 完整 JSON
uv run python -m labor_radar samples/lin_xiaomei.json --lang vi
```

已錄好的輸出在 `samples/lin_xiaomei.output.md`。Gemini 讀示範照片的實際結果在 `samples/schedule_lin_xiaomei.extracted.json`。

## 程式結構

| 檔案 | 負責 |
|---|---|
| `labor_radar/model.py` | 一位勞工的資料：班次、工作條件、薪資單 |
| `labor_radar/intake.py` | 唯一的輸入邊界。表單、JSON 檔或 AI 讀出的資料都在這裡驗證，轉成 `WorkRecord` |
| `labor_radar/extraction.py` | Gemini 視覺模型。只轉錄，輸出受 JSON Schema 約束，結果填回表單讓使用者確認 |
| `labor_radar/rules/law.py` | 法條引用與全國法規資料庫連結 |
| `labor_radar/rules/minimum_wage.py` | 最低工資年度表，含 `pending_approval` 狀態 |
| `labor_radar/rules/holidays.py` | 國定假日表（紀念日及節日實施條例，實際日期） |
| `labor_radar/rules/ledger.py` | 日帳：班次歸日、7 日週期（起算日由 engine 指定）、休息日推定、平日每小時工資額 |
| `labor_radar/rules/core.py` | 規則、結果、適用性閘門的資料形狀 |
| `labor_radar/rules/registry.py` | 規則表：每條規則一個檢查函式和一筆 `Rule` |
| `labor_radar/rules/sources.md` | 法條原文與行政解釋引文，由 `tools/fetch_sources.py` 產生 |
| `labor_radar/engine.py` | 跑全部規則、套閘門、和薪資單結算。勞工不知道 7 日週期起算日時，7 種起算日各跑一次，採用金額最低的一種 |
| `labor_radar/report.py`、`paperwork.py`、`referrals.py` | 輸出：說明、算式、證據清單、調解申請書草稿、轉介 |
| `labor_radar/i18n/*.json` | 介面字串。`zh-TW` 是基準，`vi`、`id`、`th` 是機器翻譯草稿 |
| `labor_radar/web.py`、`static/` | FastAPI 與前端 |
| `labor_radar/agent/`、`static/agent.js` | AI 助理（代理人），見下方「AI 助理」 |

## 每年要更新的資料

1. 最低工資：勞動部公告新數額後，在 `minimum_wage.py` 的 `TABLE` 加一列。只有提案、還沒公告的，狀態填 `PENDING_APPROVAL`，結果只會顯示「如果核定」的差額，不算進短少金額。
2. 國定假日：依人事行政總處的辦公日曆表確認農曆節日的國曆日期，加進 `holidays.py`，並延長 `COVERED`。只用實際日期，公務員的補假和調整放假不適用勞工。
3. 法條：執行 `uv run python tools/fetch_sources.py` 重新抓取原文，用 `git diff` 檢查條文有沒有變。

## 新增一條規則

1. 在 `rules/law.py` 加上引用。執行 `tools/fetch_sources.py`，讓原文進到 `sources.md`。
2. 在 `rules/registry.py` 寫檢查函式，回傳 `Finding`；有金額的寫成 `Calc`，算式會原樣顯示給使用者。
3. 在 `RULES` 加一筆 `Rule`：引用、生效日、適用性閘門、證據項目、是否涉及金額。
4. 在 `i18n/zh-TW.json` 加 `rule.<id>.title` 和每個 `rule.<id>.<variant>` 說明。
5. 在 `tests/test_rules.py` 寫行為測試，預期金額要從條文手算，不能從程式跑出來再抄。

`tests/test_engine.py` 會檢查每條規則都有標題、每個引用的法條都在 `sources.md` 裡有原文。

## 憑證問題

全國法規資料庫的憑證缺少 Subject Key Identifier，Python 3.13 起的嚴格驗證會拒絕。`tools/fetch_sources.py` 只關掉 `VERIFY_X509_STRICT`，憑證鏈和主機名稱仍照常驗證。

## AI 助理

網頁有兩種模式。「表單」模式要使用者自己填法律問題。「AI 助理」模式讓使用者用對話和照片交代工作情況，由助理整理成資料表。

金鑰只從環境變數 `GEMINI_API_KEY` 讀取，模型由 `GEMINI_MODEL` 指定，預設 `gemini-2.5-flash`。沒有金鑰時 `/api/meta` 的 `agent` 為 `false`，`/api/agent/turn` 回 503，AI 助理分頁無法使用，表單模式不受影響。

實機驗證用兩支腳本，都需要金鑰，會花幾十次 Gemini 呼叫：

```bash
uv run python tools/agent_smoke.py   # 腳本化工人對話，輸出 samples/agent_demo.transcript.md
uv run --with playwright python tools/agent_ui_shot.py --chrome <chrome 路徑>   # 需先啟動網頁，輸出截圖
```

### 設計原則

AI 不判斷合法與否，也不計算金額。規則引擎判斷，護欄檢查 AI 說出口的每個數字。`extraction.py` 只轉錄照片，`intake.parse_record` 是資料進入引擎的唯一入口，代理人沿用這兩條界線。

### 代理人模組（`labor_radar/agent/`）

| 模組 | 負責 |
| --- | --- |
| `case.py` | `Case`：草稿、每個欄位的來源與原話、已回答題目、是否確認、對話紀錄、最近一次報告。欄位登錄表 `FIELDS` 同時管驗證、預設值和假設標記。`materialize` 補預設值產出完整 RecordIn，`gaps` 列出缺少的必要資料或 intake 拒絕的原因。 |
| `questions.py` | `QUESTIONS` 登錄表與敏感度分析。`phase(case)` 由資料推導階段，不存放。 |
| `tools.py` | `TOOLS` 登錄表、`ALLOWED` 階段政策、`call` 分派。模型只能經由這裡改變案件。 |
| `guard.py` | 檢查模型回覆的數字與用語。 |
| `llm.py` | `LLM` 協定、`GeminiLLM`（REST）、`ScriptedLLM`（測試用）。 |
| `loop.py` | `run_turn`：依階段給工具、執行工具呼叫、記錄軌跡、交給護欄。 |

網頁介面在 `labor_radar/static/agent.js`，報告畫面與表單模式共用 `app.js` 的 `renderResult`。

### 階段與工具政策

階段由案件推導：必要資料不齊（時薪或月薪、計薪方式、至少一筆班次）或 intake 拒絕草稿為 `gather`；還有會改變結果且未回答的題目為 `clarify`；其餘未確認為 `confirm`；`confirmed` 為真為 `report`。`confirmed` 只由 `POST /api/agent/confirm` 設定，而且只在 `confirm` 階段接受。任何工具改變了事實，`confirmed` 與 `last_report` 一併清除。

| 工具 | gather | clarify | confirm | report |
| --- | --- | --- | --- | --- |
| `read_document`（僅限本則訊息附檔時） | 可 | 可 | 可 | 可 |
| `set_facts` | 可 | 可 | 可 | 可 |
| `next_questions` | 可 | 可 | 可 | 不可 |
| `run_check` | 不可 | 不可 | 不可 | 可 |
| `explain_rule` | 不可 | 不可 | 不可 | 可 |
| `draft_mediation`（需先有找到項目的 `run_check`） | 不可 | 不可 | 不可 | 可 |
| `find_help` | 不可 | 不可 | 不可 | 可 |

每一步送給模型的工具清單依階段過濾，`call` 對清單以外的呼叫也回 error。附檔時，回合的第一步強制呼叫 `read_document`。其餘回合的第一步強制呼叫某個工具（Gemini `mode: ANY`），因為 AUTO 模式下實測會出現只說「已記下」卻沒呼叫 `set_facts` 的回覆。

`set_facts` 的規則：路徑須在登錄表內，列舉值須是模型 enum 的值（Gemini 曾送出 `"weekly shifts"`，會被拒並列出允許的值）。每筆事實的 `quote` 須逐字出現在工人說過的話裡（忽略空白與全形差異）。班次依 intake 的語意處理，下班時間早於上班時間表示跨夜。工人說不知道時值設為 `unknown`，題目仍算已回答，不再追問。

### 選題

對每個尚未回答、值為 `unknown` 的題目，把每個選項代進草稿，重跑規則引擎。若各選項的發現集合（規則、判定、變體）或估計金額（短少金額、可能再加的金額）不同，這題就是決定性的。排序依金額差，再依判定改變的規則數。最多回傳 3 題，附「答案不同會改變哪些規則、金額最多差多少」。不影響結果的題目列在 `not_asked`，介面顯示為「不影響結果、所以沒問的題目」。

關係、特別工時、責任制三題各自會讓規則只能回 `needs_human`，單獨代入會互相遮住。所以每題另在「其他未回答的三題都取使其適用的答案」的背景下再測一次。家庭看護或幫傭不列為問題：預設為否，在資料表標示為「假設」，工人的描述指向看護或幫傭時由助理用 `set_facts` 更正。

### 護欄

`guard.check(text, case, phase, extra)` 回傳問題清單。

- 數字：全形數字轉半形，去千分位與 `NT$`。日期（`10/07`、`2026-10-07`、`10月7日`）比對月日，時間比對 `HH:MM`，`第 24 條`、`第 1 項` 比對條項號，其餘比對數值。每個數字都要出現在案件資料、工人本人說過的話、最近一次報告，或本回合工具產生的文字（題目資料、法條原文、調解草稿、轉介資料）裡。助理自己先前的回覆不算依據。
- 免檢的只有行首的清單編號 1 至 5（`1.`、`1)`、`1、`、`(1)`）。
- 報告出來之前，禁用判斷合法性的用語，清單在 `LEGALITY_TERMS`：違法、違反、不合法、違規、犯法、少給、積欠、欠你、欠薪、應補、可以請求、可請求。報告階段允許。
- 不通過時，把問題送回模型請它重寫一次（此時禁止呼叫工具）。重寫仍不通過就改用固定文字：`gather` 與 `confirm` 用 i18n 的固定句，`clarify` 用排名第一的題目與選項，`report` 用 `report.summary` 加免責聲明。每次護欄事件都寫入軌跡。模型呼叫失敗或步驟數達上限也走固定文字。

### 軌跡

`TurnResult.trace` 記錄每個工具呼叫（工具、參數摘要、成功或失敗、被拒絕的值與原因）、護欄事件（被攔下的原文、問題、重寫結果或改用固定文字）、模型錯誤與步驟上限。軌跡不含照片內容與金鑰。介面在「代理人做了什麼」顯示。

### 已知限制

- 護欄只讀阿拉伯數字與繁體中文用語。非中文的回覆只做數字檢查，不做用語檢查。中文數字（「三十分鐘」）不檢查。
- 數字檢查比對的是「有沒有出現過」，不比對單位或上下文。小的數字（1、2）容易剛好出現在別處。
- 護欄管不到「已記下」這類動作宣稱。模型仍可能說記下了某件事而沒有存。資料表會顯示實際存了什麼，確認前由工人核對。
- 模型可能把工人的話對應到錯的選項。`set_facts` 的說明逐項寫出選項原文，結果回傳 `stored_as` 讓模型核對，資料表同時顯示來源與原話，但最後仍由工人確認。
- 選題是一次改一題的敏感度分析，加上上述的乾淨背景。不窮舉所有答案組合，所以「不影響結果」是針對這兩種背景的結論。
- `Case` 由客戶端持有並送回，沒有簽章。伺服器只驗證格式，不能防止使用者竄改自己的案件，確認閘門防的是模型，不是使用者。
- 照片內容是轉錄資料，其中的文字可能含對模型下指令的句子。工具閘門與護欄不依賴模型守規矩，但 `unreadable` 文字仍會進入模型的上下文。
- 聊天文字與照片會傳給 Google Gemini。`labor_radar/rules/sources.md` 已記錄 Gemini API 資料使用的說明，同一段說明適用於對話文字：免費層的內容可能被用來改進產品。使用者在介面上會看到這個提醒。伺服器不保存對話、照片與金鑰。
- 非中文介面的字串是機器翻譯草稿，介面會顯示草稿提示。
