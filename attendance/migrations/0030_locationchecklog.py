from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0029_payrollrecord'),
    ]

    operations = [
        migrations.CreateModel(
            name='LocationCheckLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('lat', models.DecimalField(blank=True, decimal_places=6, max_digits=9,
                                            null=True, verbose_name='緯度')),
                ('lng', models.DecimalField(blank=True, decimal_places=6, max_digits=9,
                                            null=True, verbose_name='經度')),
                ('accuracy', models.IntegerField(
                    blank=True, null=True,
                    help_text='瀏覽器回報的誤差半徑，數字越大越不準',
                    verbose_name='定位精度（公尺）')),
                ('distance_meters', models.IntegerField(blank=True, null=True,
                                                        verbose_name='距客戶距離（公尺）')),
                ('allowed_meters', models.IntegerField(blank=True, null=True,
                                                       verbose_name='允許範圍（公尺）')),
                ('result', models.CharField(
                    choices=[('pass', '通過'), ('too_far', '距離過遠'),
                             ('low_accuracy', '定位精度不足')],
                    max_length=15, verbose_name='結果')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('customer', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    to='attendance.customer', verbose_name='客戶')),
                ('employee', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='location_checks', to='attendance.employee',
                    verbose_name='員工')),
                ('task', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    to='attendance.deliverytask', verbose_name='送貨任務')),
            ],
            options={
                'verbose_name': '定位驗證紀錄',
                'verbose_name_plural': '定位驗證紀錄',
                'ordering': ['-created_at'],
            },
        ),
    ]
