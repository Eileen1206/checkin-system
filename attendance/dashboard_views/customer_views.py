from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db import models
from ..models import Customer
from .base import require_group


@login_required
@require_group('admin', 'finance')
def import_customers(request):
    import csv
    import io

    if request.method == 'GET':
        return render(request, 'attendance/import_customers.html')

    if request.method == 'POST':
        if 'csv_file' not in request.FILES:
            messages.error(request, '請選擇 CSV 檔案')
            return render(request, 'attendance/import_customers.html')

        csv_file = request.FILES['csv_file']
        file = io.TextIOWrapper(csv_file, encoding='utf-8-sig')
        reader = csv.DictReader(file, delimiter=',')

        for row in reader:
            Customer.objects.update_or_create(
                customer_id=row['客戶編號'],
                defaults={
                    'name': row['客戶名稱'],
                    'address': row['地址'],
                    'phone': row['電話號碼']
                }
            )

        return redirect('dashboard:index')


@login_required
@require_group('admin', 'finance')
def search_customer(request):
    """AJAX：搜尋客戶（輸入編號或名稱）"""
    q = request.GET.get('q', '').strip()
    if not q:
        return JsonResponse({'results': []})

    customers = Customer.objects.filter(
        is_active=True
    ).exclude(
        customer_id='A000'
    ).filter(
        models.Q(customer_id__icontains=q) | models.Q(name__icontains=q)
    )[:10]

    results = [
        {'id': c.pk, 'customer_id': c.customer_id, 'name': c.name, 'address': c.address}
        for c in customers
    ]
    return JsonResponse({'results': results})


@login_required
def customer_list(request):
    """客戶管理列表，支援搜尋與篩選"""
    q = request.GET.get('q', '').strip()
    show = request.GET.get('show', 'active')  # active | all | no_address | no_gps

    customers = Customer.objects.all()
    if show == 'active':
        customers = customers.filter(is_active=True)
    elif show == 'no_address':
        customers = customers.filter(is_active=True, address='')
    elif show == 'no_gps':
        customers = customers.filter(is_active=True).filter(
            models.Q(lat__isnull=True) | models.Q(lng__isnull=True)
        )

    if q:
        customers = customers.filter(
            models.Q(customer_id__icontains=q) | models.Q(name__icontains=q)
        )

    customers = customers.order_by('customer_id')

    return render(request, 'attendance/customer_list.html', {
        'customers': customers,
        'q': q,
        'show': show,
        'total': Customer.objects.filter(is_active=True).count(),
        'no_address_count': Customer.objects.filter(is_active=True, address='').count(),
        'no_gps_count': Customer.objects.filter(is_active=True).filter(
            models.Q(lat__isnull=True) | models.Q(lng__isnull=True)
        ).count(),
    })


@login_required
def customer_edit(request, pk):
    """編輯單一客戶資料"""
    customer = get_object_or_404(Customer, pk=pk)

    if request.method == 'POST':
        customer.customer_id = request.POST.get('customer_id', customer.customer_id).strip()
        customer.name = request.POST.get('name', customer.name).strip()
        customer.address = request.POST.get('address', '').strip()
        customer.phone = request.POST.get('phone', '').strip()
        customer.is_active = request.POST.get('is_active') == 'on'

        lat = request.POST.get('lat', '').strip()
        lng = request.POST.get('lng', '').strip()
        customer.lat = float(lat) if lat else None
        customer.lng = float(lng) if lng else None

        customer.save()
        messages.success(request, f'已更新客戶 {customer.name}')
        return redirect('dashboard:customer_list')

    return render(request, 'attendance/customer_edit.html', {'customer': customer})


@login_required
@require_group('admin')
def geocode_customers(request):
    """批次 geocode 所有有地址但無座標的客戶"""
    if request.method != 'POST':
        return redirect('dashboard:customer_list')

    from ..utils.routing import geocode_customer
    customers = Customer.objects.filter(
        is_active=True, lat__isnull=True
    ).exclude(address='')

    count = 0
    for c in customers:
        result = geocode_customer(c)
        if result:
            count += 1

    messages.success(request, f'成功定位 {count} 筆客戶')
    return redirect('dashboard:customer_list')
