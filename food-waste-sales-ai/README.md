# ♻️ 廚餘機業務 AI

一個網頁聊天室，用 DeepSeek AI 幫你賣廚餘機。

| 分頁 | 給誰用 | 功能 |
| --- | --- | --- |
| **客戶諮詢** | 網站訪客 | 回答規格、價格、保固、安裝等問題；問清楚家庭人數、空間、預算後推薦機型；客戶有興趣時邀請留下姓名電話，自動存進客戶名單 |
| **業務助理** | 內部業務 | 寫 LINE、FB 文案，準備電話開場白，處理「太貴了」「怕吵」等客戶異議 |
| **客戶名單** | 內部業務 | 查看 AI 收集到的潛在客戶資料 |

AI 只依照 `knowledge/products.md` 的內容回答，資料沒寫到的不會自己編。

## 第一步：填入你的產品資料

打開 [`knowledge/products.md`](knowledge/products.md)，把範例機型、價格、保固、優惠、常見問題改成你們真實的資料。**裡面目前都是範例數字。**

## 第二步：架設到 Mac Mini 並放上網路

照 [`deploy/MAC-MINI.md`](deploy/MAC-MINI.md) 的步驟做。這份說明也可以直接交給 OpenClaw 執行。

在自己電腦快速試用：

```bash
cd food-waste-sales-ai
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # 打開 .env，填入 DeepSeek 金鑰和業務密碼
.venv/bin/uvicorn app.main:app --reload
```

打開瀏覽器到 http://localhost:8000 。

## 設定（寫在 `.env` 檔）

| 設定 | 說明 |
| --- | --- |
| `DEEPSEEK_API_KEY` | 必填，DeepSeek 金鑰 |
| `ADMIN_TOKEN` | 業務密碼。設定後，「業務助理」和「客戶名單」要輸入密碼才能使用。沒設定時任何人都能看到客戶名單，只適合在自己電腦測試 |
| `DEEPSEEK_MODEL` | 使用的模型，預設 `deepseek-chat` |

`.env` 不會上傳到 GitHub，金鑰只存在你自己的電腦上。

## 客戶資料存在哪裡？

存在 `data/leads.csv`，可以直接用 Excel 開啟。這個資料夾已設定為不上傳到 GitHub（`.gitignore`），以保護客戶個資。

## 檔案說明

```
food-waste-sales-ai/
├── knowledge/products.md   ← 產品資料（最常需要修改的檔案）
├── app/prompts.py          ← AI 的角色設定與規則（想調整語氣、流程改這裡）
├── app/main.py             ← 後端伺服器
├── static/index.html       ← 聊天網頁
├── deploy/MAC-MINI.md      ← Mac Mini 架設說明
├── .env.example            ← 設定範本（複製成 .env 填金鑰）
└── data/leads.csv          ← 客戶名單（自動產生）
```

## 之後可以加的功能

- 串接 LINE 官方帳號或 Telegram，直接在通訊軟體回覆客戶
- 有新客戶留資料時，自動通知業務（LINE Notify、Email）
- 把客戶名單改存到 Google Sheets 或 CRM
