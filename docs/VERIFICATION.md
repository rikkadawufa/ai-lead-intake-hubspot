# 驗證紀錄

環境：本機 Docker n8n 2.44.7（port 5679）、HubSpot Free（服務金鑰）、demo Gmail（IMAP／SMTP + App password）、Slack `#lead-alerts`。執行日期 2026-10-29（台灣時間）。

## 9.1 離線
| ID | 指令 | 結果 |
|---|---|---|
| O1 | `node --test "n8n-src/code/*.test.js" "n8n-src/*.test.mjs"` | **PASS**：41 個測試全過（normalize、contact、llm、review/records、fixtures、workflow builder） |
| O2 | `python -m unittest discover tests_scripts` | **PASS**：16 個測試全過（HubSpot client 重試與遮罩、property setup、結果判斷、reset 只刪 demo 網域） |
| O3 | `node n8n-src/build-workflows.mjs` 跑兩次後 `diff` | **PASS**：產生 5 個 workflow，兩次輸出完全相同 |
| O4 | `python scripts/send_fixtures.py --dry-run` | **PASS**：列出 T1–T7 要送的請求與信件，沒有任何機密 |

## 9.2 n8n 本機（mock LLM，真的 HubSpot／Gmail／Slack）
| ID | 結果 |
|---|---|
| N1 | **PASS**：`hubspot_setup.py` 建了 6 個 property；再跑一次 6 個全部略過。免費帳號上限 10、目前用掉 6 |
| N2 | **PASS**：`reset_demo.py --apply` 刪掉 1 個冒煙測試聯絡人和 2 則 note，再列一次是 0 |
| N3 | **PASS 8/8**：`send_fixtures.py` → `send_fixtures.py --review` → `collect_results.py`，HubSpot 有 4 個 demo 聯絡人（Dana、Jordan、Sam、Riley） |
| N4 | **PASS 8/8**：`send_fixtures.py --replay` 把同一批重送（含兩封同 Message-ID 的信）→ 6 筆全是 `duplicate_submission`、沒有新草稿，T6 仍是 400，聯絡人仍是 4 個 |

N3 細節（`output/results.md`）：
- T1 created／qualified，審核頁改稿後 approve，寄出的是改過的版本
- T2 email 追問 → 同一個 contact_id，聯絡人底下 2 則詢問 note；AI 對 T2 本身判 needs_review，但 tier 不降級，最後仍是 qualified；草稿未改直接 approve
- T3 同一份表單重送 → duplicate_submission，T1 只有 1 份草稿
- T4 推銷信 → not_qualified，沒有草稿
- T5 只問價錢 → needs_review，草稿被 reject，沒有寄信
- T6 沒有 email 也沒有電話 → HTTP 400、Slack 告警、HubSpot 沒有寫入
- T7 故意送錯模型名（mock 模式模擬 404）→ needs_review、llm_ok=false、沒有草稿、Slack 告警
- T8 第一次送出沒填審核人的表單 → 422「Your name is required」，什麼都沒寄；之後 T1 改稿 approve、T2 approve、T5 reject；demo 收件匣剛好 2 封回覆，主旨帶 `[demo → dana.whitfield@example.com]`

## 9.3 E2E（真的 OpenAI）
| ID | 結果 |
|---|---|
| E1 | **PASS 8/8（live 第 2 輪，2026-10-29 00:35）**。過程照實記錄如下：<br>・嘗試 0：OpenAI 登錯帳號，每次呼叫都 429 `credit_balance_exhausted`，沒有 AI 結果，不算一輪<br>・第 1 輪（新 key）：**4/8**。T1 被判 needs_review、沒有草稿，連帶 T2 最終 tier、T3、T8 失敗<br>・修正後第 2 輪：**8/8** |
| E2 | **NOT RUN**：HubSpot UI 截圖還沒拍（留給錄影／作品集那一步） |
| E3 | **PASS**：第 2 輪 demo 收件匣剛好 2 封核准過的回覆（T1 改稿版、T2 原稿），主旨帶 `[demo → dana.whitfield@example.com]`；T5 被 reject，沒有寄出 |

### live 第 1 輪失敗原因與修正
- AI 對 T1 的判斷其實正確（qualified／regular_cleaning／normal），引用句也是逐字出自原文，但長度約 165 字元。prompt 要求 120 字以內，模型沒有遵守；我的檢查規定上限 160，而且把「太長」也記成 `quote_not_in_message`，錯誤名稱會誤導。
- 修正（`n8n-src/code/llm.js`）：「必須逐字出現在原文」照舊嚴格檢查；逐字但太長的引用句在字詞邊界截短成 160 字以內、結尾加「…」後接受。新增 2 個單元測試，其中一個直接用第 1 輪 T1 的真實回應。
- 第 1 輪的 log 和結果存在 `output/archive-live-round-1/`，第 2 輪在 `output/archive-live-round-2/`。

### 檢查程式本身的 bug（live 第 1 輪時發現）
- `collect_results.py` 用 IMAP `SINCE` 找回覆信，但 `SINCE` 只能精確到「日」，結果把同一天稍早 mock 那輪的信也算進來。第 1 輪曾因此出現假的「改過的 T1 內容 = 1」。已改成用 Date header 只算這一輪開始之後的信，第 1 輪重算後是正確的 0。

### 真實 AI 的分級（第 2 輪）
| # | AI 判斷 | 跟預期 |
|---|---|---|
| T1 | qualified／regular_cleaning／normal | 一致 |
| T2 | needs_review（「只要求約時間、沒有清潔細節」）；聯絡人 tier 依不降級規則維持 qualified | 一致（最終 tier） |
| T4 | not_qualified／sales_pitch／low | 一致 |
| T5 | needs_review／quote_request／normal | 一致 |
| T7 | OpenAI 真的回 404 → needs_review、告警 | 一致 |

### live 跑出來、要誠實註明的限制
- **草稿看不到同一位聯絡人之前的詢問**：T2 的 AI 草稿問 Dana「辦公室多大、多久清一次」，但她在 T1 表單已經寫了。這次照 fixture 設定「未改稿直接 approve」寄出；實務上審核人應該會改掉。可以改進：產生草稿時附上該聯絡人之前的詢問 note。
- **`ai_reason` 以最新一封為準，tier 卻會保留較高的分級**：Dana 的 tier 是 qualified，但 `ai_reason` 寫的是 T2 那封的「沒有清潔細節」。各別欄位照設計運作，但放在一起看會有點矛盾。
- 跑完已切回 `LLM_MODE=mock`。HubSpot 裡留著第 2 輪的 4 個 demo 聯絡人（可以拿來截圖）。

E1「嘗試 0」順帶確認了真的 API 錯誤時的行為（log 存在 `output/archive-live-attempt-1/`）：
- T1、T2、T4、T5：OpenAI 回真的 429（n8n 重試 3 次後放棄）→ 全部照樣寫進 HubSpot、tier=needs_review、llm_ok=false、沒有草稿、各發 1 則 Slack 告警。名單一筆都沒有遺失。
- T7：送不存在的模型名 → OpenAI 回真的 **404**（模型檢查比額度檢查先發生）→ 同樣轉 needs_review 並告警。所以 T7 已經用真的 API 錯誤驗證過，不只是 mock。
- T3 仍判成重複送出，T6 仍是 400：這兩條本來就不經過 AI。
- 跑完已切回 `LLM_MODE=mock`。HubSpot 裡留著這一輪的 4 個 demo 聯絡人，重跑前 reset 會清掉。

## 開發中遇到並修正的問題
1. **HubSpot 找不到聯絡人時回 404、body 是空的**：n8n HTTP 節點設成 JSON 會直接報錯（error workflow 有正確發出 Slack 告警）。查詢類節點改成 `responseFormat: autodetect`。
2. **IMAP trigger 會讀到 Google 寄給新帳號的歡迎信**：demo 模式下，寄件人不是 `@example.com`／`@example.org` 就直接略過，表單也會回 `non_demo_address`，確保真實地址不會寫進 HubSpot。這幾封歡迎信因此被標成已讀。
3. **fixture 網域**：T4 原本用 `seo-growth.example.org` 子網域，reset 會漏刪；單元測試抓到後改成 `@example.org`。
4. **結果檢查本身的 bug**：T8「寄出內容是改過的版本」第一次判 FAIL，原因是 T1、T2 兩封回覆主旨都有 Dana 的 email，檢查拿錯信。workflow 本身沒問題，改成「剛好一封信包含改過的那段文字」。

## 與 SPEC 不同的地方（已同步更新 SPEC）
- 多一個 workflow `wfReviewPage`（`GET /webhook/review?id=…`），讓 Slack 裡的審核連結能直接在瀏覽器打開；共 5 個 workflow。
- T8 多驗一件：T2 的草稿未改直接 approve（edited=no），所以 demo 收件匣預期 2 封回覆。
- Wait 節點的 resume URL 在 2.44.7 會帶 `?signature=`，審核頁直接用 `$execution.resumeUrl`，可以正常運作。
- n8n 的 webhook HTML 回應有 CSP sandbox，但包含 `allow-forms`，審核表單可以正常送出，不需要關掉 sandbox。

## 待決定
- 無。
