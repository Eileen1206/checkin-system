from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0030_locationchecklog'),
    ]

    operations = [
        migrations.CreateModel(
            name='ShiftOverride',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('date', models.DateField(verbose_name='日期')),
                ('start_time', models.TimeField(verbose_name='上班時間')),
                ('end_time', models.TimeField(verbose_name='下班時間')),
                ('note', models.CharField(blank=True, max_length=50, verbose_name='備註')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('employee', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='shift_overrides', to='attendance.employee',
                    verbose_name='員工')),
            ],
            options={
                'verbose_name': '當日班別',
                'verbose_name_plural': '當日班別',
                'ordering': ['-date'],
                'unique_together': {('employee', 'date')},
            },
        ),
    ]
