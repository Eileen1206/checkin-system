from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('attendance', '0028_missedpunch_break'),
    ]

    operations = [
        migrations.CreateModel(
            name='PayrollRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('year', models.IntegerField(verbose_name='年')),
                ('month', models.IntegerField(verbose_name='月')),
                ('base', models.IntegerField(default=0, verbose_name='底薪')),
                ('maintenance', models.IntegerField(default=0, verbose_name='保養費（車油錢）')),
                ('allowance', models.IntegerField(default=0, verbose_name='勞務加給')),
                ('overtime', models.IntegerField(default=0, verbose_name='加班費')),
                ('deduction', models.IntegerField(default=0, verbose_name='勞健保扣除')),
                ('total', models.IntegerField(default=0, verbose_name='實領')),
                ('work_minutes', models.IntegerField(default=0, verbose_name='工時（分鐘）')),
                ('late_days', models.IntegerField(default=0, verbose_name='遲到次數')),
                ('late_minutes', models.IntegerField(default=0, verbose_name='遲到分鐘')),
                ('missed_punch', models.IntegerField(default=0, verbose_name='漏打卡次數')),
                ('locked', models.BooleanField(
                    default=True, help_text='解鎖後可修改打卡，重新結算會覆蓋金額',
                    verbose_name='已鎖定')),
                ('settled_at', models.DateTimeField(auto_now=True, verbose_name='結算時間')),
                ('employee', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='payroll_records', to='attendance.employee',
                    verbose_name='員工')),
                ('settled_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL, verbose_name='結算者')),
            ],
            options={
                'verbose_name': '薪資結算',
                'verbose_name_plural': '薪資結算',
                'ordering': ['-year', '-month', 'employee__employee_id'],
                'unique_together': {('employee', 'year', 'month')},
            },
        ),
    ]
