import hashlib

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models
from django.db.models import Min, Sum


def hash_number(number):
    digits = "".join(c for c in str(number) if c.isdigit())
    return hashlib.sha256(f"pavilion-gift-card:{digits}".encode()).hexdigest()


def keep_last4_only(apps, schema_editor):
    """Hash every card's number and keep its last 4; fill in what it started with and its history from its orders."""
    GiftCard = apps.get_model("gift_cards", "GiftCard")
    Event = apps.get_model("gift_cards", "GiftCardEvent")
    Auth = apps.get_model("gift_cards", "GiftCardAuthorization")
    today = django.utils.timezone.localdate()
    Design = apps.get_model("gift_cards", "GiftCardDesign")
    if not Design.objects.exists():  # something to issue cards with until the Gift card designs page has more
        Design.objects.create(name="Classic", amounts=[25, 50, 100], message="Enjoy something good on us.")
    for card in GiftCard.objects.all():
        auths = Auth.objects.filter(card=card).select_related("order").order_by("order__timestamp")
        spent = auths.aggregate(s=Sum("charged_balance"))["s"] or 0
        first = auths.aggregate(t=Min("order__timestamp"))["t"]
        card.number_hash, card.last4 = hash_number(card.number), card.number[-4:]
        card.original = card.available_balance + spent
        card.issued_at = first or django.utils.timezone.now()
        card.source = "online"
        if card.available_balance <= 0:
            card.empty_since = today  # the 30 days start now, not on some past date
        card.save()
        left = card.original
        Event.objects.create(card=card, at=card.issued_at, kind="issue", amount=card.original, balance=left, note="Bought online")
        for auth in auths:
            left -= auth.charged_balance
            Event.objects.create(card=card, at=auth.order.timestamp, kind="use", amount=-auth.charged_balance, balance=left, order=auth.order)


class Migration(migrations.Migration):

    dependencies = [
        ('gift_cards', '0004_staff_cards_on_shift'),
        ('pos_server', '0030_automatic_discounts'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='GiftCardDesign',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=40)),
                ('image', models.ImageField(blank=True, null=True, upload_to='files/gift-cards/designs')),
                ('color', models.CharField(default='#1F4A3A', help_text="Shown while there's no picture", max_length=7)),
                ('ink', models.CharField(choices=[('light', 'Light'), ('dark', 'Dark')], default='light', max_length=5)),
                ('show_text', models.BooleanField(default=True, help_text='Print the store name and amount on the card')),
                ('amounts', models.JSONField(blank=True, default=list, help_text='Whole dollars, e.g. [25, 50, 100]')),
                ('custom', models.BooleanField(default=False, help_text='Customers can type another amount')),
                ('min_amount', models.PositiveIntegerField(default=10)),
                ('max_amount', models.PositiveIntegerField(default=250)),
                ('message', models.CharField(blank=True, max_length=120)),
                ('online', models.BooleanField(default=True)),
                ('on_register', models.BooleanField(default=True)),
                ('seasonal', models.BooleanField(default=False)),
                ('show_from', models.DateField(blank=True, null=True)),
                ('show_until', models.DateField(blank=True, null=True)),
                ('hidden', models.BooleanField(default=False)),
                ('position', models.PositiveIntegerField(default=0)),
            ],
            options={'ordering': ['position', 'id']},
        ),
        migrations.AlterModelOptions(name='giftcard', options={'ordering': ['-issued_at', '-id']}),
        migrations.AddField(model_name='giftcard', name='number_hash', field=models.CharField(default='', editable=False, max_length=64)),
        migrations.AddField(model_name='giftcard', name='last4', field=models.CharField(default='', editable=False, max_length=4)),
        migrations.AddField(model_name='giftcard', name='design', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cards', to='gift_cards.giftcarddesign')),
        migrations.AddField(model_name='giftcard', name='recipient', field=models.CharField(blank=True, max_length=60)),
        migrations.AddField(model_name='giftcard', name='message', field=models.CharField(blank=True, max_length=120)),
        migrations.AddField(model_name='giftcard', name='note', field=models.CharField(blank=True, help_text='Only staff see it', max_length=120)),
        migrations.AddField(model_name='giftcard', name='original', field=models.DecimalField(decimal_places=2, default=0, max_digits=7)),
        migrations.AddField(model_name='giftcard', name='source', field=models.CharField(choices=[('online', 'Online store'), ('pos', 'Register'), ('staff', 'Issued by staff')], default='online', max_length=6)),
        migrations.AddField(model_name='giftcard', name='buyer', field=models.CharField(blank=True, help_text='Who bought or issued it', max_length=80)),
        migrations.AddField(model_name='giftcard', name='issued_at', field=models.DateTimeField(default=django.utils.timezone.now)),
        migrations.AddField(model_name='giftcard', name='issued_by', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name='giftcard', name='frozen', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='giftcard', name='empty_since', field=models.DateField(blank=True, null=True)),
        migrations.AlterField(model_name='giftcard', name='available_balance', field=models.DecimalField(decimal_places=2, default=0, max_digits=7)),
        migrations.AlterField(model_name='giftcard', name='image', field=models.ImageField(blank=True, help_text='Cards bought before designs', null=True, upload_to='files/gift-cards/presets')),
        migrations.AlterField(model_name='giftcardauthorization', name='card', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='gift_cards.giftcard')),
        migrations.AlterField(model_name='giftcardauthorization', name='charged_balance', field=models.DecimalField(decimal_places=2, max_digits=7)),
        migrations.CreateModel(
            name='GiftCardEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('at', models.DateTimeField(default=django.utils.timezone.now)),
                ('kind', models.CharField(choices=[('issue', 'Activated'), ('use', 'Used'), ('refund', 'Money back'), ('adjust', 'Adjusted'), ('note', 'Note')], max_length=6)),
                ('amount', models.DecimalField(decimal_places=2, default=0, max_digits=7)),
                ('balance', models.DecimalField(decimal_places=2, default=0, help_text='Left after it', max_digits=7)),
                ('note', models.CharField(blank=True, max_length=120)),
                ('by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('card', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='gift_cards.giftcard')),
                ('order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='pos_server.order')),
            ],
            options={'ordering': ['at', 'id']},
        ),
        migrations.RunPython(keep_last4_only, migrations.RunPython.noop),
        migrations.RemoveField(model_name='giftcard', name='number'),
        migrations.AlterField(model_name='giftcard', name='number_hash', field=models.CharField(editable=False, max_length=64, unique=True)),
        migrations.AlterField(model_name='giftcard', name='last4', field=models.CharField(editable=False, max_length=4)),
    ]
