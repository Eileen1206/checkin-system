# 政旭汽車材料行 打卡系統

員工出勤管理、送貨路線規劃、薪資計算的整合系統，透過 LINE Bot 操作，部署於 Railway。

---

## 功能一覽

### 員工打卡
- LINE Bot GPS 驗證打卡（上班 / 午休 / 下班）
- RFID 刷卡機打卡（店內實體刷卡）
- 管理員可補打卡

### 送貨管理
- 規劃每日送貨路線（自動最短路徑排序）
- 急單優先排序
- 透過 LINE LIFF GPS 驗證送達位置，完成即標記
- Leaflet 地圖顯示今日送貨狀況

### 請假管理
- 管理員拖拉月曆新增請假
- 員工透過 LINE Bot 申請請假，管理員審核
- 請假日自動跳過打卡提醒

### 出勤提醒
- Railway Cron 每 5 分鐘執行，上下班前 5 分鐘提醒
- 已打卡 / 請假 / 非工作日 → 不提醒
- 下班超過 30 分鐘未打卡 → 通知管理員

### 薪資計算
- 月薪制 / 時薪制
- 遲到扣薪（超過 10 分鐘扣 0.5 小時）
- 保養費、勞健保扣除
- 匯出 Excel

### 其他
- 客戶資料管理（地址自動 geocode）
- 管理員後台（Django Admin）
- 員工密碼修改

---

## 技術架構

| 項目 | 技術 |
|---|---|
| 後端框架 | Django 4.x |
| 資料庫 | PostgreSQL（Railway 提供） |
| 部署平台 | Railway |
| LINE 整合 | LINE Messaging API、LINE LIFF |
| 地圖 | Leaflet.js、OpenRouteService API |
| 前端樣式 | Tailwind CSS（CDN） |
| 靜態檔案 | WhiteNoise |
| RFID | 本機 `rfid_listener.py`（連線至 Railway） |

---

## 專案結構

```
checkin_system/
├── attendance/                  # 主要 app
│   ├── models.py                # 資料模型
│   ├── views.py                 # LINE Bot Webhook
│   ├── liff_views.py            # LIFF 送貨 GPS 驗證
│   ├── dashboard_views.py       # 後台所有 view
│   ├── dashboard_urls.py        # 後台路由
│   ├── urls.py                  # attendance 路由
│   ├── context_processors.py    # 權限注入
│   ├── admin.py                 # Django Admin 設定
│   ├── templatetags/
│   │   └── attendance_extras.py # 自訂 template filter
│   ├── utils/
│   │   ├── punch.py             # 打卡邏輯
│   │   └── routing.py          # 路線規劃
│   ├── management/commands/
│   │   ├── remind_attendance.py # 打卡提醒（Railway Cron）
│   │   ├── setup_richmenu.py    # 建立 LINE Rich Menu
│   │   ├── bind_richmenu.py     # 綁定 Rich Menu 給員工
│   │   └── geocode_customers.py # 客戶地址轉座標
│   └── templates/attendance/    # 頁面模板
├── reports/                     # 出勤報表 app
├── checkin_system/
│   ├── settings.py              # 主設定
│   ├── settings_test.py         # 測試用設定
│   └── urls.py                  # 根路由
├── templates/                   # 全域模板（login、liff 等）
├── static/                      # 靜態資源
├── rfid_listener.py             # RFID 刷卡機監聽（跑在店內電腦）
├── Procfile                     # Railway 啟動指令
├── requirements.txt
├── .env.example                 # 環境變數範本
└── 部署說明.md                   # 詳細部署步驟
```

---

## 環境變數

複製 `.env.example` 為 `.env`，填入以下內容：

```env
SECRET_KEY=
DEBUG=False

# LINE Messaging API
LINE_CHANNEL_SECRET=
LINE_CHANNEL_ACCESS_TOKEN=
LINE_BOT_BASIC_ID=
MANAGER_LINE_USER_ID=

# LINE LIFF
LIFF_DELIVERY_ID=

# LINE Rich Menu
RICHMENU_DELIVERY=
RICHMENU_STAFF=

# 公司 GPS
OFFICE_LAT=
OFFICE_LNG=
OFFICE_RADIUS_METERS=100

# 路線規劃
ORS_API_KEY=

# RFID
RFID_API_KEY=
```

---

## 本機開發

```bash
# 安裝套件
pip install -r requirements.txt

# 建立資料庫
python manage.py migrate

# 建立管理員帳號
python manage.py createsuperuser

# 啟動伺服器
python manage.py runserver
```

LINE Bot Webhook 本機測試需搭配 ngrok：

```bash
ngrok http 8000
# 將產生的 https 網址填入 LINE Developers Console → Webhook URL
```

---

## Railway 部署

詳細步驟見 [部署說明.md](./部署說明.md)。

**Railway 服務架構：**

| 服務名稱 | 指令 | 說明 |
|---|---|---|
| `checkin-system` | `gunicorn checkin_system.wsgi` | 主 Web 服務 |
| `crontab提醒打卡` | `python manage.py migrate && python manage.py remind_attendance` | 打卡提醒，每 5 分鐘執行 |

---

## RFID 刷卡機

店內電腦需持續執行：

```bash
python rfid_listener.py
```

程式會監聽 USB RFID 讀卡機，刷卡後自動送出打卡請求至 Railway。
