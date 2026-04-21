from django.contrib import admin
from .models import (
    Employee, AttendanceRecord, BindingToken,
    MonthlyAllowance, AuditLog, DeliveryTask,
)


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ['employee_id', 'user', 'department', 'employment_type', 'is_delivery', 'line_user_id']
    list_filter = ['department', 'employment_type', 'is_delivery']
    search_fields = ['employee_id', 'user__username', 'user__first_name', 'user__last_name']
    readonly_fields = ['created_at']



@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ['employee', 'record_type', 'timestamp', 'is_valid', 'distance_meters', 'source']
    list_filter = ['record_type', 'is_valid', 'source']
    search_fields = ['employee__employee_id', 'employee__user__username']
    readonly_fields = ['timestamp']
    date_hierarchy = 'timestamp'


@admin.register(BindingToken)
class BindingTokenAdmin(admin.ModelAdmin):
    list_display = ['employee', 'token', 'created_at', 'expires_at', 'used']
    list_filter = ['used']
    readonly_fields = ['token', 'created_at']


@admin.register(MonthlyAllowance)
class MonthlyAllowanceAdmin(admin.ModelAdmin):
    list_display = ['employee', 'year', 'month', 'amount', 'note', 'created_by']
    list_filter = ['year', 'month']
    search_fields = ['employee__employee_id']



@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['actor', 'action', 'target_model', 'target_id', 'timestamp']
    list_filter = ['action', 'target_model']
    readonly_fields = ['actor', 'action', 'target_model', 'target_id', 'changes', 'timestamp']
    date_hierarchy = 'timestamp'


@admin.register(DeliveryTask)
class DeliveryTaskAdmin(admin.ModelAdmin):
    list_display = ['employee', 'date', 'order', 'customer', 'is_urgent', 'status', 'arrived_at']
    list_filter = ['status', 'date','is_urgent']
    search_fields = ['employee__employee_id', 'customer_name']
    date_hierarchy = 'date'



