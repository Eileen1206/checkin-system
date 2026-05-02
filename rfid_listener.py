import keyboard
import requests
from plyer import notification
import threading
import os
from dotenv import load_dotenv

load_dotenv()

BASE_URL     = os.getenv('RFID_SERVER_URL', 'https://checkin-system-production-4544.up.railway.app')
RFID_API_KEY = os.getenv('RFID_API_KEY', '')

CHECKIN_URL  = f'{BASE_URL}/dashboard/rfid/checkin/'
RFID_PAGE_URL = f'{BASE_URL}/dashboard/rfid/'

buffer = []
timer = None

def on_key(event):
    global buffer, timer

    if event.event_type != 'down':
        return

    if event.name in ('enter', 'Return'):
        card = ''.join(buffer).strip()
        buffer.clear()
        if timer:
            timer.cancel()
        if len(card) >= 6:  # RFID 卡號通常 8~10 碼，6 是保險下限
            threading.Thread(target=send_checkin, args=(card,), daemon=True).start()
    elif len(event.name) == 1:
        buffer.append(event.name)
        # 重設超時計時器
        if timer:
            timer.cancel()
        t = threading.Timer(0.5, buffer.clear)
        t.daemon = True
        t.start()
        # 用 nonlocal 方式更新 timer
        globals()['timer'] = t

MAX_RETRIES = 3       # 最多重試次數
RETRY_DELAY = 8      # 每次重試間隔秒數（讓 Railway 有時間喚醒）

def send_checkin(rfid_uid):
    title = '❌ 連線失敗'
    message = '伺服器無回應，請稍後再試'

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            session = requests.Session()
            session.get(RFID_PAGE_URL, timeout=15)
            csrf = session.cookies.get('csrftoken', '')

            resp = session.post(
                CHECKIN_URL,
                data={
                    'rfid_uid': rfid_uid,
                    'csrfmiddlewaretoken': csrf,
                },
                headers={
                    'X-RFID-API-Key': RFID_API_KEY,
                },
                timeout=15,
            )
            data = resp.json()
            message = data.get('message', '打卡完成')
            title = '✅ 打卡成功' if data.get('ok') else '❌ 打卡失敗'
            break  # 成功就跳出，不再重試

        except Exception as e:
            message = str(e)
            if attempt < MAX_RETRIES:
                print(f'第 {attempt} 次嘗試失敗，{RETRY_DELAY} 秒後重試…（{e}）')
                import time
                time.sleep(RETRY_DELAY)
            else:
                title = '❌ 連線失敗'
                message = f'重試 {MAX_RETRIES} 次仍失敗：{e}'

    print(f'{title}：{message}')
    notification.notify(
        title=title,
        message=message,
        timeout=3,
    )

print(f'RFID 監聽中（伺服器：{BASE_URL}），請刷卡...')
keyboard.on_press(on_key)
keyboard.wait()
