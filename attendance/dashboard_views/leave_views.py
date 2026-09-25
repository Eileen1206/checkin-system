from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.urls import reverse
from django.conf import settings
from datetime import datetime, date
import json
from ..models import (
    Employee, Holiday, LeaveRecord, LeaveRequest,
    LocationCorrectionRequest, ShiftOverride,
)
from ..utils import scheduling
from .base import require_group


@login_required
@require_group('admin', 'finance')
def pending_items(request):
    """待處理彙整頁：休假申請 + 座標修正申請 + 漏打卡"""
    from ..models import MissedPunch
    from ..utils import punch_check

    pending_leaves = (
        LeaveRequest.objects
        .filter(status='pending')
        .select_related('employee__user')
        .order_by('requested_at')
    )
    pending_corrections = (
        LocationCorrectionRequest.objects
        .filter(status='pending')
        .select_related('customer', 'requested_by__user')
        .order_by('requested_at')
    )

    # 漏打卡：只列當月，讓老闆補登或註銷
    today = timezone.localdate()
    missed_punches = list(
        MissedPunch.objects
        .filter(date__year=today.year, date__month=today.month, voided=False)
        .select_related('employee__user')
        .order_by('-date')
    )
    limit = punch_check.monthly_limit()
    counts = {}
    for mp in missed_punches:
        counts[mp.employee_id] = counts.get(mp.employee_id, 0) + 1
    for mp in missed_punches:
        mp.month_count = counts[mp.employee_id]
        mp.over_limit = counts[mp.employee_id] > limit

    return render(request, 'attendance/pending_items.html', {
        'pending_leaves':      pending_leaves,
        'pending_corrections': pending_corrections,
        'missed_punches':      missed_punches,
        'missed_punch_limit':  limit,
    })


@login_required
@require_group('admin')
def missed_punch_void(request, pk):
    """註銷誤判的漏打卡（不列入計次）。"""
    from ..models import MissedPunch
    mp = get_object_or_404(MissedPunch, pk=pk)
    mp.voided = True
    mp.save(update_fields=['voided'])
    name = mp.employee.user.get_full_name() or mp.employee.user.username
    messages.success(request, f'已註銷 {name} {mp.date} 的漏打卡')
    return redirect(request.META.get('HTTP_REFERER') or 'dashboard:pending_items')


@login_required
@require_group('admin')
def leave_calendar(request):
    import calendar as cal_module
    today = timezone.localdate()
    year  = int(request.GET.get('year',  today.year))
    month = int(request.GET.get('month', today.month))

    _, days_in_month = cal_module.monthrange(year, month)
    first_weekday    = cal_module.monthrange(year, month)[0]  # 0=週一

    # 建立週陣列（None 代表空格）
    weeks, week = [], [None] * first_weekday
    for day in range(1, days_in_month + 1):
        week.append(day)
        if len(week) == 7:
            weeks.append(week); week = []
    if week:
        weeks.append(week + [None] * (7 - len(week)))

    employees = Employee.tracked.select_related('user').order_by('employee_id')

    # 國定假日：月曆上見紅並標出名稱
    holiday_by_day = {
        h.date.day: (h.name or '國定假日')
        for h in Holiday.objects.filter(date__year=year, date__month=month)
    }

    leave_records = LeaveRecord.objects.filter(
        date__year=year, date__month=month
    ).select_related('employee__user')

    # 按日期分組（排休與請假一起顯示，但帶上類別讓前端分得出來）
    leave_by_day = {}
    for lr in leave_records:
        leave_by_day.setdefault(lr.date.day, []).append({
            'id':     lr.pk,
            'emp_id': lr.employee_id,
            'name':   lr.employee.user.get_full_name() or lr.employee.user.username,
            'obj':    'leave',
            'kind':   lr.kind,
            'label':  lr.short_label,
            'hours':  lr.hours,
            'leave_type': lr.leave_type,
            'type':   lr.get_leave_type_display() if lr.leave_type else '',
        })

    # 當日班別（例如只排半天）與休假一起顯示，但看得出差別
    for so in ShiftOverride.objects.filter(
            date__year=year, date__month=month).select_related('employee__user'):
        leave_by_day.setdefault(so.date.day, []).append({
            'id':     so.pk,
            'obj':    'shift',
            'emp_id': so.employee_id,
            'name':   so.employee.user.get_full_name() or so.employee.user.username,
            'kind':   'shift',
            'label':  so.label,
            'start':  so.start_time.strftime('%H:%M'),
            'end':    so.end_time.strftime('%H:%M'),
        })

    # 上個月 / 下個月導覽
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1

    # ── 一例一休排班統計 ─────────────────────────────────────────
    # 以「週一起算」的完整 7 天週計算，查詢範圍放寬到涵蓋各週的完整日期
    month_weeks = scheduling.iter_month_weeks(year, month)
    range_start = month_weeks[0]['dates'][0]
    range_end   = month_weeks[-1]['dates'][-1]

    leave_qs_range = LeaveRecord.objects.filter(
        date__gte=range_start, date__lte=range_end
    ).select_related('employee__user')

    leave_dates_by_emp = {}
    for lr in leave_qs_range:
        # 一例一休只採計「整天」：請 2 小時不算當天有休到
        if lr.is_full_day:
            leave_dates_by_emp.setdefault(lr.employee_id, set()).add(lr.date)

    # 每員工 × 每週 達標表（週日公休為例假，只看週一~週六是否排了休息日）
    required = getattr(settings, 'SCHEDULE_WEEKDAY_REST_REQUIRED', 1)
    week_compliance = []
    for emp in employees:
        emp_leaves = leave_dates_by_emp.get(emp.pk, set())
        cells, miss = [], 0
        for wk in month_weeks:
            st = scheduling.week_rest_status(wk['dates'], emp_leaves, required=required)
            if not st['compliant']:
                miss += 1
            cells.append(st)
        week_compliance.append({
            'employee': emp,
            'name': emp.user.get_full_name() or emp.user.username,
            'cells': cells,
            'miss_count': miss,
        })

    week_headers = [{
        'index': wk['index'],
        'label': f"{wk['dates'][0].month}/{wk['dates'][0].day}–{wk['dates'][6].month}/{wk['dates'][6].day}",
    } for wk in month_weeks]

    # 人力吃緊日（只看當月）
    threshold   = getattr(settings, 'SCHEDULE_MANPOWER_WARN_THRESHOLD', 2)
    month_dates = {date(year, month, d) for d in range(1, days_in_month + 1)}
    leave_pairs = [
        (lr.date, lr.employee.user.get_full_name() or lr.employee.user.username)
        for lr in leave_qs_range
    ]
    leaves_by_date  = scheduling.group_leaves_by_date(leave_pairs)
    understaffed    = scheduling.understaffed_days(leaves_by_date, threshold, only_dates=month_dates)
    _wd_labels = ['一', '二', '三', '四', '五', '六', '日']
    for item in understaffed:
        item['weekday'] = _wd_labels[item['date'].weekday()]
    understaffed_days_nums = [item['date'].day for item in understaffed]

    return render(request, 'attendance/leave_calendar.html', {
        'year': year, 'month': month,
        'weeks': weeks,
        'employees': employees,
        'leave_by_day': leave_by_day,
        'holiday_by_day': holiday_by_day,
        'leave_by_day_json': json.dumps(leave_by_day),
        'today': today,
        'prev_year': prev_year, 'prev_month': prev_month,
        'next_year': next_year, 'next_month': next_month,
        'weekday_labels': ['一', '二', '三', '四', '五', '六', '日'],
        # 一例一休統計
        'week_headers': week_headers,
        'week_compliance': week_compliance,
        'understaffed': understaffed,
        'understaffed_days_nums': understaffed_days_nums,
        'manpower_threshold': threshold,
    })


def _parse_leave_payload(data):
    """由前端資料解出 (kind, leave_type, hours)，並套用排休／請假的規則。

    排休：整天不來，沒有假別與時數。
    請假：需要時數（整天 8、半天 4、或整數小時），假別可留空。
    """
    kind = data.get('kind') or LeaveRecord.KIND_REST
    if kind not in dict(LeaveRecord.KIND_CHOICES):
        raise ValueError('類別不正確')

    if kind == LeaveRecord.KIND_REST:
        return kind, '', None

    leave_type = (data.get('leave_type') or '').strip()
    if leave_type and leave_type not in dict(LeaveRecord.LEAVE_TYPE_CHOICES):
        raise ValueError('假別不正確')

    try:
        hours = float(data.get('hours'))
    except (TypeError, ValueError):
        raise ValueError('請填寫請假時數')
    if hours <= 0:
        raise ValueError('請假時數需大於 0')
    hours = min(hours, LeaveRecord.FULL_DAY_HOURS)
    return kind, leave_type, hours


def _save_shift(emp, shift_date, data):
    """建立／修改當日班別（例如只排下半天）。"""
    try:
        start = datetime.strptime(data['start'], '%H:%M').time()
        end = datetime.strptime(data['end'], '%H:%M').time()
    except (KeyError, TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': '請填寫上下班時間'}, status=400)
    if start >= end:
        return JsonResponse({'ok': False, 'error': '下班時間必須晚於上班時間'}, status=400)

    so, created = ShiftOverride.objects.update_or_create(
        employee=emp, date=shift_date,
        defaults={'start_time': start, 'end_time': end},
    )
    return JsonResponse({
        'ok': True, 'id': so.pk, 'created': created, 'obj': 'shift',
        'name': emp.user.get_full_name() or emp.user.username,
        'kind': 'shift', 'label': so.label,
        'start': so.start_time.strftime('%H:%M'),
        'end': so.end_time.strftime('%H:%M'),
    })


@login_required
@require_group('admin')
def shift_delete_api(request, pk):
    """AJAX：刪除當日班別，回到員工預設的上下班時間"""
    so = get_object_or_404(ShiftOverride, pk=pk)
    so.delete()
    return JsonResponse({'ok': True})


@login_required
@require_group('admin')
def leave_add_api(request):
    """AJAX：新增／修改休假紀錄（排休或請假）。

    同一員工同一天只會有一筆，重複送出視為修改（老闆可隨時調整）。
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)
    data = json.loads(request.body)
    try:
        emp = Employee.objects.get(pk=data['employee_id'])
        leave_date = datetime.strptime(data['date'], '%Y-%m-%d').date()

        # 當日班別是「那天排幾點到幾點」，跟休假是兩回事，走另一張表
        if data.get('kind') == 'shift':
            return _save_shift(emp, leave_date, data)

        kind, leave_type, hours = _parse_leave_payload(data)
        reason = (data.get('reason') or '').strip()[:100]

        lr, created = LeaveRecord.objects.get_or_create(
            employee=emp, date=leave_date,
            defaults={'kind': kind, 'leave_type': leave_type,
                      'hours': hours, 'reason': reason},
        )
        if not created:
            lr.kind, lr.leave_type, lr.hours, lr.reason = kind, leave_type, hours, reason
            lr.save(update_fields=['kind', 'leave_type', 'hours', 'reason'])

        return JsonResponse({
            'ok': True, 'id': lr.pk, 'created': created,
            'name': emp.user.get_full_name() or emp.user.username,
            'kind': lr.kind,
            'label': lr.short_label,
            'hours': lr.hours,
            'leave_type': lr.leave_type,
            'type': lr.get_leave_type_display() if lr.leave_type else '',
        })
    except (Employee.DoesNotExist, ValueError, KeyError) as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)


@login_required
@require_group('admin')
def leave_delete_api(request, pk):
    """AJAX：刪除請假紀錄"""
    lr = get_object_or_404(LeaveRecord, pk=pk)
    lr.delete()
    return JsonResponse({'ok': True})


@login_required
@require_group('admin')
def leave_add(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    if request.method == 'POST':
        date_str = request.POST.get('date', '')
        reason = request.POST.get('reason', '').strip()
        try:
            leave_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            messages.error(request, '日期格式錯誤')
            return redirect('dashboard:employee_edit', pk=pk)
        LeaveRecord.objects.get_or_create(
            employee=emp,
            date=leave_date,
            defaults={'kind': LeaveRecord.KIND_REST, 'reason': reason},
        )
    return redirect('dashboard:employee_edit', pk=pk)


@login_required
@require_group('admin')
def leave_delete(request, pk):
    lr = get_object_or_404(LeaveRecord, pk=pk)
    emp_pk = lr.employee_id
    lr.delete()
    next_url = request.GET.get('next') or reverse('dashboard:employee_edit', args=[emp_pk])
    return redirect(next_url)


@login_required
@require_group('admin')
def leave_request_list(request):
    """休假申請審核列表（排休與請假一起列，看得出差別）"""
    pending = LeaveRequest.objects.filter(status='pending').select_related('employee__user')
    recent  = LeaveRequest.objects.exclude(status='pending').select_related('employee__user')[:30]
    return render(request, 'attendance/leave_requests.html', {
        'pending': pending,
        'recent':  recent,
    })


def _process_leave_request(request, pk, approved):
    """核准／拒絕休假申請，核准時依申請的類別寫入休假紀錄。"""
    from .. import line_leave

    leave_req = get_object_or_404(LeaveRequest, pk=pk)
    emp = leave_req.employee
    emp_name = emp.user.get_full_name() or emp.user.username
    kind_label = leave_req.get_kind_display()

    if leave_req.status == 'pending':
        leave_req.status = 'approved' if approved else 'denied'
        leave_req.processed_at = timezone.now()
        leave_req.save()
        if approved:
            leave_req.apply_to_records()
        line_leave.notify_employee(leave_req, approved)
        if approved:
            messages.success(request, f'已核准 {emp_name} 的{kind_label}申請')
        else:
            messages.warning(request, f'已拒絕 {emp_name} 的{kind_label}申請')
    return redirect('dashboard:leave_request_list')


@login_required
@require_group('admin')
def leave_request_approve(request, pk):
    """核准休假申請"""
    return _process_leave_request(request, pk, approved=True)


@login_required
@require_group('admin')
def leave_request_deny(request, pk):
    """拒絕休假申請"""
    return _process_leave_request(request, pk, approved=False)
