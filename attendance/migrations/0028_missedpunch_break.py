from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0027_missedpunch'),
    ]

    operations = [
        migrations.AlterField(
            model_name='missedpunch',
            name='missing',
            field=models.CharField(
                choices=[('clock_in', '上班卡'), ('clock_out', '下班卡'),
                         ('break', '午休卡')],
                max_length=10, verbose_name='漏打'),
        ),
    ]
