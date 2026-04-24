import json
import math
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from .models import Employee, DeliveryTask


def _haversine_meters(lat1, lng1, lat2, lng2):
    R = 6371000
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) *
         math.sin((lng2 - lng1) * p / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def liff_delivery_page(request):
    """LIFF 頁面：送貨到站 GPS 驗證"""
    return render(request, 'liff/delivery.html', {
        'liff_id': settings.LIFF_DELIVERY_ID,
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
