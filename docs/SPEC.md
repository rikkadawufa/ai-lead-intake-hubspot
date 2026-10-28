# 實作規格：AI Lead Intake → HubSpot（n8n + OpenAI）

> 方向與理由見 `../00-作品集方向_繁中.md`；本檔是**可直接執行的規格**。遇到本檔沒寫到、或跟實際 API 不符的地方：在 `docs/VERIFICATION.md` 的「待決定」記下來，選最小的改動保住原意，不擴大範圍。
> 程式碼、註解、README、commit message 用英文；`docs/VERIFICATION.md` 用繁體中文。

---

## 0. 前置條件（使用者完成，見 `../01-帳號準備步驟_繁中.md`）
- `demo/.env` 已填：`HUBSPOT_TOKEN`、`GMAIL_ADDRESS`、`GMAIL_APP_PASSWORD`、`SLACK_WEBHOOK_URL`、`OPENAI_API_KEY`、`REPLY_REDIRECT_TO`。
- Docker Desktop 執行中；n8n 在 http://localhost:5679 。
- 缺值時只做離線部分（mock 模式、`--dry-run`、單元測試），並在 VERIFICATION.md 記下哪些沒測。

## 1. 硬性規則
1. **不安裝套件**：Python 只用標準函式庫（`urllib`、`json`、`unittest`、`smtplib`、`email`、`argparse`、`hashlib`）；Node 只用內建模組（`node:test`、`node:assert`、`node:fs`、`node:path`、`node:crypto`）。Node v24。
2. **機密**：不印、不寫進 log，也不寫進任何會進 git 的檔案。錯誤訊息裡的 token、key、密碼一律換成 `***`。
3. **不建付費資源**：HubSpot 維持 Free，不啟用試用；Slack 維持 Free。
4. **不 git push**、不建 GitHub repo，這兩件事由使用者決定。本機 commit 可以。
5. **OpenAI 用量**：開發一律 `LLM_MODE=mock`。真的呼叫只在 §9.3 E2E，整批最多跑 2 輪。
6. **HubSpot 寫入範圍**：只碰 email 結尾是 `@example.com`／`@example.org` 的聯絡人（fixtures 全部用這兩個保留網域）。reset 腳本也只刪這些。
7. **寄信**：`DEMO_MODE=1` 時，核准的回覆一律寄到 `REPLY_REDIRECT_TO`，主旨前面加 `[demo → <原收件人>]`，絕不寄到 fixture 的地址。

## 2. 檔案結構（最終）
```
demo/
  README.md  docker-compose.yml  .env.example  .gitignore
  scripts/
    envfile.py            # 讀 .env（照搬 UAT）
    hubspot_client.py     # HubSpot REST 共用：get by email、search by phone、create、patch、note、limits
    hubspot_setup.py      # 檢查 custom property 上限 → 建 property group 與 6 個 property（已存在就略過）
    n8n_setup.py          # 匯入 credentials 與 workflows，並 publish（照搬 UAT 做法）
    demo_common.py        # fixtures、路徑、safe_id 共用
    send_fixtures.py      # 依 fixtures 送表單 POST 或寄 SMTP 測試信；--only T1,T2；--dry-run
    reset_demo.py         # 刪除 @example.com/.org 聯絡人；--dry-run 預設開
    collect_results.py    # 讀 output/run-log/ + HubSpot → output/results.md（數字由程式算）
  tests_scripts/          # 上面腳本的 unittest（mock HTTP）
  n8n-src/
    code/*.js  code/*.test.js   # Code node 的純函式邏輯
    build-workflows.mjs         # 產生 workflows/*.json
  prompts/  qualify.md  draft-reply.md
  sample-data/
    fixtures.json         # T1–T8 的 8 筆虛構詢問與預期結果
    mock-llm/             # mock 模式的 LLM 回應（以 fixture id 命名）
  workflows/              # 產生物（commit）
  output/                 # 執行產物（gitignore）：run-log/*.json、review/*.html、results.md
  docs/  SPEC.md  SETUP.md  OPERATIONS.md  VERIFICATION.md  screenshots/
```

## 3. 共同資料格式
### 3.1 Inquiry（兩個來源都先轉成這個）
```json
{
  "source": "form | email",
  "source_id": "form:<submission_id> | email:<Message-ID>",
  "received_at": "ISO-8601",
  "name": "string|null", "email": "lowercased|null", "phone": "E.164|null",
  "company": "string|null", "subject": "string|null", "message": "string",
  "demo_flags": {"break_llm": false}
}
```
- `normalize-form.js`：讀 webhook body（`name`、`email`、`phone`、`company`、`message`、`submission_id`；缺 `submission_id` 時用 `sha256(email|message)` 前 16 碼）。
- `normalize-email.js`：寄件人優先取 `Reply-To`，沒有才取 `From`（很多網站表單通知信就是這樣寄的）；`message` 去掉引用回覆與簽名檔分隔線 `-- ` 之後的內容；`phone` 用正則從內文抓。
- `normalize-phone.js`：轉 E.164，預設國碼 `+1`；無法判斷就回 `null`，不猜。
- `validate-inquiry.js`：`email` 和 `phone` 都是 null → `{valid:false, reason:"no_contact"}`；`message` 去空白後少於 5 字 → `empty_message`；email 格式錯 → `bad_email`；`DEMO_MODE=1` 時 email 不是 `@example.com`／`@example.org` → `non_demo_address`（email 來源直接略過，不告警；例如 Google 的歡迎信）。
- 實作上 normalize／validate 合併在 `n8n-src/code/normalize.js`；其他 code node 邏輯在 `contact.js`、`llm.js`、`review.js`、`records.js`。
- `demo_flags.break_llm` 只有 `DEMO_MODE=1` 且表單帶 `x-demo-break-llm: 1` header 才會是 true。

### 3.2 HubSpot contact 自訂欄位（group：`lead_intake`）
| name | type | 說明 |
|---|---|---|
| `lead_tier` | enumeration | `qualified`／`needs_review`／`not_qualified` |
| `inquiry_topic` | enumeration | `regular_cleaning`／`deep_cleaning`／`quote_request`／`complaint`／`job_application`／`sales_pitch`／`other` |
| `inquiry_urgency` | enumeration | `high`／`normal`／`low` |
| `inquiry_source` | enumeration | `form`／`email`（記最近一次的來源） |
| `ai_reason` | textarea | AI 的分級理由，必須含一段原文引用 |
| `inquiry_source_ids` | textarea | 處理過的 `source_id`，用 `;` 分隔（冪等用） |

一個 contact 用 6 個 custom property。（2026-10-28 實測：免費帳號 `overallLimit` 10、全部物件共用，目前用了 0；服務金鑰只勾 4 個 contacts scope，就能建立 note 並關聯到 contact，associationTypeId 202。）`hubspot_setup.py` 先呼叫 `GET /crm/v3/limits/custom-properties`，剩餘額度不夠就停下並回報。

## 4. 去重與冪等（核心邏輯，`n8n-src/code/match-contact.js` 加 HTTP 節點）
1. 有 email → `GET /crm/v3/objects/contacts/{email}?idProperty=email&properties=...`（直接讀取、即時一致；不要用 search，search 有索引延遲）。
2. 沒找到且有 phone → `POST /crm/v3/objects/contacts/search`，`phone EQ <E.164>`。search 有延遲，所以 `send_fixtures.py` 每筆之間至少間隔 `--gap 8` 秒。
3. 找到 → 檢查 `inquiry_source_ids` 有沒有這筆 `source_id`。有 → **整筆停止**，run log 記 `duplicate_submission`，不分級、不寫 note、不產草稿。
4. 沒找到 → create。若 create 回 409（email 已存在，可能是同時送進來的）→ 從錯誤訊息取出既有 id，改走更新流程。
5. 更新時：tier 規則用 `merge-tier.js`，已是 `qualified` 的不降級；其他欄位以最新一筆為準；`inquiry_source_ids` 只附加不覆蓋。
6. 每筆詢問都建一則 note，關聯到 contact：`[<source>] <received_at>\n<subject>\n\n<message>\n\nAI: <tier> / <topic> / <urgency> — <reason>`。

## 5. LLM 規格
### 5.1 `prompts/qualify.md`（Chat Completions、`response_format: json_schema`、strict）
輸出：`{tier, topic, urgency, reason, quote}`，enum 同 §3.2；`quote` 必須是 `message` 裡逐字出現的片段（≤ 120 字元）。
分級規則寫在 prompt 裡，業主改這段就能調整規則：
- qualified：提到辦公室或商用空間的清潔需求，且至少有一項：坪數／地點／頻率／時間。
- needs_review：可能是需求，但資訊不足或意圖不明。
- not_qualified：求職、推銷、垃圾信，或住家清潔（服務範圍外）。
- urgency high：一週內要、提到「急」或有明確近日期。

`validate-qualification.js`：檢查 enum 和 `quote` 是否真的出現在原文（比對前先 normalize 空白）。不合格就當成 LLM 失敗。

### 5.2 `prompts/draft-reply.md`
只給 `qualified`／`needs_review` 用。輸出 `{subject, body}`：英文、120 字以內、署名 `Tidewell Commercial Cleaning team`；needs_review 的回覆要問 1–2 個缺的資訊。不能承諾價格或日期。

### 5.3 失敗處理
HTTP 節點重試 2 次（間隔 2 秒）。還是失敗、或 validate 不過 → `tier=needs_review`、`topic=other`、`urgency=normal`、`reason="AI classification failed: <error class>; needs a human look."`，照樣寫進 HubSpot（名單不能消失），而且**不產生草稿**，發 Slack 告警。
`demo_flags.break_llm=true` 時，請求的 model 改成 `does-not-exist-model`，讓 OpenAI 回傳真的 404 錯誤。

### 5.4 mock 模式
`LLM_MODE=mock`：不打 OpenAI，讀 `/files/sample-data/mock-llm/<fixture_id>.<qualify|draft>.json`。fixture_id 用 `source_id` 對照 `fixtures.json`；對不到就當成 LLM 失敗。`break_llm` 在 mock 模式也走失敗路徑。

## 6. 人工核准（改寫自 UAT 的 `build-review-page.js`／`parse-decisions.js`）
- `build-review-page.js`：一封詢問一頁。上半部唯讀：原文、來源、AI 分級和理由、HubSpot contact 連結。下半部可編輯：`subject`、`body`（textarea）、Approve／Reject 單選（預設都不選）、reviewer 名字（必填）。`<form method="POST" action="{{$execution.resumeUrl}}">`，所有內容都要 escape。頁面存到 `output/review/<source_id 安全化>.html`，也作為 Wait 前的 Slack 訊息連結。
- Slack 通知（`format-review-alert.js`）：`New lead: <name> (<tier>, <urgency>) — review the draft reply: <link>`。
- Wait 節點：On webhook call、POST，timeout 72 小時；逾時就寫 note「review timed out, nothing sent」。
- `parse-decisions.js`：reviewer 空白或沒選 → 回錯誤頁，流程繼續等（同一個 resumeUrl 可以重送）。Approve → SMTP 寄出**編輯後**的 subject/body（遵守 §1.7）→ note `Reply drafted by AI, edited: <yes|no>, approved by <reviewer> on <date>, sent to <address>`。Reject → note `Reply drafted by AI, rejected by <reviewer> on <date>, nothing sent`。
- 回應頁：`Review received: approved and sent`／`Review received: rejected, nothing sent`。

## 7. n8n 環境
- 映像固定 `2.44.7`（`.env` 的 `N8N_VERSION`），port 5679。
- Credentials（`n8n_setup.py` 匯入，固定 id）：`hubspotTokenCred01`（httpHeaderAuth，`Authorization: Bearer <service key>`；免費帳號不能建 legacy private app，改用 Service key，不支援 HubSpot webhook，本 demo 也不需要）、`openAiCred01`（httpHeaderAuth）、`gmailImapCred01`（imap，`imap.gmail.com:993` SSL）、`gmailSmtpCred01`（smtp，`smtp.gmail.com:465` SSL）。
- Code node 讀檔路徑只用 `/files/...`。

## 8. Workflows（由 `n8n-src/build-workflows.mjs` 產生）
### WF1 `wfFormIntake`
Webhook `POST /webhook/inquiry` → normalize-form → validate。不合格 → 回 `400 {error, reason}` → Slack 告警 → 寫 run log `rejected_input`。合格 → 回 `202 {accepted:true, source_id}` → Execute Workflow `wfProcessInquiry`（不等它完成）。
### WF2 `wfEmailIntake`
Email Trigger (IMAP)，INBOX，只看未讀，處理後標記已讀 → normalize-email → validate → 不合格就發 Slack 告警並寫 run log；合格就 Execute Workflow `wfProcessInquiry`。
寄件人是 `GMAIL_ADDRESS` 本身、且沒有 Reply-To 的信直接略過，避免把自己寄出的回覆當成詢問。
### WF3 `wfProcessInquiry`（核心）
match contact（§4）→ 重複就停 → qualify（§5）→ create／update contact＋note → `lead_tier != not_qualified` 且 LLM 沒失敗 → draft reply → 產生審核頁＋Slack → Wait → parse decisions → 寄信或不寄＋note → 寫 run log。
每一步的結果都累積到 `runLog` 物件，最後寫成 `output/run-log/<timestamp>-<source_id 安全化>.json`：`{source_id, fixture_id, contact_id, action: created|updated|duplicate_submission, tier, llm_ok, draft: none|pending|approved|rejected|timeout, sent_to, alerts[]}`。
### WF5 `wfReviewPage`
`GET /webhook/review?id=<safe id>` → 讀 `output/review/<id>.html` 回傳；已審過的頁面上方加提示。Slack 的審核連結指到這裡。

### WF4 `wfErrorHandler`
Error Trigger → `format-alert.js`（照搬 UAT）→ Slack。WF1–WF3 的 settings 都把 errorWorkflow 指到 WF4。

## 9. 驗證（結果寫進 `docs/VERIFICATION.md`）
### 9.1 離線
- O1 `node --test "n8n-src/code/*.test.js" "n8n-src/*.test.mjs"` 全部通過（Windows 上要用 glob，直接給資料夾會失敗）
- O2 `python -m unittest discover tests_scripts` 全部通過
- O3 `node n8n-src/build-workflows.mjs` 產生 5 個 JSON，且再跑一次內容完全相同
- O4 `send_fixtures.py --dry-run` 印出 8 筆要送的請求或信件，不含機密

### 9.2 n8n 本機（mock LLM，真的 HubSpot／Gmail／Slack）
- N1 `hubspot_setup.py` 建好 6 個 property，再跑一次全部略過
- N2 `reset_demo.py --apply` 後，`@example.*` 聯絡人數為 0
- N3 T1–T8 依 `fixtures.json` 跑一輪 → `collect_results.py` 產生 8/8 PASS
- N4 整批再送一次（同樣的 source_id）→ 聯絡人數和草稿數都不變，8 筆都記成 `duplicate_submission`；T6 仍是 400

### 9.3 E2E（真的 OpenAI）
- E1 reset 後 `LLM_MODE=live` 跑 T1–T8 → 8/8 PASS（分級有出入的話照實記錄，不改 fixture 預期去湊）
- E2 在 HubSpot UI 截圖確認：聯絡人列表（tier 欄）、T2 那位聯絡人底下的 2 則 note、T8 的審核人 note
- E3 Gmail 寄件匣只有核准過的那一封，主旨帶 `[demo → ...]`

### 9.4 T1–T8 預期（`fixtures.json` 的 `expect` 欄位，`collect_results.py` 據此判斷 PASS）
| # | 送法 | 預期 |
|---|---|---|
| T1 | form：辦公室每週清潔、有坪數和地點 | created、qualified、draft pending |
| T2 | email：T1 同一人（Reply-To 同 email），追問時間 | updated（同一個 contact_id）、note 數 2 |
| T3 | form：T1 原封不動重送（同 submission_id） | duplicate_submission，沒有新草稿 |
| T4 | email：推銷 SEO 服務 | created、not_qualified、draft none |
| T5 | form：「想問一下價錢」沒有細節 | created、needs_review、draft pending |
| T6 | form：沒有 email 也沒有電話 | HTTP 400、Slack 告警、HubSpot 沒有新聯絡人 |
| T7 | form＋`x-demo-break-llm: 1` | created、needs_review、llm_ok=false、Slack 告警、draft none |
| T8 | 先送一份沒填審核人的表單；T1 草稿改稿後 approve；T2 草稿直接 approve；T5 草稿 reject | 不完整的審核回 422、什麼都沒寄；T1 approved、edited=yes、寄出內容是改過的版本；T2 approved、edited=no；T5 rejected、沒有寄信；demo 收件匣剛好 2 封 |

## 10. 文件
- `README.md`（英文，對外）：做什麼、架構圖（文字版即可）、跑法、測過什麼、**沒做什麼**、明講是 self-initiated portfolio project，資料都是虛構的。
- `docs/SETUP.md`（英文）：從零安裝到跑完 T1–T8。
- `docs/OPERATIONS.md`（英文，交接用）：怎麼改分級規則（`prompts/qualify.md`）、加 enum 值、加一個新來源（只要轉成 §3.1 格式再呼叫 WF3）、看 run log、處理 Slack 告警、輪替 token。
- `docs/VERIFICATION.md`（繁中）：每個檢查 ID 標 PASS／FAIL／NOT RUN 並寫原因。
