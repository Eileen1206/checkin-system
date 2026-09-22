"""LINE 員工端的休假流程：排休與請假是兩個獨立入口。

設計原則（客戶需求）：
- 排休 = 整天不來，可一次選好幾天，不需要理由。這是員工最常用的功能。
- 請假 = 原本要上班但臨時有事，單天、要選時數與假別。
- 全程用按鈕和日期選擇器，員工不用打任何字。
- 員工不能自己選特休（特休要直接跟老闆談），假別只有事假／病假／喪假。

進行中的申請暫存在 cache（每個 LINE 使用者一份草稿），送出或取消就清掉。
"""
from datetime import datetime

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from linebot.v3.messaging import (
    FlexContainer,
    FlexMessage,
    TextMessage,
)

from .models import LeaveRecord, LeaveRequest
from .utils import line_push


DRAFT_TTL = 900          # 草稿 15 分鐘沒動作就過期
MAX_REST_DAYS = 14       # 一次排休最多選幾天，避免誤觸連點

# Castiglioni 色系：排休用灰綠、請假用赭黃
COLOR_REST = '#5E6E5E'
COLOR_LEAVE = '#9C8428'
COLOR_INK = '#1F1E21'
COLOR_MUTED = '#8A8A8A'

WEEKDAY_TXT = ['一', '二', '三', '四', '五', '六', '日']

# 請假時數選項：整天、半天、其餘整數小時
HOUR_OPTIONS = [
    ('8', '整天'), ('4', '半天'),
    ('1', '1 小時'), ('2', '2 小時'), ('3', '3 小時'),
    ('5', '5 小時'), ('6', '6 小時'), ('7', '7 小時'),
]


# ───────────────────── 草稿存取 ─────────────────────

def _draft_key(line_user_id):
    return f'leave_draft_{line_user_id}'


def _get_draft(line_user_id):
    return cache.get(_draft_key(line_user_id))


def _set_draft(line_user_id, draft):
    cache.set(_draft_key(line_user_id), draft, DRAFT_TTL)


def _clear_draft(line_user_id):
    cache.delete(_draft_key(line_user_id))


# ───────────────────── 小工具 ─────────────────────

def _fmt_date(d):
    """2026-09-20 → 9/20（日）"""
    if isinstance(d, str):
        d = datetime.strptime(d, '%Y-%m-%d').date()
    return f'{d.month}/{d.day}（{WEEKDAY_TXT[d.weekday()]}）'


def _hours_label(hours):
    if hours is None:
        return '整天'
    if hours >= LeaveRecord.FULL_DAY_HOURS:
        return '整天'
    if hours == LeaveRecord.HALF_DAY_HOURS:
        return '半天'
    return f'{hours:g} 小時'


def _button(label, data, color=None, style='primary'):
    btn = {
        'type': 'button', 'height': 'sm', 'style': style,
        'action': {'type': 'postback', 'label': label, 'data': data},
    }
    if color and style == 'primary':
        btn['color'] = color
    return btn


def _date_picker_button(label, data, color):
    """日期選擇器：員工用滾輪選日期，不用打字。"""
    today = timezone.localdate()
    return {
        'type': 'button', 'height': 'sm', 'style': 'primary', 'color': color,
        'action': {
            'type': 'datetimepicker', 'label': label, 'data': data,
            'mode': 'date', 'initial': str(today), 'min': str(today),
        },
    }


def _bubble(title, color, body_contents, footer_buttons):
    return {
        'type': 'bubble',
        'header': {
            'type': 'box', 'layout': 'vertical', 'backgroundColor': color,
            'paddingAll': '14px',
            'contents': [
                {'type': 'text', 'text': title, 'color': '#FFFFFF',
                 'size': 'lg', 'weight': 'bold'},
            ],
        },
        'body': {
            'type': 'box', 'layout': 'vertical', 'spacing': 'sm',
            'contents': body_contents,
        },
        'footer': {
            'type': 'box', 'layout': 'vertical', 'spacing': 'sm',
            'contents': footer_buttons,
        },
    }


def _text(text, size='sm', color=COLOR_INK, weight=None):
    item = {'type': 'text', 'text': text, 'size': size, 'color': color, 'wrap': True}
    if weight:
        item['weight'] = weight
    return item


def _flex(alt_text, bubble):
    return FlexMessage(alt_text=alt_text, contents=FlexContainer.from_dict(bubble))


# ───────────────────── 各步驟畫面 ─────────────────────

def _ask_rest_date(draft):
    """排休：選日期（可重複加）"""
    dates = draft.get('dates', [])
    if dates:
        body = [
            _text('已選日期', size='xs', color=COLOR_MUTED, weight='bold'),
            *[_text(f'・{_fmt_date(d)}') for d in dates],
        ]
        footer = [
            _date_picker_button('＋ 再加一天', 'action=lv_date', COLOR_REST),
            _button('✅ 送出申請', 'action=lv_submit', COLOR_INK),
            _button('✖ 取消', 'action=lv_cancel', style='secondary'),
        ]
    else:
        body = [_text('選擇要休的日期', color=COLOR_MUTED)]
        footer = [
            _date_picker_button('📅 選擇日期', 'action=lv_date', COLOR_REST),
            _button('✖ 取消', 'action=lv_cancel', style='secondary'),
        ]
    return _flex('排休申請', _bubble('排休', COLOR_REST, body, footer))


def _ask_leave_date():
    body = [_text('選擇請假日期', color=COLOR_MUTED)]
    footer = [
        _date_picker_button('📅 選擇日期', 'action=lv_date', COLOR_LEAVE),
        _button('✖ 取消', 'action=lv_cancel', style='secondary'),
    ]
    return _flex('請假申請', _bubble('請假', COLOR_LEAVE, body, footer))


def _ask_hours(draft):
    body = [
        _text(_fmt_date(draft['dates'][0]), size='md', weight='bold'),
        _text('請多久？', color=COLOR_MUTED),
    ]
    # 兩個一列，手機上好按
    rows = []
    for i in range(0, len(HOUR_OPTIONS), 2):
        pair = HOUR_OPTIONS[i:i + 2]
        rows.append({
            'type': 'box', 'layout': 'horizontal', 'spacing': 'sm',
            'contents': [
                _button(label, f'action=lv_hours&h={value}',
                        COLOR_LEAVE if value in ('8', '4') else None,
                        style='primary' if value in ('8', '4') else 'secondary')
                for value, label in pair
            ],
        })
    rows.append(_button('✖ 取消', 'action=lv_cancel', style='secondary'))
    return _flex('請假時數', _bubble('請假', COLOR_LEAVE, body, rows))


def _ask_type(draft):
    body = [
        _text(f"{_fmt_date(draft['dates'][0])}・{_hours_label(draft.get('hours'))}",
              size='md', weight='bold'),
        _text('選擇假別', color=COLOR_MUTED),
    ]
    type_labels = dict(LeaveRecord.LEAVE_TYPE_CHOICES)
    footer = [
        {
            'type': 'box', 'layout': 'horizontal', 'spacing': 'sm',
            'contents': [
                _button(type_labels[t], f'action=lv_type&t={t}', COLOR_LEAVE)
                for t in LeaveRecord.EMPLOYEE_LEAVE_TYPES
            ],
        },
        _button('✖ 取消', 'action=lv_cancel', style='secondary'),
    ]
    return _flex('假別', _bubble('請假', COLOR_LEAVE, body, footer))


def _confirm_leave(draft):
    type_labels = dict(LeaveRecord.LEAVE_TYPE_CHOICES)
    body = [
        _text('確認送出', size='xs', color=COLOR_MUTED, weight='bold'),
        _text(f"日期　{_fmt_date(draft['dates'][0])}", size='md'),
        _text(f"時數　{_hours_label(draft.get('hours'))}", size='md'),
        _text(f"假別　{type_labels.get(draft.get('leave_type'), '—')}", size='md'),
    ]
    footer = [
        _button('✅ 送出申請', 'action=lv_submit', COLOR_INK),
        _button('✖ 取消', 'action=lv_cancel', style='secondary'),
    ]
    return _flex('確認請假內容', _bubble('請假', COLOR_LEAVE, body, footer))


# ───────────────────── 進入點 ─────────────────────

def start_rest(line_user_id):
    _set_draft(line_user_id, {'kind': LeaveRecord.KIND_REST, 'dates': []})
    return [_ask_rest_date({'dates': []})]


def start_leave(line_user_id):
    _set_draft(line_user_id, {'kind': LeaveRecord.KIND_LEAVE, 'dates': []})
    return [_ask_leave_date()]


def _expired():
    return [TextMessage(text='這個申請已經逾時了，請重新點一次「排休」或「請假」。')]


def handle_postback(employee, line_user_id, action, params, postback_params):
    """處理 lv_* 系列 postback，回傳要回覆的訊息 list；不屬於此流程則回 None。"""
    if not action.startswith('lv_'):
        return None

    if action == 'lv_cancel':
        _clear_draft(line_user_id)
        return [TextMessage(text='已取消，沒有送出任何申請。')]

    if action == 'lv_start':
        kind = params.get('kind')
        if kind == LeaveRecord.KIND_LEAVE:
            return start_leave(line_user_id)
        return start_rest(line_user_id)

    draft = _get_draft(line_user_id)
    if not draft:
        return _expired()

    if action == 'lv_date':
        picked = (postback_params or {}).get('date')
        if not picked:
            return _expired()
        if draft['kind'] == LeaveRecord.KIND_REST:
            if picked in draft['dates']:
                return [TextMessage(text=f'{_fmt_date(picked)} 已經選過了。'),
                        _ask_rest_date(draft)]
            if len(draft['dates']) >= MAX_REST_DAYS:
                return [TextMessage(text=f'一次最多選 {MAX_REST_DAYS} 天，請先送出。'),
                        _ask_rest_date(draft)]
            draft['dates'].append(picked)
            draft['dates'].sort()
            _set_draft(line_user_id, draft)
            return [_ask_rest_date(draft)]
        # 請假一次只處理一天
        draft['dates'] = [picked]
        _set_draft(line_user_id, draft)
        return [_ask_hours(draft)]

    if action == 'lv_hours':
        try:
            hours = float(params.get('h'))
        except (TypeError, ValueError):
            return _expired()
        draft['hours'] = min(max(hours, 0.5), LeaveRecord.FULL_DAY_HOURS)
        _set_draft(line_user_id, draft)
        return [_ask_type(draft)]

    if action == 'lv_type':
        t = params.get('t')
        if t not in LeaveRecord.EMPLOYEE_LEAVE_TYPES:
            return [TextMessage(text='這個假別不能自己選，請重新選一次。'), _ask_type(draft)]
        draft['leave_type'] = t
        _set_draft(line_user_id, draft)
        return [_confirm_leave(draft)]

    if action == 'lv_submit':
        return _submit(employee, line_user_id, draft)

    return None


# ───────────────────── 送出申請 ─────────────────────

def _submit(employee, line_user_id, draft):
    if not draft.get('dates'):
        return [TextMessage(text='還沒選日期喔，請先選日期。')]

    is_rest = draft['kind'] == LeaveRecord.KIND_REST
    if not is_rest and not draft.get('hours'):
        return [TextMessage(text='還沒選時數，請重新選一次。'), _ask_hours(draft)]

    # 連點兩下「送出」不會變成兩筆申請
    submit_lock = 'leave_submit_{}_{}_{}'.format(
        employee.pk, draft['kind'], ','.join(str(d) for d in draft['dates']))
    if not cache.add(submit_lock, True, 60):
        _clear_draft(line_user_id)
        return [TextMessage(text='這筆申請已經送出了，請等老闆確認。')]

    leave_req = LeaveRequest.objects.create(
        employee=employee,
        dates=list(draft['dates']),
        kind=draft['kind'],
        leave_type='' if is_rest else draft.get('leave_type', ''),
        hours=None if is_rest else draft.get('hours'),
    )
    _clear_draft(line_user_id)

    notify_manager(leave_req)

    dates_display = '\n'.join(f'・{_fmt_date(d)}' for d in leave_req.dates)
    title = '排休' if is_rest else '請假'
    detail = '' if is_rest else f'\n{leave_req.hours_label}・{leave_req.get_leave_type_display()}'
    return [TextMessage(
        text=f'✅ {title}申請已送出，等待老闆確認：\n{dates_display}{detail}'
    )]


def notify_manager(leave_req):
    """推播給老闆，附同意／拒絕按鈕。推播失敗不影響申請已建立。"""
    manager_id = getattr(settings, 'MANAGER_LINE_USER_ID', '')
    if not manager_id:
        return

    emp_name = leave_req.employee.user.get_full_name() or leave_req.employee.user.username
    is_rest = leave_req.is_rest
    color = COLOR_REST if is_rest else COLOR_LEAVE

    body = [
        _text(emp_name, size='lg', weight='bold'),
        _text(leave_req.summary_label, size='sm', color=COLOR_MUTED),
        {'type': 'separator', 'margin': 'md'},
        *[_text(f'・{_fmt_date(d)}', size='sm') for d in leave_req.dates],
    ]
    footer = [{
        'type': 'box', 'layout': 'horizontal', 'spacing': 'sm',
        'contents': [
            _button('✅ 同意', f'action=leave_approve&request_pk={leave_req.pk}', COLOR_INK),
            _button('❌ 拒絕', f'action=leave_deny&request_pk={leave_req.pk}', style='secondary'),
        ],
    }]
    bubble = _bubble(f'{leave_req.get_kind_display()}申請', color, body, footer)

    # 同一筆申請只通知老闆一次（連點、重送都不會重複發）
    line_push.push_once(
        manager_id,
        [_flex(f'{emp_name} 申請{leave_req.get_kind_display()}', bubble)],
        dedupe_key=f'leave_req_manager_{leave_req.pk}',
    )


def notify_employee(leave_req, approved):
    """核准／拒絕後通知員工。"""
    emp = leave_req.employee
    if not emp.line_user_id:
        return
    dates_display = '\n'.join(f'・{_fmt_date(d)}' for d in leave_req.dates)
    title = leave_req.get_kind_display()
    if approved:
        text = f'✅ 你的{title}申請已核准：\n{dates_display}'
        if not leave_req.is_rest:
            text += f'\n{leave_req.hours_label}・{leave_req.get_leave_type_display()}'
    else:
        text = f'❌ 你的{title}申請被拒絕了：\n{dates_display}\n有問題請直接找老闆。'
    # 核准／拒絕各只通知一次
    verdict = 'approved' if approved else 'denied'
    line_push.push_once(
        emp.line_user_id, text,
        dedupe_key=f'leave_req_emp_{leave_req.pk}_{verdict}',
    )


def menu_message():
    """「休假」入口：讓員工選排休還是請假。"""
    body = [_text('要排休還是請假？', color=COLOR_MUTED)]
    footer = [
        _button('🗓 排休', f'action=lv_start&kind={LeaveRecord.KIND_REST}', COLOR_REST),
        _button('📝 請假', f'action=lv_start&kind={LeaveRecord.KIND_LEAVE}', COLOR_LEAVE),
    ]
    return [_flex('休假', _bubble('休假', COLOR_INK, body, footer))]
