from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import datetime, timedelta
from django.core.cache import cache
from attendance.models import Employee, AttendanceRecord, LeaveRecord
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    PushMessageRequest, TextMessage,
)
from django.conf import settings


def send_line_push(line_user_id, message):
    """發送 LINE Push 訊息給指定使用者"""
    configuration = Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        api.push_message(PushMessageRequest(
            to=line_user_id,
            messages=[TextMessage(text=message)],
        ))


class Command(BaseCommand):
    help = '檢查遲到與忘打卡，發送 LINE 提醒'

    def handle(self, *args, **options):
        now = timezone.localtime()
        today = now.date()

        employees = Employee.objects.filter(
            line_user_id__isnull=False,
            remind_enabled=True,          # 管理員已開啟提醒
        )

        for emp in employees:

            # ① 確認今天是員工的工作日
            work_days = [int(d) for d in emp.work_days.split(',') if d.strip().isdigit()]
            if today.weekday() not in work_days:
                continue

            # ② 確認今天沒有請假紀錄
            if LeaveRecord.objects.filter(employee=emp, date=today).exists():
                continue

            # 今天的打卡紀錄
            records = AttendanceRecord.objects.filter(
                employee=emp,
                timestamp__date=today,
            )
            has_clock_in = records.filter(record_type='clock_in').exists()
            has_clock_out = records.filter(record_type='clock_out').exists()

            start_key = f"reminded_{emp.pk}_clock_in_{today}"
            end_key = f"reminded_{emp.pk}_clock_out_{today}"

            naive_now = now.replace(tzinfo=None)

            # ③ 上班打卡提醒
            if emp.work_start_time and not has_clock_in:
                start_dt = datetime.combine(today, emp.work_start_time)
                if naive_now >= start_dt - timedelta(minutes=5) and not cache.get(start_key):
                    send_line_push(emp.line_user_id, '⏰ 上班打卡時間快到了，請記得打卡！')
                    cache.set(start_key, True, 86400)

            # ④ 下班打卡提醒
            if emp.work_end_time and has_clock_in and not has_clock_out:
                end_dt = datetime.combine(today, emp.work_end_time)
                if naive_now >= end_dt - timedelta(minutes=5) and not cache.get(end_key):
                    send_line_push(emp.line_user_id, '⏰ 下班打卡時間快到了，請記得打卡！')
                    cache.set(end_key, True, 86400)

        self.stdout.write('提醒檢查完成')
