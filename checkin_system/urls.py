from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from attendance import liff_views

handler404 = 'django.views.defaults.page_not_found'
handler500 = 'django.views.defaults.server_error'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),
    path('attendance/', include('attendance.urls', namespace='attendance')),
    path('reports/', include('reports.urls', namespace='reports')),
    path('dashboard/', include('attendance.dashboard_urls', namespace='dashboard')),
    path('liff/delivery/', liff_views.liff_delivery_page, name='liff_delivery'),
    path('liff/delivery/complete/', liff_views.liff_delivery_complete, name='liff_delivery_complete'),
    path('', RedirectView.as_view(url='/dashboard/', permanent=False)),
]
