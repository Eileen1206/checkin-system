from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0026_leaverequest_kind_type_hours'),
    ]

    operations = [
        migrations.CreateModel(
            name='MissedPunch',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('date', models.DateField(verbose_name='日期')),
                ('missing', models.CharField(
                    choices=[('clock_in', '上班卡'), ('clock_out', '下班卡')],
                    max_length=10, verbose_name='漏打')),
                ('notified_at', models.DateTimeField(blank=True, null=True,
                                                     verbose_name='已通知時間')),
                ('voided', models.BooleanField(
                    default=False, help_text='誤判或特殊情況，註銷後不列入計次',
                    verbose_name='已註銷')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('employee', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='missed_punches', to='attendance.employee',
                    verbose_name='員工')),
            ],
            options={
                'verbose_name': '漏打卡',
                'verbose_name_plural': '漏打卡',
                'ordering': ['-date'],
                'unique_together': {('employee', 'date')},
            },
        ),
    ]
