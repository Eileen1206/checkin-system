from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.urls import reverse
from django.utils import timezone
from datetime import datetime, date
import requests

from ..models import Employee, Holiday
from ..utils import payroll
from .base import require_group


def _import_taiwan_holidays(year):
    """從政府行事曆開放資料（TaiwanCalendar）匯入該年度國定假日。
    只取「有節日名稱」的放假日（跳過一般週末），並補上勞動節（政府日曆多半不含）。
    回傳新增筆數。"""
    url = f'https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar/data/{year}.json'
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    added = 0
    for item in resp.json():
        desc = (item.get('description') or '').strip()
        if not item.get('isHoliday') or not desc:
            continue  # 只匯入有節日名稱的國定假日
        try:
            d = datetime.strptime(item['date'], '%Y%m%d').date()
        except (ValueError, KeyError, TypeError):
            continue
        _, created = Holiday.objects.get_or_create(date=d, defaults={'name': desc})
        if created:
            added += 1
    # 勞動節（5/1）政府機關日曆通常不放，但勞基法視為假日 → 補上
    _, created = Holiday.objects.get_or_create(
        date=date(year, 5, 1), defaults={'name': '勞動節'})
    if created:
        added += 1
    return added


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
        elif action == 'import':
            try:
                y = int(request.POST.get('import_year') or timezone.localdate().year)
            except ValueError:
                y = timezone.localdate().year
            try:
                added = _import_taiwan_holidays(y)
                messages.success(request, f'已匯入 {y} 年國定假日，新增 {added} 天（已存在的會略過）')
            except Exception as e:
                print(f'[holiday import error] {e}')
                messages.error(request, '匯入失敗：無法取得政府行事曆資料，請稍後再試或手動新增。')
            return redirect(f"{reverse('dashboard:holiday_list')}?year={y}")
        return redirect('dashboard:holiday_list')

    today = timezone.localdate()
    year = int(request.GET.get('year', today.year))
    holidays = Holiday.objects.filter(date__year=year).order_by('date')
    return render(request, 'attendance/holiday_list.html', {
        'holidays': holidays,
        'year':     year,
        'years':    range(today.year + 1, today.year - 3, -1),
    })
