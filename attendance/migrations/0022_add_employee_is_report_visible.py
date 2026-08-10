from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0021_add_employee_is_active'),
    ]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='is_report_visible',
            field=models.BooleanField(
                default=True,
                help_text='取消勾選＝純管理帳號，不列入出勤、薪資、請假、分析等報表',
                verbose_name='列入出勤/報表',
            ),
        ),
    ]
