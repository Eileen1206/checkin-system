import json
import math
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from .models import Employee, DeliveryTask, DeliverySession


def _haversine_meters(lat1, lng1, lat2, lng2):
    R = 6371000
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) *
         math.sin((lng2 - lng1) * p / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def liff_delivery_page(request):
    """LIFF 頁面：送貨到站 GPS 驗證（舊版單站）"""
    return render(request, 'liff/delivery.html', {
        'liff_id': settings.LIFF_DELIVERY_ID,
    })


@csrf_exempt
def liff_delivery_finish(request):
    """POST API：完成本次運送，寫入整趟結束時間"""
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)

    from django.utils import timezone as tz
    data         = json.loads(request.body)
    line_user_id = data.get('line_user_id', '').strip()
    date_str     = data.get('date', str(tz.localdate()))

    emp = Employee.objects.filter(line_user_id=line_user_id).first()
    if not emp:
        return JsonResponse({'ok': False, 'error': '找不到員工'})

    session, _ = DeliverySession.objects.get_or_create(
        employee=emp, date=date_str
    )
    if not session.finished_at:
        session.finished_at = tz.now()
        session.save(update_fields=['finished_at'])

    total     = DeliveryTask.objects.filter(employee=emp, date=date_str).count()
    completed = DeliveryTask.objects.filter(employee=emp, date=date_str, status='completed').count()

    return JsonResponse({
        'ok':       True,
        'total':    total,
        'completed': completed,
        'duration': session.duration_minutes(),
    })


def liff_delivery_route_page(request):
    """LIFF 頁面：完整路線管理（開始送貨入口）"""
    return render(request, 'liff/delivery_route.html', {
        'liff_route_id': settings.LIFF_DELIVERY_ROUTE_ID,
    })


@csrf_exempt
def liff_delivery_tasks_api(request):
    """GET API：回傳今日任務清單"""
    from django.utils import timezone
    line_user_id = request.GET.get('line_user_id', '').strip()
    date_str     = request.GET.get('date', str(timezone.localdate()))

    if not line_user_id:
        return JsonResponse({'ok': False, 'error': '缺少 line_user_id'})

    emp = Employee.objects.filter(line_user_id=line_user_id).first()
    if not emp:
        return JsonResponse({'ok': False, 'error': '找不到對應員工，請先綁定 LINE'})

    tasks = DeliveryTask.objects.filter(
        employee=emp, date=date_str
    ).order_by('order')

    return JsonResponse({
        'ok': True,
        'tasks': [
            {
                'id':            t.pk,
                'order':         t.order,
                'customer_name': t.customer_name,
                'address':       t.address,
                'status':        t.status,
            }
            for t in tasks
        ],
    })


@csrf_exempt
def liff_delivery_complete(request):
    """AJAX API：驗證 GPS 並標記送貨完成"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)

    data = json.loads(request.body)
    task_id     = data.get('task_id')
    lat         = data.get('lat')
    lng         = data.get('lng')
    line_user_id = data.get('line_user_id')

    # 查任務
    try:
        task = DeliveryTask.objects.select_related('customer', 'employee__user').get(pk=task_id)
    except DeliveryTask.DoesNotExist:
        return JsonResponse({'ok': False, 'error': '找不到此送貨任務'})

    # 驗證是否為該員工的任務
    if task.employee.line_user_id != line_user_id:
        return JsonResponse({'ok': False, 'error': '無權限操作此任務'})

    if task.status == 'completed':
        return JsonResponse({'ok': False, 'error': '此站已標記完成'})

    # 若是今日第一站完成 → 寫入整趟出發時間
    from django.utils import timezone as tz
    already_done = DeliveryTask.objects.filter(
        employee=task.employee, date=task.date, status='completed'
    ).exists()
    if not already_done:
        session, _ = DeliverySession.objects.get_or_create(
            employee=task.employee, date=task.date
        )
        if not session.started_at:
            session.started_at = tz.now()
            session.save(update_fields=['started_at'])

    # 客戶沒有座標 → 直接完成，不驗證
    cust = task.customer
    if not cust or not cust.lat or not cust.lng:
        from django.utils import timezone
        task.status = 'completed'
        task.completed_at = timezone.localtime()
        task.save()
        return JsonResponse({'ok': True, 'message': f'✅ 第 {task.order} 站（{task.customer_name}）完成！', 'validated': False})

    # 計算距離
    distance = _haversine_meters(float(lat), float(lng), float(cust.lat), float(cust.lng))
    ALLOWED_METERS = 500

    if distance <= ALLOWED_METERS:
        from django.utils import timezone
        task.status = 'completed'
        task.completed_at = timezone.localtime()
        task.save()
        return JsonResponse({
            'ok': True,
            'message': f'✅ 位置驗證通過（距客戶 {int(distance)} 公尺）\n第 {task.order} 站（{task.customer_name}）完成！',
            'validated': True,
            'distance': int(distance),
        })
    else:
        return JsonResponse({
            'ok': False,
            'error': f'❌ 距離客戶 {int(distance)} 公尺，需在 {ALLOWED_METERS} 公尺內',
            'distance': int(distance),
        })
