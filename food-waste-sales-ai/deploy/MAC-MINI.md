# 在 Mac Mini 上架設廚餘機業務 AI

這份說明寫給人看，也可以直接交給 OpenClaw 照著做。
（例如在 Telegram 跟 OpenClaw 說：「請依照 https://github.com/stark640423/flowring/blob/claude/github-project-addition-3580pt/food-waste-sales-ai/deploy/MAC-MINI.md 的步驟架設」）

架構：

```
客戶手機/電腦 ──> 你的網域（Cloudflare） ──> Cloudflare Tunnel ──> Mac Mini 上的 App ──> DeepSeek
                                                                  │
                                              知識庫從 GitHub 同步（git pull）
```

- 不需要租雲端伺服器，也不用在路由器開 port。
- DeepSeek 金鑰只存在 Mac Mini 的 `.env` 檔，不會上傳到 GitHub。

## 1. 下載程式

```bash
cd ~
git clone -b claude/github-project-addition-3580pt https://github.com/stark640423/flowring.git
cd ~/flowring/food-waste-sales-ai
```

## 2. 安裝

需要 Python 3.10 以上（`python3 --version` 檢查；太舊的話 `brew install python`）。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 3. 設定金鑰與密碼

```bash
cp .env.example .env
open -e .env        # 用文字編輯器打開，填入 DEEPSEEK_API_KEY 和 ADMIN_TOKEN 後存檔
```

## 4. 先在本機試試看

```bash
.venv/bin/uvicorn app.main:app --port 8000
```

瀏覽器打開 http://localhost:8000 ，問它幾個問題。確認沒問題後按 Ctrl+C 停止。

## 5. 開機自動啟動

```bash
sed "s/YOUR_USER/$(whoami)/g" deploy/com.flowring.sales-ai.plist > ~/Library/LaunchAgents/com.flowring.sales-ai.plist
launchctl load ~/Library/LaunchAgents/com.flowring.sales-ai.plist
```

紀錄檔在 `/tmp/flowring-sales-ai.log`。要停止：`launchctl unload ~/Library/LaunchAgents/com.flowring.sales-ai.plist`

## 6. 放上網路（Cloudflare Tunnel）

```bash
brew install cloudflared
```

**快速試用（不需要帳號，網址每次重開會變）：**

```bash
cloudflared tunnel --url http://localhost:8000
```

畫面會出現一個 `https://xxxx.trycloudflare.com` 網址，用手機打開就能測試。

**正式使用（用你自己的網域）：** 網域需要先加到 Cloudflare（免費方案即可），然後：

```bash
cloudflared tunnel login                                  # 瀏覽器會跳出來，選你的網域
cloudflared tunnel create flowring-sales-ai
cloudflared tunnel route dns flowring-sales-ai chat.你的網域.com
cloudflared tunnel --url http://localhost:8000 run flowring-sales-ai
```

要讓 tunnel 也開機自動啟動，請依照 Cloudflare 官方說明設定成系統服務：
https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/

## 7. 更新知識庫

知識庫是 `knowledge/products.md`。在 GitHub 上修改後，在 Mac Mini 執行：

```bash
cd ~/flowring && git pull
```

就會生效，不用重開 App（每次對話都會重新讀取知識庫）。

也可以讓 OpenClaw 定時執行 `git pull`，這樣只要在 GitHub 改檔案就會自動更新。

## 8. 客戶名單

存在 Mac Mini 的 `~/flowring/food-waste-sales-ai/data/leads.csv`，可以用 Excel 開，也可以在網頁的「客戶名單」分頁輸入業務密碼查看。
