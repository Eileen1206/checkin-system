# Re-export all public functions so that dashboard_urls.py (which uses
# `from . import dashboard_views` and `dashboard_views.function_name`) needs
# no changes at all.

from .base import (
    require_group,
    get_today_status,
    get_work_hours,
    WORK_DAY_CHOICES,
    calculate_salary,
)

from .attendance_views import (
    index,
    add_record,
    rfid_page,
    rfid_checkin,
)

from .employee_views import (
    binding_list,
    generate_token,
    employee_list,
    employee_add,
    employee_edit,
)

from .customer_views import (
    import_customers,
    search_customer,
    customer_list,
    customer_edit,
    geocode_customers,
)

from .delivery_views import (
    delivery_push,
    delivery_reorder,
    delivery_plan,
    delivery_today,
)

from .salary_views import (
    salary,
    add_allowance,
    export_salary_excel,
)

from .leave_views import (
    leave_calendar,
    leave_add_api,
    leave_delete_api,
    leave_add,
    leave_delete,
)

__all__ = [
    # base
    'require_group',
    'get_today_status',
    'get_work_hours',
    'WORK_DAY_CHOICES',
    'calculate_salary',
    # attendance
    'index',
    'add_record',
    'rfid_page',
    'rfid_checkin',
    # employee
    'binding_list',
    'generate_token',
    'employee_list',
    'employee_add',
    'employee_edit',
    # customer
    'import_customers',
    'search_customer',
    'customer_list',
    'customer_edit',
    'geocode_customers',
    # delivery
    'delivery_push',
    'delivery_reorder',
    'delivery_plan',
    'delivery_today',
    # salary
    'salary',
    'add_allowance',
    'export_salary_excel',
    # leave
    'leave_calendar',
    'leave_add_api',
    'leave_delete_api',
    'leave_add',
    'leave_delete',
]
