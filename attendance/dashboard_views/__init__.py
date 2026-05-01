from .base import (
    require_group, get_today_status, get_work_hours, WORK_DAY_CHOICES, calculate_salary,
)
from .attendance_views import (
    index, add_record, rfid_page, rfid_checkin,
)
from .employee_views import (
    binding_list, generate_token, employee_list, employee_add, employee_edit,
)
from .customer_views import (
    import_customers, search_customer, customer_list, customer_edit, geocode_customers, parse_gmaps_url,
)
from .delivery_views import (
    delivery_push, delivery_add_task, delivery_delete_task, delivery_reorder, delivery_plan, delivery_today,
)
from .salary_views import (
    salary, add_allowance, export_salary_excel,
)
from .leave_views import (
    leave_calendar, leave_add_api, leave_delete_api, leave_add, leave_delete,
    leave_request_list, leave_request_approve, leave_request_deny,
)

__all__ = [
    'require_group', 'get_today_status', 'get_work_hours', 'WORK_DAY_CHOICES', 'calculate_salary',
    'index', 'add_record', 'rfid_page', 'rfid_checkin',
    'binding_list', 'generate_token', 'employee_list', 'employee_add', 'employee_edit',
    'import_customers', 'search_customer', 'customer_list', 'customer_edit', 'geocode_customers', 'parse_gmaps_url',
    'delivery_push', 'delivery_add_task', 'delivery_delete_task', 'delivery_reorder', 'delivery_plan', 'delivery_today',
    'salary', 'add_allowance', 'export_salary_excel',
    'leave_calendar', 'leave_add_api', 'leave_delete_api', 'leave_add', 'leave_delete',
    'leave_request_list', 'leave_request_approve', 'leave_request_deny',
]
