# Generated by Django 4.2.9 on 2024-01-11 20:16

import datetime
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pos_server', '0009_alter_order_timestamp'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='prep_time',
            field=models.DurationField(default=datetime.timedelta(seconds=1200)),
            preserve_default=False,
        ),
    ]
