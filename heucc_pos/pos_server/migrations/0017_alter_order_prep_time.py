# Generated by Django 4.2.9 on 2024-01-14 00:53

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pos_server', '0016_order_special_instructions_alter_order_table'),
    ]

    operations = [
        migrations.AlterField(
            model_name='order',
            name='prep_time',
            field=models.DurationField(null=True),
        ),
    ]
