from django.urls import path
from . import views

app_name = 'attendance'

urlpatterns = [
    path('webhook/', views.webhook, name='webhook'),
]
