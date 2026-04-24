from django import template

register = template.Library()

COLORS = [
    '#4f7cff', '#e05c5c', '#44b89c', '#f5a623',
    '#9b59b6', '#e67e22', '#2ecc71', '#e91e8c',
    '#00b4d8', '#c0392b', '#27ae60', '#8e44ad',
]

@register.filter
def emp_color(pk):
    """依員工 pk 回傳固定顏色"""
    return COLORS[int(pk) % len(COLORS)]
