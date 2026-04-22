"""
測試專用設定：繼承主設定，移除需要額外套件的 middleware。
用法：python manage.py test --settings=checkin_system.settings_test
"""
from .settings import *  # noqa

# 移除 whitenoise（本機測試環境不一定有安裝）
MIDDLEWARE = [m for m in MIDDLEWARE if 'whitenoise' not in m]

# 靜態檔案改用預設 backend
STORAGES = {
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
