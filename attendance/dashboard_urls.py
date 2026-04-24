from django.urls import path
from . import dashboard_views

app_name = 'dashboard'

urlpatterns = [
    path('', dashboard_views.index, name='index'),
    path('binding/', dashboard_views.binding_list, name='binding_list'),
    path('binding/generate/<int:employee_id>/', dashboard_views.generate_token, name='generate_token'),
    path('import-customers/', dashboard_views.import_customers, name='import_customers'),
    path('delivery/plan/', dashboard_views.delivery_plan, name='delivery_plan'),
    path('delivery/search-customer/', dashboard_views.search_customer, name='search_customer'),
    path('customers/', dashboard_views.customer_list, name='customer_list'),
    path('customers/geocode/', dashboard_views.geocode_customers, name='geocode_customers'),
    path('customers/<int:pk>/edit/', dashboard_views.customer_edit, name='customer_edit'),
    path('delivery/reorder/', dashboard_views.delivery_reorder, name='delivery_reorder'),
    path('delivery/push/', dashboard_views.delivery_push, name='delivery_push'),
    path('delivery/today/', dashboard_views.delivery_today, name = 'delivery_today'),
    path('salary/', dashboard_views.salary, name= 'salary'),
    path('salary/allowance/add/', dashboard_views.add_allowance, name='add_allowance'),
    path('employees/', dashboard_views.employee_list, name='employee_list'),
    path('employees/add/', dashboard_views.employee_add, name='employee_add'),
    path('employees/<int:pk>/edit/', dashboard_views.employee_edit, name='employee_edit'),
    path('salary/export/', dashboard_views.export_salary_excel, name='export_salary_excel'),
    path('rfid/', dashboard_views.rfid_page, name='rfid_page'),
    path('rfid/checkin/', dashboard_views.rfid_checkin, name='rfid_checkin'),
    path('attendance/add-record/', dashboard_views.add_record, name='add_record'),
    path('employees/<int:pk>/leave/add/', dashboard_views.leave_add, name='leave_add'),
    path('leave/<int:pk>/delete/', dashboard_views.leave_delete, name='leave_delete'),
    path('leave/', dashboard_views.leave_calendar, name='leave_calendar'),
    path('leave/api/add/', dashboard_views.leave_add_api, name='leave_add_api'),
    path('leave/api/<int:pk>/delete/', dashboard_views.leave_delete_api, name='leave_delete_api'),
]
