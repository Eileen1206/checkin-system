import subprocess
import sys
import os
import time
import webbrowser
import threading
import shutil
import socket
import requests
import atexit
import signal

# 正確設定路徑
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
    PYTHON = shutil.which('python') or shutil.which('python3')
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    PYTHON = sys.executable

MANAGE = os.path.join(BASE_DIR, 'manage.py')
procs = []

def cleanup(sig=None, frame=None):
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    os.system("taskkill /F /IM python.exe >nul 2>&1")
    sys.exit(0)

def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def start_django():
    if not is_port_in_use(8000):
        p = subprocess.Popen(
            [PYTHON, MANAGE, 'runserver', '--noreload'],
            cwd=BASE_DIR,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        procs.append(p)

def start_rfid():
    p = subprocess.Popen(
        [PYTHON, os.path.join(BASE_DIR, 'rfid_listener.py')],
        cwd=BASE_DIR,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    procs.append(p)

# def start_reminder():
#     def loop():
#         while True:
#             subprocess.run(
#                 [PYTHON, MANAGE, 'remind_attendance'],
#                 cwd=BASE_DIR,
#                 creationflags=subprocess.CREATE_NO_WINDOW,
#             )
#             time.sleep(300)
#     threading.Thread(target=loop, daemon=True).start()

def open_browser():
    for _ in range(30):
        try:
            requests.get('http://127.0.0.1:8000/dashboard/', timeout=1)
            break
        except Exception:
            time.sleep(1)
    webbrowser.open('http://127.0.0.1:8000/dashboard/')

if __name__ == '__main__':
    atexit.register(cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    start_django()
    start_rfid()
    # start_reminder()
    open_browser()

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        cleanup()