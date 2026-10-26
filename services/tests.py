from unittest import mock
from decimal import Decimal

from django.test import TestCase, override_settings

from .models import Service


class QuoteTests(TestCase):
    @mock.patch("admin_portal.store_info.tax_rate", new=lambda: Decimal("0.05"))
    def test_staff_by_the_hour_with_gratuity_tax_and_minimum(self):
        server = Service(title="Server", category=Service.STAFF, pricing=Service.HOUR, price=Decimal("38"), minimum=4,
                         details={"staff": 2, "gratuity": 15})
        q = server.quote(amount=2, count=3)
        self.assertEqual((q["quantity"], q["count"]), (4, 3))  # charged the 4-hour minimum
        self.assertEqual(q["subtotal"], Decimal("456.00"))
        self.assertEqual(q["gratuity"], Decimal("68.40"))
        self.assertEqual(q["tax"], Decimal("26.22"))
        self.assertEqual(q["total"], Decimal("550.62"))
        self.assertEqual([str(n) for n in q["notes"]], ["Below the minimum, so it’s charged as 4 hours.", "Only 2 people on the roster."])

    def test_equipment_deposit_and_untaxed_flat_venue(self):
        urn = Service(category=Service.EQUIPMENT, pricing=Service.DAY, price=Decimal("45"), minimum=1, details={"owned": 3},
                      deposit=True, deposit_amount=Decimal("75"), tax=Service.NO_TAX)
        q = urn.quote(amount=2, count=2)
        self.assertEqual((q["total"], q["deposit"]), (Decimal("180.00"), Decimal("150")))
        urn.deposit_per = Service.PER_BOOKING
        self.assertEqual(urn.quote(2, 2)["deposit"], Decimal("75"))
        venue = Service(category=Service.VENUE, pricing=Service.FLAT, price=Decimal("1800"), details={"min_spend": 3500}, tax=Service.NO_TAX)
        q = venue.quote(amount=9, count=5)  # flat: neither counts
        self.assertEqual((q["quantity"], q["count"], q["total"], q["min_spend"]), (1, 1, Decimal("1800.00"), Decimal("3500")))
