from django import template

register = template.Library()

# 配色取自 Fondazione Achille Castiglioni 識別色系：
#   Black / Pantone 7626C（紅）/ Pantone 7752C（赭黃）/ Pantone 559U（灰綠）
# 員工數量多於四色，故以同家族的深淺變體延伸，維持整體調性一致。
COLORS = [
    '#1F1E21',  # Black
    '#B0382A',  # Pantone 7626C 紅
    '#C7AD34',  # Pantone 7752C 赭黃
    '#BECEBE',  # Pantone 559U 灰綠
    '#7A2A20',  # 深磚紅
    '#8A9A8A',  # 深灰綠
    '#4A4A4E',  # 石墨灰
    '#D2624F',  # 陶土橘紅
    '#9C8428',  # 深赭黃
    '#5E6E5E',  # 橄欖綠
    '#E0C64F',  # 淺赭黃
    '#6E6A63',  # 暖灰
]

# 淺色底需搭配深色文字，確保對比度足夠
LIGHT_BACKGROUNDS = {'#C7AD34', '#BECEBE', '#E0C64F'}

INK = '#1F1E21'


@register.filter
def emp_color(pk):
    """依員工 pk 回傳固定顏色（Castiglioni 色系）"""
    return COLORS[int(pk) % len(COLORS)]


@register.filter
def emp_text_color(pk):
    """依員工底色回傳可讀的文字顏色（淺底用墨黑、深底用白）"""
    return INK if emp_color(pk) in LIGHT_BACKGROUNDS else '#FFFFFF'
