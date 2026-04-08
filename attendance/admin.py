from django.contrib import admin
from .models import (
    Employee, OfficeLocation, AttendanceRecord, BindingToken,
    MonthlyAllowance, ReminderSetting, AuditLog,
    DeliveryTask, TaskCheckIn,
)


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ['employee_id', 'user', 'department', 'employment_type', 'is_delivery', 'line_user_id']
    list_filter = ['department', 'employment_type', 'is_delivery']
    search_fields = ['employee_id', 'user__username', 'user__first_name', 'user__last_name']
    readonly_fields = ['created_at']


@admin.register(OfficeLocation)
class OfficeLocationAdmin(admin.ModelAdmin):
    list_display = ['name', 'latitude', 'longitude', 'radius_meters', 'is_active']
    list_filter = ['is_active']


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


@admin.register(ReminderSetting)
class ReminderSettingAdmin(admin.ModelAdmin):
    list_display = ['work_start_time', 'work_end_time', 'late_reminder_minutes', 'enabled']


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['actor', 'action', 'target_model', 'target_id', 'timestamp']
    list_filter = ['action', 'target_model']
    readonly_fields = ['actor', 'action', 'target_model', 'target_id', 'changes', 'timestamp']
    date_hierarchy = 'timestamp'


@admin.register(DeliveryTask)
class DeliveryTaskAdmin(admin.ModelAdmin):
    list_display = ['employee', 'date', 'order', 'customer_name', 'status', 'arrived_at', 'completed_at']
    list_filter = ['status', 'date']
    search_fields = ['employee__employee_id', 'customer_name']
    date_hierarchy = 'date'


@admin.register(TaskCheckIn)
class TaskCheckInAdmin(admin.ModelAdmin):
    list_display = ['task', 'check_type', 'timestamp', 'distance_meters', 'is_valid']
    list_filter = ['check_type', 'is_valid']
    readonly_fields = ['timestamp']
