# 物流路線系統 API

⚠️ **安全提醒**：此專案不包含任何敏感資訊，所有憑證都需在 Render 平台設定。

## 🚀 部署步驟

### 前置準備

1. **Google Cloud 設定**
   - 建立 Google Cloud 專案
   - 啟用 Google Sheets API
   - 啟用 Google Maps Distance Matrix API
   - 建立服務帳號並下載 JSON 金鑰
   - 將服務帳號加入 Google Sheets 共用

2. **取得必要資訊**
   - Google Sheets ID（從試算表網址取得）
   - Google 服務帳號 JSON（完整內容）
   - Google Maps API Key

### Render 部署

1. Fork 此 repository
2. 登入 [Render](https://render.com/)
3. 建立新的 Web Service
4. 連接您的 GitHub repository
5. 設定環境變數（見下方）
6. 部署

### 環境變數設定

在 Render 的 Environment Variables 頁面新增：

