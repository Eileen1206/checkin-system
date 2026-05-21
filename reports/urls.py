from django.urls import path
from . import views

app_name = 'reports'

urlpatterns = [
    path('', views.report, name='report'),
    path('record/<int:pk>/edit/', views.edit_record, name='edit_record'),
    path('export/csv/', views.export_attendance_csv, name='export_attendance_csv'),
]
