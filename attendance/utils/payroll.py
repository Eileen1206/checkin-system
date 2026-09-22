"""
勞基法薪資計算：特別休假（§38，週年制、全額折現）與加班費（§24 / §39）。

純函式為主，方便測試與核對。金額基準：
- 平日每小時工資（加班基數）：月薪制 = 月薪 / 240（=/30/8）；時薪制 = 時薪。
- 日薪（特休折現 / 假日加給）：月薪制 = 月薪 / 30；時薪制 = 時薪 × 8。

工時一律以「分鐘」為精度，不做半小時進位。

金額結構（時薪制與月薪制共用同一套加班函式，皆回傳「全額」）：
- 底薪：時薪制 = 平日正常工時（每日上限 8 小時）× 時薪；月薪制 = 固定月薪。
- 加班費：平日超過 8 小時的部分、休息日全日、例假與國定假日全日，
  都不含在底薪裡，由加班函式一次算足全額，不會重複計算。

時薪制的金額「逐日四捨五入到元」後加總，明細加起來會剛好等於總額。
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

def weekday_ot(hours, hourly):
    """平日延長工時加班費（全額）：前 2h ×4/3、其後 ×5/3（超過 8h 的部分）。"""
    ot = max(hours - 8, 0)
    if ot <= 0:
        return 0.0
    first = min(ot, 2.0)
    rest = ot - first
    return hourly * (first * 4 / 3 + rest * 5 / 3)


def restday_ot(hours, hourly):
    """休息日出勤加班費（全額）：前 2h ×4/3、第 3~8h ×5/3、第 9~12h ×8/3。"""
    if hours <= 0:
        return 0.0
    t1 = min(hours, 2.0)
    t2 = min(max(hours - 2, 0), 6.0)   # 第 3~8 小時
    t3 = min(max(hours - 8, 0), 4.0)   # 第 9~12 小時
    return hourly * (t1 * 4 / 3 + t2 * 5 / 3 + t3 * 8 / 3)


def holiday_ot(hours, hourly, daily, is_monthly):
    """例假 / 國定假日出勤：工資加倍（§39，全額）。"""
    if hours <= 0:
        return 0.0
    if is_monthly:
        # 月薪：當日工資已含在月薪 → 加發一日日薪 + 超過 8h 比照平日延長
        over = max(hours - 8, 0)
        first = min(over, 2.0)
        rest = over - first
        return daily + hourly * (first * 4 / 3 + rest * 5 / 3)
    # 時薪：整日工時加倍
    return hourly * hours * 2


# ───────────────────── 每月加班費彙總 ─────────────────────

def fmt_hm(minutes):
    """分鐘 → 「8小時30分」這種給人看的寫法。"""
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f'{h}小時{m}分'
    if h:
        return f'{h}小時'
    return f'{m}分'


def monthly_work_detail(emp, year, month):
    """逐日走過當月出勤，回傳每日明細與彙總。

    每日明細包含：日別（平日／休息日／例假／國定假日）、計薪分鐘、
    正常工時與加班時數、底薪金額與加班費金額（皆已四捨五入到元），
    以及遲到分鐘（不影響金額，只供顯示）。

    時薪制每日金額先四捨五入再加總，明細加起來即為總額。
    """
    from django.utils.timezone import localtime
    from attendance.models import AttendanceRecord, LeaveRecord, Holiday
    from attendance.dashboard_views.base import get_work_minutes, get_late_minutes

    is_monthly = emp.employment_type == 'monthly'
    hourly = hourly_wage(emp)
    daily = daily_wage(emp)

    PUNCH_TYPES = ('clock_in', 'break_start', 'break_end', 'clock_out')

    holiday_set = set(Holiday.objects.filter(
        date__year=year, date__month=month).values_list('date', flat=True))
    # 只採計「整天」不上班者為休息日；部分時數請假當天仍有出勤
    leave_dates = {
        lr.date for lr in LeaveRecord.objects.filter(
            employee=emp, date__year=year, date__month=month)
        if lr.is_full_day
    }
    # 含「只有下班卡」或「缺下班卡」的日子：那些正是老闆要補登的，
    # 不能因為算出 0 分鐘就從明細裡消失。
    day_records = AttendanceRecord.objects.filter(
        employee=emp, record_type__in=PUNCH_TYPES,
        timestamp__year=year, timestamp__month=month,
    ).order_by('timestamp')

    punches_by_day = {}
    for r in day_records:
        d = localtime(r.timestamp).date()
        slot = punches_by_day.setdefault(d, {})
        if r.record_type not in slot:      # 同型多筆取最早那張
            slot[r.record_type] = {
                'id': r.pk,
                'time': localtime(r.timestamp).strftime('%H:%M'),
            }
    days = sorted(punches_by_day)

    detail = []
    tiers = {
        'weekday_1_2': 0.0, 'weekday_3plus': 0.0,
        'restday_1_2': 0.0, 'restday_3_8': 0.0, 'restday_9_12': 0.0,
        'holiday': 0.0,
    }
    base_total = 0          # 時薪制底薪（逐日四捨五入後加總）
    ot_total = 0            # 加班費（逐日四捨五入後加總）
    normal_minutes = 0
    work_minutes = 0
    late_minutes_total = 0
    late_days = 0

    for d in days:
        minutes = get_work_minutes(emp, d)
        late = get_late_minutes(emp, d)
        if late:
            late_days += 1
            late_minutes_total += late

        punches = punches_by_day.get(d, {})
        cls = classify_day(emp, d, holiday_set, leave_dates)

        if not minutes:
            # 打卡不完整（最常見是缺下班卡）→ 當天算不出工時，
            # 仍列進明細讓老闆看得到並補登。
            detail.append({
                'date': d, 'cls': cls, 'minutes': 0, 'hours': 0.0,
                'hm': '—', 'normal_hours': 0.0, 'ot_hours': 0.0,
                'base_amount': 0, 'ot_amount': 0, 'amount': 0,
                'late_minutes': late, 'worked': False, 'punches': punches,
                'incomplete': True,
            })
            continue

        h = minutes / 60.0
        normal_h = 0.0

        if cls == '平日':
            normal_h = min(h, 8.0)               # 平日前 8 小時為正常工時
            ot_amt = weekday_ot(h, hourly)
            ot = max(h - 8, 0)
            tiers['weekday_1_2'] += min(ot, 2.0)
            tiers['weekday_3plus'] += max(ot - 2, 0)
        elif cls == '休息日':
            ot_amt = restday_ot(h, hourly)        # 休息日整日皆加班
            tiers['restday_1_2'] += min(h, 2.0)
            tiers['restday_3_8'] += min(max(h - 2, 0), 6.0)
            tiers['restday_9_12'] += min(max(h - 8, 0), 4.0)
        else:                                     # 例假 / 國定假日
            ot_amt = holiday_ot(h, hourly, daily, is_monthly)
            tiers['holiday'] += h

        base_amt = 0 if is_monthly else round(normal_h * hourly)
        ot_amt = round(ot_amt)

        work_minutes += minutes
        normal_minutes += int(round(normal_h * 60))
        base_total += base_amt
        ot_total += ot_amt

        detail.append({
            'date': d,
            'cls': cls,
            'minutes': minutes,
            'hours': round(h, 2),
            'hm': fmt_hm(minutes),
            'normal_hours': round(normal_h, 2),
            'ot_hours': round(h - normal_h, 2),
            'base_amount': base_amt,
            'ot_amount': ot_amt,
            'amount': base_amt + ot_amt,
            'late_minutes': late,
            'worked': True,
            'punches': punches,
            'incomplete': False,
        })

    return {
        'detail': detail,
        'tiers': tiers,
        'base': base_total,
        'overtime': ot_total,
        'work_minutes': work_minutes,
        'work_hm': fmt_hm(work_minutes),
        'normal_minutes': normal_minutes,
        'normal_hours': round(normal_minutes / 60, 2),
        'late_days': late_days,
        'late_minutes': late_minutes_total,
        'late_hm': fmt_hm(late_minutes_total),
        'incomplete_days': [x['date'] for x in detail if x['incomplete']],
    }


def monthly_overtime(emp, year, month):
    """加班費彙總（薄包裝，明細由 monthly_work_detail 產生）。

    回傳 {'amount': 加班費總額, 'detail': [有加班費的日子], 'tiers': {分級時數},
          'normal_hours': 平日正常工時}。
    """
    d = monthly_work_detail(emp, year, month)
    return {
        'amount': d['overtime'],
        'detail': [
            {'date': x['date'], 'cls': x['cls'], 'hours': x['hours'],
             'hm': x['hm'], 'amount': x['ot_amount']}
            for x in d['detail'] if x['ot_amount'] > 0
        ],
        'tiers': d['tiers'],
        'normal_hours': d['normal_hours'],
    }


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
