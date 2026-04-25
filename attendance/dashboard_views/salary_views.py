from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.utils import timezone
from django.urls import reverse
import openpyxl
from ..models import Employee, AttendanceRecord, MonthlyAllowance
from .base import require_group, get_work_hours, calculate_salary


@login_required
@require_group('admin', 'finance')
def salary(request):
    # 預設查當月
    year = int(request.GET.get('year', timezone.localdate().year))
    month = int(request.GET.get('month', timezone.localdate().month))

    employees = Employee.objects.select_related('user').all()
    results = []

    for emp in employees:
        result = calculate_salary(emp, year, month)

        if emp.employment_type == 'monthly':
            result['detail'] = f'月薪制：${int(result["base"]):,}'
        else:
            records = AttendanceRecord.objects.filter(
                employee=emp, timestamp__year=year, timestamp__month=month)
            total_hours = sum(
                get_work_hours(emp, d)
                for d in records.filter(record_type='clock_in').dates('timestamp', 'day'))
            result['detail'] = f'時薪 ${float(emp.hourly_rate):.0f} × {total_hours:.1f}小時 = ${int(result["base"]):,}\n保養費：${int(result["maintenance"]):,}\n勞健保扣除：-${int(result["deduction"]):,}'
        results.append(result)

    return render(request, 'attendance/salary.html', {
        'results': results,
        'year': year,
        'years': range(timezone.localdate().year, timezone.localdate().year - 3, -1),
        'month': month,
        'months': range(1, 13)
    })


@login_required
@require_group('admin', 'finance')
def add_allowance(request):
    employee_id = request.POST.get('employee_id')
    year = int(request.POST.get('year', timezone.localdate().year))
    month = int(request.POST.get('month', timezone.localdate().month))
    amount = request.POST.get('amount')
    note = request.POST.get('note')

    emp = Employee.objects.get(pk=employee_id)

    MonthlyAllowance.objects.update_or_create(employee=emp, year=year, month=month, defaults={'amount': amount, 'note': note})
    return redirect(f"{reverse('dashboard:salary')}?year={year}&month={month}")


@login_required
@require_group('admin', 'finance')
def export_salary_excel(request):
    year = int(request.GET.get('year', timezone.localdate().year))
    month = int(request.GET.get('month', timezone.localdate().month))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"{year}-{month:02d} 薪資表"

    # 表頭
    ws.append(['工號', '姓名', '部門', '底薪', '保養費', '勞務加給', '勞健保扣除', '實領'])

    employees = Employee.objects.select_related('user').order_by('employee_id')
    for emp in employees:
        result = calculate_salary(emp, year, month)
        ws.append([
            emp.employee_id,
            emp.user.get_full_name() or emp.user.username,
            emp.department,
            float(result['base']),
            float(result['maintenance']),
            float(result['allowance']),
            float(result['deduction']),
            float(result['total']),
        ])

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="salary_{year}_{month:02d}.xlsx"'
    wb.save(response)
    return response
