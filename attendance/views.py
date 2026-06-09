from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.shortcuts import get_object_or_404
from .models import DeliveryTask, AttendanceRecord, LeaveRecord, LeaveRequest
from django.utils import timezone
from django.core.cache import cache
from datetime import datetime, timedelta, date as date_type, time
import requests

from linebot.v3.webhook import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import MessageEvent, TextMessageContent, LocationMessageContent, FollowEvent, PostbackEvent
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    PostbackAction,
    TemplateMessage,
    ButtonsTemplate,
    PushMessageRequest,
    FlexMessage,
    FlexContainer,
    QuickReply,
    QuickReplyItem,
    LocationAction,
)
import math

def _haversine_meters(lat1, lng1, lat2, lng2):
    """計算兩座標距離（公尺）"""
    R = 6371000
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) *
         math.sin((lng2 - lng1) * p / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


handler = WebhookHandler(settings.LINE_CHANNEL_SECRET)
configuration = Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)


def _welcome_flex():
    return {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#1a1a2e",
            "contents": [
                {"type": "text", "text": "政旭汽車材料行", "color": "#ffffff", "size": "lg", "weight": "bold"},
                {"type": "text", "text": "員工打卡系統", "color": "#aaaacc", "size": "sm"}
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {"type": "text", "text": "📋 可用功能", "weight": "bold", "size": "sm"},
                {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "contents": [
                        {
                            "type": "box", "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "打卡", "flex": 5, "size": "sm", "color": "#333333"},
                                {"type": "text", "text": "刷卡感應即會記錄，立即通知", "flex": 8, "size": "sm", "color": "#888888", "wrap": True}
                            ]
                        },
                        {
                            "type": "box", "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "查詢", "flex": 5, "size": "sm", "color": "#333333"},
                                {"type": "text", "text": "查看今日出勤紀錄", "flex": 8, "size": "sm", "color": "#888888"}
                            ]
                        },
                        {
                            "type": "box", "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "本月出勤", "flex": 5, "size": "sm", "color": "#333333"},
                                {"type": "text", "text": "查看本月總工時", "flex": 8, "size": "sm", "color": "#888888"}
                            ]
                        },
                        {
                            "type": "box", "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "送貨路線", "flex": 5, "size": "sm", "color": "#333333"},
                                {"type": "text", "text": "管理員推播後可確認完成", "flex": 8, "size": "sm", "color": "#888888", "wrap": True}
                            ]
                        },
                        {
                            "type": "box", "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "說明", "flex": 5, "size": "sm", "color": "#333333"},
                                {"type": "text", "text": "顯示功能使用說明", "flex": 8, "size": "sm", "color": "#888888"}
                            ]
                        },
                    ]
                }
            ]
        },
        "footer": {
            "type": "box", "layout": "vertical",
            "contents": [
                {"type": "text", "text": "如有問題請聯絡管理員", "size": "xs", "color": "#aaaaaa", "align": "center"}
            ]
        }
    }


# ──────────────────────────────────────────
# Webhook 入口
# ──────────────────────────────────────────

@csrf_exempt
def webhook(request):
    if request.method != 'POST':
        return HttpResponse('Method Not Allowed', status=405)

    signature = request.META.get('HTTP_X_LINE_SIGNATURE', '')
    body = request.body.decode('utf-8')

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return HttpResponse('Invalid signature', status=400)

    return HttpResponse('OK')


# ──────────────────────────────────────────
# 加好友事件
# ──────────────────────────────────────────

@handler.add(FollowEvent)
def handle_follow(event):
    """使用者加 Bot 好友時觸發"""
    print(f'[LINE] 新好友 user_id: {event.source.user_id}')
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(text='👋 歡迎加入政旭汽車材料行！\n請輸入管理員提供的員工綁定碼完成綁定。'),
                    FlexMessage(
                        alt_text='功能說明',
                        contents=FlexContainer.from_dict(_welcome_flex())
                    )
                ]
            )
        )


# ──────────────────────────────────────────
# 文字訊息事件
# ──────────────────────────────────────────

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    """收到文字訊息時觸發"""
    line_user_id = event.source.user_id
    text = event.message.text.strip()

    reply_messages = _process_message(text, line_user_id)

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=reply_messages
            )
        )


# ──────────────────────────────────────────
# Postback 事件
# ──────────────────────────────────────────

@handler.add(PostbackEvent)
def handle_postback(event):
    """使用者點選按鈕後觸發"""
    from attendance.models import Employee

    line_user_id = event.source.user_id
    data = event.postback.data

    params = dict(p.split('=') for p in data.split('&'))
    action = params.get('action')

    if action == 'leave_approve':
        request_pk = params.get('request_pk')
        try:
            leave_req = LeaveRequest.objects.select_related('employee__user').get(pk=request_pk)
            emp = leave_req.employee
            emp_name = emp.user.get_full_name() or emp.user.username
            if leave_req.status == 'pending':
                leave_req.status = 'approved'
                leave_req.processed_at = timezone.now()
                leave_req.save()
                for d in leave_req.dates:
                    LeaveRecord.objects.get_or_create(employee=emp, date=d)
                dates_display = '\n'.join(leave_req.dates)
                # ✅ 通知員工在 if 裡面，只執行一次
                if emp.line_user_id:
                    with ApiClient(configuration) as api_client:
                        MessagingApi(api_client).push_message(PushMessageRequest(
                            to=emp.line_user_id,
                            messages=[TextMessage(text=f'✅ 以下請假申請已核准：\n{dates_display}')]
                        ))
                reply_msg = TextMessage(text=f'✅ 已核准 {emp_name} 請假：\n{dates_display}')
            else:
                reply_msg = TextMessage(text='⚠️ 此請假申請已處理過了')
        except LeaveRequest.DoesNotExist:
            reply_msg = TextMessage(text='⚠️ 找不到此請假申請')
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(ReplyMessageRequest(
                reply_token=event.reply_token, messages=[reply_msg]
            ))
        return

    elif action == 'leave_deny':
        request_pk = params.get('request_pk')
        try:
            leave_req = LeaveRequest.objects.select_related('employee__user').get(pk=request_pk)
            emp = leave_req.employee
            emp_name = emp.user.get_full_name() or emp.user.username
            if leave_req.status == 'pending':
                leave_req.status = 'denied'
                leave_req.processed_at = timezone.now()
                leave_req.save()
                dates_display = '\n'.join(leave_req.dates)
                # ✅ 通知員工在 if 裡面，只執行一次
                if emp.line_user_id:
                    with ApiClient(configuration) as api_client:
                        MessagingApi(api_client).push_message(PushMessageRequest(
                            to=emp.line_user_id,
                            messages=[TextMessage(text=f'❌ 以下請假申請已被拒絕：\n{dates_display}')]
                        ))
                reply_msg = TextMessage(text=f'已拒絕 {emp_name} 請假：\n{dates_display}')
            else:
                reply_msg = TextMessage(text='⚠️ 此請假申請已處理過了')
        except LeaveRequest.DoesNotExist:
            reply_msg = TextMessage(text='⚠️ 找不到此請假申請')
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(ReplyMessageRequest(
                reply_token=event.reply_token, messages=[reply_msg]
            ))
        return

    elif action == 'approve_clockout':
        # 舊版 postback 流程已停用，改用網頁表單（delivery/approve-clockout/）
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text='此確認按鈕已過期，請請員工重新申請。')]
            ))
        return

    else:
        try:
            employee = Employee.objects.get(line_user_id=line_user_id)
        except Employee.DoesNotExist:
            return

        if action == 'query':
            reply_msg = TextMessage(text=get_today_summary(employee))
        elif action == 'monthly':
            reply_msg = TextMessage(text=get_monthly_summary(employee))
        elif action == 'rfid_punch':
            # 第一步：推確認訊息，不直接寫入
            emp_id = params.get('employee_id')
            record_type = params.get('record_type')
            swipe_ts = params.get('swipe_ts', '0')
            emp = get_object_or_404(Employee, pk=emp_id)

            # 時效檢查：超過 10 分鐘拒絕
            import time as _time
            if _time.time() - float(swipe_ts) > 600:
                reply_msg = TextMessage(text='⚠️ 此打卡確認已逾時（超過 10 分鐘），請重新刷卡。')
                with ApiClient(configuration) as api_client:
                    MessagingApi(api_client).reply_message(ReplyMessageRequest(
                        reply_token=event.reply_token, messages=[reply_msg]
                    ))
                return

            # 若要下班但午休未結束 → 拒絕
            if record_type == 'clock_out':
                today = timezone.localdate()
                has_break_start = AttendanceRecord.objects.filter(
                    employee=emp, record_type='break_start', timestamp__date=today
                ).exists()
                has_break_end = AttendanceRecord.objects.filter(
                    employee=emp, record_type='break_end', timestamp__date=today
                ).exists()
                if has_break_start and not has_break_end:
                    reply_msg = TextMessage(text='⚠️ 請先刷卡結束午休，再下班打卡')
                    with ApiClient(configuration) as api_client:
                        MessagingApi(api_client).reply_message(ReplyMessageRequest(
                            reply_token=event.reply_token, messages=[reply_msg]
                        ))
                    return

            label = '午休開始' if record_type == 'break_start' else '直接下班'
            confirm_flex = {
                "type": "bubble",
                "body": {
                    "type": "box", "layout": "vertical",
                    "contents": [
                        {"type": "text", "text": f"確定要【{label}】嗎？",
                         "weight": "bold", "size": "lg", "wrap": True},
                        {"type": "text", "text": f"時間：{timezone.localtime().strftime('%H:%M')}",
                         "size": "sm", "color": "#888888", "margin": "sm"},
                    ]
                },
                "footer": {
                    "type": "box", "layout": "horizontal", "spacing": "sm",
                    "contents": [
                        {
                            "type": "button", "style": "primary",
                            "color": "#1a1a1a", "height": "sm",
                            "action": {
                                "type": "postback", "label": "✅ 確定",
                                "data": f"action=rfid_confirm&record_type={record_type}&employee_id={emp_id}&swipe_ts={swipe_ts}"
                            }
                        },
                        {
                            "type": "button", "style": "secondary",
                            "height": "sm",
                            "action": {
                                "type": "postback", "label": "✖ 取消",
                                "data": "action=rfid_cancel"
                            }
                        },
                    ]
                }
            }
            reply_msg = FlexMessage(
                alt_text=f'確定要{label}嗎？',
                contents=FlexContainer.from_dict(confirm_flex)
            )

        elif action == 'rfid_confirm':
            emp_id = params.get('employee_id')
            record_type = params.get('record_type')
            swipe_ts = params.get('swipe_ts', '0')
            emp = get_object_or_404(Employee, pk=emp_id)

            # 時效驗證（10 分鐘內有效）
            import time as _time
            if _time.time() - float(swipe_ts) > 600:
                reply_msg = TextMessage(text='⚠️ 此打卡確認已逾時（超過 10 分鐘），請重新刷卡。')
                with ApiClient(configuration) as api_client:
                    MessagingApi(api_client).reply_message(ReplyMessageRequest(
                        reply_token=event.reply_token, messages=[reply_msg]
                    ))
                return

            # 若選直接下班但午休尚未結束 → 拒絕
            if record_type == 'clock_out':
                today = timezone.localdate()
                has_break_start = AttendanceRecord.objects.filter(
                    employee=emp, record_type='break_start', timestamp__date=today
                ).exists()
                has_break_end = AttendanceRecord.objects.filter(
                    employee=emp, record_type='break_end', timestamp__date=today
                ).exists()
                if has_break_start and not has_break_end:
                    reply_msg = TextMessage(text='⚠️ 請先刷卡結束午休，再下班打卡')
                    with ApiClient(configuration) as api_client:
                        MessagingApi(api_client).reply_message(ReplyMessageRequest(
                            reply_token=event.reply_token, messages=[reply_msg]
                        ))
                    return

            from datetime import timedelta as _td
            duplicate = AttendanceRecord.objects.filter(
                employee=emp,
                record_type=record_type,
                timestamp__gte=timezone.now() - _td(minutes=5),
            ).exists()

            label = '午休開始' if record_type == 'break_start' else '下班打卡'
            if not duplicate:
                AttendanceRecord.objects.create(
                    employee=emp,
                    record_type=record_type,
                    timestamp=timezone.now(),
                    latitude=0, longitude=0,
                    is_valid=True, distance_meters=0,
                    source='rfid',
                )
            reply_msg = TextMessage(text=f'✅ {label}成功！\n時間：{timezone.localtime().strftime("%H:%M")}')

        elif action == 'rfid_cancel':
            reply_msg = TextMessage(text='已取消，打卡紀錄不受影響。')

        elif action == 'delivery_done':
            task_id = params.get('task_id')
            task = get_object_or_404(DeliveryTask, pk=task_id)

            if task.status == 'completed':
                reply_msg = TextMessage(text='這站已經完成過了！')
            elif not task.customer or not task.customer.lat or not task.customer.lng:
                task.status = 'completed'
                task.completed_at = timezone.localtime()
                task.save()
                reply_msg = TextMessage(text=f'✅ 第 {task.order} 站（{task.customer_name}）完成！')
            else:
                cache.set(f'delivery_loc_{line_user_id}', task_id, 300)
                reply_msg = TextMessage(
                    text=f'📍 請分享你的位置，確認已到達第 {task.order} 站（{task.customer_name}）',
                    quick_reply=QuickReply(items=[
                        QuickReplyItem(action=LocationAction(label='📍 分享位置'))
                    ])
                )


        elif action == 'delivery_clockout_request':
            # 第一步：顯示確認卡，防止誤觸
            try:
                today_str = timezone.localtime().strftime('%Y-%m-%d')
                day_key   = f'clockout_req_{employee.pk}_{today_str}'

                if AttendanceRecord.objects.filter(employee=employee, timestamp__date=date_type.today(), record_type='clock_out').exists():
                    reply_msg = TextMessage(text='⚠️ 已經記錄過下班時間了')
                elif cache.get(day_key):
                    reply_msg = TextMessage(text='⏳ 今天已送出申請，請等待管理員確認。')
                else:
                    confirm_flex = {
                        "type": "bubble",
                        "body": {
                            "type": "box", "layout": "vertical", "spacing": "sm",
                            "contents": [
                                {"type": "text", "text": "送貨接下班",
                                 "weight": "bold", "size": "xl", "color": "#111827"},
                                {"type": "text", "text": "確定要送出申請給管理員嗎？",
                                 "size": "sm", "color": "#6b7280", "wrap": True, "margin": "sm"},
                            ],
                        },
                        "footer": {
                            "type": "box", "layout": "horizontal", "spacing": "sm",
                            "contents": [
                                {
                                    "type": "button", "style": "primary",
                                    "color": "#111827", "height": "sm",
                                    "action": {
                                        "type": "postback", "label": "✅ 確定送出",
                                        "data": "action=delivery_clockout_confirm"
                                    }
                                },
                                {
                                    "type": "button", "style": "secondary",
                                    "height": "sm",
                                    "action": {
                                        "type": "postback", "label": "✖ 取消",
                                        "data": "action=delivery_clockout_cancel"
                                    }
                                },
                            ],
                        },
                    }
                    reply_msg = FlexMessage(
                        alt_text='確定要申請送貨接下班嗎？',
                        contents=FlexContainer.from_dict(confirm_flex)
                    )
            except Exception as _e:
                print(f'[delivery_clockout_request error] {_e}')
                reply_msg = TextMessage(text='⚠️ 操作失敗，請稍後再試。')

        elif action == 'delivery_clockout_confirm':
            # 第二步：員工確認後才真正送出給管理員
            try:
                import uuid as _uuid
                now_local = timezone.localtime()
                today_str = now_local.strftime('%Y-%m-%d')
                day_key   = f'clockout_req_{employee.pk}_{today_str}'

                if AttendanceRecord.objects.filter(employee=employee, timestamp__date=date_type.today(), record_type='clock_out').exists():
                    reply_msg = TextMessage(text='⚠️ 已經記錄過下班時間了')
                elif cache.get(day_key):
                    reply_msg = TextMessage(text='⏳ 今天已送出申請，請等待管理員確認。')
                else:
                    token = str(_uuid.uuid4())
                    cache.set(f'clockout_token_{token}', {
                        'employee_id': employee.pk,
                        'date': today_str,
                        'request_time': now_local.strftime('%H:%M'),
                    }, 86400)

                    site_url = getattr(settings, 'SITE_URL', '').rstrip('/')
                    approve_url = f"{site_url}/dashboard/delivery/approve-clockout/?token={token}"

                    flex_body = {
                        "type": "bubble",
                        "body": {
                            "type": "box", "layout": "vertical", "spacing": "sm",
                            "contents": [
                                {"type": "text", "text": "送貨接下班申請",
                                 "weight": "bold", "size": "lg", "color": "#111827"},
                                {"type": "text",
                                 "text": employee.user.get_full_name() or employee.user.username,
                                 "size": "md", "color": "#374151", "margin": "sm"},
                                {"type": "text",
                                 "text": f"申請時間：{now_local.strftime('%H:%M')}",
                                 "size": "sm", "color": "#9ca3af", "margin": "xs"},
                            ],
                        },
                        "footer": {
                            "type": "box", "layout": "vertical",
                            "contents": [{
                                "type": "button", "style": "primary",
                                "color": "#111827", "height": "md",
                                "action": {
                                    "type": "uri",
                                    "label": "選擇下班時間 →",
                                    "uri": approve_url,
                                }
                            }],
                        }
                    }
                    with ApiClient(configuration) as api_client:
                        MessagingApi(api_client).push_message(PushMessageRequest(
                            to=settings.MANAGER_LINE_USER_ID,
                            messages=[FlexMessage(
                                alt_text=f'{employee.user.get_full_name() or employee.user.username} 申請送貨接下班，請點選確認',
                                contents=FlexContainer.from_dict(flex_body)
                            )]
                        ))
                    cache.set(day_key, True, 86400)
                    reply_msg = TextMessage(text='✅ 申請已送出，等待管理員確認')
            except Exception as _e:
                print(f'[delivery_clockout_confirm error] {_e}')
                reply_msg = TextMessage(text='⚠️ 申請失敗，請稍後再試。')

        elif action == 'delivery_clockout_cancel':
            reply_msg = TextMessage(text='已取消，申請未送出。')

        else:
            reply_msg = TextMessage(text='收到！')

        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[reply_msg]
                )
            )


# ──────────────────────────────────────────
# 位置訊息事件（送貨到站驗證）
# ──────────────────────────────────────────

@handler.add(MessageEvent, message=LocationMessageContent)
def handle_location(event):
    from attendance.models import Employee
    line_user_id = event.source.user_id
    lat = event.message.latitude
    lng = event.message.longitude

    pending_key = f'delivery_loc_{line_user_id}'
    task_id = cache.get(pending_key)

    if not task_id:
        return

    try:
        task = DeliveryTask.objects.select_related('customer').get(pk=task_id)
    except DeliveryTask.DoesNotExist:
        return

    cust = task.customer
    if not cust or not cust.lat or not cust.lng:
        return

    distance = _haversine_meters(lat, lng, float(cust.lat), float(cust.lng))
    ALLOWED_METERS = 100

    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        if distance <= ALLOWED_METERS:
            task.status = 'completed'
            task.completed_at = timezone.localtime()
            task.save()
            cache.delete(pending_key)
            msg = TextMessage(text=f'✅ 位置驗證通過（距客戶 {int(distance)} 公尺）\n第 {task.order} 站（{task.customer_name}）完成！')
        else:
            msg = TextMessage(text=f'❌ 位置不符，距客戶 {int(distance)} 公尺（需在 {ALLOWED_METERS} 公尺內）\n請到達客戶位置後再試一次。')

        api.reply_message(ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[msg]
        ))


# ──────────────────────────────────────────
# 訊息處理邏輯
# ──────────────────────────────────────────

def _process_message(text, line_user_id):
    """判斷訊息是綁定碼還是一般指令，回傳 list of message objects"""
    from attendance.models import BindingToken, Employee

    # 情況一：已綁定員工
    try:
        employee = Employee.objects.get(line_user_id=line_user_id)

        state_key = f'leave_state_{line_user_id}'
        if cache.get(state_key) == 'waiting_date':
            cache.delete(state_key)

            import re
            raw_parts = re.split(r'[,\s、，]+', text.strip())
            today = timezone.localdate()
            valid_dates, errors = [], []

            for part in raw_parts:
                part = part.strip()
                if not part:
                    continue
                try:
                    d = datetime.strptime(part, '%Y-%m-%d').date()
                    if d < today:
                        errors.append(f'{part}（不能是過去日期）')
                    elif d in valid_dates:
                        pass
                    else:
                        valid_dates.append(d)
                except ValueError:
                    errors.append(f'{part}（格式錯誤）')

            if errors:
                error_list = '\n'.join(errors)
                return [TextMessage(text=f'⚠️ 以下日期有問題：\n{error_list}\n\n請重新輸入「請假」再試一次。')]
            if not valid_dates:
                return [TextMessage(text='⚠️ 沒有有效日期，請重新輸入「請假」再試一次。')]

            valid_dates.sort()
            dates_display = '\n'.join(str(d) for d in valid_dates)

            leave_req = LeaveRequest.objects.create(
                employee=employee,
                dates=[str(d) for d in valid_dates],
            )

            manager_id = getattr(settings, 'MANAGER_LINE_USER_ID', '')
            emp_name = employee.user.get_full_name() or employee.user.username
            if manager_id:
                from linebot.v3.messaging import TemplateMessage, ButtonsTemplate, PostbackAction
                preview = dates_display if len(dates_display) <= 80 else dates_display[:77] + '…'
                template_msg = TemplateMessage(
                    alt_text=f'{emp_name} 申請請假',
                    template=ButtonsTemplate(
                        text=f'📋 請假申請\n員工：{emp_name}\n日期：\n{preview}',
                        actions=[
                            PostbackAction(
                                label='✅ 同意',
                                data=f'action=leave_approve&request_pk={leave_req.pk}'
                            ),
                            PostbackAction(
                                label='❌ 拒絕',
                                data=f'action=leave_deny&request_pk={leave_req.pk}'
                            ),
                        ]
                    )
                )
                with ApiClient(configuration) as api_client:
                    MessagingApi(api_client).push_message(PushMessageRequest(
                        to=manager_id,
                        messages=[template_msg]
                    ))

            return [TextMessage(text=f'✅ 已送出以下日期的請假申請，等待管理員審核：\n{dates_display}')]

        if text == '查詢':
            return [TextMessage(text=get_today_summary(employee))]
        elif text == '本月出勤':
            return [TextMessage(text=get_monthly_summary(employee))]
        elif text == '請假':
            cache.set(f'leave_state_{line_user_id}', 'waiting_date', 300)
            return [TextMessage(text='📅 請輸入請假日期，可一次輸入多個（用空格或逗號分隔）\n\n例如單天：\n2026-05-01\n\n例如多天：\n2026-05-01 2026-05-02 2026-05-03')]
        elif text == '說明':
            return [FlexMessage(
                alt_text='功能說明',
                contents=FlexContainer.from_dict(_welcome_flex())
            )]
        name = employee.user.get_full_name() or employee.user.username
        return [TextMessage(text=f'你好，{name}！\n輸入「說明」可查看所有功能。')]
    except Employee.DoesNotExist:
        pass

    # 情況二：綁定碼
    try:
        token_obj = BindingToken.objects.get(token=text)

        if token_obj.used:
            return [TextMessage(text='此綁定碼已使用過，請聯絡管理員重新產生。')]
        if not token_obj.is_valid_token():
            return [TextMessage(text='此綁定碼已過期，請聯絡管理員重新產生。')]

        employee = token_obj.employee
        employee.line_user_id = line_user_id
        employee.save()
        token_obj.used = True
        token_obj.save()

        menu_id = settings.RICHMENU_DELIVERY if employee.is_delivery else settings.RICHMENU_STAFF
        if menu_id:
            requests.post(
                f'https://api.line.me/v2/bot/user/{line_user_id}/richmenu/{menu_id}',
                headers={'Authorization': f'Bearer {settings.LINE_CHANNEL_ACCESS_TOKEN}'},
            )

        name = employee.user.get_full_name() or employee.user.username
        return [
            TextMessage(text=f'✅ 綁定成功！歡迎，{name}！\n工號：{employee.employee_id}\n\n現在可以使用打卡功能了。'),
            FlexMessage(
                alt_text='功能說明',
                contents=FlexContainer.from_dict(_welcome_flex())
            )
        ]

    except BindingToken.DoesNotExist:
        return [TextMessage(text='找不到此綁定碼，請確認是否正確，或聯絡管理員。')]


# ──────────────────────────────────────────
# 出勤查詢輔助函式
# ──────────────────────────────────────────

def get_today_summary(employee):
    from attendance.models import AttendanceRecord
    from django.utils import timezone
    from attendance.dashboard_views import get_work_hours

    today = timezone.localdate()
    records = AttendanceRecord.objects.filter(
        employee=employee,
        timestamp__date=today,
    ).order_by('timestamp')

    if not records:
        return '今天尚無打卡紀錄'

    type_label = {
        'clock_in': '上班打卡',
        'break_start': '午休開始',
        'break_end': '午休結束',
        'clock_out': '下班打卡',
    }

    lines = ['今日打卡紀錄', '──────────────']
    for record in records:
        time_str = timezone.localtime(record.timestamp).strftime('%H:%M')
        label = type_label[record.record_type]
        lines.append(f'{label} {time_str}')

    lines.append('──────────────')
    lines.append(f'今日工時：{get_work_hours(employee)}小時')

    return '\n'.join(lines)


def get_monthly_summary(employee):
    from attendance.models import AttendanceRecord
    from django.utils import timezone
    from django.utils.timezone import localtime
    import calendar
    import datetime

    now = timezone.localtime()
    year = now.year
    month = now.month
    _, total_days = calendar.monthrange(year, month)

    total_hours = 0.0
    worked_days = 0

    for day in range(1, total_days + 1):
        d = datetime.date(year, month, day)
        records = AttendanceRecord.objects.filter(
            employee=employee, timestamp__date=d
        )
        clock_in    = records.filter(record_type='clock_in').first()
        clock_out   = records.filter(record_type='clock_out').first()
        break_start = records.filter(record_type='break_start').first()
        break_end   = records.filter(record_type='break_end').first()

        if clock_in and clock_out:
            secs = (clock_out.timestamp - clock_in.timestamp).total_seconds()
            if break_start and break_end:
                secs -= (break_end.timestamp - break_start.timestamp).total_seconds()
            total_hours += round(max(secs, 0) / 3600, 1)
            worked_days += 1

    lines = ['本月出勤紀錄']
    lines.append(f'出勤天數：{worked_days} 天')
    lines.append(f'本月總工時：{round(total_hours, 1)} 小時')

    return '\n'.join(lines)
