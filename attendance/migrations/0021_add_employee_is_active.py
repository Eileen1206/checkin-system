from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0020_add_planned_drive_minutes'),
    ]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='is_active',
            field=models.BooleanField(
                default=True,
                help_text='取消勾選＝停用（離職）；資料保留，但不在各列表顯示',
                verbose_name='在職',
            ),
        ),
    ]
