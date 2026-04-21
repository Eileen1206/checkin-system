import time
import urllib.request
import urllib.parse
import json
from django.core.management.base import BaseCommand
from attendance.models import Customer


def nominatim_geocode(address):
    """
    用 Nominatim 將地址轉成 (lat, lng)，失敗回傳 None。
    嘗試順序：完整地址 → 去掉門牌號 → 只取縣市+路段
    """
    import re
    candidates = [address]

    # 去掉末尾門牌號（如「78-15號」「97號之3」）
    stripped = re.sub(r'\d+[-\d]*號.*$', '', address).strip()
    if stripped and stripped != address:
        candidates.append(stripped)

    # 只取到「X段」（去掉門牌）
    to_section = re.sub(r'(\d+段).*$', r'\1', address).strip()
    if to_section and to_section not in candidates:
        candidates.append(to_section)

    # 去掉「X段」，只保留路名（Nominatim 對段不擅長）
    no_section = re.sub(r'\d+段.*$', '', address).strip()
    if no_section and no_section not in candidates:
        candidates.append(no_section)

    # 只保留縣市+區+路名（不含號數與段）
    road_only = re.sub(r'(\S+[路街道巷弄]).*$', r'\1', address).strip()
    if road_only and road_only not in candidates and len(road_only) > 4:
        candidates.append(road_only)

    headers = {'User-Agent': 'checkin-system-geocoder/1.0'}

    for query in candidates:
        params = urllib.parse.urlencode({
            'q': query,
            'format': 'json',
            'countrycodes': 'tw',
            'limit': 1,
            'accept-language': 'zh-TW',
        })
        url = f'https://nominatim.openstreetmap.org/search?{params}'
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            if data:
                lat = float(data[0]['lat'])
                lng = float(data[0]['lon'])
                # 粗略驗證是否在台灣範圍內
                if 21.5 <= lat <= 25.5 and 119.5 <= lng <= 122.5:
                    return lat, lng
        except Exception:
            pass
        time.sleep(1)  # 每次嘗試之間也要等

    return None


class Command(BaseCommand):
    help = '批次將客戶地址轉換為 GPS 座標（使用 Nominatim，每秒一筆）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--all',
            action='store_true',
            help='重新轉換所有客戶（包含已有座標的）',
        )
        parser.add_argument(
            '--id',
            type=str,
            help='只轉換指定 customer_id',
        )

    def handle(self, *args, **options):
        if options['id']:
            customers = Customer.objects.filter(customer_id=options['id'])
        elif options['all']:
            customers = Customer.objects.filter(is_active=True).exclude(address='')
        else:
            # 預設：只處理有地址但無座標的客戶
            customers = Customer.objects.filter(
                is_active=True, lat__isnull=True
            ).exclude(address='')

        total = customers.count()
        self.stdout.write(f'共 {total} 筆待轉換...\n')

        success = 0
        fail = 0

        for i, c in enumerate(customers, 1):
            self.stdout.write(f'[{i}/{total}] {c.customer_id} {c.name} ... ', ending='')
            self.stdout.flush()

            result = nominatim_geocode(c.address)

            if result:
                c.lat, c.lng = result
                c.save(update_fields=['lat', 'lng'])
                self.stdout.write(f'✓ ({result[0]:.4f}, {result[1]:.4f})')
                success += 1
            else:
                self.stdout.write('✗ 失敗')
                fail += 1

            time.sleep(1)  # Nominatim 使用規範：每秒最多一筆

        self.stdout.write(f'\n完成：成功 {success} 筆，失敗 {fail} 筆')
