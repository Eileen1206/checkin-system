import uuid
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Employee(models.Model):
    EMPLOYMENT_TYPE_CHOICES = [
        ('monthly', '月薪制'),
        ('hourly', '時薪制'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='employee')
    employee_id = models.CharField('工號', max_length=20, unique=True)
    department = models.CharField('部門', max_length=50)
    phone = models.CharField('電話', max_length=20, blank=True)
    line_user_id = models.CharField('LINE User ID', max_length=50, unique=True, null=True, blank=True)
    rfid_uid = models.CharField('RFID 卡號', max_length=20, unique=True, null=True, blank=True)
    employment_type = models.CharField('薪資類型', max_length=10, choices=EMPLOYMENT_TYPE_CHOICES, default='monthly')
    monthly_salary = models.DecimalField('月薪', max_digits=10, decimal_places=2, null=True, blank=True)
    hourly_rate = models.DecimalField('時薪', max_digits=8, decimal_places=2, null=True, blank=True)
    fuel_daily_allowance = models.DecimalField('每日油費補貼', max_digits=8, decimal_places=2, default=0)
    is_delivery = models.BooleanField('是否為送貨員', default=False)
    labor_insurance_amount = models.DecimalField('勞保自負月額', max_digits=8, decimal_places=2, null=True, blank=True)
    health_insurance_amount = models.DecimalField('健保自負月額', max_digits=8, decimal_places=2, null=True, blank=True)
    work_start_time = models.TimeField('上班時間', null=True, blank=True)
    work_end_time = models.TimeField('下班時間', null=True, blank=True)


    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '員工'
        verbose_name_plural = '員工'

    def __str__(self):
        return f"{self.employee_id} - {self.user.get_full_name() or self.user.username}"


class OfficeLocation(models.Model):
    name = models.CharField('地點名稱', max_length=100)
    latitude = models.DecimalField('緯度', max_digits=9, decimal_places=6)
    longitude = models.DecimalField('經度', max_digits=9, decimal_places=6)
    radius_meters = models.IntegerField('有效半徑（公尺）', default=100)
    is_active = models.BooleanField('啟用', default=True)

    class Meta:
        verbose_name = '公司地點'
        verbose_name_plural = '公司地點'

    def __str__(self):
        return self.name


class AttendanceRecord(models.Model):
    RECORD_TYPE_CHOICES = [
        ('clock_in', '上班打卡'),
        ('break_start', '午休開始'),
        ('break_end', '午休結束'),
        ('clock_out', '下班打卡'),
    ]
    SOURCE_CHOICES = [
        ('line', 'LINE Bot'),
        ('rfid', 'RFID'),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='records', verbose_name='員工')
    record_type = models.CharField('打卡類型', max_length=15, choices=RECORD_TYPE_CHOICES)
    timestamp = models.DateTimeField('打卡時間', default=timezone.now)
    latitude = models.DecimalField('緯度', max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField('經度', max_digits=9, decimal_places=6, null=True, blank=True)
    is_valid = models.BooleanField('GPS 驗證通過', default=True)
    distance_meters = models.IntegerField('距公司距離（公尺）', null=True, blank=True)
    source = models.CharField('打卡來源', max_length=10, choices=SOURCE_CHOICES, default='line')

    class Meta:
        verbose_name = '打卡紀錄'
        verbose_name_plural = '打卡紀錄'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['employee', 'timestamp']),
        ]

    def __str__(self):
        return f"{self.employee} - {self.get_record_type_display()} - {self.timestamp:%Y/%m/%d %H:%M}"

    @classmethod
    def get_today_records(cls, employee):
        today = timezone.localdate()
        return cls.objects.filter(employee=employee, timestamp__date=today)


class BindingToken(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='binding_tokens', verbose_name='員工')
    token = models.CharField('綁定碼', max_length=64, unique=True, default=uuid.uuid4)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField('有效期限')
    used = models.BooleanField('已使用', default=False)

    class Meta:
        verbose_name = '綁定 Token'
        verbose_name_plural = '綁定 Token'

    def __str__(self):
        return f"{self.employee} - {'已使用' if self.used else '未使用'}"

    def is_valid_token(self):
        return not self.used and timezone.now() < self.expires_at

    def save(self, *args, **kwargs):
        if not self.pk and not self.expires_at:
            self.expires_at = timezone.now() + timezone.timedelta(hours=24)
        super().save(*args, **kwargs)


class MonthlyAllowance(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='allowances', verbose_name='員工')
    year = models.IntegerField('年')
    month = models.IntegerField('月')
    amount = models.DecimalField('加給金額', max_digits=10, decimal_places=2)
    note = models.TextField('備註', blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name='建立者')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '特殊加給'
        verbose_name_plural = '特殊加給'
        unique_together = [['employee', 'year', 'month']]

    def __str__(self):
        return f"{self.employee} - {self.year}/{self.month:02d} +${self.amount}"



class AuditLog(models.Model):
    ACTION_CHOICES = [
        ('create', '新增'),
        ('update', '修改'),
        ('delete', '刪除'),
    ]

    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name='操作者')
    action = models.CharField('動作', max_length=10, choices=ACTION_CHOICES)
    target_model = models.CharField('目標 Model', max_length=50)
    target_id = models.IntegerField('目標 ID')
    changes = models.JSONField('變更內容', default=dict)
    timestamp = models.DateTimeField('操作時間', auto_now_add=True)

    class Meta:
        verbose_name = '稽核日誌'
        verbose_name_plural = '稽核日誌'
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.actor} - {self.get_action_display()} {self.target_model}#{self.target_id}"


class DeliveryTask(models.Model):
    STATUS_CHOICES = [
        ('pending', '待出發'),
        ('arrived', '已到達'),
        ('completed', '已完成'),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='delivery_tasks', verbose_name='送貨員')
    date = models.DateField('任務日期')
    order = models.IntegerField('第幾站')
    customer_name = models.CharField('客戶名稱', max_length=100)
    address = models.CharField('地址', max_length=200)
    status = models.CharField('狀態', max_length=15, choices=STATUS_CHOICES, default='pending')
    arrived_at = models.DateTimeField('到達時間', null=True, blank=True)
    completed_at = models.DateTimeField('完成時間', null=True, blank=True)
    is_urgent = models.BooleanField('急單', default=False)
    note = models.TextField('備註', blank=True)
    customer = models.ForeignKey(
    'Customer', 
    on_delete=models.SET_NULL, 
    null=True, blank=True,
    verbose_name='客戶'
    )


    class Meta:
        verbose_name = '送貨任務'
        verbose_name_plural = '送貨任務'
        ordering = ['date', 'order']

    def __str__(self):
        return f"{self.employee} - {self.date} 第{self.order}站：{self.customer_name}"
    
    



    
class Customer(models.Model):
    customer_id = models.CharField('客戶編號', max_length=20, unique=True)
    name        = models.CharField('客戶名稱', max_length=100)
    address     = models.CharField('地址', max_length=200)
    phone       = models.CharField('電話', max_length=20, blank=True)
    lat         = models.DecimalField('緯度', max_digits=9, decimal_places=6, null=True, blank=True)
    lng         = models.DecimalField('經度', max_digits=9, decimal_places=6, null=True, blank=True)
    is_active   = models.BooleanField('啟用', default=True)
    updated_at  = models.DateTimeField('更新時間', auto_now=True)

