import hashlib
import openrouteservice
from django.conf import settings
from django.core.cache import cache

# 行車時間快取保留秒數。相同路線短時間內不會改變，快取可避免每次
# 開啟儀表板都同步呼叫 ORS，導致 gunicorn worker timeout。
DRIVE_CACHE_TTL = 60 * 60 * 12  # 12 小時


def get_client():
    # retry_over_query_limit=False：ORS 免費額度被限流 (429) 時，不要反覆重試。
    # retry_timeout=5：ORS 伺服器掛掉 (503) 時，客戶端預設會重試整整 60 秒
    #   （retry_timeout 預設值），會超過 gunicorn timeout 拖垮 worker；
    #   縮短重試視窗，讓呼叫端快速放棄並走既有的 fallback（退回原順序 / 不預測）。
    return openrouteservice.Client(
        key=settings.ORS_API_KEY,
        timeout=8,
        retry_timeout=5,
        retry_over_query_limit=False,
    )


def _drive_cache_key(coords):
    """以座標序列產生穩定的快取 key。"""
    raw = ';'.join(f'{lng:.5f},{lat:.5f}' for lng, lat in coords)
    return f'ors_drive_{hashlib.md5(raw.encode()).hexdigest()}'


def get_office_coords():
    """取得公司 GPS 座標，從客戶表 A000 取得"""
    from attendance.models import Customer
    company = Customer.objects.filter(customer_id='A000').first()
    if company and company.lat and company.lng:
        return float(company.lat), float(company.lng)
    return None


def geocode_customer(customer):
    """將客戶地址轉換為 GPS 座標，儲存後回傳 (lat, lng) 或 None"""
    client = get_client()
    try:
        result = client.pelias_search(customer.address, country='TW')
        if result['features']:
            coords = result['features'][0]['geometry']['coordinates']
            lng, lat = coords[0], coords[1]
            customer.lat = lat
            customer.lng = lng
            customer.save()
            return float(lat), float(lng)
    except Exception:
        pass
    return None


def get_route_drive_minutes(ordered_customers, cache_only=False):
    """
    給定已排好順序的客戶列表（含 lat/lng），
    呼叫 ORS directions API，回傳公司→各站→公司的行車分鐘數。
    無法計算時回傳 None。

    cache_only=True 時只讀快取、絕不呼叫 ORS（也不觸發 geocode），
    供只讀的儀表板頁使用，確保頁面渲染永遠不會因外部 API 卡住而讓
    gunicorn worker timeout。快取由實際派單/推播的寫入路徑預熱。
    """
    if not ordered_customers:
        return None

    office = get_office_coords()
    if not office:
        return None

    # 只讀頁不觸發 geocode（geocode 本身也是一次對外 API 呼叫）
    if not cache_only:
        for c in ordered_customers:
            if not c.lat or not c.lng:
                geocode_customer(c)

    if any(not c.lat or not c.lng for c in ordered_customers):
        return None

    # [lng, lat] 格式：公司 → 各站 → 公司
    coords  = [[float(office[1]), float(office[0])]]
    coords += [[float(c.lng), float(c.lat)] for c in ordered_customers]
    coords += [[float(office[1]), float(office[0])]]

    cache_key = _drive_cache_key(coords)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    if cache_only:
        return None

    try:
        client = get_client()
        result = client.directions(
            coordinates=coords,
            profile='driving-car',
            format='json',
        )
        duration_seconds = result['routes'][0]['summary']['duration']
        minutes = round(duration_seconds / 60, 1)   # 分鐘
        cache.set(cache_key, minutes, DRIVE_CACHE_TTL)
        return minutes
    except Exception:
        return None


def get_optimal_order(customers):
    """
    計算最短路線，以公司為出發點，回傳排序後的 customer list。
    急單已在傳入前排到最前面，這裡只處理非急單的順序。
    """
    if len(customers) <= 1:
        return customers

    # 確保每個客戶都有 GPS 座標
    for customer in customers:
        if not customer.lat or not customer.lng:
            geocode_customer(customer)

    # 若有客戶仍無座標（geocode 失敗），直接回傳原始順序
    if any(not c.lat or not c.lng for c in customers):
        return customers

    # 取得公司座標作為出發點
    office = get_office_coords()
    if office:
        # coords[0] = 公司，coords[1..] = 客戶
        office_coord = [[float(office[1]), float(office[0])]]  # [lng, lat]
        customer_coords = [[float(c.lng), float(c.lat)] for c in customers]
        coords = office_coord + customer_coords
    else:
        # 無公司座標，退回純客戶間排列
        coords = [[float(c.lng), float(c.lat)] for c in customers]

    try:
        client = get_client()
        matrix = client.distance_matrix(
            locations=coords,
            metrics=['distance'],
            units='m',
        )
        distances = matrix['distances']

        # 最近鄰居法：O(n²)，取代暴力全排列 O(n!)
        # 從 index 0（公司）出發，每次選距離最近的未拜訪客戶
        start = 0 if office else None
        unvisited = set(range(len(customers) if not office else 1, len(coords)))
        current = start if start is not None else 0
        order = []

        while unvisited:
            nearest = min(unvisited, key=lambda i: distances[current][i])
            order.append(nearest - (1 if office else 0))  # 轉回 customer index
            unvisited.remove(nearest)
            current = nearest

        return [customers[i] for i in order]

    except Exception:
        return customers
