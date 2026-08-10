from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0023_add_employee_hire_date'),
    ]

    operations = [
        migrations.CreateModel(
            name='Holiday',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(unique=True, verbose_name='日期')),
                ('name', models.CharField(blank=True, max_length=50, verbose_name='名稱')),
            ],
            options={
                'verbose_name': '國定假日',
                'verbose_name_plural': '國定假日',
                'ordering': ['date'],
            },
        ),
    ]
