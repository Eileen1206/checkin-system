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
    """出勤分析：遲到頻率、每月工時趨勢"""
    months = _last_n_months(6)
    labels = [f"{y}/{m:02d}" for y, m in months]
    employees = Employee.objects.select_related('user').order_by('employee_id')

    # 每月遲到次數（全員合計）
    late_data = []
    absent_data = []
    for y, m in months:
        late_count = 0
        absent_count = 0
        for emp in employees:
            import calendar
            _, days = calendar.monthrange(y, m)
            for day in range(1, days + 1):
                d = date(y, m, day)
                if d.weekday() >= 5:
                    continue
                if d > timezone.localdate():
                    break
                ci = AttendanceRecord.objects.filter(
                    employee=emp, timestamp__date=d, record_type='clock_in'
                ).first()
                if ci is None:
                    absent_count += 1
                elif emp.work_start_time:
                    from django.utils.timezone import localtime
                    from datetime import datetime
                    ci_time = localtime(ci.timestamp).time()
                    scheduled = datetime.combine(d, emp.work_start_time)
                    actual = datetime.combine(d, ci_time)
                    if (actual - scheduled).total_seconds() > 600:
                        late_count += 1
        late_data.append(late_count)
        absent_data.append(absent_count)

    # 各員工本月工時（長條圖）
    today = timezone.localdate()
    from .base import get_work_hours
    import calendar as cal
    _, days_in_month = cal.monthrange(today.year, today.month)
    emp_labels = []
    emp_hours = []
    for emp in employees:
        total = 0.0
        for day in range(1, today.day + 1):
            d = date(today.year, today.month, day)
            if d.weekday() >= 5:
                continue
            total += get_work_hours(emp, d)
        emp_labels.append(emp.user.get_full_name() or emp.user.username)
        emp_hours.append(round(total, 1))

    return render(request, 'attendance/analytics_attendance.html', {
        'labels':      json.dumps(labels, ensure_ascii=False),
        'late_data':   json.dumps(late_data),
        'absent_data': json.dumps(absent_data),
        'emp_labels':  json.dumps(emp_labels, ensure_ascii=False),
        'emp_hours':   json.dumps(emp_hours),
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

    # 各員工本月送貨趟數
    emp_labels    = []
    emp_trips     = []
    emp_stations  = []
    employees = Employee.objects.select_related('user').order_by('employee_id')
    for emp in employees:
        trips    = DeliverySession.objects.filter(employee=emp, date__year=today.year, date__month=today.month).count()
        stations = DeliveryTask.objects.filter(employee=emp, date__year=today.year, date__month=today.month, status='completed').count()
        emp_labels.append(emp.user.get_full_name() or emp.user.username)
        emp_trips.append(trips)
        emp_stations.append(stations)

    return render(request, 'attendance/analytics_delivery.html', {
        'labels':        json.dumps(labels, ensure_ascii=False),
        'trip_counts':   json.dumps(trip_counts),
        'finish_counts': json.dumps(finish_counts),
        'emp_labels':    json.dumps(emp_labels, ensure_ascii=False),
        'emp_trips':     json.dumps(emp_trips),
        'emp_stations':  json.dumps(emp_stations),
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
