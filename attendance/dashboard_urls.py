from django.urls import path
from . import dashboard_views

app_name = 'dashboard'

urlpatterns = [
    path('', dashboard_views.index, name='index'),
    path('binding/', dashboard_views.binding_list, name='binding_list'),
    path('binding/generate/<int:employee_id>/', dashboard_views.generate_token, name='generate_token'),
    path('import-customers/', dashboard_views.import_customers, name='import_customers'),

]
