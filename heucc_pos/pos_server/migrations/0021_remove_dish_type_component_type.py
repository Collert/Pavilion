# Generated by Django 4.2.9 on 2024-01-25 19:50

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pos_server', '0020_dish_type_alter_dish_station'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='dish',
            name='type',
        ),
        migrations.AddField(
            model_name='component',
            name='type',
            field=models.CharField(choices=[('food', 'Food'), ('beverage', 'Beverage')], default='food', max_length=10),
            preserve_default=False,
        ),
    ]
