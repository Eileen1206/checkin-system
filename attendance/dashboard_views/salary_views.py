from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.utils import timezone
from django.urls import reverse
from django.conf import settings
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

    from ..models import PayrollRecord
    employees = _salary_employees(show_inactive)
    settled_count = PayrollRecord.objects.filter(
        year=year, month=month, locked=True, employee__in=employees).count()
    return render(request, 'attendance/salary.html', {
        'employees': employees,
        'employee_count': employees.count(),
        'settled_count': settled_count,
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
        'missed_punch': r.get('missed_punch', 0),
        'missed_punch_limit': r.get('missed_punch_limit', 0),
        'missed_punch_over': r.get('missed_punch_over', False),
        'settled_id': r['settled'].pk if r.get('settled') else None,
    })


@login_required
@require_group('admin', 'finance')
def salary_settle(request):
    """結算並鎖定當月薪資：把當下的金額凍結起來。"""
    from django.contrib import messages
    from ..models import PayrollRecord

    year = int(request.POST.get('year', timezone.localdate().year))
    month = int(request.POST.get('month', timezone.localdate().month))
    show_inactive = request.POST.get('show_inactive') == '1'

    count = 0
    for emp in _salary_employees(show_inactive):
        r = calculate_salary(emp, year, month, live=True)
        PayrollRecord.objects.update_or_create(
            employee=emp, year=year, month=month,
            defaults={
                'base': round(r['base']),
                'maintenance': round(r['maintenance']),
                'allowance': round(r['allowance']),
                'overtime': round(r['overtime']),
                'deduction': round(r['deduction']),
                'total': round(r['total']),
                'work_minutes': r['work_minutes'],
                'late_days': r['late_days'],
                'late_minutes': r['late_minutes'],
                'missed_punch': r['missed_punch'],
                'locked': True,
                'settled_by': request.user,
            },
        )
        count += 1

    messages.success(request, f'已結算並鎖定 {year} 年 {month} 月薪資（{count} 位）')
    return redirect(f"{reverse('dashboard:salary')}?year={year}&month={month}"
                    f"{'&show_inactive=1' if show_inactive else ''}")


@login_required
@require_group('admin', 'finance')
def salary_unlock(request, pk):
    """解鎖單一員工的結算，讓打卡可以修改後重新結算。"""
    from django.contrib import messages
    from ..models import PayrollRecord

    record = get_object_or_404(PayrollRecord, pk=pk)
    record.locked = False
    record.save(update_fields=['locked'])
    name = record.employee.user.get_full_name() or record.employee.user.username
    messages.warning(request, f'已解鎖 {name} {record.year}/{record.month:02d} 的薪資，'
                              f'改完記得重新結算')
    return redirect(request.META.get('HTTP_REFERER') or
                    f"{reverse('dashboard:salary')}?year={record.year}&month={record.month}")


@login_required
@require_group('admin', 'finance')
def payslip(request):
    """薪資條（A4 一頁一人，列印後紙本簽名）。

    employee_id 留空 = 全員一次列印。
    """
    from ..models import PayrollRecord

    year = int(request.GET.get('year', timezone.localdate().year))
    month = int(request.GET.get('month', timezone.localdate().month))
    emp_id = request.GET.get('employee_id', '').strip()
    show_inactive = request.GET.get('show_inactive') == '1'

    employees = _salary_employees(show_inactive).order_by('employee_id')
    if emp_id:
        employees = employees.filter(pk=emp_id)

    settled_map = {
        r.employee_id: r for r in PayrollRecord.objects.filter(
            year=year, month=month, employee__in=employees)
    }

    slips = []
    for emp in employees:
        r = calculate_salary(emp, year, month)
        slips.append({
            'emp': emp,
            'result': r,
            'settled': settled_map.get(emp.pk),
            'is_monthly': emp.employment_type == 'monthly',
        })

    return render(request, 'attendance/payslip.html', {
        'slips': slips,
        'year': year,
        'month': month,
        'company_name': getattr(settings, 'COMPANY_NAME', '政旭汽車材料行'),
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
        'punch_slots': [
            {'key': 'clock_in',    'label': '上班打卡'},
            {'key': 'break_start', 'label': '午休開始'},
            {'key': 'break_end',   'label': '午休結束'},
            {'key': 'clock_out',   'label': '下班打卡'},
        ],
        'work_hm': result['work_hm'],
        'late_days': result['late_days'],
        'late_hm': result['late_hm'],
        'missed_punch': result['missed_punch'],
        'missed_punch_limit': result['missed_punch_limit'],
        'missed_punch_over': result['missed_punch_over'],
        'missed_punch_dates': result['missed_punch_dates'],
        'settled': result.get('settled'),
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

    ws.append(['工號', '姓名', '部門', '工時', '遲到次數', '遲到分鐘', '漏打卡次數',
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
            result['missed_punch'],
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
