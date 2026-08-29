from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.utils import timezone
from django.urls import reverse
import openpyxl
from ..models import Employee, AttendanceRecord, MonthlyAllowance
from ..utils import payroll
from .base import require_group, get_work_hours, calculate_salary


def _salary_row(emp, year, month):
    """計算單一員工當月薪資（含顯示用明細）。"""
    result = calculate_salary(emp, year, month)

    if emp.employment_type == 'monthly':
        result['detail'] = f'月薪制：${int(result["base"]):,}'
    else:
        records = AttendanceRecord.objects.filter(
            employee=emp, timestamp__year=year, timestamp__month=month)
        days = list(records.filter(record_type='clock_in').dates('timestamp', 'day'))
        day_hours = [(d, get_work_hours(emp, d)) for d in days]
        total_hours = sum(h for _, h in day_hours)
        hourly = float(emp.hourly_rate) if emp.hourly_rate else 0
        normal_hours = result.get('normal_hours', 0)

        # 底薪只算正常工時（加班另計全額加班費），total 已由 calculate_salary 算好
        day_detail = '\n'.join(f'  {d} → {h}h' for d, h in day_hours)
        result['detail'] = (
            f'時薪 ${hourly:.0f}｜正常工時 {normal_hours:.1f}h = ${result["base"]:,.0f}\n'
            f'加班費（全額）：${result.get("overtime", 0):,.0f}\n'
            f'保養費：${result["maintenance"]:,.0f}\n'
            f'勞健保扣除：-${result["deduction"]:,.0f}\n'
            f'--- 每日工時 ---\n{day_detail}'
        )
        result['day_hours'] = day_hours
        result['total_hours'] = total_hours
        result['hourly'] = hourly
    return result


def _salary_employees(show_inactive):
    emp_qs = Employee.objects if show_inactive else Employee.tracked
    return emp_qs.select_related('user').all()


@login_required
@require_group('admin', 'finance')
def salary(request):
    """薪資頁：先秒開並顯示進度條，實際計算由 salary_calc_api 逐位回傳。"""
    year = int(request.GET.get('year', timezone.localdate().year))
    month = int(request.GET.get('month', timezone.localdate().month))
    show_inactive = request.GET.get('show_inactive') == '1'

    employees = _salary_employees(show_inactive)
    return render(request, 'attendance/salary.html', {
        'employees': employees,
        'employee_count': employees.count(),
        'year': year,
        'years': range(timezone.localdate().year, timezone.localdate().year - 3, -1),
        'month': month,
        'months': range(1, 13),
        'show_inactive': show_inactive,
        'inactive_count': Employee.objects.filter(is_active=False).count(),
    })


@login_required
@require_group('admin', 'finance')
def salary_calc_api(request):
    """AJAX：計算單一員工當月薪資，供薪資頁逐位載入並更新進度條。"""
    from django.http import JsonResponse
    try:
        year = int(request.GET.get('year'))
        month = int(request.GET.get('month'))
        emp = Employee.objects.select_related('user').get(pk=request.GET.get('employee_id'))
    except (TypeError, ValueError, Employee.DoesNotExist):
        return JsonResponse({'ok': False, 'error': '參數錯誤'}, status=400)

    r = _salary_row(emp, year, month)
    return JsonResponse({
        'ok': True,
        'employee_id': emp.pk,
        'name': emp.user.get_full_name() or emp.user.username,
        'base': round(r['base']),
        'maintenance': round(r['maintenance']),
        'allowance': round(r['allowance']),
        'overtime': round(r['overtime']),
        'deduction': round(r['deduction']),
        'total': round(r['total']),
        'overtime_detail': [
            {'date': str(x['date']), 'cls': x['cls'], 'hours': x['hours'], 'amount': x['amount']}
            for x in r.get('overtime_detail', [])
        ],
        'day_hours': [{'date': str(d), 'hours': h} for d, h in r.get('day_hours', [])],
        'hourly': r.get('hourly', 0),
        'total_hours': r.get('total_hours', 0),
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
    MonthlyAllowance.objects.update_or_create(
        employee=emp, year=year, month=month,
        defaults={'amount': amount, 'note': note}
    )
    return redirect(f"{reverse('dashboard:salary')}?year={year}&month={month}")


@login_required
@require_group('admin', 'finance')
def salary_detail(request, pk):
    """單一員工的薪資詳細計算報表（含加班分級時數）。"""
    emp = get_object_or_404(Employee, pk=pk)
    year = int(request.GET.get('year', timezone.localdate().year))
    month = int(request.GET.get('month', timezone.localdate().month))

    result = calculate_salary(emp, year, month)

    # 時薪制：補上每日工時明細（與薪資頁一致）
    day_hours, total_hours = [], 0
    if emp.employment_type == 'hourly':
        records = AttendanceRecord.objects.filter(
            employee=emp, timestamp__year=year, timestamp__month=month)
        days = list(records.filter(record_type='clock_in').dates('timestamp', 'day'))
        day_hours = [(d, get_work_hours(emp, d)) for d in days]
        total_hours = sum(h for _, h in day_hours)

    def _hm(x):
        h = int(x)
        return {'h': h, 'm': int(round((x - h) * 60))}

    t = result['overtime_tiers']
    weekday_tiers = [
        {'label': '第 1-2 小時', **_hm(t['weekday_1_2'])},
        {'label': '第 3 小時以上', **_hm(t['weekday_3plus'])},
    ]
    restday_tiers = [
        {'label': '第 1-2 小時', **_hm(t['restday_1_2'])},
        {'label': '第 3-8 小時', **_hm(t['restday_3_8'])},
        {'label': '第 9-12 小時', **_hm(t['restday_9_12'])},
    ]

    return render(request, 'attendance/salary_detail.html', {
        'emp': emp, 'year': year, 'month': month,
        'result': result,
        'weekday_tiers': weekday_tiers,
        'restday_tiers': restday_tiers,
        'holiday_hm': _hm(t['holiday']),
        'overtime_detail': result['overtime_detail'],
        'day_hours': day_hours, 'total_hours': total_hours,
        'hourly': float(emp.hourly_rate or 0),
        'hourly_wage': round(payroll.hourly_wage(emp), 2),
        'daily_wage': round(payroll.daily_wage(emp)),
        'is_monthly': emp.employment_type == 'monthly',
        'months': range(1, 13),
        'years': range(timezone.localdate().year, timezone.localdate().year - 3, -1),
    })


@login_required
@require_group('admin', 'finance')
def export_salary_excel(request):
    year = int(request.GET.get('year', timezone.localdate().year))
    month = int(request.GET.get('month', timezone.localdate().month))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"{year}-{month:02d} 薪資表"

    ws.append(['工號', '姓名', '部門', '底薪', '保養費', '勞務加給', '加班費', '勞健保扣除', '實領'])

    emp_qs = Employee.objects if request.GET.get('show_inactive') == '1' else Employee.tracked
    employees = emp_qs.select_related('user').order_by('employee_id')
    for emp in employees:
        result = calculate_salary(emp, year, month)
        ws.append([
            emp.employee_id,
            emp.user.get_full_name() or emp.user.username,
            emp.department,
            float(result['base']),
            float(result['maintenance']),
            float(result['allowance']),
            float(result.get('overtime', 0)),
            float(result['deduction']),
            float(result['total']),
        ])

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="salary_{year}_{month:02d}.xlsx"'
    wb.save(response)
    return response
