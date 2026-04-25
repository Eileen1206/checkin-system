from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime
from django.conf import settings
from ..models import Employee, AttendanceRecord, DeliveryTask
from collections import defaultdict
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    PushMessageRequest,
    TextMessage,
    FlexMessage,
    FlexContainer,
)
from .base import get_today_status, get_work_hours


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

    # 今日送貨狀況
    all_tasks = list(
        DeliveryTask.objects
        .filter(date=today)
        .select_related('employee__user')
        .order_by('employee', 'order')
    )
    emp_tasks_map = defaultdict(list)
    for task in all_tasks:
        emp_tasks_map[task.employee].append(task)

    delivery_status = []
    for emp, tasks in emp_tasks_map.items():
        total     = len(tasks)
        completed = sum(1 for t in tasks if t.status == 'completed')
        next_task = next((t for t in tasks if t.status == 'pending'), None)
        last_done = next((t for t in reversed(tasks) if t.status == 'completed'), None)
        delivery_status.append({
            'employee':  emp,
            'total':     total,
            'completed': completed,
            'next_task': next_task,
            'last_done': last_done,
            'progress':  int(completed / total * 100) if total else 0,
            'all_done':  completed == total and total > 0,
        })

    return render(request, 'attendance/dashboard.html', {
        'employee_list':   employee_list,
        'counts':          counts,
        'today':           today,
        'delivery_status': delivery_status,
    })


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
