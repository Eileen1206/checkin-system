from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),
    path('attendance/', include('attendance.urls', namespace='attendance')),
    path('reports/', include('reports.urls', namespace='reports')),
    path('dashboard/', include('attendance.dashboard_urls', namespace='dashboard')),
    path('', RedirectView.as_view(url='/dashboard/', permanent=False)),
]
