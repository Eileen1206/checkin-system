from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db import models
import re, urllib.request
from ..models import Customer, LocationCorrectionRequest
from django.utils import timezone
from django.views.decorators.http import require_POST
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
            defaults = {
                'name': row['客戶名稱'],
                'address': row['地址'],
                'phone': row['電話號碼'],
            }
            # 可選欄位：緯度 / 經度
            try:
                lat_val = row.get('緯度', '').strip()
                lng_val = row.get('經度', '').strip()
                if lat_val: defaults['lat'] = float(lat_val)
                if lng_val: defaults['lng'] = float(lng_val)
            except (ValueError, KeyError):
                pass

            Customer.objects.update_or_create(
                customer_id=row['客戶編號'],
                defaults=defaults,
            )

        return redirect('dashboard:index')


@login_required
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


@login_required
def parse_gmaps_url(request):
    """展開 Google Maps 短網址並解析座標（供 customer_edit.html 使用）"""
    url = request.GET.get('url', '').strip()
    if not url:
        return JsonResponse({'error': '缺少 url 參數'}, status=400)

    def extract_coords(s):
        # /@lat,lng,
        m = re.search(r'/@(-?\d+\.\d+),(-?\d+\.\d+)', s)
        if m: return float(m.group(1)), float(m.group(2))
        # ?q=lat,lng
        m = re.search(r'[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)', s)
        if m: return float(m.group(1)), float(m.group(2))
        # ?ll=lat,lng
        m = re.search(r'[?&]ll=(-?\d+\.\d+),(-?\d+\.\d+)', s)
        if m: return float(m.group(1)), float(m.group(2))
        return None

    # 先嘗試直接解析
    result = extract_coords(url)
    if result:
        return JsonResponse({'lat': result[0], 'lng': result[1]})

    # 短網址展開（HEAD request 取 Location）
    try:
        req = urllib.request.Request(url, method='HEAD',
                                     headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            final_url = resp.url
        result = extract_coords(final_url)
        if result:
            return JsonResponse({'lat': result[0], 'lng': result[1]})
    except Exception:
        pass

    return JsonResponse({'error': '無法解析座標'})


@login_required
@require_group('admin', 'finance')
def location_correction_list(request):
    """座標修正申請列表"""
    corrections = LocationCorrectionRequest.objects.select_related(
        'customer', 'requested_by__user', 'reviewed_by'
    ).all()
    return render(request, 'attendance/location_correction_list.html', {
        'corrections': corrections,
    })


@login_required
@require_group('admin', 'finance')
@require_POST
def location_correction_review(request, pk):
    """核准或拒絕座標修正申請"""
    correction = get_object_or_404(LocationCorrectionRequest, pk=pk)
    action = request.POST.get('action')

    if action == 'approve':
        correction.customer.lat = correction.new_lat
        correction.customer.lng = correction.new_lng
        correction.customer.save(update_fields=['lat', 'lng'])
        correction.status = 'approved'
        messages.success(request, f'已核准並更新 {correction.customer.name} 的座標')
    elif action == 'reject':
        correction.status = 'rejected'
        messages.info(request, f'已拒絕 {correction.customer.name} 的座標修正申請')

    correction.reviewed_by = request.user
    correction.reviewed_at = timezone.now()
    correction.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
    return redirect('dashboard:location_correction_list')
