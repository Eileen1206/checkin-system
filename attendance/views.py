from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings

from linebot.v3.webhook import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FollowEvent, PostbackEvent
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    QuickReply,
    QuickReplyItem,
    PostbackAction,
)

# 初始化 LINE SDK（用 settings.py 裡的憑證）
handler = WebhookHandler(settings.LINE_CHANNEL_SECRET)
configuration = Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)


@csrf_exempt
def webhook(request):
    if request.method != 'POST':
        return HttpResponse('Method Not Allowed', status=405)

    # 從 header 取出 LINE 的簽章
    signature = request.META.get('HTTP_X_LINE_SIGNATURE', '')
    body = request.body.decode('utf-8')

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return HttpResponse('Invalid signature', status=400)

    return HttpResponse('OK')


@handler.add(FollowEvent)
def handle_follow(event):
    """使用者加 Bot 好友時觸發"""
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(
                    text='歡迎加入政旭汽車材料行打卡系統！\n請輸入您的員工綁定碼完成綁定。'
                )]
            )
        )


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    """收到文字訊息時觸發"""
    from attendance.models import Employee
    from attendance.utils.punch import handle_punch

    text = event.message.text.strip()
    line_user_id = event.source.user_id

    # 處理綁定碼或一般訊息
    reply_text = _process_message(text, line_user_id)

    # 如果是已綁定員工傳「打卡」，執行打卡狀態機
    if reply_text is None:
        employee = Employee.objects.get(line_user_id=line_user_id)
        result = handle_punch(employee)

        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)

            if result['status'] == 'ask':
                # 需要詢問：顯示 Quick Reply 按鈕
                msg = TextMessage(
                    text='請選擇打卡類型：',
                    quick_reply=QuickReply(items=[
                        QuickReplyItem(action=PostbackAction(
                            label='🍱 午休', data='action=break_start'
                        )),
                        QuickReplyItem(action=PostbackAction(
                            label='👋 下班', data='action=clock_out'
                        )),
                    ])
                )
            else:
                msg = TextMessage(text=result['message'])

            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[msg])
            )
        return

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )


@handler.add(PostbackEvent)
def handle_postback(event):
    """使用者點選 Quick Reply 按鈕後觸發"""
    from attendance.models import Employee
    from attendance.utils.punch import handle_punch

    line_user_id = event.source.user_id
    data = event.postback.data  # 例如 'action=break_start'

    # 把 'action=break_start' 解析成 {'action': 'break_start'}
    params = dict(p.split('=') for p in data.split('&'))
    action = params.get('action')

    try:
        employee = Employee.objects.get(line_user_id=line_user_id)
    except Employee.DoesNotExist:
        return

    result = handle_punch(employee, action=action)

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=result['message'])]
            )
        )


def _process_message(text, line_user_id):
    """判斷訊息是綁定碼還是一般訊息"""
    from attendance.models import BindingToken, Employee

    # 情況一：已經綁定過的員工 → 傳「打卡」就執行打卡
    try:
        employee = Employee.objects.get(line_user_id=line_user_id)
        if text == '打卡':
            return None  # 交給 handle_text_message 用 Quick Reply 處理
        name = employee.user.get_full_name() or employee.user.username
        return f'你好，{name}！\n傳送「打卡」即可打卡。'
    except Employee.DoesNotExist:
        pass

    # 情況二：嘗試用綁定碼綁定
    try:
        token_obj = BindingToken.objects.get(token=text)

        if token_obj.used:
            return '此綁定碼已使用過，請聯絡管理員重新產生。'
        if not token_obj.is_valid_token():
            return '此綁定碼已過期，請聯絡管理員重新產生。'

        # 綁定成功：把 LINE user_id 存進員工資料
        employee = token_obj.employee
        employee.line_user_id = line_user_id
        employee.save()

        token_obj.used = True
        token_obj.save()

        name = employee.user.get_full_name() or employee.user.username
        return (
            f'✅ 綁定成功！\n'
            f'歡迎，{name}！\n'
            f'工號：{employee.employee_id}\n\n'
            f'現在可以使用打卡功能了。'
        )

    except BindingToken.DoesNotExist:
        return '找不到此綁定碼，請確認是否正確，或聯絡管理員。'
