from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.urls import reverse
from datetime import datetime
import json
from ..models import Employee, LeaveRecord, LeaveRequest
from .base import require_group


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

    employees = Employee.objects.select_related('user').order_by('employee_id')

    leave_records = LeaveRecord.objects.filter(
        date__year=year, date__month=month
    ).select_related('employee__user')

    # 按日期分組
    leave_by_day = {}
    for lr in leave_records:
        leave_by_day.setdefault(lr.date.day, []).append({
            'id':   lr.pk,
            'emp_id': lr.employee_id,
            'name': lr.employee.user.get_full_name() or lr.employee.user.username,
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

    return render(request, 'attendance/leave_calendar.html', {
        'year': year, 'month': month,
        'weeks': weeks,
        'employees': employees,
        'leave_by_day': leave_by_day,
        'leave_by_day_json': json.dumps(leave_by_day),
        'today': today,
        'prev_year': prev_year, 'prev_month': prev_month,
        'next_year': next_year, 'next_month': next_month,
        'weekday_labels': ['一', '二', '三', '四', '五', '六', '日'],
    })


@login_required
@require_group('admin')
def leave_add_api(request):
    """AJAX：新增請假紀錄"""
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)
    data = json.loads(request.body)
    try:
        emp = Employee.objects.get(pk=data['employee_id'])
        leave_date = datetime.strptime(data['date'], '%Y-%m-%d').date()
        lr, created = LeaveRecord.objects.get_or_create(employee=emp, date=leave_date)
        return JsonResponse({
            'ok': True, 'id': lr.pk, 'created': created,
            'name': emp.user.get_full_name() or emp.user.username,
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
            defaults={'reason': reason},
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
    """請假申請審核列表"""
    pending = LeaveRequest.objects.filter(status='pending').select_related('employee__user')
    recent  = LeaveRequest.objects.exclude(status='pending').select_related('employee__user')[:30]
    return render(request, 'attendance/leave_requests.html', {
        'pending': pending,
        'recent':  recent,
    })


@login_required
@require_group('admin')
def leave_request_approve(request, pk):
    """核准請假申請"""
    leave_req = get_object_or_404(LeaveRequest, pk=pk)
    if leave_req.status == 'pending':
        leave_req.status = 'approved'
        leave_req.processed_at = timezone.now()
        leave_req.save()
        emp = leave_req.employee
        for d in leave_req.dates:
            LeaveRecord.objects.get_or_create(employee=emp, date=d)
        # 通知員工 LINE
        from django.conf import settings
        from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, PushMessageRequest, TextMessage
        if emp.line_user_id:
            dates_display = '\n'.join(leave_req.dates)
            try:
                cfg = Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)
                with ApiClient(cfg) as api_client:
                    MessagingApi(api_client).push_message(PushMessageRequest(
                        to=emp.line_user_id,
                        messages=[TextMessage(text=f'✅ 以下請假申請已核准：\n{dates_display}')]
                    ))
            except Exception:
                pass
        messages.success(request, f'已核准 {emp.user.get_full_name() or emp.user.username} 的請假申請')
    return redirect('dashboard:leave_request_list')


@login_required
@require_group('admin')
def leave_request_deny(request, pk):
    """拒絕請假申請"""
    leave_req = get_object_or_404(LeaveRequest, pk=pk)
    if leave_req.status == 'pending':
        leave_req.status = 'denied'
        leave_req.processed_at = timezone.now()
        leave_req.save()
        emp = leave_req.employee
        if emp.line_user_id:
            dates_display = '\n'.join(leave_req.dates)
            from django.conf import settings
            from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, PushMessageRequest, TextMessage
            try:
                cfg = Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)
                with ApiClient(cfg) as api_client:
                    MessagingApi(api_client).push_message(PushMessageRequest(
                        to=emp.line_user_id,
                        messages=[TextMessage(text=f'❌ 以下請假申請已被拒絕：\n{dates_display}')]
                    ))
            except Exception:
                pass
        messages.warning(request, f'已拒絕 {emp.user.get_full_name() or emp.user.username} 的請假申請')
    return redirect('dashboard:leave_request_list')
