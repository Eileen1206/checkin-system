"""漏打卡判定與計次。

「完整的卡」＝ 當天上班卡與下班卡都有（午休不列入判定）。
判定與出勤報表的 missing_clockout 一致：只看已經過去的日子，
當天還在進行中不算漏打。

補登打卡不會抹掉既有的漏打紀錄（否則計次失去意義），
誤判的由老闆註銷（voided）。
"""
import calendar
from datetime import date as date_cls

from django.conf import settings
from django.utils import timezone

from ..models import AttendanceRecord, LeaveRecord, MissedPunch


def monthly_limit():
    """一個月可接受的漏打卡次數上限，超過即標紅。"""
    return getattr(settings, 'MISSED_PUNCH_MONTHLY_LIMIT', 5)


def detect(employee, d):
    """回傳當天漏掉哪一張卡；沒漏或當天不該上班則回 None。

    - 兩張都沒打 → 那是缺勤或休假，不是漏打
    - 只有上班沒下班 → 漏下班卡（最常見）
    - 只有下班沒上班 → 漏上班卡
    """
    if d >= timezone.localdate():
        return None            # 今天還沒結束，不判定

    # 整天休假的日子不該有卡
    leave = LeaveRecord.objects.filter(employee=employee, date=d).first()
    if leave and leave.is_full_day:
        return None

    records = AttendanceRecord.objects.filter(employee=employee, timestamp__date=d)
    has_in = records.filter(record_type='clock_in').exists()
    has_out = records.filter(record_type='clock_out').exists()

    if has_in and not has_out:
        return MissedPunch.MISSING_CLOCK_OUT
    if has_out and not has_in:
        return MissedPunch.MISSING_CLOCK_IN
    return None


def record_for_day(employee, d):
    """判定某天並建立漏打卡紀錄；已存在則沿用。回傳 (紀錄, 是否新建)。"""
    missing = detect(employee, d)
    if not missing:
        return None, False
    return MissedPunch.objects.get_or_create(
        employee=employee, date=d, defaults={'missing': missing},
    )


def monthly_count(employee, year, month):
    """當月漏打卡次數（不含已註銷）。"""
    return MissedPunch.objects.filter(
        employee=employee, date__year=year, date__month=month, voided=False,
    ).count()


def monthly_records(employee, year, month):
    return MissedPunch.objects.filter(
        employee=employee, date__year=year, date__month=month, voided=False,
    ).order_by('date')


def monthly_stats(employee, year, month):
    """給薪資表與出勤報表用的彙總。"""
    records = list(monthly_records(employee, year, month))
    limit = monthly_limit()
    return {
        'count': len(records),
        'limit': limit,
        'over_limit': len(records) > limit,
        'dates': [r.date for r in records],
        'records': records,
    }


def count_in_month_of(employee, d):
    """d 當月累計到目前為止的漏打卡次數（含 d 當天那筆）。"""
    return monthly_count(employee, d.year, d.month)


def month_dates(year, month):
    _, days = calendar.monthrange(year, month)
    return [date_cls(year, month, i) for i in range(1, days + 1)]
