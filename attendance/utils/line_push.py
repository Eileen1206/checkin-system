"""LINE 推播的統一出口，內建去重。

會重複觸發的情況很多：老闆連點兩下同意、LINE 重送 webhook 事件、
排程比預期跑得密、使用者重整頁面。這裡用 cache.add 做原子性的鎖，
同一個 dedupe_key 在期限內只會真的送出一次。

送失敗會把鎖放掉，讓下次重試；送成功才留著鎖。
"""
from django.conf import settings
from django.core.cache import cache

from linebot.v3.messaging import (
    ApiClient, Configuration, MessagingApi, PushMessageRequest, TextMessage,
)


DEFAULT_TTL = 86400      # 鎖的預設保留時間（秒）


def _client():
    return Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)


def push(line_user_id, messages):
    """直接推播，不做去重。messages 可以是字串或 LINE 訊息物件的 list。"""
    if not line_user_id:
        return False
    if isinstance(messages, str):
        messages = [TextMessage(text=messages)]
    elif not isinstance(messages, list):
        messages = [messages]

    with ApiClient(_client()) as api_client:
        MessagingApi(api_client).push_message(PushMessageRequest(
            to=line_user_id, messages=messages,
        ))
    return True


def push_once(line_user_id, messages, dedupe_key, ttl=DEFAULT_TTL):
    """同一個 dedupe_key 在 ttl 內只會真的送出一次。

    回傳 True 表示這次有送出；False 表示被去重擋下或沒有收件人。
    推播失敗會放掉鎖並回 False，讓下一次有機會重試。
    """
    if not line_user_id or not dedupe_key:
        return False

    lock_key = f'linepush:{dedupe_key}'
    if not cache.add(lock_key, True, ttl):
        return False        # 已經送過（或正在送）

    try:
        push(line_user_id, messages)
    except Exception as e:
        cache.delete(lock_key)
        print(f'[line push] {dedupe_key}: {e}')
        return False
    return True


def already_sent(dedupe_key):
    """查詢某個 key 是否已送過（測試與除錯用）。"""
    return cache.get(f'linepush:{dedupe_key}') is not None


def reset(dedupe_key):
    """放掉某個 key 的鎖，讓它可以重送。"""
    cache.delete(f'linepush:{dedupe_key}')
