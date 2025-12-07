# 物流路線系統 API

## 環境變數設定

需要在 Render 設定以下環境變數：

1. `SPREADSHEET_ID`: Google Sheets 的 ID
2. `GOOGLE_CREDENTIALS`: Service Account 的 JSON 內容

## API 端點

- `POST /api/step1` - 清除桌面
- `POST /api/step2` - 日期篩選
- `POST /api/step3` - 路線比對
- `POST /api/step4` - 創建工作表
- `POST /api/execute_all` - 執行所有步驟
- `GET /api/status` - 查看狀態
- `GET /health` - 健康檢查

## 部署

推送到 GitHub 後，Render 會自動部署。
