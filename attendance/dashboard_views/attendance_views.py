from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime
from django.conf import settings
from ..models import Employee, AttendanceRecord, DeliveryTask, DeliverySession, LeaveRequest, LocationCorrectionRequest, AttendanceAnomalyDismissal
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
from .delivery_views import _get_avg_stop_minutes, _build_prediction


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

    # 今日送貨趟次：已推播但尚未完成（含待出發 + 送貨中）
    active_sessions = DeliverySession.objects.filter(
        date=today,
        finished_at__isnull=True,
    ).select_related('employee__user').order_by('employee', 'trip_number')

    delivery_status = []
    for session in active_sessions:
        tasks = list(session.tasks.select_related('customer').order_by('order'))
        total = len(tasks)
        if total == 0:
            continue   # 空趟次不顯示
        completed  = sum(1 for t in tasks if t.status == 'completed')
        next_task  = next((t for t in tasks if t.status == 'pending'), None)
        last_done  = next((t for t in reversed(tasks) if t.status == 'completed'), None)
        pending_tasks = [t for t in tasks if t.status == 'pending']
        task_customers = [t.customer for t in pending_tasks if t.customer]
        avg_stop  = _get_avg_stop_minutes(session.employee)
        predicted, drive = _build_prediction(task_customers, len(pending_tasks), avg_stop)
        delivery_status.append({
            'employee':   session.employee,
            'session':    session,
            'tasks':      tasks,
            'total':      total,
            'completed':  completed,
            'next_task':  next_task,
            'last_done':  last_done,
            'progress':   int(completed / total * 100) if total else 0,
            'all_done':   completed == total,
            'is_started': session.started_at is not None,
            'predicted':  predicted,
            'drive':      drive,
            'avg_stop':   avg_stop,
            'remaining':  len(pending_tasks),
        })

    # 今日送貨統計
    today_trips_total     = DeliverySession.objects.filter(date=today).count()
    today_trips_finished  = DeliverySession.objects.filter(date=today, finished_at__isnull=False).count()
    today_trips_active    = DeliverySession.objects.filter(date=today, started_at__isnull=False, finished_at__isnull=True).count()

    # 今日遲到人數（同時標記每位員工的 is_late 旗標供前端篩選用）
    from datetime import datetime as dt
    late_count = 0
    for emp_data in employee_list:
        emp = emp_data['employee']
        emp_data['is_late'] = False
        if emp_data['status'] != 'absent' and emp.work_start_time:
            clock_in = AttendanceRecord.objects.filter(
                employee=emp, timestamp__date=today, record_type='clock_in'
            ).first()
            if clock_in:
                from django.utils.timezone import localtime
                ci_time = localtime(clock_in.timestamp).time()
                scheduled = dt.combine(today, emp.work_start_time)
                actual    = dt.combine(today, ci_time)
                if (actual - scheduled).total_seconds() > 600:
                    late_count += 1
                    emp_data['is_late'] = True

    # 待處理事項（僅 admin / superuser）
    is_admin = (
        request.user.is_superuser or
        request.user.groups.filter(name__in=['admin', 'finance']).exists()
    )
    pending_leaves = (
        LeaveRequest.objects
        .filter(status='pending')
        .select_related('employee__user')
        .order_by('requested_at')
    ) if is_admin else []

    pending_corrections = (
        LocationCorrectionRequest.objects
        .filter(status='pending')
        .select_related('customer', 'requested_by__user')
        .order_by('requested_at')
    ) if is_admin else []

    # 補打卡提醒：最近 14 天有上班打卡，但有缺打卡的日期
    # 偵測情境：缺下班、缺午休結束、有上下班但完全沒有午休紀錄
    attendance_anomalies = []
    if is_admin:
        from datetime import timedelta
        period_start = today - timedelta(days=14)
        # 取期間內所有 clock_in（不含今天，今天還在工作中不算異常）
        ci_records = (
            AttendanceRecord.objects
            .filter(record_type='clock_in',
                    timestamp__date__gte=period_start,
                    timestamp__date__lt=today)
            .select_related('employee__user')
            .order_by('-timestamp__date', 'employee')
        )
        seen = set()  # 避免同一 employee+date 重複
        for ci in ci_records:
            from django.utils.timezone import localtime as ltime
            ci_date = ltime(ci.timestamp).date()
            key = (ci.employee_id, ci_date)
            if key in seen:
                continue
            seen.add(key)
            emp = ci.employee
            has_out = AttendanceRecord.objects.filter(
                employee=emp, record_type='clock_out', timestamp__date=ci_date
            ).exists()
            has_bs  = AttendanceRecord.objects.filter(
                employee=emp, record_type='break_start', timestamp__date=ci_date
            ).exists()
            has_be  = AttendanceRecord.objects.filter(
                employee=emp, record_type='break_end', timestamp__date=ci_date
            ).exists()

            if not has_out:
                # 優先標記缺下班；若同時有午休開始但缺午休結束，先提示那個
                missing = 'break_end' if (has_bs and not has_be) else 'clock_out'
                attendance_anomalies.append({
                    'employee': emp,
                    'date':     ci_date,
                    'missing':  missing,
                })
            else:
                # 有上下班的情況下，檢查午休紀錄
                if has_bs and not has_be:
                    # 有午休開始但缺午休結束
                    attendance_anomalies.append({
                        'employee': emp,
                        'date':     ci_date,
                        'missing':  'break_end',
                    })
                elif not has_bs:
                    # 完全沒有午休紀錄
                    # 只在「早上來、下午才走」的情況下提醒（真的跨越了午餐時段）
                    # 例：08:00 → 12:30 不提醒；08:00 → 13:00 以後才提醒
                    co_rec = AttendanceRecord.objects.filter(
                        employee=emp, record_type='clock_out', timestamp__date=ci_date
                    ).first()
                    from django.utils.timezone import localtime as ltime
                    from datetime import time as dtime
                    ci_time  = ltime(ci.timestamp).time()
                    co_time  = ltime(co_rec.timestamp).time()
                    # 上班在中午前，下班在下午一點後 → 確實跨越午餐時段
                    if ci_time < dtime(12, 0) and co_time >= dtime(13, 0):
                        attendance_anomalies.append({
                            'employee': emp,
                            'date':     ci_date,
                            'missing':  'break_start',
                        })

        # 過濾掉已被標記為「正常」的異常
        dismissed = set(
            AttendanceAnomalyDismissal.objects
            .filter(date__gte=period_start)
            .values_list('employee_id', 'date', 'anomaly_type')
        )
        attendance_anomalies = [
            a for a in attendance_anomalies
            if (a['employee'].pk, a['date'], a['missing']) not in dismissed
        ]

    # ── 新手引導（管理員專用，5 步驟）─────────────────────────
    onboarding_steps = []
    if is_admin:
        from django.urls import reverse
        has_employees  = Employee.objects.exists()
        has_schedule   = Employee.objects.filter(work_start_time__isnull=False).exists()
        has_line       = Employee.objects.filter(
            line_user_id__isnull=False
        ).exclude(line_user_id='').exists()
        has_delivery   = DeliveryTask.objects.exists()
        has_attendance = AttendanceRecord.objects.exists()

        onboarding_steps = [
            {
                'step':  1,
                'label': '建立員工名單',
                'desc':  '新增員工姓名、職稱與薪資結構',
                'done':  has_employees,
                'url':   reverse('dashboard:employee_add'),
            },
            {
                'step':  2,
                'label': '設定班次規則',
                'desc':  '設定每位員工的上班時間',
                'done':  has_schedule,
                'url':   reverse('dashboard:employee_list'),
            },
            {
                'step':  3,
                'label': '員工綁定 LINE',
                'desc':  '讓員工掃 QR Code 完成綁定',
                'done':  has_line,
                'url':   reverse('dashboard:binding_list'),
            },
            {
                'step':  4,
                'label': '建立第一筆配送任務',
                'desc':  '測試配送推播與送達流程',
                'done':  has_delivery,
                'url':   reverse('dashboard:delivery_plan'),
            },
            {
                'step':  5,
                'label': '確認打卡與薪資計算',
                'desc':  '確認出勤記錄與薪資數字正確',
                'done':  has_attendance,
                'url':   reverse('dashboard:salary'),
            },
        ]

    onboarding_done       = all(s['done'] for s in onboarding_steps)
    onboarding_done_count = sum(1 for s in onboarding_steps if s['done'])
    onboarding_total      = len(onboarding_steps)

    return render(request, 'attendance/dashboard.html', {
        'employee_list':          employee_list,
        'counts':                 counts,
        'today':                  today,
        'delivery_status':        delivery_status,
        'pending_leaves':         pending_leaves,
        'pending_corrections':    pending_corrections,
        'today_trips_total':      today_trips_total,
        'today_trips_finished':   today_trips_finished,
        'today_trips_active':     today_trips_active,
        'late_count':             late_count,
        'attendance_anomalies':   attendance_anomalies,
        'is_admin':               is_admin,
        'onboarding_steps':       onboarding_steps,
        'onboarding_done':        onboarding_done,
        'onboarding_done_count':  onboarding_done_count,
        'onboarding_total':       onboarding_total,
    })


@login_required
def add_record(request):
    """管理員補打卡（新增一筆 AttendanceRecord）"""
    VALID_TYPES = dict(AttendanceRecord.RECORD_TYPE_CHOICES)
    employees   = Employee.objects.select_related('user').order_by('employee_id')

    # ── POST 處理（表單送出）──────────────────────────────
    if request.method == 'POST':
        employee_id = request.POST.get('employee_id', '').strip()
        date_str    = request.POST.get('date', '').strip()
        record_type = request.POST.get('record_type', '').strip()
        time_str    = request.POST.get('time', '').strip()
        next_url    = request.POST.get('next') or '/'

        # 驗證
        try:
            datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            messages.error(request, '日期格式錯誤')
            return redirect(request.path)

        if record_type not in VALID_TYPES:
            messages.error(request, '打卡類型不合法')
            return redirect(request.path)

        employee = get_object_or_404(Employee, pk=employee_id)

        try:
            naive_dt = datetime.strptime(f"{date_str} {time_str}", '%Y-%m-%d %H:%M')
            aware_dt = timezone.make_aware(naive_dt)
            target_date = naive_dt.date()

            # 同員工同日同類型 → 蓋掉；沒有才新增
            existing = AttendanceRecord.objects.filter(
                employee=employee,
                record_type=record_type,
                timestamp__date=target_date,
            ).first()

            label = VALID_TYPES.get(record_type, record_type)
            name  = employee.user.get_full_name() or employee.user.username

            if existing:
                existing.timestamp = aware_dt
                existing.source    = 'manual'
                existing.save(update_fields=['timestamp', 'source'])
                messages.success(request, f'✅ 已更新 {name} 補打卡：{label} {time_str}')
            else:
                AttendanceRecord.objects.create(
                    employee=employee,
                    record_type=record_type,
                    timestamp=aware_dt,
                    source='manual',
                    is_valid=True,
                )
                messages.success(request, f'✅ 已為 {name} 補打卡：{label} {time_str}')
        except (ValueError, Exception) as e:
            messages.error(request, f'補打卡失敗：{e}')

        return redirect(next_url)

    # ── GET：顯示表單 ─────────────────────────────────────
    # 如果 URL 帶了預填參數（從報表頁跳來），就預帶入
    employee_id = request.GET.get('employee_id', '')
    date_str    = request.GET.get('date') or timezone.localdate().strftime('%Y-%m-%d')
    record_type = request.GET.get('type', '')
    next_url    = request.GET.get('next') or '/'

    # 嘗試預選員工
    pre_employee = None
    if employee_id:
        try:
            pre_employee = Employee.objects.get(pk=employee_id)
        except Employee.DoesNotExist:
            pass

    return render(request, 'attendance/add_record.html', {
        'employees':     employees,
        'pre_employee':  pre_employee,
        'date_str':      date_str,
        'record_type':   record_type,
        'valid_types':   VALID_TYPES,
        'next_url':      next_url,
    })


@login_required
def daily_records(request):
    """
    檢視並一次性補齊某員工某天的全部打卡紀錄。
    GET：顯示當天 4 種打卡的現況（已有的預帶入、沒有的空白）。
    POST：整批 upsert（有紀錄→更新時間，無紀錄→新增）。
    """
    SLOTS = [
        ('clock_in',    '▶ 上班打卡'),
        ('break_start', '⏸ 午休開始'),
        ('break_end',   '▶ 午休結束'),
        ('clock_out',   '■ 下班打卡'),
    ]
    employees = Employee.objects.select_related('user').order_by('employee_id')

    # 讀取員工 / 日期（GET 或 POST 都可能帶）
    employee_id = (request.POST.get('employee_id') or request.GET.get('employee_id', '')).strip()
    date_str    = (request.POST.get('date') or request.GET.get('date') or
                   timezone.localdate().strftime('%Y-%m-%d')).strip()

    # 解析日期
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        target_date = timezone.localdate()
        date_str    = target_date.strftime('%Y-%m-%d')

    # 載入員工與當天既有紀錄
    employee    = None
    records_map = {}   # record_type → AttendanceRecord
    if employee_id:
        try:
            employee = Employee.objects.select_related('user').get(pk=employee_id)
            for rec in AttendanceRecord.objects.filter(
                employee=employee, timestamp__date=target_date
            ).order_by('timestamp'):
                # 同類型只取第一筆（理論上不應有重複）
                if rec.record_type not in records_map:
                    records_map[rec.record_type] = rec
        except Employee.DoesNotExist:
            pass

    # ── POST：整批儲存 ────────────────────────────────────
    if request.method == 'POST' and employee:
        saved_labels  = []
        error_labels  = []

        for rtype, label in SLOTS:
            time_str = request.POST.get(f'time_{rtype}', '').strip()
            if not time_str:
                continue  # 空白 → 不處理

            try:
                naive_dt = datetime.strptime(f"{date_str} {time_str}", '%Y-%m-%d %H:%M')
                aware_dt = timezone.make_aware(naive_dt)

                existing = records_map.get(rtype)
                if existing:
                    # 只有時間真的改了才寫入
                    old_time = timezone.localtime(existing.timestamp).strftime('%H:%M')
                    if old_time != time_str:
                        existing.timestamp = aware_dt
                        existing.save(update_fields=['timestamp'])
                        saved_labels.append(f'{label}（更新）')
                else:
                    AttendanceRecord.objects.create(
                        employee=employee,
                        record_type=rtype,
                        timestamp=aware_dt,
                        source='manual',
                        is_valid=True,
                    )
                    saved_labels.append(f'{label}（新增）')
            except (ValueError, Exception) as e:
                error_labels.append(f'{label}：{e}')

        name = employee.user.get_full_name() or employee.user.username
        if saved_labels:
            messages.success(request, f'✅ {name} {target_date} 已儲存：{"、".join(saved_labels)}')
        elif not error_labels:
            messages.info(request, '沒有異動（時間未改變或均空白）')
        if error_labels:
            messages.error(request, f'部分失敗：{"、".join(error_labels)}')

        # 重新 GET 同頁以反映最新紀錄
        return redirect(f"{request.path}?employee_id={employee.pk}&date={date_str}")

    # ── GET：組裝 slots 給模板 ────────────────────────────
    slots = []
    for rtype, label in SLOTS:
        rec = records_map.get(rtype)
        slots.append({
            'type':       rtype,
            'label':      label,
            'record':     rec,
            'time_value': timezone.localtime(rec.timestamp).strftime('%H:%M') if rec else '',
            'exists':     rec is not None,
        })

    return render(request, 'attendance/daily_records.html', {
        'employees':   employees,
        'employee':    employee,
        'date_str':    date_str,
        'slots':       slots,
        'target_date': target_date,
    })


@login_required
def dismiss_anomaly(request):
    """AJAX POST：將某筆出勤異常標記為「正常，不需補打卡」"""
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)

    employee_id  = request.POST.get('employee_id', '').strip()
    date_str     = request.POST.get('date', '').strip()
    anomaly_type = request.POST.get('anomaly_type', '').strip()

    VALID_TYPES = {'clock_out', 'break_start', 'break_end'}
    if anomaly_type not in VALID_TYPES:
        return JsonResponse({'ok': False, 'error': '不合法的異常類型'}, status=400)

    try:
        date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'ok': False, 'error': '日期格式錯誤'}, status=400)

    employee = get_object_or_404(Employee, pk=employee_id)

    AttendanceAnomalyDismissal.objects.get_or_create(
        employee=employee,
        date=date,
        anomaly_type=anomaly_type,
        defaults={'dismissed_by': request.user},
    )
    return JsonResponse({'ok': True})


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

    # 判斷打卡類型（依最後一筆的 record_type 決定，避免重複寫入導致 count 錯誤）
    today = timezone.localdate()
    last = AttendanceRecord.objects.filter(
        employee=emp, timestamp__date=today
    ).order_by('-timestamp').first()

    # 重複防護：2 分鐘內已有紀錄 → 直接回傳成功
    if last and (timezone.now() - last.timestamp).total_seconds() < 120:
        return JsonResponse({'ok': True, 'message': f'{emp.user.get_full_name() or emp.user.username} 打卡已記錄', 'duplicate': True})

    last_type = last.record_type if last else None

    if last_type is None:
        record_type = 'clock_in'
    elif last_type == 'clock_in':
        if not emp.line_user_id:
            return JsonResponse({'ok': False, 'message': '該員工未綁定 LINE，無法選擇打卡類型'})

        # 推播 Flex Message 給員工選擇（帶入刷卡時間，供 rfid_confirm 做時效驗證）
        swipe_ts = int(timezone.now().timestamp())
        flex = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": "請選擇打卡類型", "weight": "bold", "size": "xl"},
                    {"type": "text", "text": "請在 10 分鐘內選擇，逾時需重新刷卡",
                     "size": "xs", "color": "#9ca3af", "margin": "sm", "wrap": True},
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
                            "data": f"action=rfid_punch&record_type=break_start&employee_id={emp.pk}&swipe_ts={swipe_ts}"
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
                            "data": f"action=rfid_punch&record_type=clock_out&employee_id={emp.pk}&swipe_ts={swipe_ts}"
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
    elif last_type == 'break_start':
        record_type = 'break_end'
    elif last_type in ('break_end', 'clock_in'):
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
