import json
from datetime import date, timedelta
from collections import defaultdict

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from ..models import AttendanceRecord, Employee, DeliverySession, DeliveryTask, Customer
from .base import require_group


def _last_n_months(n=6):
    """回傳最近 n 個月的 (year, month) list，由舊到新"""
    today = timezone.localdate()
    result = []
    for i in range(n - 1, -1, -1):
        d = today.replace(day=1) - timedelta(days=i * 28)
        d = d.replace(day=1)
        result.append((d.year, d.month))
    # 去重並保留順序
    seen = set()
    months = []
    for ym in result:
        if ym not in seen:
            seen.add(ym)
            months.append(ym)
    return months[-n:]


@login_required
@require_group('admin', 'finance')
def analytics_attendance(request):
    """出勤分析 BI：KPI 摘要、出勤率趨勢、員工健康度、週幾缺勤分布"""
    import calendar as cal
    from datetime import datetime as dt
    from django.utils.timezone import localtime
    from .base import get_work_hours
    from ..models import LeaveRequest

    today = timezone.localdate()

    # 只分析「有設定上班時間」的員工（排除純帳號）
    employees = list(Employee.objects.filter(
        work_start_time__isnull=False
    ).select_related('user').order_by('employee_id'))

    if not employees:
        return render(request, 'attendance/analytics_attendance.html', {'no_data': True})

    # ── 批次撈 12 個月的 clock_in（只查 1 次 DB）─────────────
    months_12 = _last_n_months(12)
    period_start = date(months_12[0][0], months_12[0][1], 1)

    raw_ci = AttendanceRecord.objects.filter(
        employee__in=employees,
        record_type='clock_in',
        timestamp__date__gte=period_start,
        timestamp__date__lte=today,
    ).values_list('employee_id', 'timestamp')

    clockin_map = {}      # (emp_id, date) -> local datetime
    for emp_id, ts in raw_ci:
        loc = localtime(ts)
        key = (emp_id, loc.date())
        if key not in clockin_map:
            clockin_map[key] = loc

    # 工具函式
    def _is_work_day(emp, d):
        if d.weekday() >= 5:
            return False
        if emp.work_days:
            return str(d.weekday()) in emp.work_days.split(',')
        return True

    def _is_late(emp, ci_local):
        if not emp.work_start_time:
            return False
        scheduled = dt.combine(ci_local.date(), emp.work_start_time)
        actual    = dt.combine(ci_local.date(), ci_local.time())
        return (actual - scheduled).total_seconds() > 600

    # ── 每月統計（全 12 個月，純 Python 計算）──────────────────
    def _month_stats(y, m):
        total_wd = absent_c = late_c = 0
        _, days_in_m = cal.monthrange(y, m)
        for day in range(1, days_in_m + 1):
            d = date(y, m, day)
            if d > today:
                break
            for emp in employees:
                if not _is_work_day(emp, d):
                    continue
                total_wd += 1
                ci = clockin_map.get((emp.id, d))
                if ci is None:
                    absent_c += 1
                elif _is_late(emp, ci):
                    late_c += 1
        return total_wd, absent_c, late_c

    monthly = {(y, m): _month_stats(y, m) for y, m in months_12}

    # ── 現有圖表：近 6 個月遲到 / 曠職────────────────────────
    months_6    = months_12[-6:]
    labels      = [f"{y}/{m:02d}" for y, m in months_6]
    late_data   = [monthly[(y, m)][2] for y, m in months_6]
    absent_data = [monthly[(y, m)][1] for y, m in months_6]

    # ── 12 個月出勤率折線（新增）────────────────────────────
    labels_12 = [f"{y}/{m:02d}" for y, m in months_12]
    attendance_rate_12 = []
    for y, m in months_12:
        total_wd, absent_c, _ = monthly[(y, m)]
        rate = round((1 - absent_c / max(total_wd, 1)) * 100, 1) if total_wd else 100.0
        attendance_rate_12.append(rate)

    # ── KPI 卡片（本月 vs 上月）──────────────────────────────
    cy, cm = today.year, today.month
    prev   = date(cy, cm, 1) - timedelta(days=1)
    ly, lm = prev.year, prev.month

    def _att_rate(y, m):
        total_wd, absent_c, _ = monthly.get((y, m), (0, 0, 0))
        return round((1 - absent_c / max(total_wd, 1)) * 100, 1) if total_wd else 100.0

    def _late_rate(y, m):
        total_wd, _, late_c = monthly.get((y, m), (0, 0, 0))
        return round(late_c / max(total_wd, 1) * 100, 1) if total_wd else 0.0

    # 本月各員工工時
    emp_labels = []
    emp_hours  = []
    for emp in employees:
        total = sum(
            get_work_hours(emp, date(cy, cm, d))
            for d in range(1, today.day + 1)
            if date(cy, cm, d).weekday() < 5
        )
        emp_labels.append(emp.user.get_full_name() or emp.user.username)
        emp_hours.append(round(total, 1))
    avg_hours = round(sum(emp_hours) / max(len(emp_hours), 1), 1)

    # 本月 / 上月請假天數
    def _leave_days(y, m):
        count = 0
        for req in LeaveRequest.objects.filter(
            employee__in=employees, status='approved',
            requested_at__year=y, requested_at__month=m,
        ):
            try:
                dates_list = req.dates if isinstance(req.dates, list) \
                             else json.loads(req.dates or '[]')
                count += len(dates_list)
            except Exception:
                pass
        return count

    leave_this = _leave_days(cy, cm)
    leave_last = _leave_days(ly, lm)

    kpi = {
        'attendance_rate':       _att_rate(cy, cm),
        'attendance_rate_delta': round(_att_rate(cy, cm) - _att_rate(ly, lm), 1),
        'late_rate':             _late_rate(cy, cm),
        'late_rate_delta':       round(_late_rate(cy, cm) - _late_rate(ly, lm), 1),
        'avg_hours':             avg_hours,
        'leave_days':            leave_this,
        'leave_days_delta':      leave_this - leave_last,
    }

    # ── 週幾缺勤分布（近 3 個月）────────────────────────────
    weekday_absent = [0] * 5
    for y, m in months_12[-3:]:
        _, days_in_m = cal.monthrange(y, m)
        for day in range(1, days_in_m + 1):
            d = date(y, m, day)
            if d.weekday() >= 5 or d > today:
                continue
            for emp in employees:
                if not _is_work_day(emp, d):
                    continue
                if clockin_map.get((emp.id, d)) is None:
                    weekday_absent[d.weekday()] += 1

    # ── 員工出勤健康度（本月）───────────────────────────────
    emp_health = []
    for i, emp in enumerate(employees):
        total_wd = absent_c = late_c = 0
        for day in range(1, today.day + 1):
            d = date(cy, cm, day)
            if not _is_work_day(emp, d):
                continue
            total_wd += 1
            ci = clockin_map.get((emp.id, d))
            if ci is None:
                absent_c += 1
            elif _is_late(emp, ci):
                late_c += 1
        rate = round((1 - absent_c / max(total_wd, 1)) * 100, 1) if total_wd else 100.0
        emp_health.append({
            'name':   emp_labels[i],
            'rate':   rate,
            'late':   late_c,
            'absent': absent_c,
            'color':  'emerald' if rate >= 90 else ('amber' if rate >= 80 else 'red'),
        })
    emp_health.sort(key=lambda x: x['rate'])  # 最差排最前，方便老闆看

    return render(request, 'attendance/analytics_attendance.html', {
        # 現有圖表（6 個月）
        'labels':      json.dumps(labels, ensure_ascii=False),
        'late_data':   json.dumps(late_data),
        'absent_data': json.dumps(absent_data),
        'emp_labels':  json.dumps(emp_labels, ensure_ascii=False),
        'emp_hours':   json.dumps(emp_hours),
        # 新增
        'labels_12':          json.dumps(labels_12, ensure_ascii=False),
        'attendance_rate_12': json.dumps(attendance_rate_12),
        'kpi':                kpi,
        'weekday_labels':     json.dumps(['週一', '週二', '週三', '週四', '週五'], ensure_ascii=False),
        'weekday_absent':     json.dumps(weekday_absent),
        'emp_health':         emp_health,
    })


@login_required
@require_group('admin', 'finance')
def analytics_delivery(request):
    """送貨分析：每日趟數、完成率、平均時間"""
    today = timezone.localdate()
    # 最近 30 天
    days = [(today - timedelta(days=i)) for i in range(29, -1, -1)]
    labels = [d.strftime('%-m/%-d') if hasattr(d, 'strftime') else d.strftime('%m/%d') for d in days]
    # Windows 不支援 %-m，用 %m/%d 再去掉前導零
    labels = [d.strftime('%m/%d').lstrip('0').replace('/0', '/') for d in days]

    trip_counts   = []
    finish_counts = []
    for d in days:
        total    = DeliverySession.objects.filter(date=d).count()
        finished = DeliverySession.objects.filter(date=d, finished_at__isnull=False).count()
        trip_counts.append(total)
        finish_counts.append(finished)

    # 近 30 天每日「未按完成」次數
    auto_closed_counts = [
        DeliverySession.objects.filter(date=d, auto_closed=True).count()
        for d in days
    ]

    # 各員工本月送貨趟數
    emp_labels      = []
    emp_trips       = []
    emp_stations    = []
    emp_auto_closed = []
    employees = Employee.objects.select_related('user').order_by('employee_id')
    for emp in employees:
        trips       = DeliverySession.objects.filter(employee=emp, date__year=today.year, date__month=today.month).count()
        stations    = DeliveryTask.objects.filter(employee=emp, date__year=today.year, date__month=today.month, status='completed').count()
        auto_closed = DeliverySession.objects.filter(employee=emp, date__year=today.year, date__month=today.month, auto_closed=True).count()
        emp_labels.append(emp.user.get_full_name() or emp.user.username)
        emp_trips.append(trips)
        emp_stations.append(stations)
        emp_auto_closed.append(auto_closed)

    return render(request, 'attendance/analytics_delivery.html', {
        'labels':             json.dumps(labels, ensure_ascii=False),
        'trip_counts':        json.dumps(trip_counts),
        'finish_counts':      json.dumps(finish_counts),
        'auto_closed_counts': json.dumps(auto_closed_counts),
        'emp_labels':         json.dumps(emp_labels, ensure_ascii=False),
        'emp_trips':          json.dumps(emp_trips),
        'emp_stations':       json.dumps(emp_stations),
        'emp_auto_closed':    json.dumps(emp_auto_closed),
    })


@login_required
@require_group('admin', 'finance')
def analytics_customer(request):
    """客戶分析：送貨頻率排行"""
    today = timezone.localdate()
    # 本月送貨頻率 Top 15
    from django.db.models import Count
    top_customers = (
        DeliveryTask.objects
        .filter(date__year=today.year, date__month=today.month, status='completed')
        .values('customer_name')
        .annotate(count=Count('id'))
        .order_by('-count')[:15]
    )
    cust_labels = json.dumps([c['customer_name'] for c in top_customers], ensure_ascii=False)
    cust_counts = json.dumps([c['count'] for c in top_customers])

    # 近 3 個月趨勢（Top 5 客戶）
    top5 = [c['customer_name'] for c in top_customers[:5]]
    months = _last_n_months(3)
    month_labels = json.dumps([f"{y}/{m:02d}" for y, m in months], ensure_ascii=False)
    trend_datasets = []
    colors = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6']
    for i, name in enumerate(top5):
        data = []
        for y, m in months:
            cnt = DeliveryTask.objects.filter(
                customer_name=name, date__year=y, date__month=m, status='completed'
            ).count()
            data.append(cnt)
        trend_datasets.append({
            'label': name,
            'data': data,
            'borderColor': colors[i % len(colors)],
            'backgroundColor': colors[i % len(colors)] + '22',
            'tension': 0.4,
        })

    return render(request, 'attendance/analytics_customer.html', {
        'cust_labels':     cust_labels,
        'cust_counts':     cust_counts,
        'month_labels':    month_labels,
        'trend_datasets':  json.dumps(trend_datasets, ensure_ascii=False),
    })
