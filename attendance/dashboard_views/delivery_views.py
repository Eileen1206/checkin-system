from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.conf import settings
from django.db import models
import json
from ..utils.routing import get_office_coords
from ..models import Employee, Customer, DeliveryTask, DeliverySession
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
    employee = get_object_or_404(Employee, pk=employee_id)

    if not employee.line_user_id:
        messages.error(request, '該員工尚未綁定 LINE 帳號，無法推播')
        return redirect('dashboard:delivery_plan')

    tasks = DeliveryTask.objects.filter(
        employee=employee, date=date, status='pending'
    ).order_by('order')

    if not tasks.exists():
        messages.error(request, '目前沒有待送的任務，無法推播')
        return redirect('dashboard:delivery_plan')

    # 建立本趟 DeliverySession
    trip_number = DeliverySession.objects.filter(
        employee=employee, date=date
    ).count() + 1
    session = DeliverySession.objects.create(
        employee=employee,
        date=date,
        trip_number=trip_number,
        pushed_at=timezone.now(),
    )
    tasks.update(session=session)

    # 文字摘要
    lines = [f'🚚 今日共 {tasks.count()} 站，出發前請確認路線']
    for task in tasks:
        lines.append(f'第 {task.order} 站｜{task.customer_name}')
    message_text = '\n'.join(lines)

    # Flex 卡片：路線摘要 + 開始送貨按鈕
    route_url = f"https://liff.line.me/{settings.LIFF_DELIVERY_ROUTE_ID}"
    stop_items = [
        {
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {"type": "text", "text": f"{task.order}", "size": "sm",
                 "color": "#27ACB2", "flex": 0, "gravity": "center"},
                {"type": "text", "text": task.customer_name,
                 "size": "sm", "color": "#333333", "margin": "md"},
            ],
            "margin": "xs",
        }
        for task in tasks
    ]

    bubble = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#1a1a1a",
            "paddingAll": "20px",
            "contents": [
                {"type": "text", "text": "今日送貨路線",
                 "color": "#ffffff", "weight": "bold", "size": "lg"},
                {"type": "text", "text": f"共 {tasks.count()} 站",
                 "color": "#aaaaaa", "size": "sm", "margin": "xs"},
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": stop_items,
            "spacing": "sm",
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [{
                "type": "button",
                "style": "primary",
                "color": "#27ACB2",
                "height": "md",
                "action": {
                    "type": "uri",
                    "label": "🚚 開始送貨",
                    "uri": route_url,
                }
            }],
        }
    }

    flex_msg = FlexMessage(
        alt_text='今日送貨路線，點擊開始送貨',
        contents=FlexContainer.from_dict(bubble)
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
def delivery_add_task(request):
    """臨時加站：加在當天最後一站，並推播通知員工"""
    if request.method != 'POST':
        return redirect('dashboard:delivery_plan')

    employee_id = request.POST.get('employee_id')
    customer_id = request.POST.get('customer_id')
    date = request.POST.get('date', str(timezone.localdate()))

    if not customer_id:
        messages.error(request, '請先搜尋並選擇客戶')
        return redirect('dashboard:delivery_plan')

    employee = get_object_or_404(Employee, pk=employee_id)
    customer = get_object_or_404(Customer, pk=customer_id)

    last_order = DeliveryTask.objects.filter(
        employee=employee, date=date
    ).order_by('-order').values_list('order', flat=True).first() or 0

    # 找最新未完成的趟次
    active_session = DeliverySession.objects.filter(
        employee=employee, date=date, finished_at__isnull=True
    ).order_by('-trip_number').first()

    task = DeliveryTask.objects.create(
        employee=employee,
        date=date,
        order=last_order + 1,
        customer=customer,
        customer_name=customer.name,
        address=customer.address,
        is_urgent=False,
        session=active_session,
    )

    if employee.line_user_id:
        try:
            configuration = Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)
            with ApiClient(configuration) as api_client:
                MessagingApi(api_client).push_message(PushMessageRequest(
                    to=employee.line_user_id,
                    messages=[TextMessage(
                        text=f'📢 老闆新增了一站！\n第 {task.order} 站｜{customer.name}\n📍 {customer.address}\n\n請在送貨路線頁查看並完成。'
                    )]
                ))
        except Exception:
            pass

    messages.success(request, f'已新增「{customer.name}」為第 {task.order} 站，並通知 {employee.user.get_full_name() or employee.user.username}')
    return redirect('dashboard:delivery_plan')


@login_required
def delivery_delete_task(request, pk):
    """刪除送貨任務"""
    if request.method != 'POST':
        return redirect('dashboard:delivery_plan')

    task = get_object_or_404(DeliveryTask, pk=pk)
    emp_name = task.employee.user.get_full_name() or task.employee.user.username
    customer_name = task.customer_name
    task.delete()
    messages.success(request, f'已刪除「{customer_name}」的送貨任務（{emp_name}）')
    return redirect('dashboard:delivery_plan')


@login_required
def delivery_reorder(request):
    """AJAX：儲存手動調整後的送貨順序"""
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)

    data = json.loads(request.body)
    task_ids = data.get('task_ids', [])

    for i, task_id in enumerate(task_ids, start=1):
        DeliveryTask.objects.filter(pk=task_id).update(order=i)

    return JsonResponse({'ok': True})


@login_required
def delivery_plan(request):
    """送貨路線規劃頁面"""
    from ..utils.routing import get_optimal_order, geocode_customer

    employees = Employee.objects.filter(is_delivery=True).select_related('user')

    if request.method == 'GET':
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

        return render(request, 'attendance/delivery_plan.html', {
            'employees': employees,
            'today': today,
            'pending_by_employee': pending_by_employee,
        })

    # POST：計算路線並建立任務
    employee_id  = request.POST.get('employee_id')
    customer_ids = list(dict.fromkeys(request.POST.getlist('customer_ids')))  # 保持順序去重
    urgent_ids   = request.POST.getlist('urgent_ids')
    date         = request.POST.get('date', str(timezone.localdate()))
    office       = get_office_coords()

    employee  = get_object_or_404(Employee, pk=employee_id)
    # 依送出順序排列（filter 不保證順序）
    cust_map  = {str(c.pk): c for c in Customer.objects.filter(pk__in=customer_ids)}
    customers = [cust_map[cid] for cid in customer_ids if cid in cust_map]

    urgent = [c for c in customers if str(c.pk) in urgent_ids]
    normal = [c for c in customers if str(c.pk) not in urgent_ids]
    normal_sorted = get_optimal_order(normal)
    final_order = urgent + normal_sorted

    # 若有正在送貨中的趟次（已出發但未完成），禁止覆蓋
    active_session = DeliverySession.objects.filter(
        employee=employee, date=date,
        started_at__isnull=False,
        finished_at__isnull=True,
    ).first()
    if active_session:
        messages.error(request,
            f'{employee.user.get_full_name() or employee.user.username} 目前第 {active_session.trip_number} 趟送貨進行中，'
            '請等送貨員完成後再重新規劃。'
        )
        return redirect('dashboard:delivery_plan')

    # 刪除以下三種 pending 任務：
    #   1. 尚未推播（無 session）
    #   2. 已推播但未出發（session.started_at 為空）
    #   3. 已結束趟次裡殘留的 pending（session.finished_at 有值）
    DeliveryTask.objects.filter(
        employee=employee, date=date, status='pending'
    ).filter(
        models.Q(session__isnull=True) |
        models.Q(session__started_at__isnull=True) |
        models.Q(session__finished_at__isnull=False)
    ).delete()
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

    tasks = DeliveryTask.objects.filter(
        employee=employee, date=date
    ).order_by('order')

    return render(request, 'attendance/delivery_plan.html', {
        'employees': employees,
        'success': True,
        'tasks': tasks,
        'employee': employee,
        'date': date,
        'today': timezone.localdate(),
        'pending_by_employee': {},
        'office_lat': office[0] if office else None,  
        'office_lng': office[1] if office else None, 
    })


@login_required
def delivery_today(request):
    """今日送貨狀況總覽（按趟次顯示）"""
    date        = request.GET.get('date', str(timezone.localdate()))
    employee_id = request.GET.get('employee_id', '')

    sessions_qs = DeliverySession.objects.filter(date=date).select_related('employee__user').order_by('employee', 'trip_number')
    if employee_id:
        sessions_qs = sessions_qs.filter(employee_id=employee_id)

    # 每個 session 附上其 tasks（跳過 0 站的空趟次）
    trips = []
    for session in sessions_qs:
        tasks = list(DeliveryTask.objects.filter(session=session).order_by('order'))
        total = len(tasks)
        if total == 0:
            continue   # 空趟次不顯示
        completed = sum(1 for t in tasks if t.status == 'completed')
        trips.append({
            'session':   session,
            'tasks':     tasks,
            'total':     total,
            'completed': completed,
            'all_done':  completed == total,
        })

    # 無 session 的任務（舊資料或直接建立的）另外處理
    orphan_tasks = DeliveryTask.objects.filter(
        date=date, session__isnull=True
    ).select_related('employee__user').order_by('employee', 'order')
    if employee_id:
        orphan_tasks = orphan_tasks.filter(employee_id=employee_id)

    delivery_employees = Employee.objects.filter(is_delivery=True).select_related('user')

    return render(request, 'attendance/delivery_today.html', {
        'trips':                trips,
        'orphan_tasks':         orphan_tasks,
        'date':                 date,
        'delivery_employees':   delivery_employees,
        'selected_employee_id': employee_id,
    })
