from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0024_holiday'),
    ]

    operations = [
        # 既有紀錄皆為月曆拖曳建立的排休 → kind 預設 rest 即為正確值
        migrations.AddField(
            model_name='leaverecord',
            name='kind',
            field=models.CharField(
                choices=[('rest', '排休'), ('leave', '請假')],
                default='rest', max_length=10, verbose_name='類別',
            ),
        ),
        migrations.AddField(
            model_name='leaverecord',
            name='leave_type',
            field=models.CharField(
                blank=True,
                choices=[('annual', '特休'), ('personal', '事假'),
                         ('sick', '病假'), ('funeral', '喪假')],
                help_text='僅請假需要；排休留空',
                max_length=10, verbose_name='假別',
            ),
        ),
        migrations.AddField(
            model_name='leaverecord',
            name='hours',
            field=models.FloatField(
                blank=True, null=True,
                help_text='排休為整天（留空）；請假填時數，整天為 8、半天為 4',
                verbose_name='請假時數',
            ),
        ),
        migrations.AlterField(
            model_name='leaverecord',
            name='date',
            field=models.DateField(verbose_name='日期'),
        ),
        migrations.AlterField(
            model_name='leaverecord',
            name='reason',
            field=models.CharField(blank=True, max_length=100, verbose_name='備註'),
        ),
        migrations.AlterModelOptions(
            name='leaverecord',
            options={'ordering': ['-date'], 'verbose_name': '休假紀錄',
                     'verbose_name_plural': '休假紀錄'},
        ),
    ]
