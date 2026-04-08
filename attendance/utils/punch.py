from django.utils import timezone
from attendance.models import AttendanceRecord


def get_today_last_record(employee):
    """取得員工今日最後一筆打卡紀錄（沒有則回傳 None）"""
    today = timezone.localdate()
    return AttendanceRecord.objects.filter(
        employee=employee,
        timestamp__date=today,
    ).first()


def handle_punch(employee, action=None):
    """
    執行打卡，根據員工目前狀態決定下一步。

    action: 可以是 None（讓系統自動判斷）、'break_start'、'clock_out'
            當狀態需要詢問時，才會用到 action。

    回傳 dict：
        status      → 'ok'（成功）、'ask'（需要詢問）、'already_done'（今日已打完）
        record_type → 建立的紀錄類型（或 None）
        message     → 要回傳給使用者的文字
    """
    last = get_today_last_record(employee)
    last_type = last.record_type if last else None
    now_str = timezone.localtime().strftime('%H:%M')
    name = employee.user.get_full_name() or employee.user.username

    # ── 狀態一：今天還沒打過卡 ──────────────────────────────
    if last_type is None:
        AttendanceRecord.objects.create(
            employee=employee,
            record_type='clock_in',
        )
        return {
            'status': 'ok',
            'record_type': 'clock_in',
            'message': f'✅ 上班打卡成功！\n姓名：{name}\n時間：{now_str}',
        }

    # ── 狀態二：上班中 需要詢問 ─────────────
    if last_type in ('clock_in'):
        if action == 'break_start':
            AttendanceRecord.objects.create(
                employee=employee,
                record_type='break_start',
            )
            return {
                'status': 'ok',
                'record_type': 'break_start',
                'message': f'🍱 午休開始！\n姓名：{name}\n時間：{now_str}',
            }
        elif action == 'clock_out':
            AttendanceRecord.objects.create(
                employee=employee,
                record_type='clock_out',
            )
            return {
                'status': 'ok',
                'record_type': 'clock_out',
                'message': f'👋 下班打卡成功！\n姓名：{name}\n時間：{now_str}',
            }
        else:
            # 還不知道要午休還是下班，回傳 ask 讓 views 顯示按鈕
            return {
                'status': 'ask',
                'record_type': None,
                'message': '請選擇：',
            }
        
    if last_type == 'break_end':
            AttendanceRecord.objects.create(
                employee=employee,
                record_type='clock_out',
            )
            return {
                'status': 'ok',
                'record_type': 'clock_out',
                'message': f'👋 下班打卡成功！\n姓名：{name}\n時間：{now_str}',
            }


    # ── 狀態三：午休中 → 午休結束 ──────────────────────────
    if last_type == 'break_start':
        AttendanceRecord.objects.create(
            employee=employee,
            record_type='break_end',
        )
        return {
            'status': 'ok',
            'record_type': 'break_end',
            'message': f'✅ 午休結束，繼續加油！\n姓名：{name}\n時間：{now_str}',
        }

    # ── 狀態四：今日已下班 ───────────────────────────────────
    if last_type == 'clock_out':
        return {
            'status': 'already_done',
            'record_type': None,
            'message': '您今日已完成下班打卡。\n如有問題請聯絡管理員。',
        }

    return {
        'status': 'error',
        'record_type': None,
        'message': '無法判斷打卡狀態，請聯絡管理員。',
    }

