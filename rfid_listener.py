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

def send_checkin(rfid_uid):
    try:
        session = requests.Session()
        session.get(RFID_PAGE_URL, timeout=10)
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
            timeout=10,
        )
        data = resp.json()
        message = data.get('message', '打卡完成')
        title = '✅ 打卡成功' if data.get('ok') else '❌ 打卡失敗'
    except Exception as e:
        title = '❌ 連線失敗'
        message = str(e)  # ← 顯示真正的錯誤原因

    print(f'{title}：{message}')  # ← 同時印到 console 方便除錯
    notification.notify(
        title=title,
        message=message,
        timeout=3,
    )

print(f'RFID 監聽中（伺服器：{BASE_URL}），請刷卡...')
keyboard.on_press(on_key)
keyboard.wait()
