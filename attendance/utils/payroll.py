"""
勞基法薪資計算：特別休假（§38，週年制、全額折現）與加班費（§24 / §39）。

純函式為主，方便測試與核對。金額基準：
- 平日每小時工資（加班基數）：月薪制 = 月薪 / 240（=/30/8）；時薪制 = 時薪。
- 日薪（特休折現 / 假日加給）：月薪制 = 月薪 / 30；時薪制 = 時薪 × 8。

加班費一律「加在既有 base 之上」：
- 時薪制 base 已含每一工時 ×1 → 加班只補「加成」部分（倍率 − 1）。
- 月薪制 base 為固定月薪、未含時薪 → 加班補「全額」。
"""
import calendar as _calendar
from datetime import date


# ───────────────────── 年資 / 特休（§38，週年制）─────────────────────

def _add_months(d, months):
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, _calendar.monthrange(y, m)[1])
    return date(y, m, day)


def _completed_years(hire_date, as_of):
    years = as_of.year - hire_date.year
    if (as_of.month, as_of.day) < (hire_date.month, hire_date.day):
        years -= 1
    return years


def service_length(hire_date, as_of):
    """回傳 (年, 月) 年資。"""
    if not hire_date or as_of < hire_date:
        return (0, 0)
    months = (as_of.year - hire_date.year) * 12 + (as_of.month - hire_date.month)
    if as_of.day < hire_date.day:
        months -= 1
    return (months // 12, months % 12)


def annual_leave_days(hire_date, as_of):
    """依到職日與結算日回傳應有特休天數（§38，週年制）。未滿 6 個月 → 0。"""
    if not hire_date or as_of < hire_date:
        return 0
    yrs = _completed_years(hire_date, as_of)
    if yrs >= 10:
        return min(15 + (yrs - 9), 30)
    if yrs >= 5:
        return 15
    if yrs >= 3:
        return 14
    if yrs == 2:
        return 10
    if yrs == 1:
        return 7
    # 未滿 1 年 → 判斷是否滿 6 個月
    return 3 if _add_months(hire_date, 6) <= as_of else 0


# ───────────────────── 工資基準 ─────────────────────

def hourly_wage(emp):
    """平日每小時工資（加班基數）。"""
    if emp.employment_type == 'monthly':
        return float(emp.monthly_salary or 0) / 240.0
    return float(emp.hourly_rate or 0)


def daily_wage(emp):
    """日薪（特休折現 / 假日加給）。"""
    if emp.employment_type == 'monthly':
        return float(emp.monthly_salary or 0) / 30.0
    return float(emp.hourly_rate or 0) * 8.0


# ───────────────────── 日別判定 ─────────────────────

def classify_day(emp, d, holiday_set, leave_dates):
    """回傳 '平日' / '休息日' / '例假' / '國定假日'。"""
    if d in holiday_set:
        return '國定假日'
    if d.weekday() == 6:          # 週日公休 = 例假
        return '例假'
    work_set = {int(x) for x in str(emp.work_days).split(',') if x.strip().isdigit()}
    if d.weekday() not in work_set or d in leave_dates:
        return '休息日'
    return '平日'


# ───────────────────── 加班費（回傳「加在 base 之上」的金額）─────────────────────

def weekday_ot(hours, hourly, is_monthly):
    """平日延長工時：前 2h ×4/3、其後 ×5/3（超過 8h 的部分）。"""
    ot = max(hours - 8, 0)
    if ot <= 0:
        return 0.0
    first = min(ot, 2.0)
    rest = ot - first
    if is_monthly:
        return hourly * (first * 4 / 3 + rest * 5 / 3)
    return hourly * (first * 1 / 3 + rest * 2 / 3)   # 時薪 base 已含 ×1，只補加成


def restday_ot(hours, hourly, is_monthly):
    """休息日出勤：前 2h ×4/3、第 3~8h ×5/3、第 9~12h ×8/3。"""
    if hours <= 0:
        return 0.0
    t1 = min(hours, 2.0)
    t2 = min(max(hours - 2, 0), 6.0)   # 第 3~8 小時
    t3 = min(max(hours - 8, 0), 4.0)   # 第 9~12 小時
    if is_monthly:
        return hourly * (t1 * 4 / 3 + t2 * 5 / 3 + t3 * 8 / 3)
    return hourly * (t1 * 1 / 3 + t2 * 2 / 3 + t3 * 5 / 3)


def holiday_ot(hours, hourly, daily, is_monthly):
    """例假 / 國定假日出勤：工資加倍（§39）。"""
    if hours <= 0:
        return 0.0
    over = max(hours - 8, 0)
    first = min(over, 2.0)
    rest = over - first
    if is_monthly:
        # 月薪 base 未含當日時薪 → 加發一日日薪 + 超過 8h 比照平日延長全額
        return daily + hourly * (first * 4 / 3 + rest * 5 / 3)
    # 時薪 base 已含 ×1 → 補到 ×2（+1 倍）；超過 8h 再補加成
    return hourly * hours + hourly * (first * 1 / 3 + rest * 2 / 3)


# ───────────────────── 每月加班費彙總 ─────────────────────

def monthly_overtime(emp, year, month):
    """
    回傳 {'amount': 加班費總額, 'detail': [每日明細], 'tiers': {分級時數}}。
    tiers 依勞基法級距彙總當月加班時數（供明細報表顯示）：
      weekday_1_2 / weekday_3plus / restday_1_2 / restday_3_8 / restday_9_12 / holiday
    """
    from attendance.models import AttendanceRecord, LeaveRecord, Holiday
    from attendance.dashboard_views.base import get_work_hours

    is_monthly = emp.employment_type == 'monthly'
    hourly = hourly_wage(emp)
    daily = daily_wage(emp)

    holiday_set = set(Holiday.objects.filter(
        date__year=year, date__month=month).values_list('date', flat=True))
    leave_dates = set(LeaveRecord.objects.filter(
        employee=emp, date__year=year, date__month=month).values_list('date', flat=True))
    days = AttendanceRecord.objects.filter(
        employee=emp, record_type='clock_in',
        timestamp__year=year, timestamp__month=month,
    ).dates('timestamp', 'day')

    total = 0.0
    detail = []
    tiers = {
        'weekday_1_2': 0.0, 'weekday_3plus': 0.0,
        'restday_1_2': 0.0, 'restday_3_8': 0.0, 'restday_9_12': 0.0,
        'holiday': 0.0,
    }
    for d in days:
        h = get_work_hours(emp, d)
        if not h:
            continue
        cls = classify_day(emp, d, holiday_set, leave_dates)
        if cls == '平日':
            amt = weekday_ot(h, hourly, is_monthly)
            ot = max(h - 8, 0)
            tiers['weekday_1_2'] += min(ot, 2.0)
            tiers['weekday_3plus'] += max(ot - 2, 0)
        elif cls == '休息日':
            amt = restday_ot(h, hourly, is_monthly)
            tiers['restday_1_2'] += min(h, 2.0)
            tiers['restday_3_8'] += min(max(h - 2, 0), 6.0)
            tiers['restday_9_12'] += min(max(h - 8, 0), 4.0)
        else:  # 例假 / 國定假日
            amt = holiday_ot(h, hourly, daily, is_monthly)
            tiers['holiday'] += h
        if amt > 0:
            total += amt
            detail.append({'date': d, 'cls': cls, 'hours': h, 'amount': round(amt)})
    return {'amount': round(total), 'detail': detail, 'tiers': tiers}


# ───────────────────── 特休結算（全額折現）─────────────────────

def annual_leave_settlement(emp, as_of=None):
    """回傳特休結算：年資、應有特休天數、日薪、折現金額（天數 × 日薪，全額）。"""
    from django.utils import timezone
    as_of = as_of or timezone.localdate()
    yrs, mos = service_length(emp.hire_date, as_of)
    days = annual_leave_days(emp.hire_date, as_of)
    daily = daily_wage(emp)
    return {
        'has_hire_date': bool(emp.hire_date),
        'as_of': as_of,
        'years': yrs,
        'months': mos,
        'days': days,
        'daily_wage': round(daily),
        'payout': round(days * daily),
    }
