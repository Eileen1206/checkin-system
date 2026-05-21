"""
anonymize_gps — 定期匿名化舊打卡記錄的 GPS 座標

保留：員工、打卡類型、打卡時間（薪資計算、出勤記錄用）
清除：latitude、longitude、distance_meters（個資法合規）

建議排程：每月 1 日 03:00 跑一次
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from attendance.models import AttendanceRecord


class Command(BaseCommand):
    help = '將 6 個月前的打卡 GPS 座標匿名化（歸 NULL）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--months',
            type=int,
            default=6,
            help='保留幾個月內的 GPS 資料（預設 6）',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='只顯示會影響幾筆，不實際寫入',
        )

    def handle(self, *args, **options):
        months   = options['months']
        dry_run  = options['dry_run']
        cutoff   = timezone.now() - timedelta(days=months * 30)

        # 只找有 GPS 資料（lat 或 lng 不為 NULL）的舊記錄
        qs = AttendanceRecord.objects.filter(
            timestamp__lt=cutoff,
        ).exclude(
            latitude__isnull=True,
            longitude__isnull=True,
        )

        count = qs.count()

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f'[Dry Run] 將匿名化 {count} 筆 {months} 個月前的 GPS 座標'
                    f'（截止時間：{cutoff.strftime("%Y-%m-%d")}）'
                )
            )
            return

        if count == 0:
            self.stdout.write('無需匿名化的舊 GPS 資料。')
            return

        updated = qs.update(
            latitude=None,
            longitude=None,
            distance_meters=None,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f'✅ 已匿名化 {updated} 筆打卡 GPS 座標'
                f'（{months} 個月前，截止 {cutoff.strftime("%Y-%m-%d")}）'
            )
        )
