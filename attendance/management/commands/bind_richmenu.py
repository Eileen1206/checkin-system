"""
將 Rich Menu 綁定給所有已綁定 LINE 的員工。
每次部署時自動執行，確保新員工也有選單。
用法：python manage.py bind_richmenu
"""
from django.core.management.base import BaseCommand
from django.conf import settings
from attendance.models import Employee
import requests


class Command(BaseCommand):
    help = '將 Rich Menu 綁定給所有已綁定 LINE 的員工'

    def handle(self, *args, **options):
        delivery_menu_id = settings.RICHMENU_DELIVERY
        staff_menu_id = settings.RICHMENU_STAFF

        if not delivery_menu_id or not staff_menu_id:
            self.stdout.write(self.style.WARNING('RICHMENU_DELIVERY 或 RICHMENU_STAFF 未設定，跳過綁定'))
            return

        token = settings.LINE_CHANNEL_ACCESS_TOKEN
        headers = {'Authorization': f'Bearer {token}'}

        employees = Employee.objects.filter(
            line_user_id__isnull=False
        ).exclude(line_user_id='')

        count = 0
        for emp in employees:
            menu_id = delivery_menu_id if emp.is_delivery else staff_menu_id
            try:
                resp = requests.post(
                    f'https://api.line.me/v2/bot/user/{emp.line_user_id}/richmenu/{menu_id}',
                    headers=headers,
                )
                resp.raise_for_status()
                count += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'  綁定失敗 {emp}：{e}'))

        self.stdout.write(self.style.SUCCESS(f'Rich Menu 綁定完成，共 {count} 位員工'))
