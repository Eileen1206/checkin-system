import itertools
import openrouteservice
from django.conf import settings


def get_client():
    return openrouteservice.Client(key=settings.ORS_API_KEY)


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

        best_order = list(range(len(customers)))
        best_distance = float('inf')

        if office:
            # 固定從 index 0（公司）出發，對客戶 index 1..N 排列
            for perm in itertools.permutations(range(1, len(coords))):
                # 公司→第一站 + 各站之間
                total = distances[0][perm[0]]
                total += sum(distances[perm[i]][perm[i + 1]] for i in range(len(perm) - 1))
                if total < best_distance:
                    best_distance = total
                    best_order = [p - 1 for p in perm]  # 轉回 customer index
        else:
            for perm in itertools.permutations(range(len(customers))):
                total = sum(distances[perm[i]][perm[i + 1]] for i in range(len(perm) - 1))
                if total < best_distance:
                    best_distance = total
                    best_order = list(perm)

        return [customers[i] for i in best_order]

    except Exception:
        return customers
