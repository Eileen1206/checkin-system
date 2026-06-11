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
    ).values_list('employee_id', 'timestamp', 'source')

    clockin_map = {}      # (emp_id, date) -> local datetime
    makeup_set  = set()   # (emp_id, date) — source='admin' 表示補打卡
    for emp_id, ts, source in raw_ci:
        loc = localtime(ts)
        key = (emp_id, loc.date())
        if key not in clockin_map:
            clockin_map[key] = loc
        if source == 'admin':
            makeup_set.add(key)

    # 工具函式
    def _is_work_day(emp, d):
        if d.weekday() >= 7:   # 永遠不會是 True，保留供未來擴充
            return False
        if emp.work_days:
            # 員工有設定工作日（可含週六=5）時，以此為準
            return str(d.weekday()) in emp.work_days.split(',')
        # 未設定時預設週一~週五（0–4）
        return d.weekday() < 5

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
            if _is_work_day(emp, date(cy, cm, d))
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

    # ── 週幾缺勤分布（近 3 個月，含週六）────────────────────
    weekday_absent = [0] * 6   # 0=週一 … 5=週六
    for y, m in months_12[-3:]:
        _, days_in_m = cal.monthrange(y, m)
        for day in range(1, days_in_m + 1):
            d = date(y, m, day)
            if d.weekday() >= 6 or d > today:   # 只跳過週日(6)
                continue
            for emp in employees:
                if not _is_work_day(emp, d):
                    continue
                if clockin_map.get((emp.id, d)) is None:
                    weekday_absent[d.weekday()] += 1

    # ── 員工出勤健康度（本月）───────────────────────────────
    emp_health = []
    for i, emp in enumerate(employees):
        total_wd = absent_c = late_c = makeup_c = 0
        for day in range(1, today.day + 1):
            d = date(cy, cm, day)
            if not _is_work_day(emp, d):
                continue
            total_wd += 1
            ci = clockin_map.get((emp.id, d))
            if ci is None:
                absent_c += 1
            else:
                if _is_late(emp, ci):
                    late_c += 1
                if (emp.id, d) in makeup_set:
                    makeup_c += 1
        rate = round((1 - absent_c / max(total_wd, 1)) * 100, 1) if total_wd else 100.0
        makeup_rate = round(makeup_c / max(total_wd, 1) * 100, 1) if total_wd else 0.0
        emp_health.append({
            'name':        emp_labels[i],
            'rate':        rate,
            'late':        late_c,
            'absent':      absent_c,
            'makeup':      makeup_c,
            'makeup_rate': makeup_rate,
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
        'weekday_labels':     json.dumps(['週一', '週二', '週三', '週四', '週五', '週六'], ensure_ascii=False),
        'weekday_absent':     json.dumps(weekday_absent),
        'emp_health':         emp_health,
    })


@login_required
@require_group('admin', 'finance')
def analytics_delivery(request):
    """送貨分析 BI：KPI 摘要、趟次趨勢、員工健康度、時段分布"""
    from django.utils.timezone import localtime as ltime
    from django.db.models import Count
    from collections import defaultdict

    today        = timezone.localdate()
    period_start = today - timedelta(days=29)
    prev_end     = today - timedelta(days=30)
    prev_start   = today - timedelta(days=59)

    # ── 批次載入（避免 N+1）──────────────────────────────────
    sessions_now  = list(DeliverySession.objects.filter(
        date__range=(period_start, today)
    ).select_related('employee__user'))

    sessions_prev = list(DeliverySession.objects.filter(
        date__range=(prev_start, prev_end)
    ).select_related('employee__user'))

    station_map = {
        row['employee_id']: row['count']
        for row in DeliveryTask.objects.filter(
            date__range=(period_start, today), status='completed'
        ).values('employee_id').annotate(count=Count('id'))
    }

    # ── KPI 計算函式（純 Python）─────────────────────────────
    def _completion_rate(sl):
        if not sl:
            return None
        done = sum(1 for s in sl if not s.auto_closed and s.finished_at)
        return round(done / len(sl) * 100, 1)

    def _avg_trip_min(sl):
        valid = [s for s in sl if s.started_at and s.finished_at and not s.auto_closed]
        if not valid:
            return None
        return round(sum((s.finished_at - s.started_at).total_seconds() for s in valid) / len(valid) / 60)

    def _avg_response_min(sl):
        valid = [s for s in sl if s.pushed_at and s.started_at and s.started_at >= s.pushed_at]
        if not valid:
            return None
        return round(sum((s.started_at - s.pushed_at).total_seconds() for s in valid) / len(valid) / 60, 1)

    def _avg_plan_delta(sl):
        valid = [s for s in sl if s.planned_drive_minutes and s.started_at and s.finished_at and not s.auto_closed]
        if not valid:
            return None
        total = sum((s.finished_at - s.started_at).total_seconds() / 60 - s.planned_drive_minutes for s in valid)
        return round(total / len(valid), 1)

    def _delta(a, b):
        return round(a - b, 1) if a is not None and b is not None else None

    # ── KPI 卡片 ─────────────────────────────────────────────
    kpi = {
        'completion_rate':       _completion_rate(sessions_now),
        'completion_rate_delta': _delta(_completion_rate(sessions_now),  _completion_rate(sessions_prev)),
        'trip_min':              _avg_trip_min(sessions_now),
        'trip_min_delta':        _delta(_avg_trip_min(sessions_now),     _avg_trip_min(sessions_prev)),
        'response_min':          _avg_response_min(sessions_now),
        'response_min_delta':    _delta(_avg_response_min(sessions_now), _avg_response_min(sessions_prev)),
        'plan_delta':            _avg_plan_delta(sessions_now),
        'plan_delta_delta':      _delta(_avg_plan_delta(sessions_now),   _avg_plan_delta(sessions_prev)),
    }

    # ── 近 30 天每日趟次（現有圖表，純 Python 計算）──────────
    days   = [(today - timedelta(days=i)) for i in range(29, -1, -1)]
    labels = [d.strftime('%m/%d').lstrip('0').replace('/0', '/') for d in days]

    day_total    = defaultdict(int)
    day_finished = defaultdict(int)
    day_closed   = defaultdict(int)
    for s in sessions_now:
        day_total[s.date] += 1
        if s.finished_at and not s.auto_closed:
            day_finished[s.date] += 1
        if s.auto_closed:
            day_closed[s.date] += 1

    trip_counts        = [day_total.get(d, 0)    for d in days]
    finish_counts      = [day_finished.get(d, 0) for d in days]
    auto_closed_counts = [day_closed.get(d, 0)   for d in days]

    # ── 各員工本月績效（現有圖表）────────────────────────────
    employees = list(Employee.objects.select_related('user').order_by('employee_id'))
    emp_labels      = []
    emp_trips       = []
    emp_stations    = []
    emp_auto_closed = []
    for emp in employees:
        emp_s = [s for s in sessions_now if s.employee_id == emp.id]
        emp_labels.append(emp.user.get_full_name() or emp.user.username)
        emp_trips.append(len(emp_s))
        emp_stations.append(station_map.get(emp.id, 0))
        emp_auto_closed.append(sum(1 for s in emp_s if s.auto_closed))

    # ── 員工 Exception 健康度表──────────────────────────────
    exception_rows = []
    for i, emp in enumerate(employees):
        emp_s = [s for s in sessions_now if s.employee_id == emp.id]
        if not emp_s:
            continue
        rate = _completion_rate(emp_s)
        exception_rows.append({
            'name':     emp_labels[i],
            'rate':     rate,
            'response': _avg_response_min(emp_s),
            'trip_min': _avg_trip_min(emp_s),
            'color':    'emerald' if (rate or 0) >= 85 else ('amber' if (rate or 0) >= 65 else 'red'),
        })
    exception_rows.sort(key=lambda x: (x['rate'] or 0))

    # ── 出發時段分布──────────────────────────────────────────
    hour_counts = defaultdict(int)
    for s in sessions_now:
        if s.started_at:
            hour_counts[ltime(s.started_at).hour] += 1
    heatmap_hours  = list(range(6, 20))
    heatmap_values = [hour_counts.get(h, 0) for h in heatmap_hours]

    return render(request, 'attendance/analytics_delivery.html', {
        # 現有圖表
        'labels':             json.dumps(labels, ensure_ascii=False),
        'trip_counts':        json.dumps(trip_counts),
        'finish_counts':      json.dumps(finish_counts),
        'auto_closed_counts': json.dumps(auto_closed_counts),
        'emp_labels':         json.dumps(emp_labels, ensure_ascii=False),
        'emp_trips':          json.dumps(emp_trips),
        'emp_stations':       json.dumps(emp_stations),
        'emp_auto_closed':    json.dumps(emp_auto_closed),
        # 新增
        'kpi':            kpi,
        'exception_rows': exception_rows,
        'heatmap_hours':  json.dumps([f'{h}時' for h in heatmap_hours], ensure_ascii=False),
        'heatmap_values': json.dumps(heatmap_values),
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
