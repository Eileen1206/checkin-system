from pathlib import Path
import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    OFFICE_RADIUS_METERS=(int, 100),
)
environ.Env.read_env(BASE_DIR / '.env')

SECRET_KEY = env('SECRET_KEY', default='django-insecure-dev-key-change-in-production')
DEBUG = env('DEBUG')
# 開發時接受所有來源（含 ngrok），上線前改成實際網域
if DEBUG:
    ALLOWED_HOSTS = ['*']
    CSRF_TRUSTED_ORIGINS = [
        'http://localhost:8000',
        'http://127.0.0.1:8000',
        'https://*.ngrok-free.app',
        'https://*.ngrok-free.dev',
        'https://*.railway.app',
    ]
else:
    ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1'])
    CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])

    # ── Production HTTPS 安全設定 ──────────────────────────
    # Railway 在 load balancer 層做 SSL termination，需信任 X-Forwarded-Proto
    SECURE_PROXY_SSL_HEADER      = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT          = True          # HTTP → HTTPS 自動跳轉
    SESSION_COOKIE_SECURE        = True          # Session cookie 只走 HTTPS
    CSRF_COOKIE_SECURE           = True          # CSRF cookie 只走 HTTPS
    SECURE_HSTS_SECONDS          = 31536000      # 1 年，告知瀏覽器只信任 HTTPS
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD          = True
    SECURE_CONTENT_TYPE_NOSNIFF  = True          # 防止 MIME type sniffing
    X_FRAME_OPTIONS              = 'DENY'        # 禁止 iframe 嵌入（防點擊劫持）

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'attendance',
    'reports',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'checkin_system.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'attendance.context_processors.user_permissions',
            ],
        },
    },
]

WSGI_APPLICATION = 'checkin_system.wsgi.application'

import dj_database_url, os
_db = dj_database_url.config(conn_max_age=600)
if _db:
    DATABASES = {'default': _db}
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'zh-hant'
TIME_ZONE = 'Asia/Taipei'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Cache：使用資料庫，避免程式重啟後遺忘「已提醒」狀態
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
        'LOCATION': 'django_cache',
    }
}

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

# LINE Bot
LINE_CHANNEL_SECRET = env('LINE_CHANNEL_SECRET', default='')
LINE_CHANNEL_ACCESS_TOKEN = env('LINE_CHANNEL_ACCESS_TOKEN', default='')
LINE_BOT_BASIC_ID = env('LINE_BOT_BASIC_ID', default='')

# 公司 GPS 設定
OFFICE_LAT = env.float('OFFICE_LAT', default=0.0)
OFFICE_LNG = env.float('OFFICE_LNG', default=0.0)
OFFICE_RADIUS_METERS = env('OFFICE_RADIUS_METERS')

# RFID API Key
RFID_API_KEY = env('RFID_API_KEY', default='')

MANAGER_LINE_USER_ID = env('MANAGER_LINE_USER_ID', default='')
RICHMENU_DELIVERY = env('RICHMENU_DELIVERY', default='')
RICHMENU_STAFF = env('RICHMENU_STAFF', default='')

ORS_API_KEY = env('ORS_API_KEY', default='')

LIFF_DELIVERY_ID       = env('LIFF_DELIVERY_ID', default='')
LIFF_DELIVERY_ROUTE_ID = env('LIFF_DELIVERY_ROUTE_ID', default='')

# 系統對外網址（用於 LINE push 連結）
SITE_URL = env('SITE_URL', default='http://localhost:8000')

# GPS 同意書版本號：條款改版時請遞增，員工將被要求重新同意
GPS_CONSENT_VERSION = 'v1.0'


