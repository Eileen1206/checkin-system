from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from datetime import datetime

from ..models import Employee, Holiday
from ..utils import payroll
from .base import require_group


@login_required
@require_group('admin', 'finance')
def annual_leave(request):
    """特休結算（週年 / 離職）：全額折現＝應有特休天數 × 日薪。"""
    # 含離職員工（離職結算需要），依工號排序
    employees = Employee.objects.select_related('user').order_by('employee_id')

    emp_id    = request.GET.get('employee_id')
    as_of_str = (request.GET.get('as_of') or '').strip()
    as_of = None
    if as_of_str:
        try:
            as_of = datetime.strptime(as_of_str, '%Y-%m-%d').date()
        except ValueError:
            as_of = None

    selected = None
    if emp_id:
        selected = employees.filter(pk=emp_id).first()
    if not selected and employees.exists():
        selected = employees.first()

    settlement = payroll.annual_leave_settlement(selected, as_of) if selected else None

    return render(request, 'attendance/annual_leave_settlement.html', {
        'employees':  employees,
        'selected':   selected,
        'settlement': settlement,
        'as_of':      as_of_str,
    })


@login_required
@require_group('admin')
def holiday_list(request):
    """國定假日管理：列表 + 批次新增 + 刪除（供加班費『假日加倍』判定）。"""
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            raw  = request.POST.get('dates', '')
            name = request.POST.get('name', '').strip()
            added = 0
            for chunk in raw.replace(',', '\n').split('\n'):
                s = chunk.strip()
                if not s:
                    continue
                try:
                    d = datetime.strptime(s, '%Y-%m-%d').date()
                except ValueError:
                    continue
                _, created = Holiday.objects.get_or_create(date=d, defaults={'name': name})
                if created:
                    added += 1
            messages.success(request, f'已新增 {added} 個國定假日')
        elif action == 'delete':
            Holiday.objects.filter(pk=request.POST.get('pk')).delete()
            messages.success(request, '已刪除')
        return redirect('dashboard:holiday_list')

    today = timezone.localdate()
    year = int(request.GET.get('year', today.year))
    holidays = Holiday.objects.filter(date__year=year).order_by('date')
    return render(request, 'attendance/holiday_list.html', {
        'holidays': holidays,
        'year':     year,
        'years':    range(today.year + 1, today.year - 3, -1),
    })
