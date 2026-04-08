from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from .models import Employee, AttendanceRecord, BindingToken


def get_today_status():
    """
    回傳今日所有員工的出勤狀態。
    回傳格式：{employee: status_string}
    status 值：'absent' | 'working' | 'break' | 'left'
    """
    employees = Employee.objects.select_related('user').all()
    today = timezone.localdate()
    status_map = {}

    # 查每位員工今天的最後一筆打卡
    for emp in employees:
        last = AttendanceRecord.objects.filter(
            employee=emp,
            timestamp__date=today  
        ).first()                  

        
        if last is None:
            status_map[emp] = 'absent'
        elif last.record_type in ('clock_in', 'break_end'):
            status_map[emp] = 'working'
        elif last.record_type == 'break_start':
            status_map[emp] = 'break'
        else:
            status_map[emp] = 'left'

    return status_map

def get_work_hours(employee):
    today = timezone.localdate()

    clock_in = AttendanceRecord.objects.filter(
        employee = employee,
        timestamp__date = today,
        record_type = 'clock_in'
    ).first()

    if clock_in is None:
        return 0
    
    clock_out = AttendanceRecord.objects.filter(
        employee = employee,
        timestamp__date = today,
        record_type = 'clock_out'
    ).first()

    end_time = clock_out.timestamp if clock_out is not None else timezone.now()
    duration = end_time - clock_in.timestamp
    hours =  duration.total_seconds() / 3600
    return round(hours,1)

@login_required
def index(request):
    today = timezone.localdate()
    status_map = get_today_status()

    counts = {
        'working': sum(1 for s in status_map.values() if s == 'working'),
        'break':   sum(1 for s in status_map.values() if s == 'break'),
        'left':    sum(1 for s in status_map.values() if s == 'left'),
        'absent':  sum(1 for s in status_map.values() if s == 'absent'),
    }

    return render(request, 'attendance/dashboard.html', {
        'status_map': status_map,
        'counts': counts,
        'today': today,
    })


@login_required
def binding_list(request):
    """顯示所有員工的綁定狀態"""
    employees = Employee.objects.select_related('user').all()

    employee_data = []
    for emp in employees:
        # 找這位員工目前最新的、未使用的綁定碼
        latest_token = BindingToken.objects.filter(
            employee=emp,
            used=False,
        ).order_by('-created_at').first()

        # 如果有找到，但已過期，就當作沒有
        if latest_token and not latest_token.is_valid_token():
            latest_token = None

        employee_data.append({
            'employee': emp,
            'is_bound': emp.line_user_id is not None,
            'latest_token': latest_token,
        })

    return render(request, 'attendance/binding.html', {
        'employee_data': employee_data,
    })


@login_required
def generate_token(request, employee_id):
    """為指定員工產生新的綁定碼"""
    if request.method != 'POST':
        return redirect('dashboard:binding_list')

    employee = get_object_or_404(Employee, pk=employee_id)

    # 建立新的 BindingToken（舊的讓它自然過期）
    token = BindingToken.objects.create(employee=employee)

    messages.success(request, f'已為【{employee}】產生綁定碼：{token.token}')
    return redirect('dashboard:binding_list')
