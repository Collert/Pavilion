# Generated by Django 4.2.9 on 2024-01-09 01:55

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pos_server', '0008_order_timestamp'),
    ]

    operations = [
        migrations.AlterField(
            model_name='order',
            name='timestamp',
            field=models.DateTimeField(auto_now_add=True),
        ),
    ]
