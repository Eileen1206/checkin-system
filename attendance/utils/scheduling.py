"""
一例一休排班統計的純計算邏輯（不碰 DB，資料由呼叫端帶入，方便測試）。

規則（依老闆設定）：
- 例假 = 週日（固定）。
- 休息日 = 週一~週六擇一，由員工自排。
- 每週達標 = 例假 + 休息日 共 2 天。
- 「休息日」認定：該日「不在固定上班日(work_days)」或「有請假紀錄」皆算休，
  如此週一~週五的員工週六本來就算休，不會被誤報缺休。
"""
import calendar as _calendar
from datetime import date, timedelta


def parse_work_days(work_days_str):
    """'0,1,2,3,4' → {0,1,2,3,4}（0=週一…6=週日）。"""
    return {int(d) for d in str(work_days_str).split(',') if d.strip().isdigit()}


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


def employee_week_status(work_day_set, week_dates, leave_date_set):
    """
    判定單週一例一休達標。
      work_day_set  : set[int] 固定上班日（0=週一…6=週日）
      week_dates    : [週一..週日 共 7 個 date]
      leave_date_set: set[date] 該員工已排休（LeaveRecord）的日期
    回傳 dict：mandatory_ok（例假）、flex_ok（休息日）、compliant、reasons（中文清單）。
    """
    def is_rest(d):
        return (d.weekday() not in work_day_set) or (d in leave_date_set)

    mandatory_ok = is_rest(week_dates[6])              # 週日例假
    flex_ok      = any(is_rest(d) for d in week_dates[:6])  # 週一~週六休息日

    reasons = []
    if not mandatory_ok:
        reasons.append('缺例假（週日未休）')
    if not flex_ok:
        reasons.append('缺休息日（平日未排休）')

    return {
        'mandatory_ok': mandatory_ok,
        'flex_ok': flex_ok,
        'compliant': mandatory_ok and flex_ok,
        'reasons': reasons,
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
