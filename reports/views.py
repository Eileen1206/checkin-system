import calendar
import csv
from datetime import date, datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.utils.timezone import localtime
from django.views.decorators.http import require_POST

from attendance.models import AttendanceRecord, Employee, Holiday, LeaveRecord


def _holiday_map(year, month):
    """{date: 假日名稱}，一個月查一次就好。"""
    return {
        h.date: (h.name or '國定假日')
        for h in Holiday.objects.filter(date__year=year, date__month=month)
    }


def _build_day(employee, d, holidays=None):
    """回傳某天的出勤資料 dict。holidays 可先用 _holiday_map 準備好，避免逐日查詢。"""
    records = AttendanceRecord.objects.filter(
        employee=employee, timestamp__date=d
    ).order_by('timestamp')

    clock_in    = records.filter(record_type='clock_in').first()
    clock_out   = records.filter(record_type='clock_out').first()
    break_start = records.filter(record_type='break_start').first()
    break_end   = records.filter(record_type='break_end').first()

    is_weekend = d.weekday() >= 5

    # 國定假日（見紅；沒打卡也不算缺勤，有打卡則工資加倍）
    if holidays is None:
        holiday = Holiday.objects.filter(date=d).first()
        holiday_name = (holiday.name or '國定假日') if holiday else None
    else:
        holiday_name = holidays.get(d)
    is_holiday = holiday_name is not None

    # 當天的排休／請假（整天不來者會蓋掉「缺勤」判定）
    leave = LeaveRecord.objects.filter(employee=employee, date=d).first()
    leave_kind   = leave.kind if leave else None
    leave_label  = leave.short_label if leave else None
    leave_hours  = (leave.hours or 0) if (leave and not leave.is_rest) else 0
    leave_is_full = leave.is_full_day if leave else False

    # 判斷狀態
    today = timezone.localdate()
    if clock_in:
        is_late = False
        if employee.work_start_time:
            ci_time   = localtime(clock_in.timestamp).time()
            scheduled = datetime.combine(d, employee.work_start_time)
            actual    = datetime.combine(d, ci_time)
            is_late   = (actual - scheduled).total_seconds() > 600  # 超過10分鐘

        # 異常：已過去的天、有上班但沒下班
        if d < today and not clock_out:
            status = 'missing_clockout'
        # 異常：午休開始但沒結束
        elif d < today and break_start and not break_end:
            status = 'missing_breakend'
        else:
            status = 'late' if is_late else 'normal'
    elif leave_is_full:
        # 整天排休／請假 → 不是缺勤
        status = 'rest' if leave_kind == LeaveRecord.KIND_REST else 'leave'
    elif is_holiday:
        # 國定假日沒來上班是正常的 → 不是缺勤
        status = 'holiday'
    elif is_weekend:
        status = 'weekend'
    else:
        status = 'absent'

    # 計算工時（扣午休）
    hours = 0.0
    if clock_in and clock_out:
        total = (clock_out.timestamp - clock_in.timestamp).total_seconds()
        if break_start and break_end:
            total -= (break_end.timestamp - break_start.timestamp).total_seconds()
        hours = round(total / 3600, 1)

    # 打卡時間（本地時間字串）
    def fmt(record):
        return localtime(record.timestamp).strftime('%H:%M') if record else None

    return {
        'date':           d,
        'weekday':        d.weekday(),
        'is_weekend':     is_weekend,
        'clock_in':       fmt(clock_in),
        'clock_out':      fmt(clock_out),
        'break_start':    fmt(break_start),
        'break_end':      fmt(break_end),
        'clock_in_id':    clock_in.pk    if clock_in    else None,
        'clock_out_id':   clock_out.pk   if clock_out   else None,
        'break_start_id': break_start.pk if break_start else None,
        'break_end_id':   break_end.pk   if break_end   else None,
        'status':         status,
        'hours':          hours,
        'is_today':       d == timezone.localdate(),
        # 休假資訊（排休／請假一起帶出，報表只顯示「休假／請假」不露原因）
        'leave_kind':     leave_kind,
        'leave_label':    leave_label,
        'leave_hours':    leave_hours,
        'leave_is_full':  leave_is_full,
        # 國定假日（見紅；當天有出勤代表工資加倍）
        'is_holiday':     is_holiday,
        'holiday_name':   holiday_name,
        'holiday_worked': bool(is_holiday and clock_in),
    }


@login_required
def report(request):
    today    = timezone.localdate()
    year     = int(request.GET.get('year',  today.year))
    month    = int(request.GET.get('month', today.month))
    emp_id   = request.GET.get('employee_id')

    employees = Employee.tracked.select_related('user').order_by('employee_id')

    # 選定員工
    selected = None
    if emp_id:
        selected = employees.filter(pk=emp_id).first()
    if not selected and employees.exists():
        selected = employees.first()

    month_data      = []
    calendar_weeks  = []
    stats           = {}

    if selected:
        _, days_in_month = calendar.monthrange(year, month)
        holidays = _holiday_map(year, month)
        month_data = [_build_day(selected, date(year, month, d), holidays)
                      for d in range(1, days_in_month + 1)]

        # 統計（排休／請假不列入缺勤，另計休假天數）
        worked_days = [d for d in month_data if d['status'] in ('normal', 'late', 'missing_clockout', 'missing_breakend')]
        stats = {
            'worked':    len(worked_days),
            'absent':    sum(1 for d in month_data if d['status'] == 'absent'),
            'late':      sum(1 for d in month_data if d['status'] == 'late'),
            'anomaly':   sum(1 for d in month_data if d['status'] in ('missing_clockout', 'missing_breakend')),
            'rest':      sum(1 for d in month_data if d['status'] == 'rest'),
            'leave':     sum(1 for d in month_data if d['status'] == 'leave'),
            'off_total': sum(1 for d in month_data if d['status'] in ('rest', 'leave')),
            'holiday':        sum(1 for d in month_data if d['status'] == 'holiday'),
            'holiday_worked': sum(1 for d in month_data if d['holiday_worked']),
            # 部分時數請假（當天仍有出勤）累計時數
            'partial_leave_hours': round(
                sum(d['leave_hours'] for d in month_data
                    if d['leave_hours'] and not d['leave_is_full']), 1),
            'total_hours': round(sum(d['hours'] for d in month_data), 1),
        }

        # 月曆格子（週一為第一欄）
        first_wd = month_data[0]['weekday']  # 0=Mon
        week = [None] * first_wd
        for day in month_data:
            week.append(day)
            if len(week) == 7:
                calendar_weeks.append(week)
                week = []
        if week:
            calendar_weeks.append(week + [None] * (7 - len(week)))

    # 上下月導覽
    prev_dt = date(year, month, 1) - timedelta(days=1)
    next_dt = date(year, month, days_in_month if month_data else 28) + timedelta(days=1)

    is_admin = request.user.is_superuser or request.user.groups.filter(name__in=['admin', 'finance']).exists()

    return render(request, 'reports/report.html', {
        'employees':       employees,
        'selected':        selected,
        'year':            year,
        'month':           month,
        'month_data':      month_data,
        'calendar_weeks':  calendar_weeks,
        'stats':           stats,
        'prev_year':       prev_dt.year,
        'prev_month':      prev_dt.month,
        'next_year':       next_dt.year,
        'next_month':      next_dt.month,
        'today':           today,
        'weekday_names':   ['一', '二', '三', '四', '五', '六', '日'],
        'is_admin':        is_admin,
    })


@login_required
def export_attendance_csv(request):
    """
    匯出出勤記錄 CSV（勞基法 5 年保存備份用）。
    GET 參數：
      employee_id  — 指定員工（留空 = 全員）
      year_from / month_from  — 起始年月（預設當年1月）
      year_to   / month_to    — 結束年月（預設當月）
    格式：員工工號, 姓名, 部門, 日期, 星期, 上班, 下班, 午休開始, 午休結束, 工時(h), 狀態, 請假類型
    """
    is_admin = (
        request.user.is_superuser or
        request.user.groups.filter(name__in=['admin', 'finance']).exists()
    )
    if not is_admin:
        return HttpResponse('無權限', status=403)

    today = timezone.localdate()

    # ── 參數解析 ──────────────────────────────────────────
    try:
        year_from  = int(request.GET.get('year_from',  today.year))
        month_from = int(request.GET.get('month_from', 1))
        year_to    = int(request.GET.get('year_to',    today.year))
        month_to   = int(request.GET.get('month_to',   today.month))
    except ValueError:
        return HttpResponse('日期參數錯誤', status=400)

    employee_id = request.GET.get('employee_id', '').strip()
    employees = Employee.tracked.select_related('user').order_by('employee_id')
    if employee_id:
        employees = employees.filter(pk=employee_id)

    # ── 建立日期範圍（月份清單）──────────────────────────
    def iter_months(y_from, m_from, y_to, m_to):
        y, m = y_from, m_from
        while (y, m) <= (y_to, m_to):
            yield y, m
            m += 1
            if m > 12:
                m, y = 1, y + 1

    WEEKDAY_NAMES = ['一', '二', '三', '四', '五', '六', '日']
    STATUS_LABELS = {
        'normal':           '正常',
        'late':             '遲到',
        'absent':           '缺勤',
        'missing_clockout': '缺下班打卡',
        'missing_breakend': '缺午休結束',
        'weekend':          '假日',
        'rest':             '休假',
        'leave':            '請假',
        'holiday':          '國定假日',
    }

    # ── 建立 CSV 回應 ──────────────────────────────────────
    filename = f'attendance_{year_from}{month_from:02d}-{year_to}{month_to:02d}.csv'
    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow([
        '工號', '姓名', '部門',
        '日期', '星期',
        '上班打卡', '下班打卡', '午休開始', '午休結束',
        '工時(h)', '狀態', '休假', '請假時數', '國定假日',
    ])

    for emp in employees:
        name = emp.user.get_full_name() or emp.user.username

        for year, month in iter_months(year_from, month_from, year_to, month_to):
            _, days_in_month = calendar.monthrange(year, month)
            holidays = _holiday_map(year, month)

            for day_num in range(1, days_in_month + 1):
                d = date(year, month, day_num)
                if d > today:
                    break   # 未來日期不輸出

                day_data = _build_day(emp, d, holidays)

                # 休假欄：排休／請假（不輸出假別與原因）
                if day_data['leave_kind'] == LeaveRecord.KIND_REST:
                    leave_col = '休假'
                elif day_data['leave_kind'] == LeaveRecord.KIND_LEAVE:
                    leave_col = '請假'
                else:
                    leave_col = '—'
                leave_hours_col = day_data['leave_hours'] or ''

                writer.writerow([
                    emp.employee_id,
                    name,
                    emp.department,
                    d.strftime('%Y-%m-%d'),
                    WEEKDAY_NAMES[d.weekday()],
                    day_data.get('clock_in')    or '',
                    day_data.get('clock_out')   or '',
                    day_data.get('break_start') or '',
                    day_data.get('break_end')   or '',
                    day_data.get('hours')       or '',
                    STATUS_LABELS.get(day_data['status'], day_data['status']),
                    leave_col,
                    leave_hours_col,
                    day_data['holiday_name'] or '',
                ])

    return response


@login_required
@require_POST
def edit_record(request, pk):
    """管理員修改打卡時間（AJAX）"""
    if not (request.user.is_superuser or request.user.groups.filter(name__in=['admin', 'finance']).exists()):
        return JsonResponse({'ok': False, 'error': '無權限'}, status=403)

    record = get_object_or_404(AttendanceRecord, pk=pk)
    time_str = request.POST.get('time', '').strip()  # HH:MM

    try:
        local_dt = localtime(record.timestamp)
        naive_new = datetime.combine(local_dt.date(), datetime.strptime(time_str, '%H:%M').time())
        record.timestamp = timezone.make_aware(naive_new)
        record.save(update_fields=['timestamp'])
        return JsonResponse({'ok': True, 'new_time': time_str})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)})
