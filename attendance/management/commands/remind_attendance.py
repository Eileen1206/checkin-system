from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import datetime, timedelta
from django.core.cache import cache
from django.conf import settings

from attendance.models import Employee, AttendanceRecord, MissedPunch
from attendance.utils import line_push, punch_check


# 隔天幾點之後才推送漏打卡通知（排程每幾分鐘跑一次都不會重複發）
NOTIFY_HOUR = 8
# 排程若曾經沒跑，往回補判定幾天
BACKFILL_DAYS = 7


def send_line_push(line_user_id, message, dedupe_key=None):
    """推播；有給 dedupe_key 就只會送一次。"""
    if dedupe_key:
        return line_push.push_once(line_user_id, message, dedupe_key)
    return line_push.push(line_user_id, message)


def _missed_message(record, count, limit):
    """給員工看的漏打卡通知。未超標是提醒，超標語氣轉為警告。"""
    what = record.get_missing_display()
    when = f"{record.date.month}/{record.date.day}"
    head = f'📋 {when} 忘了打{what}'
    body = f'這個月第 {count} 次（每月上限 {limit} 次）'
    if count > limit:
        return (f'⚠️ {when} 忘了打{what}\n'
                f'這個月已經第 {count} 次，超過每月 {limit} 次的上限。\n'
                f'請找老闆補登並多留意。')
    if count == limit:
        return f'{head}\n{body}\n已達上限，之後請多留意。\n請找老闆補登。'
    return f'{head}\n{body}\n請找老闆補登。'


class Command(BaseCommand):
    help = '判定漏打卡並於隔天早上通知員工；下班後未打卡的異常另行通知管理員'

    def handle(self, *args, **options):
        now = timezone.localtime()
        today = now.date()
        naive_now = now.replace(tzinfo=None)

        employees = list(Employee.tracked.filter(remind_enabled=True))

        created = self._detect_missed(employees, today)
        notified = self._notify_missed(employees, now)
        self._notify_manager_pending(employees, today, naive_now)

        self.stdout.write(f'漏打卡檢查完成：新增 {created} 筆、通知 {notified} 位')

    # ── ① 判定漏打卡 ────────────────────────────────────────
    def _detect_missed(self, employees, today):
        """檢查昨天（並往回補幾天，防止排程曾經沒跑）。"""
        created = 0
        for emp in employees:
            for back in range(1, BACKFILL_DAYS + 1):
                d = today - timedelta(days=back)
                _, is_new = punch_check.record_for_day(emp, d)
                if is_new:
                    created += 1
        return created

    # ── ② 隔天早上通知員工 ──────────────────────────────────
    def _notify_missed(self, employees, now):
        if now.hour < NOTIFY_HOUR:
            return 0

        limit = punch_check.monthly_limit()
        notified = 0
        for emp in employees:
            if not emp.line_user_id:
                continue
            pending = MissedPunch.objects.filter(
                employee=emp, notified_at__isnull=True, voided=False,
            ).order_by('date')
            for record in pending:
                count = punch_check.monthly_count(emp, record.date.year, record.date.month)
                sent = send_line_push(
                    emp.line_user_id,
                    _missed_message(record, count, limit),
                    dedupe_key=f'missed_punch_{record.pk}',
                )
                if not sent:
                    continue
                record.notified_at = timezone.now()
                record.save(update_fields=['notified_at'])
                notified += 1
        return notified

    # ── ③ 當天下班後仍未打卡 → 通知管理員 ───────────────────
    def _notify_manager_pending(self, employees, today, naive_now):
        manager_id = getattr(settings, 'MANAGER_LINE_USER_ID', '')
        if not manager_id:
            return

        anomaly = []
        for emp in employees:
            if not emp.work_end_time:
                continue
            records = AttendanceRecord.objects.filter(employee=emp, timestamp__date=today)
            if not records.filter(record_type='clock_in').exists():
                continue
            if records.filter(record_type='clock_out').exists():
                continue
            end_dt = datetime.combine(today, emp.work_end_time)
            # 每位員工每天最多進一次名單
            if naive_now >= end_dt + timedelta(minutes=30) and \
                    cache.add(f'anomaly_notified_{emp.pk}_{today}', True, 86400):
                anomaly.append(emp)

        if anomaly:
            names = '、'.join(e.user.get_full_name() or e.user.username for e in anomaly)
            send_line_push(
                manager_id,
                f'⚠️ 以下員工下班超過 30 分鐘尚未打下班卡，請確認：\n{names}',
                dedupe_key=f'anomaly_manager_{today}_{"_".join(str(e.pk) for e in anomaly)}',
            )
