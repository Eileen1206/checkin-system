from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.conf import settings
import json
from ..models import Employee, Customer, DeliveryTask
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    PushMessageRequest,
    TextMessage,
    FlexMessage,
    FlexContainer,
)


@login_required
def delivery_push(request):
    """將今日路線以 LINE 訊息推播給指定員工"""
    employee_id = request.POST.get('employee_id')
    date = request.POST.get('date', str(timezone.localdate()))
    employee = get_object_or_404(
        Employee,
        pk=employee_id
    )
    if not employee.line_user_id:
        messages.error(request, '該員工尚未綁定line帳號，無法推播')
        return redirect('dashboard:delivery_plan')
    tasks = DeliveryTask.objects.filter(
        employee=employee,
        date=date,
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
                        "type": "uri",
                        "label": "✅ 完成",
                        "uri": f"https://liff.line.me/{settings.LIFF_DELIVERY_ID}?task_id={task.pk}"
                    }
                }]
            }
        }
        bubbles.append(bubble)

    carousel = {"type": 'carousel', "contents": bubbles}
    flex_msg = FlexMessage(
        alt_text='今日送貨路線',
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

    data = json.loads(request.body)
    task_ids = data.get('task_ids', [])  # 按新順序排列的 DeliveryTask PK 列表

    for i, task_id in enumerate(task_ids, start=1):
        DeliveryTask.objects.filter(pk=task_id).update(order=i)

    return JsonResponse({'ok': True})


@login_required
def delivery_plan(request):
    """送貨路線規劃頁面"""
    from ..utils.routing import get_optimal_order, geocode_customer

    employees = Employee.objects.filter(
        is_delivery=True
    ).select_related('user')

    if request.method == 'GET':
        from ..utils.routing import get_office_coords
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

    from ..utils.routing import get_office_coords
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
