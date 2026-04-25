from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from ..models import Employee, BindingToken, User
from .base import require_group, WORK_DAY_CHOICES


@login_required
@require_group('admin', 'finance')
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
        'line_bot_basic_id': settings.LINE_BOT_BASIC_ID,
    })


@login_required
@require_group('admin', 'finance')
def generate_token(request, employee_id):
    """為指定員工產生新的綁定碼"""
    if request.method != 'POST':
        return redirect('dashboard:binding_list')

    employee = get_object_or_404(Employee, pk=employee_id)

    # 建立新的 BindingToken（舊的讓它自然過期）
    token = BindingToken.objects.create(employee=employee)

    messages.success(request, f'已為【{employee}】產生綁定碼：{token.token}')
    return redirect('dashboard:binding_list')


@login_required
@require_group('admin', 'finance')
def employee_list(request):
    employees = Employee.objects.select_related('user').order_by('employee_id')
    return render(request, 'attendance/employee_list.html', {'employees': employees})


@login_required
@require_group('admin', 'finance')
def employee_add(request):
    if request.method == 'POST':
        # 從表單取得資料
        username = request.POST.get('username')
        password = request.POST.get('password')
        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')
        employee_id = request.POST.get('employee_id')
        department = request.POST.get('department')
        employment_type = request.POST.get('employment_type')
        monthly_salary = request.POST.get('monthly_salary') or None
        hourly_rate = request.POST.get('hourly_rate') or None
        work_start_time = request.POST.get('work_start_time') or None
        work_end_time = request.POST.get('work_end_time') or None
        is_delivery = request.POST.get('is_delivery') == 'on'
        fuel_daily_allowance = request.POST.get('fuel_daily_allowance') or 0
        labor_insurance_amount = request.POST.get('labor_insurance_amount') or None
        health_insurance_amount = request.POST.get('health_insurance_amount') or None

        # 建立系統user
        if User.objects.filter(username=username).exists():
            messages.error(request, '此帳號已存在')
            return render(request, 'attendance/employee_add.html')

        if Employee.objects.filter(employee_id=employee_id).exists():
            messages.error(request, '此工號已存在')
            return render(request, 'attendance/employee_add.html')

        user = User.objects.create_user(
            username=username,
            password=password,
            first_name=first_name,
            last_name=last_name,
        )

        # 建立員工
        Employee.objects.create(
            user=user,
            employee_id=employee_id,
            department=department,
            employment_type=employment_type,
            monthly_salary=monthly_salary,
            hourly_rate=hourly_rate,
            work_start_time=work_start_time,
            work_end_time=work_end_time,
            is_delivery=is_delivery,
            fuel_daily_allowance=fuel_daily_allowance,
            labor_insurance_amount=labor_insurance_amount,
            health_insurance_amount=health_insurance_amount,
        )
        return redirect('dashboard:employee_list')
    return render(request, 'attendance/employee_add.html')


@login_required
@require_group('admin', 'finance')
def employee_edit(request, pk):
    emp = get_object_or_404(Employee.objects.select_related('user'), pk=pk)

    if request.method == 'POST':
        # 更新 User
        emp.user.first_name = request.POST.get('first_name')
        emp.user.last_name = request.POST.get('last_name')
        emp.user.save()

        # 更新 Employee
        emp.department = request.POST.get('department')
        emp.employment_type = request.POST.get('employment_type')
        emp.monthly_salary = request.POST.get('monthly_salary') or None
        emp.hourly_rate = request.POST.get('hourly_rate') or None
        emp.work_start_time = request.POST.get('work_start_time') or None
        emp.work_end_time = request.POST.get('work_end_time') or None
        emp.is_delivery = request.POST.get('is_delivery') == 'on'
        emp.fuel_daily_allowance = request.POST.get('fuel_daily_allowance') or 0
        emp.labor_insurance_amount = request.POST.get('labor_insurance_amount') or None
        emp.health_insurance_amount = request.POST.get('health_insurance_amount') or None
        emp.remind_enabled = request.POST.get('remind_enabled') == 'on'
        selected_days = request.POST.getlist('work_days')
        emp.work_days = ','.join(selected_days) if selected_days else ''
        emp.save()

        return redirect('dashboard:employee_list')

    emp_work_days = [d.strip() for d in emp.work_days.split(',') if d.strip()]
    leave_records = emp.leave_records.all()
    return render(request, 'attendance/employee_edit.html', {
        'emp': emp,
        'work_day_choices': WORK_DAY_CHOICES,
        'emp_work_days': emp_work_days,
        'leave_records': leave_records,
    })
