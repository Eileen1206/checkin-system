from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0022_add_employee_is_report_visible'),
    ]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='hire_date',
            field=models.DateField(
                blank=True, null=True,
                help_text='用於週年制特休與年資計算',
                verbose_name='到職日',
            ),
        ),
    ]
