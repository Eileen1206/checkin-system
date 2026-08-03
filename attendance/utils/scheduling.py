"""
一例一休排班統計的純計算邏輯（不碰 DB，資料由呼叫端帶入，方便測試）。

規則（依老闆最終確認）：
- 例假（一例）= 週日公司公休，自動成立，不需排、不計算。
- 休息日（一休）= 員工在「週一~週六」自行排的請假（LeaveRecord），每週需 >= 需求天數（預設 1）。
- 達標 = 該週平日已排休天數 >= 需求。固定週休二但沒排平日休 → 仍算缺休（不看 work_days）。
"""
import calendar as _calendar
from datetime import date, timedelta


def iter_month_weeks(year, month):
    """
    回傳涵蓋該月的各週（週一起算，含跨月邊界的完整 7 天）。
    每項 = {'index': 1-based, 'monday': date, 'dates': [週一..週日 共 7 個 date]}。
    """
    first = date(year, month, 1)
    days_in_month = _calendar.monthrange(year, month)[1]
    last = date(year, month, days_in_month)

    monday = first - timedelta(days=first.weekday())  # 回推到該週週一
    weeks, idx = [], 1
    while monday <= last:
        dates = [monday + timedelta(days=i) for i in range(7)]
        weeks.append({'index': idx, 'monday': monday, 'dates': dates})
        monday += timedelta(days=7)
        idx += 1
    return weeks


def week_rest_status(week_dates, leave_date_set, required=1):
    """
    判定單週「休息日（一休）」是否達標。
      week_dates    : [週一..週日 共 7 個 date]
      leave_date_set: set[date] 該員工已排休（LeaveRecord）的日期
      required      : 該週需排的平日休息日數（週日公休例假不計入）
    只計「週一~週六」中有排請假的天數；週日公休（例假）自動成立、不計入。
    回傳 dict：weekday_rest、required、compliant、shortfall。
    """
    weekday_rest = sum(1 for d in week_dates[:6] if d in leave_date_set)
    return {
        'weekday_rest': weekday_rest,
        'required': required,
        'compliant': weekday_rest >= required,
        'shortfall': max(required - weekday_rest, 0),
    }


def group_leaves_by_date(leave_pairs):
    """leave_pairs: iterable of (date, name) → {date: [name, ...]}。"""
    out = {}
    for d, name in leave_pairs:
        out.setdefault(d, []).append(name)
    return out


def understaffed_days(leaves_by_date, threshold, only_dates=None):
    """
    回傳同日休假人數 >= threshold 的清單（依日期排序）。
      leaves_by_date: {date: [name, ...]}
      only_dates    : 若提供，只列在此集合內的日期（通常是當月日期）
    每項 = {'date': date, 'names': [...], 'count': int}。
    """
    result = []
    for d, names in leaves_by_date.items():
        if only_dates is not None and d not in only_dates:
            continue
        if len(names) >= threshold:
            result.append({'date': d, 'names': names, 'count': len(names)})
    result.sort(key=lambda x: x['date'])
    return result
