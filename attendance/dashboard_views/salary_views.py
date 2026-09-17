from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.utils import timezone
from django.urls import reverse
import openpyxl
from ..models import Employee, MonthlyAllowance
from ..utils import payroll
from .base import require_group, calculate_salary


def _salary_row(emp, year, month):
    """計算單一員工當月薪資（含顯示用明細）。"""
    result = calculate_salary(emp, year, month)

    if emp.employment_type == 'monthly':
        result['detail'] = f'月薪制：${int(result["base"]):,}'
    else:
        hourly = float(emp.hourly_rate) if emp.hourly_rate else 0
        day_lines = '\n'.join(
            f'  {x["date"]} {x["hm"]} → ${x["amount"]:,}' for x in result['day_detail']
        )
        result['detail'] = (
            f'時薪 ${hourly:.0f}｜正常工時 {result["work_hm"]} 中的 '
            f'{result["normal_hours"]:.2f}h = ${result["base"]:,.0f}\n'
            f'加班費（全額）：${result.get("overtime", 0):,.0f}\n'
            f'保養費：${result["maintenance"]:,.0f}\n'
            f'勞健保扣除：-${result["deduction"]:,.0f}\n'
            f'--- 每日工時（分鐘制，逐日四捨五入）---\n{day_lines}'
        )
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
            {'date': str(x['date']), 'cls': x['cls'], 'hours': x['hours'],
             'hm': x['hm'], 'amount': x['amount']}
            for x in r.get('overtime_detail', [])
        ],
        'day_hours': [
            {'date': str(x['date']), 'hours': x['hours'], 'hm': x['hm'],
             'minutes': x['minutes'], 'amount': x['amount'],
             'base_amount': x['base_amount'], 'ot_amount': x['ot_amount'],
             'late_minutes': x['late_minutes']}
            for x in r.get('day_detail', [])
        ],
        'hourly': r.get('hourly', 0),
        'work_hm': r.get('work_hm', ''),
        'work_minutes': r.get('work_minutes', 0),
        'total_hours': round(r.get('work_minutes', 0) / 60, 2),
        'late_days': r.get('late_days', 0),
        'late_minutes': r.get('late_minutes', 0),
        'late_hm': r.get('late_hm', ''),
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

    # 每日工時明細（分鐘制，金額已逐日四捨五入）
    day_detail = result['day_detail']
    total_hours = round(result['work_minutes'] / 60, 2)

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
        'day_detail': day_detail, 'total_hours': total_hours,
        'work_hm': result['work_hm'],
        'late_days': result['late_days'],
        'late_hm': result['late_hm'],
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

    ws.append(['工號', '姓名', '部門', '工時', '遲到次數', '遲到分鐘',
               '底薪', '保養費', '勞務加給', '加班費', '勞健保扣除', '實領'])

    emp_qs = Employee.objects if request.GET.get('show_inactive') == '1' else Employee.tracked
    employees = emp_qs.select_related('user').order_by('employee_id')
    for emp in employees:
        result = calculate_salary(emp, year, month)
        ws.append([
            emp.employee_id,
            emp.user.get_full_name() or emp.user.username,
            emp.department,
            result['work_hm'],
            result['late_days'],
            result['late_minutes'],
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
