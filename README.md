# 物流路線系統 API

## 🚀 快速開始

### 環境變數設定

本專案需要以下環境變數，**請勿將敏感資訊提交到 GitHub**：

1. **SPREADSHEET_ID**: Google Sheets 的 ID
   - 從 URL 取得：`https://docs.google.com/spreadsheets/d/[這裡是ID]/edit`

2. **GOOGLE_CREDENTIALS**: Google Service Account 的 JSON 憑證
   - 格式：完整的 JSON 內容（需壓縮成單行）
   - 取得方式：從 Google Cloud Console 下載

### 本地開發設定

1. 複製環境變數範例檔案：
```bash
cp .env.example .env
