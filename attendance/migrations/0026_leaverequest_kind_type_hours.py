from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0025_leaverecord_kind_type_hours'),
    ]

    operations = [
        # 既有申請都是「整天不來」→ kind 預設 rest 即為正確值
        migrations.AddField(
            model_name='leaverequest',
            name='kind',
            field=models.CharField(
                choices=[('rest', '排休'), ('leave', '請假')],
                default='rest', max_length=10, verbose_name='類別',
            ),
        ),
        migrations.AddField(
            model_name='leaverequest',
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
            model_name='leaverequest',
            name='hours',
            field=models.FloatField(
                blank=True, null=True,
                help_text='排休為整天（留空）；請假填時數，整天為 8、半天為 4',
                verbose_name='請假時數',
            ),
        ),
        migrations.AlterField(
            model_name='leaverequest',
            name='dates',
            field=models.JSONField(verbose_name='日期'),
        ),
        migrations.AlterModelOptions(
            name='leaverequest',
            options={'ordering': ['-requested_at'], 'verbose_name': '休假申請',
                     'verbose_name_plural': '休假申請'},
        ),
    ]
