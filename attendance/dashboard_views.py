from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db import models
from django.utils import timezone
from django.urls import reverse
from django.conf import settings
from django.core.exceptions import PermissionDenied
from datetime import datetime, date as date_type
import openpyxl
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
import json
from .models import Employee, AttendanceRecord, BindingToken, Customer, DeliveryTask, MonthlyAllowance, LeaveRecord, User
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
    QuickReply,
    QuickReplyItem,
    PostbackAction,
    FlexMessage, 
    FlexContainer,
    
)
def require_group(*group_names):
    def decorator(view_func):
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                from django.contrib.auth.views import redirect_to_login
                return redirect_to_login(request.get_full_path())
            if request.user.groups.filter(name__in=group_names).exists() or request.user.is_superuser:
                return view_func(request, *args, **kwargs)
            raise PermissionDenied
        return wrapper
    return decorator



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

def get_work_hours(employee, date=None):
    date = date or timezone.localdate()
    clock_in = AttendanceRecord.objects.filter(
        employee=employee, timestamp__date=date, record_type='clock_in'
    ).first()

    if clock_in is None:
        return 0

    clock_out = AttendanceRecord.objects.filter(
        employee=employee, timestamp__date=date, record_type='clock_out'
    ).first()

    end_time = clock_out.timestamp if clock_out else timezone.now()
    duration = end_time - clock_in.timestamp
    total_seconds = duration.total_seconds()

    # 工時進位：以半小時為單位，15分(900秒)以上進半小時
    half_hours = total_seconds // 1800       # 完整的半小時數
    remainder  = total_seconds % 1800        # 不足半小時的秒數
    if remainder >= 900:                  
        half_hours += 1
    hours = half_hours / 2                   # 換算回小時

    # 遲到扣薪：比上班時間晚超過10分鐘 → 扣0.5小時
    if employee.work_start_time:
        clock_in_time = clock_in.timestamp.astimezone().time()
        clock_in_date = clock_in.timestamp.astimezone().date()
        # 計算遲到幾分鐘
        
        scheduled = datetime.combine(clock_in_date, employee.work_start_time)
        actual    = datetime.combine(date_type.today(), clock_in_time)
        late_minutes = (actual - scheduled).total_seconds() / 60  # 換算成分鐘
        if late_minutes > 10:            
            hours -= 0.5                  

    return hours


@login_required
def index(request):
    today = timezone.localdate()
    status_map = get_today_status()
    employee_list = []
    for employee in status_map:
        employee_list.append({
            'employee': employee,
            'status': status_map[employee],
            'hours': get_work_hours(employee)
        })

    counts = {
        'working': sum(1 for s in status_map.values() if s == 'working'),
        'break':   sum(1 for s in status_map.values() if s == 'break'),
        'left':    sum(1 for s in status_map.values() if s == 'left'),
        'absent':  sum(1 for s in status_map.values() if s == 'absent'),
    }

  

    return render(request, 'attendance/dashboard.html', {
        'employee_list': employee_list,
        'counts': counts,
        'today': today,
        
    })


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
def import_customers(request):
    from .models import Customer
    import csv
    import io

    if request.method == 'GET':
        return render(request, 'attendance/import_customers.html')

    if request.method == 'POST':
        if 'csv_file' not in request.FILES:
            messages.error(request, '請選擇 CSV 檔案')
            return render(request, 'attendance/import_customers.html')

        csv_file = request.FILES['csv_file']
        file = io.TextIOWrapper(csv_file, encoding='utf-8-sig')
        reader = csv.DictReader(file, delimiter=',')

        for row in reader:
            Customer.objects.update_or_create(
                customer_id=row['客戶編號'],
                defaults={
                    'name': row['客戶名稱'],
                    'address': row['地址'],
                    'phone': row['電話號碼']
                }
            )

        return redirect('dashboard:index')

@login_required
@require_group('admin', 'finance')
def search_customer(request):
    """AJAX：搜尋客戶（輸入編號或名稱）"""
    q = request.GET.get('q', '').strip()
    if not q:
        return JsonResponse({'results': []})

    customers = Customer.objects.filter(
        is_active=True
    ).exclude(
        customer_id='A000'  # 排除公司本身
    ).filter(
        models.Q(customer_id__icontains=q) | models.Q(name__icontains=q)
    )[:10]

    results = [
        {'id': c.pk, 'customer_id': c.customer_id, 'name': c.name, 'address': c.address}
        for c in customers
    ]
    return JsonResponse({'results': results})


@login_required
def delivery_push(request):
    """將今日路線以 LINE 訊息推播給指定員工"""
    
    employee_id = request.POST.get('employee_id')
    date = request.POST.get('date', str(timezone.localdate()))
    employee = get_object_or_404(
        Employee,
        pk = employee_id
    )
    if not employee.line_user_id:
        messages.error(request, '該員工尚未綁定line帳號，無法推播')
        return redirect('dashboard:delivery_plan')
    tasks = DeliveryTask.objects.filter(
        employee = employee,
        date = date,
        status='pending'
    )

    lines = ['🚚 本趟路線']

    for task in tasks:
        lines.append(f'第 {task.order}站 | {task.customer_name}')
        lines.append(f'📍 {task.address}')

    message_text = '\n'.join(lines)

    bubbles = []
    for task in tasks:
        bubble = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": f"第 {task.order} 站", "weight": "bold"},
                    {"type": "text", "text": task.customer_name},
                    {"type": "text", "text": f"📍 {task.address}", "wrap": True, "color": "#888888"},
                

                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [{
                    "type": "button",
                    "style": "primary",
                    "color": "#27ACB2",
                    "action": {
                        "type": "postback",
                        "label": "✅ 完成",
                        "data": f"action=delivery_done&task_id={task.pk}"
                    }
                
                }]
            }
        }
        bubbles.append(bubble)

    carousel = {"type": 'carousel', "contents": bubbles}
    flex_msg = FlexMessage(
        alt_text = '今日送貨路線',
        contents=FlexContainer.from_dict(carousel)
    )

    configuration = Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        api.push_message(PushMessageRequest(
            to=employee.line_user_id,
            messages=[TextMessage(text=message_text), flex_msg]
        ))
                

    messages.success(request, f'已推播路線給 {employee.user.get_full_name()}！')
    return redirect('dashboard:delivery_plan')


@login_required
def delivery_reorder(request):
    """AJAX：儲存手動調整後的送貨順序"""
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)

    import json
    data = json.loads(request.body)
    task_ids = data.get('task_ids', [])  # 按新順序排列的 DeliveryTask PK 列表

    for i, task_id in enumerate(task_ids, start=1):
        DeliveryTask.objects.filter(pk=task_id).update(order=i)

    return JsonResponse({'ok': True})


@login_required
def customer_list(request):
    """客戶管理列表，支援搜尋與篩選"""
    q = request.GET.get('q', '').strip()
    show = request.GET.get('show', 'active')  # active | all | no_address

    customers = Customer.objects.all()
    if show == 'active':
        customers = customers.filter(is_active=True)
    elif show == 'no_address':
        customers = customers.filter(is_active=True, address='')

    if q:
        customers = customers.filter(
            models.Q(customer_id__icontains=q) | models.Q(name__icontains=q)
        )

    customers = customers.order_by('customer_id')

    return render(request, 'attendance/customer_list.html', {
        'customers': customers,
        'q': q,
        'show': show,
        'total': Customer.objects.count(),
        'no_address_count': Customer.objects.filter(is_active=True, address='').count(),
    })


@login_required
def geocode_customers(request):
    """批次將無座標的客戶地址轉換為 GPS 座標（每次最多 30 筆避免逾時）"""
    if request.method != 'POST':
        return redirect('dashboard:customer_list')

    from attendance.management.commands.geocode_customers import nominatim_geocode
    import time

    customers = Customer.objects.filter(
        is_active=True, lat__isnull=True
    ).exclude(address='')[:5]

    success = 0
    for c in customers:
        result = nominatim_geocode(c.address)
        if result:
            c.lat, c.lng = result
            c.save(update_fields=['lat', 'lng'])
            success += 1
        time.sleep(1)

    from django.contrib import messages
    messages.success(request, f'定位完成：成功 {success} 筆，可再次點擊繼續定位剩餘客戶。')
    return redirect('dashboard:customer_list')


@login_required
def customer_edit(request, pk):
    """編輯單一客戶資料"""
    customer = get_object_or_404(Customer, pk=pk)

    if request.method == 'POST':
        customer.customer_id = request.POST.get('customer_id', customer.customer_id).strip()
        customer.name = request.POST.get('name', customer.name).strip()
        customer.address = request.POST.get('address', '').strip()
        customer.phone = request.POST.get('phone', '').strip()
        customer.is_active = request.POST.get('is_active') == 'on'

        lat = request.POST.get('lat', '').strip()
        lng = request.POST.get('lng', '').strip()
        try:
            customer.lat = float(lat) if lat else None
            customer.lng = float(lng) if lng else None
            if customer.lat is not None and not (-90 <= customer.lat <= 90):
                raise ValueError('緯度必須在 -90 到 90 之間')
            if customer.lng is not None and not (-180 <= customer.lng <= 180):
                raise ValueError('經度必須在 -180 到 180 之間')
        except ValueError as e:
            messages.error(request, f'座標格式錯誤：{e}')
            return render(request, 'attendance/customer_edit.html', {'customer': customer})

        customer.save()
        messages.success(request, f'客戶【{customer.name}】已更新')
        return redirect('dashboard:customer_list')

    return render(request, 'attendance/customer_edit.html', {'customer': customer})


@login_required
def delivery_plan(request):
    """送貨路線規劃頁面"""
    from attendance.utils.routing import get_optimal_order, geocode_customer

    employees = Employee.objects.filter(
        is_delivery=True
    ).select_related('user')

    if request.method == 'GET':
        from attendance.utils.routing import get_office_coords
        today = timezone.localdate()
        pending_tasks = (
            DeliveryTask.objects
            .filter(date=today, status='pending')
            .select_related('employee__user')
            .order_by('employee', 'order')
        )
        pending_by_employee = {}
        for task in pending_tasks:
            emp = task.employee
            if emp not in pending_by_employee:
                pending_by_employee[emp] = []
            pending_by_employee[emp].append(task)
        office = get_office_coords()
        return render(request, 'attendance/delivery_plan.html', {
            'employees': employees,
            'today': today,
            'pending_by_employee': pending_by_employee,
            'office_lat': office[0] if office else None,
            'office_lng': office[1] if office else None,
        })

    # POST：計算路線並建立任務
    employee_id = request.POST.get('employee_id')
    customer_ids = request.POST.getlist('customer_ids')  # list of Customer PKs
    urgent_ids = request.POST.getlist('urgent_ids')       # 急單的 Customer PKs
    date = request.POST.get('date', str(timezone.localdate()))

    employee = get_object_or_404(Employee, pk=employee_id)
    customers = list(Customer.objects.filter(pk__in=customer_ids))

    # 分成急單和非急單
    urgent = [c for c in customers if str(c.pk) in urgent_ids]
    normal = [c for c in customers if str(c.pk) not in urgent_ids]

    # 非急單計算最短路線
    normal_sorted = get_optimal_order(normal)

    # 合併：急單在前
    final_order = urgent + normal_sorted

    # 建立 DeliveryTask
    DeliveryTask.objects.filter(employee=employee, date=date, status='pending').delete()
    completed_count = DeliveryTask.objects.filter(employee=employee, date=date, status='completed').count()
    for i, customer in enumerate(final_order, start=completed_count + 1):
        DeliveryTask.objects.create(
            employee=employee,
            date=date,
            order=i,
            customer=customer,
            customer_name=customer.name,
            address=customer.address,
            is_urgent=(customer in urgent),
        )

    from attendance.utils.routing import get_office_coords
    tasks = DeliveryTask.objects.filter(employee=employee, date=date, status='pending').order_by('order').select_related('customer')
    office = get_office_coords()  # (lat, lng) or None
    return render(request, 'attendance/delivery_plan.html', {
        'employees': employees,
        'today': timezone.localdate(),
        'tasks': tasks,
        'employee': employee,
        'office_lat': office[0] if office else None,
        'office_lng': office[1] if office else None,
        'success': True,
    })

@login_required
def delivery_today(request):
    date = request.GET.get('date', str(timezone.localdate()))
    employee_id = request.GET.get('employee_id', '')

    tasks = DeliveryTask.objects.filter(date=date).select_related('employee__user', 'customer').order_by('employee', 'order')
    if employee_id:
        tasks = tasks.filter(employee_id=employee_id)

    delivery_employees = Employee.objects.filter(is_delivery=True).select_related('user')

    # 地圖用的 JSON 資料（只取有座標的站）
    map_points = []
    for t in tasks:
        lat = float(t.customer.lat) if t.customer and t.customer.lat else None
        lng = float(t.customer.lng) if t.customer and t.customer.lng else None
        if lat and lng:
            map_points.append({
                'order':    t.order,
                'name':     t.customer_name,
                'address':  t.address,
                'status':   t.status,
                'lat':      lat,
                'lng':      lng,
                'employee': t.employee.user.get_full_name() or t.employee.user.username,
            })

    return render(request, 'attendance/delivery_today.html', {
        'tasks': tasks,
        'date': date,
        'delivery_employees': delivery_employees,
        'selected_employee_id': employee_id,
        'map_points_json': json.dumps(map_points, ensure_ascii=False),
        'has_map': len(map_points) > 0,
    })


def calculate_salary(emp, year, month):
    allowance = MonthlyAllowance.objects.filter(
        employee=emp, year=year, month=month
    ).first()
    allowance_amount = float(allowance.amount) if allowance else 0

    records = AttendanceRecord.objects.filter(
        employee=emp,
        timestamp__year=year,
        timestamp__month=month
    )

    if emp.employment_type == 'monthly':
        base = float(emp.monthly_salary or 0)
        maintenance = 0
        deduction = 0
    else:
        total_hours = sum(
            get_work_hours(emp, d)
            for d in records.filter(record_type='clock_in').dates('timestamp', 'day')
        )
        base = total_hours * float(emp.hourly_rate or 0)
        maintenance = sum(
            100 if get_work_hours(emp, d) >= 4 else 50
            for d in records.filter(record_type='clock_in').dates('timestamp', 'day')
        )
        deduction = float(emp.labor_insurance_amount or 0) + \
                    float(emp.health_insurance_amount or 0)

    total = base + maintenance + allowance_amount - deduction
    return {
        'employee': emp,
        'base': base,
        'maintenance': maintenance,
        'allowance': allowance_amount,
        'deduction': deduction,
        'total': total,
    }


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

       
    return render(request, 'attendance/salary.html',{
        'results': results,
        'year': year,
        'years': range(timezone.localdate().year, timezone.localdate().year - 3, -1),
        'month': month,
        'months': range(1,13)
    }
    )

@login_required
@require_group('admin', 'finance')
def add_allowance(request):
    employee_id = request.POST.get('employee_id')
    year = int(request.POST.get('year', timezone.localdate().year))
    month = int(request.POST.get('month', timezone.localdate().month))
    amount = request.POST.get('amount')
    note = request.POST.get('note')

    emp = Employee.objects.get(pk=employee_id)

    MonthlyAllowance.objects.update_or_create(employee = emp, year = year, month = month, defaults={'amount': amount, 'note': note})
    return redirect(f"{reverse('dashboard:salary')}?year={year}&month={month}")

@login_required
@require_group('admin', 'finance')
def employee_list(request):
    employees = Employee.objects.select_related('user').order_by('employee_id')
    return render(request, 'attendance/employee_list.html', {'employees': employees })

@login_required
@require_group('admin', 'finance')
def employee_add(request):
    if request.method == 'POST':
        #從表單取得資料
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

        #建立系統user
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


        #建立員工
        Employee.objects.create(
            user = user,
            employee_id = employee_id,
            department = department,
            employment_type = employment_type,
            monthly_salary = monthly_salary,
            hourly_rate = hourly_rate,
            work_start_time = work_start_time,
            work_end_time = work_end_time,
            is_delivery = is_delivery,
            fuel_daily_allowance = fuel_daily_allowance,
            labor_insurance_amount = labor_insurance_amount,
            health_insurance_amount = health_insurance_amount,
        )
        return redirect('dashboard:employee_list')
    return render(request, 'attendance/employee_add.html')

WORK_DAY_CHOICES = [
    (0, '週一'), (1, '週二'), (2, '週三'), (3, '週四'),
    (4, '週五'), (5, '週六'), (6, '週日'),
]

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
        'weekday_labels': ['一','二','三','四','五','六','日'],
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


@login_required
def add_record(request):
    """管理員補打卡（新增一筆 AttendanceRecord）"""
    employee_id = request.GET.get('employee_id') or request.POST.get('employee_id')
    date_str    = request.GET.get('date')        or request.POST.get('date')
    record_type = request.GET.get('type')        or request.POST.get('record_type')
    next_url    = request.GET.get('next')        or request.POST.get('next') or '/reports/'

    # 驗證 date_str 格式
    try:
        datetime.strptime(date_str or '', '%Y-%m-%d')
    except ValueError:
        messages.error(request, '日期格式錯誤')
        return redirect(request.GET.get('next') or '/reports/')

    # 驗證 record_type 白名單
    VALID_TYPES = dict(AttendanceRecord.RECORD_TYPE_CHOICES)
    if record_type not in VALID_TYPES:
        messages.error(request, '打卡類型不合法')
        return redirect(request.GET.get('next') or '/reports/')

    employee = get_object_or_404(Employee, pk=employee_id)

    if request.method == 'POST':
        time_str = request.POST.get('time', '').strip()
        try:
            naive_dt = datetime.strptime(f"{date_str} {time_str}", '%Y-%m-%d %H:%M')
            aware_dt = timezone.make_aware(naive_dt)
            AttendanceRecord.objects.create(
                employee=employee,
                record_type=record_type,
                timestamp=aware_dt,
                source='line',
                is_valid=True,
            )
            messages.success(request, f'已為 {employee} 補打卡：{VALID_TYPES.get(record_type, record_type)} {time_str}')
        except (ValueError, Exception) as e:
            messages.error(request, f'補打卡失敗：{e}')
        return redirect(next_url)

    return render(request, 'attendance/add_record.html', {
        'employee':    employee,
        'date_str':    date_str,
        'record_type': record_type,
        'type_label':  VALID_TYPES.get(record_type, record_type),
        'next_url':    next_url,
    })


def rfid_page(request):
    """RFID 打卡待機頁面"""
    return render(request, 'attendance/rfid.html')

@csrf_exempt
def rfid_checkin(request):
    """接收 RFID 卡號，建立打卡紀錄"""
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)

    rfid_uid = request.POST.get('rfid_uid', '').strip()
    if not rfid_uid:
        return JsonResponse({'ok': False, 'message': '未收到卡號'})
    if len(rfid_uid) > 20 or not rfid_uid.replace('-', '').replace(':', '').isalnum():
        return JsonResponse({'ok': False, 'message': '卡號格式不合法'}, status=400)

    # 查詢員工
    emp = Employee.objects.filter(rfid_uid=rfid_uid).first()
    if emp is None:
        return JsonResponse({'ok': False, 'message': '此卡片尚未綁定員工'})

    # 判斷打卡類型（今日第幾次）
    today = timezone.localdate()
    count = AttendanceRecord.objects.filter(
        employee=emp,
        timestamp__date=today
    ).count()

    if count == 0:
        record_type = 'clock_in'
    elif count == 1:
        if not emp.line_user_id:
            return JsonResponse({'ok': False, 'message': '該員工未綁定 LINE，無法選擇打卡類型'})
        
        # 推播 Flex Message 給員工選擇
        flex = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": "請選擇打卡類型", "weight": "bold", "size": "xl"},
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#F59E0B",
                        "height": "md",
                        "action": {
                            "type": "postback",
                            "label": "🍱 午休開始",
                            "data": f"action=rfid_punch&record_type=break_start&employee_id={emp.pk}"
                        }
                    },
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#3B82F6",
                        "height": "md",
                        "action": {
                            "type": "postback",
                            "label": "🏠 直接下班",
                            "data": f"action=rfid_punch&record_type=clock_out&employee_id={emp.pk}"
                        }
                    }
                ]
            }
        }

        configuration = Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)
        with ApiClient(configuration) as api_client:
            api = MessagingApi(api_client)
            api.push_message(PushMessageRequest(
                to=emp.line_user_id,
                messages=[FlexMessage(
                    alt_text='請選擇打卡類型',
                    contents=FlexContainer.from_dict(flex)
                )]
            ))

        name = emp.user.get_full_name() or emp.user.username
        return JsonResponse({'ok': True, 'message': f'{name} 請用手機選擇打卡類型'})
    elif count == 2:
        record_type = 'break_end'
    elif count == 3:
        record_type = 'clock_out'
    else:
        return JsonResponse({'ok': False, 'message': '今日打卡已完成'})
    # 建立紀錄
    AttendanceRecord.objects.create(
        employee=emp,
        record_type=record_type,
        timestamp=timezone.now(),
        latitude=0,
        longitude=0,
        is_valid=True,
        distance_meters=0,
        source='rfid',
    )

    label = '上班打卡' if record_type == 'clock_in' else \
            '午休結束' if record_type == 'break_end' else '下班打卡'

    # 推播 LINE 通知
    if emp.line_user_id:
        time_str = timezone.localtime(timezone.now()).strftime('%H:%M')
        text = f'✅ {label}成功！\n時間：{time_str}'
        try:
            configuration = Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)
            with ApiClient(configuration) as api_client:
                api = MessagingApi(api_client)
                api.push_message(PushMessageRequest(
                    to=emp.line_user_id,
                    messages=[TextMessage(text=text)]
                ))
        except Exception:
            pass

    name = emp.user.get_full_name() or emp.user.username
    return JsonResponse({
        'ok': True,
        'message': f'{name} {label}成功',
        'record_type': record_type,
    })