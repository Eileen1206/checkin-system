"""
執行一次即可建立 Rich Menu 並綁定給所有員工。
用法：python manage.py setup_richmenu
"""
from django.core.management.base import BaseCommand
from django.conf import settings
from attendance.models import Employee
import requests
import json
from pathlib import Path

BASE_DIR = Path(settings.BASE_DIR)


CHANNEL_ACCESS_TOKEN = settings.LINE_CHANNEL_ACCESS_TOKEN
HEADERS = {'Authorization': f'Bearer {CHANNEL_ACCESS_TOKEN}'}

# Rich Menu 尺寸（LINE 規定最小 800px 寬）
W, H = 1200, 405


def make_image(path: str) -> bytes:
    """讀取圖片檔案，回傳 bytes"""
    with open(path, 'rb') as f:
        return f.read()


def create_richmenu(name: str, areas: list[dict]) -> str:
    """建立 Rich Menu，回傳 richMenuId"""
    body = {
        "size": {"width": W, "height": H},
        "selected": True,
        "name": name,
        "chatBarText": "選單",
        "areas": areas,
    }
    resp = requests.post(
        'https://api.line.me/v2/bot/richmenu',
        headers={**HEADERS, 'Content-Type': 'application/json'},
        data=json.dumps(body),
    )
    resp.raise_for_status()
    return resp.json()['richMenuId']


def upload_image(rich_menu_id: str, image_bytes: bytes):
    """上傳背景圖"""
    requests.post(
        f'https://api-data.line.me/v2/bot/richmenu/{rich_menu_id}/content',
        headers={**HEADERS, 'Content-Type': 'image/png'},
        data=image_bytes,
    ).raise_for_status()


def link_user(line_user_id: str, rich_menu_id: str):
    """把 Rich Menu 綁給指定使用者"""
    requests.post(
        f'https://api.line.me/v2/bot/user/{line_user_id}/richmenu/{rich_menu_id}',
        headers=HEADERS,
    ).raise_for_status()


def make_areas(labels_data: list[tuple[str, str]]) -> list[dict]:
    """
    labels_data: [(label, postback_data), ...]
    回傳 LINE Rich Menu areas 格式
    """
    cols = len(labels_data)
    cell_w = W // cols
    areas = []
    for i, (label, data) in enumerate(labels_data):
        areas.append({
            "bounds": {"x": i * cell_w, "y": 0, "width": cell_w, "height": H},
            "action": {"type": "postback", "label": label, "data": data},
        })
    return areas


class Command(BaseCommand):
    help = '建立 Rich Menu 並綁定給所有員工'

    def handle(self, *args, **options):
        # === 送貨員 Rich Menu ===
        delivery_buttons = [
            ('🏠 送貨接下班', 'action=delivery_clockout_request'),
            ('📅 查詢今日', 'action=query'),
            ('📊 本月出勤', 'action=monthly'),
            
        ]
        delivery_areas = make_areas(delivery_buttons)
        delivery_labels = [b[0] for b in delivery_buttons]

        self.stdout.write('建立送貨員 Rich Menu...')
        delivery_menu_id = create_richmenu('送貨員選單', delivery_areas)
        upload_image(delivery_menu_id, make_image(BASE_DIR / 'richmenu_delivery.png'))
        self.stdout.write(f'送貨員 Rich Menu ID: {delivery_menu_id}')

        # === 一般員工 Rich Menu ===
        staff_buttons = [
            ('📅 查詢今日', 'action=query'),
            ('📊 本月出勤', 'action=monthly'),
        ]
        staff_areas = make_areas(staff_buttons)
        staff_labels = [b[0] for b in staff_buttons]

        self.stdout.write('建立一般員工 Rich Menu...')
        staff_menu_id = create_richmenu('一般員工選單', staff_areas)
        upload_image(staff_menu_id, make_image(BASE_DIR / 'richmenu_staff.png'))
        self.stdout.write(f'一般員工 Rich Menu ID: {staff_menu_id}')

        # === 綁定給員工 ===
        employees = Employee.objects.filter(line_user_id__isnull=False).exclude(line_user_id='')
        count = 0
        for emp in employees:
            menu_id = delivery_menu_id if emp.is_delivery else staff_menu_id
            link_user(emp.line_user_id, menu_id)
            count += 1

        self.stdout.write(self.style.SUCCESS(f'完成！已綁定 {count} 位員工'))
        self.stdout.write('請將以下內容填入 .env：')
        self.stdout.write(f'RICHMENU_DELIVERY={delivery_menu_id}')
        self.stdout.write(f'RICHMENU_STAFF={staff_menu_id}')
