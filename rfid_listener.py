import keyboard
import requests
from plyer import notification
import threading

DJANGO_URL = 'https://checkin-system-production-4544.up.railway.app/dashboard/rfid/checkin/'
CSRF_TOKEN = 'dummy'  # 待處理

buffer = []
timer = None

def on_key(event):
    global buffer, timer

    if event.event_type != 'down':
        return

    if event.name == 'enter':
        card = ''.join(buffer).strip()
        buffer = []
        if len(card) >= 10:
            threading.Thread(target=send_checkin, args=(card,), daemon=True).start()
    elif len(event.name) == 1:
        buffer.append(event.name)

    # 超過 500ms 沒輸入就清空
    global timer
    if timer:
        timer.cancel()
    timer = threading.Timer(0.5, lambda: buffer.clear())
    timer.start()

def send_checkin(rfid_uid):
    try:
        # 先取得 CSRF token
        session = requests.Session()
        session.get('https://checkin-system-production-4544.up.railway.app/dashboard/rfid/')
        csrf = session.cookies.get('csrftoken', '')

        resp = session.post(DJANGO_URL, data={
            'rfid_uid': rfid_uid,
            'csrfmiddlewaretoken': csrf,
        })
        data = resp.json()
        message = data.get('message', '打卡完成')
        title = '✅ 打卡成功' if data.get('ok') else '❌ 打卡失敗'
    except Exception as e:
        title = '❌ 連線失敗'
        message = '請確認系統是否啟動'

    notification.notify(
        title=title,
        message=message,
        timeout=3,
    )

print('RFID 監聽中，請刷卡...')
keyboard.on_press(on_key)
keyboard.wait()