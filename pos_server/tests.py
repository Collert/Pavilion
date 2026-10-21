"""
Tests for pricing (customizations, combos, discounts, tax), order creation, stock, station status
transitions, the KDS, the register (quote, payments, cash drawer, PIN lock) and access rules.

Run with: venv\\Scripts\\python manage.py test
Square is never called: payments.square functions are patched where they'd be used.
"""
import datetime
import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Permission, User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from gift_cards.models import GiftCard, GiftCardAuthorization
from online_store.models import RejectedOrder
from payments.models import PaymentAuthorization, Transaction
from users.models import Staff
from .menu_schedule import Schedule
from .models import (CashDrawerSession, RegisterSignIn, Combo, ComboSlot, ComboSlotOption, Component, Discount, Dish, DishComponent,
                     Extra, Menu, MenuRule, Option, OptionGroup, Order, OrderPayment, OrderStation, Station)
from .orders import OrderError, create_order
from .pricing import price_cart


class MenuFixture:
    """
    A small menu: a burger (kitchen) with adjustable parts, a patty-temperature option, a bun swap, an add-on
    and a condiment; coffee (bar) with a size option; and a lunch combo (burger + coffee or tea, set price).
    """

    @classmethod
    def build_menu(cls):
        cls.kitchen = Station.objects.get(code="kitchen")
        cls.bar = Station.objects.get(code="bar")

        def component(title, inventory, option="buy", extra_price="0"):
            return Component.objects.create(title=title, unit_of_measurement="ea", type="food", crafting_option=option,
                                            inventory=inventory, extra_price=Decimal(extra_price))

        cls.bun = component("Bun", 100)
        cls.gf_bun = component("Gluten-free bun", 10)
        cls.patty = component("Patty", 100, extra_price="4.00")
        cls.onion = component("Onion", 100)
        cls.avocado = component("Avocado", 20)
        cls.ketchup = component("Ketchup packet", 100)
        cls.shot = component("Espresso shot", 0, option="auto")

        cls.burger = Dish.objects.create(title="Burger", price=Decimal("14.50"), station=cls.kitchen)
        cls.dc_bun = DishComponent.objects.create(dish=cls.burger, component=cls.bun, quantity=1, adjustable=True)
        cls.dc_patty = DishComponent.objects.create(dish=cls.burger, component=cls.patty, quantity=1, adjustable=True)
        cls.dc_onion = DishComponent.objects.create(dish=cls.burger, component=cls.onion, quantity=1, adjustable=True)
        temp = OptionGroup.objects.create(title="Temperature", component=cls.patty, required=True)
        cls.medium = Option.objects.create(group=temp, title="Medium")
        cls.well = Option.objects.create(group=temp, title="Well done")
        cls.temp_key = f"{temp.id}.{cls.dc_patty.id}"
        bun_group = OptionGroup.objects.create(title="Bun", component=cls.bun, required=True)
        Option.objects.create(group=bun_group, title="Brioche")
        cls.gf = Option.objects.create(group=bun_group, title="Gluten-free", price=Decimal("1.50"), component=cls.gf_bun)
        cls.bun_key = f"{bun_group.id}.{cls.dc_bun.id}"
        addons = OptionGroup.objects.create(title="Add-ons", dish=cls.burger, pick=OptionGroup.ANY, addons=True)
        cls.avocado_addon = Option.objects.create(group=addons, title="Avocado", price=Decimal("2.25"), component=cls.avocado)
        cls.addons_key = str(addons.id)
        cls.ketchup_side = Extra.objects.create(dish=cls.burger, component=cls.ketchup, title="Ketchup")

        cls.coffee = Dish.objects.create(title="Coffee", price=Decimal("2.50"), station=cls.bar)
        DishComponent.objects.create(dish=cls.coffee, component=cls.shot, quantity=1)
        size = OptionGroup.objects.create(title="Size", dish=cls.coffee, required=True)
        Option.objects.create(group=size, title="Regular")
        cls.large = Option.objects.create(group=size, title="Large", price=Decimal("0.60"), multiplier=2)
        cls.size_key = str(size.id)
        cls.tea = Dish.objects.create(title="Tea", price=Decimal("2.00"), station=cls.bar)

        cls.combo = Combo.objects.create(title="Lunch combo", price=Decimal("16.00"))
        main = ComboSlot.objects.create(combo=cls.combo, title="Main", position=0)
        ComboSlotOption.objects.create(slot=main, dish=cls.burger, is_default=True)
        cls.drink = ComboSlot.objects.create(combo=cls.combo, title="Drink", position=1)
        ComboSlotOption.objects.create(slot=cls.drink, dish=cls.coffee, is_default=True)
        ComboSlotOption.objects.create(slot=cls.drink, dish=cls.tea, price=Decimal("0.50"))

        cls.menu = Menu.objects.create(title="Test menu", manual_on=True)
        cls.menu.dishes.set([cls.burger, cls.coffee, cls.tea])
        cls.menu.combos.set([cls.combo])

    @staticmethod
    def dish(dish, qty=1, cust=None, discount=None):
        return {"type": "dish", "id": dish.id, "qty": qty, "cust": cust or {}, **({"discount": discount} if discount else {})}

    @classmethod
    def lunch(cls, drink=None, qty=1, **extra):
        drink = drink or cls.coffee
        return {"type": "combo", "id": cls.combo.id, "qty": qty, "slots": {str(cls.drink.id): [{"dish": drink.id}]}, **extra}


class PricingTests(MenuFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_menu()

    def test_plain_dishes_are_priced_from_the_database(self):
        cart = price_cart([self.dish(self.burger, 2), self.dish(self.coffee)])
        self.assertEqual(cart.subtotal, Decimal("31.50"))
        self.assertEqual(cart.total, Decimal("31.50"))

    def test_extra_portions_cost_the_component_extra_price(self):
        cart = price_cart([self.dish(self.burger, cust={"parts": {str(self.dc_patty.id): 2, str(self.dc_onion.id): 0}})])
        self.assertEqual(cart.subtotal, Decimal("18.50"))
        self.assertEqual(cart.lines[0].picks[0].mods, ["2× patty", "No onion"])

    def test_options_addons_condiments_and_allergies(self):
        cust = {"options": {self.temp_key: self.well.id, self.bun_key: self.gf.id, self.addons_key: {str(self.avocado_addon.id): 1}},
                "condiments": {str(self.ketchup_side.id): 2}, "allergies": ["Dairy"], "note": "Cut in half"}
        cart = price_cart([self.dish(self.burger, cust=cust)])
        self.assertEqual(cart.subtotal, Decimal("18.25"))  # 14.50 + 1.50 bun + 2.25 avocado
        picked = cart.lines[0].picks[0]
        self.assertEqual(picked.mods, ["+ Avocado", "Gluten-free", "Well done", "2× Ketchup", "Allergy: dairy"])
        self.assertEqual(picked.note, "Cut in half")
        # The swapped bun is what gets used
        self.assertEqual(picked.use[self.gf_bun.id], 1)
        self.assertNotIn(self.bun.id, picked.use)

    def test_options_of_a_removed_part_are_ignored(self):
        cart = price_cart([self.dish(self.burger, cust={"parts": {str(self.dc_bun.id): 0}, "options": {self.bun_key: self.gf.id}})])
        self.assertEqual(cart.subtotal, Decimal("14.50"))

    def test_size_scales_what_is_used(self):
        cart = price_cart([self.dish(self.coffee, cust={"options": {self.size_key: self.large.id}})])
        self.assertEqual(cart.subtotal, Decimal("3.10"))
        self.assertEqual(cart.lines[0].picks[0].use[self.shot.id], 2)

    def test_invalid_customizations_are_refused(self):
        for cust in ({"parts": {str(self.dc_patty.id): 12}}, {"options": {self.temp_key: self.gf.id}},
                     {"options": {self.addons_key: {str(self.ketchup_side.id): 1}}}, {"options": {self.addons_key: {str(self.avocado_addon.id): 100}}},
                     {"options": {self.addons_key: [self.avocado_addon.id]}}, {"allergies": ["Glitter"]}):
            with self.assertRaises(OrderError, msg=cust):
                price_cart([self.dish(self.burger, cust=cust)])

    def test_add_ons_can_be_picked_many_times(self):
        cust = {"options": {self.addons_key: {str(self.avocado_addon.id): 3}}}
        picked = price_cart([self.dish(self.burger, cust=cust)]).lines[0].picks[0]
        self.assertEqual(picked.extra, Decimal("6.75"))
        self.assertEqual(picked.mods, ["+ 3× Avocado"])
        self.assertEqual(picked.use[self.avocado.id], 3)

    def test_parts_of_whole_pieces_go_up_to_ten_more(self):
        pierogi = Component.objects.create(title="Pierogi", unit_of_measurement="ea", type="food", inventory=500,
                                           extra_price=Decimal("0.75"))
        plate = Dish.objects.create(title="Pierogi plate", price=Decimal("12.00"), station=self.kitchen)
        part = DishComponent.objects.create(dish=plate, component=pierogi, quantity=8, adjustable=True)
        picked = price_cart([self.dish(plate, cust={"parts": {str(part.id): 18}})]).lines[0].picks[0]
        self.assertEqual(picked.extra, Decimal("7.50"))  # ten more at 0.75
        self.assertEqual(picked.use[pierogi.id], 18)
        self.assertEqual(price_cart([self.dish(plate, cust={"parts": {str(part.id): 5}})]).lines[0].picks[0].use[pierogi.id], 5)
        with self.assertRaises(OrderError):
            price_cart([self.dish(plate, cust={"parts": {str(part.id): 19}})])

    def test_required_and_optional_groups(self):
        sauce = OptionGroup.objects.create(title="Sauce", dish=self.tea)
        honey = Option.objects.create(group=sauce, title="Honey", price=Decimal("0.50"))
        self.assertEqual(price_cart([self.dish(self.tea)]).subtotal, Decimal("2.00"))  # optional: nothing picked
        self.assertEqual(price_cart([self.dish(self.tea, cust={"options": {str(sauce.id): honey.id}})]).subtotal, Decimal("2.50"))
        fillings = OptionGroup.objects.create(title="Fillings", dish=self.tea, pick=OptionGroup.ANY, required=True)
        lemon = Option.objects.create(group=fillings, title="Lemon")
        with self.assertRaises(OrderError):
            price_cart([self.dish(self.tea)])
        cart = price_cart([self.dish(self.tea, cust={"options": {str(fillings.id): {str(lemon.id): 2}}})])
        self.assertEqual(cart.lines[0].picks[0].mods, ["+ 2× Lemon"])

    def test_combo_price_with_swaps(self):
        cart = price_cart([self.lunch(self.tea)])
        self.assertEqual(cart.subtotal, Decimal("16.50"))
        self.assertEqual([p.dish for p in cart.lines[0].picks], [self.burger, self.tea])

    def test_required_choices_start_on_their_defaults(self):
        cart = price_cart([{"type": "combo", "id": self.combo.id, "qty": 1, "slots": {}}])
        self.assertEqual(cart.subtotal, Decimal("16.00"))
        self.assertEqual([p.dish for p in cart.lines[0].picks], [self.burger, self.coffee])
        line = self.lunch()
        line["slots"][str(self.drink.id)] = []  # a required choice can't be left out
        with self.assertRaises(OrderError):
            price_cart([line])

    def test_optional_choices_and_several_picks(self):
        treat = ComboSlot.objects.create(combo=self.combo, title="Treat", position=2, required=False, picks=2)
        ComboSlotOption.objects.create(slot=treat, dish=self.tea, price=Decimal("1.00"))
        self.assertEqual(price_cart([self.lunch()]).subtotal, Decimal("16.00"))  # skipped unless picked
        line = self.lunch(slots={str(treat.id): [{"dish": self.tea.id}, {"dish": self.tea.id}]})
        cart = price_cart([line])
        self.assertEqual(cart.subtotal, Decimal("18.00"))
        self.assertEqual([p.upcharge for p in cart.lines[0].picks], [Decimal("0.00"), Decimal("0.00"), Decimal("1.00"), Decimal("1.00")])
        line["slots"][str(treat.id)].append({"dish": self.tea.id})  # three is one too many
        with self.assertRaises(OrderError):
            price_cart([line])

    def test_percent_off_combos(self):
        Combo.objects.filter(pk=self.combo.pk).update(pricing=Combo.PERCENT, percent_off=Decimal("10"))
        cart = price_cart([self.lunch(self.tea)])
        self.assertEqual(cart.subtotal, Decimal("14.85"))  # (14.50 + 2.00) less 10%, upcharges don't apply
        self.assertEqual(Combo.objects.get(pk=self.combo.pk).standard_price(), Decimal("15.30"))

    def test_hidden_combos_are_refused(self):
        Combo.objects.filter(pk=self.combo.pk).update(active=False)
        with self.assertRaises(OrderError):
            price_cart([self.lunch()])

    def test_combo_pick_must_be_an_option(self):
        line = self.lunch()
        line["slots"][str(self.drink.id)] = [{"dish": self.burger.id}]
        with self.assertRaises(OrderError):
            price_cart([line])
        line["slots"][str(self.drink.id)] = {"dish": self.coffee.id}  # picks are a list
        with self.assertRaises(OrderError):
            price_cart([line])

    def test_out_of_stock_dish_is_rejected(self):
        Dish.objects.filter(pk=self.tea.pk).update(in_stock=False)
        with self.assertRaises(OrderError):
            price_cart([self.dish(self.tea)])
        with self.assertRaises(OrderError):
            price_cart([self.lunch(self.tea)])

    def test_unknown_items_are_rejected(self):
        with self.assertRaises(OrderError):
            price_cart([{"type": "dish", "id": 999999, "qty": 1}])
        with self.assertRaises(OrderError):
            price_cart([{"type": "dish", "id": self.tea.id, "qty": 0}])

    @override_settings(TAX_RATE="0.05")
    def test_tax_is_charged_after_discounts(self):
        cart = price_cart([self.dish(self.burger)], order_discount={"manual": {"kind": "amt", "value": "4.50"}}, can_manual=True)
        self.assertEqual(cart.tax, Decimal("0.50"))
        self.assertEqual(cart.total, Decimal("10.50"))


class DiscountTests(MenuFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_menu()
        cls.student = Discount.objects.create(name="Student", kind="pct", value=Decimal("15"))
        cls.coupon = Discount.objects.create(name="Welcome", kind="amt", value=Decimal("5"), scope="order", code="welcome",
                                             online=True, max_uses=1)

    def test_item_and_order_discounts(self):
        cart = price_cart([self.dish(self.burger, 2, discount={"offer": self.student.id}), self.dish(self.coffee)],
                          order_discount={"code": "WELCOME"})
        self.assertEqual(cart.lines[0].discount.amount, Decimal("4.35"))  # 15% of 29.00
        self.assertEqual(cart.order_discount.amount, Decimal("5.00"))
        self.assertEqual(cart.total, Decimal("31.50") - Decimal("9.35"))

    def test_codes_are_case_insensitive_and_checked(self):
        with self.assertRaises(OrderError):
            price_cart([self.dish(self.burger)], order_discount={"code": "NOPE"})
        with self.assertRaises(OrderError):  # an order coupon can't go on one item
            price_cart([self.dish(self.burger, discount={"code": "welcome"})])

    def test_manual_discounts_need_permission(self):
        spec = {"manual": {"kind": "pct", "value": "10"}}
        with self.assertRaises(OrderError):
            price_cart([self.dish(self.burger)], order_discount=spec)
        self.assertEqual(price_cart([self.dish(self.burger)], order_discount=spec, can_manual=True).discount, Decimal("1.45"))

    def test_conditions(self):
        happy = Discount.objects.create(name="Happy hour", kind="pct", value=Decimal("10"), weekdays="12345",
                                        start_time=datetime.time(14), end_time=datetime.time(17), min_items=2)
        tz = timezone.get_current_timezone()
        tuesday_3pm = datetime.datetime(2026, 10, 13, 15, 0, tzinfo=tz)
        lines = [self.dish(self.burger), self.dish(self.coffee)]
        self.assertEqual(price_cart(lines, order_discount={"offer": happy.id}, now=tuesday_3pm).discount, Decimal("1.70"))
        for when, cart_lines in ((tuesday_3pm.replace(hour=18), lines), (tuesday_3pm + datetime.timedelta(days=5), lines),
                                 (tuesday_3pm, lines[:1])):
            with self.assertRaises(OrderError):
                price_cart(cart_lines, order_discount={"offer": happy.id}, now=when)

    def test_item_discount_limited_to_some_dishes(self):
        self.student.dishes.set([self.coffee])
        with self.assertRaises(OrderError):
            price_cart([self.dish(self.burger, discount={"offer": self.student.id})])

    def test_lenient_quote_reports_instead_of_failing(self):
        cart = price_cart([self.dish(self.burger)], order_discount={"code": "NOPE"}, strict=False)
        self.assertEqual(cart.discount, 0)
        self.assertEqual(cart.order_discount_error, "That code isn't valid.")

    def test_use_is_counted_and_limited(self):
        create_order(lines=[self.dish(self.burger)], channel="web", initial_status=0, order_discount={"code": "welcome"})
        self.coupon.refresh_from_db()
        self.assertEqual(self.coupon.times_used, 1)
        with self.assertRaises(OrderError):
            create_order(lines=[self.dish(self.burger)], channel="web", initial_status=0, order_discount={"code": "welcome"})

    def test_register_only_coupons_are_refused_online(self):
        Discount.objects.create(name="Staff", kind="pct", value=Decimal("50"), code="STAFF")
        with self.assertRaises(OrderError):
            price_cart([self.dish(self.burger)], order_discount={"code": "STAFF"}, channel="web")


class CreateOrderTests(MenuFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_menu()

    def test_order_rows_and_statuses(self):
        cust = {"parts": {str(self.dc_onion.id): 0}, "note": "Extra crispy"}
        order = create_order(lines=[self.dish(self.burger, 2, cust=cust), self.lunch(self.tea)], channel="store",
                             initial_status=OrderStation.STATUS_APPROVED, name="Olena")
        self.assertEqual(order.total, Decimal("45.50"))
        self.assertFalse(order.picked_up)
        self.assertEqual(order.station_status_map(), {"kitchen": 1, "bar": 1})
        burger = order.items.get(dish=self.burger, parent=None)
        self.assertEqual((burger.quantity, burger.mods, burger.note), (2, ["No onion"], "Extra crispy"))
        combo = order.items.get(combo=self.combo)
        self.assertEqual([(c.title, c.slot) for c in combo.children.all()], [("Burger", "Main"), ("Tea", "Drink")])

    def test_stock_follows_customizations(self):
        cust = {"parts": {str(self.dc_patty.id): 2}, "options": {self.bun_key: self.gf.id}}
        create_order(lines=[self.dish(self.burger, 3, cust=cust)], channel="store", initial_status=1)
        for component, left in ((self.patty, 94), (self.gf_bun, 7), (self.bun, 100), (self.onion, 97)):
            component.refresh_from_db()
            self.assertEqual(component.inventory, left, component.title)

    def test_dish_goes_out_of_stock_when_component_runs_low(self):
        create_order(lines=[self.dish(self.burger, 5, cust={"options": {self.bun_key: self.gf.id}}),
                            self.dish(self.burger, 5, cust={"options": {self.bun_key: self.gf.id}})],
                     channel="store", initial_status=1)
        self.gf.refresh_from_db()
        self.assertFalse(self.gf.available)
        self.assertTrue(Dish.objects.get(pk=self.burger.pk).in_stock)  # the standard bun is still there

    def test_adding_a_missing_component_takes_a_dish_out_of_stock(self):
        empty = Component.objects.create(title="Empty", unit_of_measurement="ea", type="food", inventory=0)
        DishComponent.objects.create(dish=self.tea, component=empty, quantity=1)
        self.assertFalse(Dish.objects.get(pk=self.tea.pk).in_stock)

    def test_self_crafting_component_never_blocks_its_dish(self):
        create_order(lines=[self.dish(self.coffee, 5)], channel="store", initial_status=1)
        self.assertTrue(Dish.objects.get(pk=self.coffee.pk).in_stock)

    def test_register_payments_must_add_up(self):
        drawer = CashDrawerSession.objects.create(opening_amount=Decimal("100"))
        with self.assertRaises(OrderError):
            create_order(lines=[self.dish(self.tea)], channel="store", initial_status=1, drawer=drawer,
                         payments=[{"method": "cash", "amount": "1.00"}])
        with self.assertRaises(OrderError):  # cash needs an open drawer
            create_order(lines=[self.dish(self.tea)], channel="store", initial_status=1,
                         payments=[{"method": "cash", "amount": "2.00"}])
        order = create_order(lines=[self.dish(self.tea)], channel="store", initial_status=1, drawer=drawer,
                             payments=[{"method": "cash", "amount": "2.00", "tendered": "5.00"}])
        self.assertEqual(order.payments.get().change, Decimal("3.00"))

    def test_expected_total_must_match(self):
        with self.assertRaises(OrderError):
            create_order(lines=[self.dish(self.tea)], channel="store", initial_status=1, expected_total="1.00")

    def test_gift_card_partial_payment_with_cents(self):
        card = GiftCard.objects.create(available_balance=Decimal("10.00"))
        order = create_order(lines=[self.dish(self.burger)], channel="store", initial_status=1,
                             gift_card_payments=[(card.number, 3.5)])
        card.refresh_from_db()
        self.assertEqual(card.available_balance, Decimal("6.50"))
        self.assertEqual(GiftCardAuthorization.objects.get(order=order).charged_balance, Decimal("3.50"))
        self.assertEqual(order.payments.get().method, OrderPayment.GIFT)

    def test_failed_order_rolls_everything_back(self):
        card = GiftCard.objects.create(available_balance=Decimal("1.00"))
        with self.assertRaises(OrderError):
            create_order(lines=[self.dish(self.burger)], channel="store", initial_status=1,
                         gift_card_payments=[(card.number, "5.00")])
        self.assertFalse(Order.objects.exists())
        self.patty.refresh_from_db()
        self.assertEqual(self.patty.inventory, 100)

    def test_payment_check_can_block_the_order(self):
        def unpaid(remaining):
            raise OrderError("unpaid")
        with self.assertRaises(OrderError):
            create_order(lines=[self.dish(self.tea)], channel="web", initial_status=0, verify_payment=unpaid)
        self.assertFalse(Order.objects.exists())

    def test_prep_time_set_when_last_station_finishes(self):
        order = create_order(lines=[self.dish(self.burger), self.dish(self.tea)], channel="store", initial_status=1)
        order.set_station_status(["kitchen"], OrderStation.STATUS_DONE)
        self.assertIsNone(Order.objects.get(pk=order.pk).prep_time)
        order.set_station_status(["bar"], OrderStation.STATUS_DONE)
        self.assertIsNotNone(Order.objects.get(pk=order.pk).prep_time)

    def test_order_ready_signal_fires_once(self):
        from .signals import order_ready
        received = []
        handler = lambda sender, order, **kw: received.append(order.pk)
        order_ready.connect(handler)
        try:
            order = create_order(lines=[self.dish(self.burger), self.dish(self.tea)], channel="store", initial_status=1)
            order.set_station_status(["kitchen"], OrderStation.STATUS_DONE)
            order.set_station_status(["bar"], OrderStation.STATUS_DONE)
            order.set_station_status(["bar"], OrderStation.STATUS_DONE)
        finally:
            order_ready.disconnect(handler)
        self.assertEqual(received, [order.pk])


class MenuScheduleTests(TestCase):
    """Several menus can be live; automatic ones follow their rules (pos_server.menu_schedule)."""

    def at(self, weekday, hour, minute=0):
        monday = datetime.date(2026, 10, 19)  # a Monday
        return timezone.make_aware(datetime.datetime.combine(monday + datetime.timedelta(days=weekday), datetime.time(hour, minute)))

    def weekly(self, menu, kind, days, hour):
        return MenuRule.objects.create(menu=menu, kind=kind, type=MenuRule.WEEKLY, days=days, time=datetime.time(hour))

    def test_manual_menus_and_drafts(self):
        on = Menu.objects.create(title="On", manual_on=True, position=1)
        Menu.objects.create(title="Off")
        Menu.objects.create(title="Draft", manual_on=True, draft=True)
        top = Menu.objects.create(title="Top", manual_on=True, position=0)
        self.assertEqual(Menu.live_menus(), [top, on])
        self.assertEqual(Menu.active(), top)

    def test_weekly_rules_and_overnight(self):
        lunch = Menu.objects.create(title="Lunch", auto=True)
        self.weekly(lunch, "on", "0123456", 11)
        self.weekly(lunch, "off", "0123456", 21)
        late = Menu.objects.create(title="Late", auto=True)
        self.weekly(late, "on", "4", 22)  # Friday 10 PM ...
        self.weekly(late, "off", "5", 2)  # ... to Saturday 2 AM
        self.assertTrue(lunch.is_live(self.at(2, 12)))
        self.assertFalse(lunch.is_live(self.at(2, 22)))
        self.assertTrue(late.is_live(self.at(5, 1)))
        self.assertFalse(late.is_live(self.at(5, 3)))
        self.assertIn("Turns off today at 9:00 PM", Schedule().next_change(lunch, self.at(2, 12)))

    def test_date_ranges_and_another_menu_starting(self):
        breakfast = Menu.objects.create(title="Breakfast", auto=True)
        self.weekly(breakfast, "on", "0123456", 7)
        holiday = Menu.objects.create(title="Holiday", auto=True)
        MenuRule.objects.create(menu=holiday, kind="on", type=MenuRule.DATES, start_date=datetime.date(2026, 10, 21),
                                end_date=datetime.date(2026, 10, 22))
        MenuRule.objects.create(menu=breakfast, kind="off", type=MenuRule.MENU, other_menu=holiday)
        brunch = Menu.objects.create(title="Brunch", auto=True)
        self.weekly(brunch, "on", "56", 9)
        self.weekly(brunch, "off", "56", 14)
        MenuRule.objects.create(menu=brunch, kind="off", type=MenuRule.DATES, start_date=datetime.date(2026, 10, 24),
                                end_date=datetime.date(2026, 10, 24))
        self.assertTrue(holiday.is_live(self.at(2, 15)))  # Wednesday, in its dates
        self.assertFalse(holiday.is_live(self.at(4, 15)))
        self.assertFalse(brunch.is_live(self.at(5, 10)))  # kept off on Saturday the 24th
        self.assertTrue(brunch.is_live(self.at(6, 10)))
        # Turned off when another menu's weekly "on" fires (lunch at 11 here)
        lunch = Menu.objects.create(title="Lunch", auto=True)
        self.weekly(lunch, "on", "0123456", 11)
        MenuRule.objects.create(menu=breakfast, kind="off", type=MenuRule.MENU, other_menu=lunch)
        self.assertTrue(breakfast.is_live(self.at(1, 10)))
        self.assertFalse(breakfast.is_live(self.at(1, 12)))

    def test_the_register_rings_up_from_a_picked_live_menu(self):
        first = Menu.objects.create(title="First", manual_on=True, position=0)
        second = Menu.objects.create(title="Second", manual_on=True, position=1)
        Menu.objects.create(title="Off", position=2)
        request = mock.Mock(session={})
        from .register import register_menu
        self.assertEqual(register_menu(request), first)
        request.session["register_menu"] = second.id
        self.assertEqual(register_menu(request), second)
        second.manual_on = False
        second.save()
        self.assertEqual(register_menu(request), first)


@override_settings(ENFORCE_DEVICE_CHECK=False)
class KitchenDisplayTests(MenuFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_menu()
        cls.staff = User.objects.create_user("cook", password="x", is_staff=True)

    def setUp(self):
        self.client.force_login(self.staff)
        self.url = reverse("order_marking")

    def online_order(self, channel="web", **fields):
        return create_order(lines=[self.dish(self.burger), self.dish(self.tea)], channel=channel,
                            initial_status=OrderStation.STATUS_PENDING, **fields)

    def send(self, order, action, stations=(), **extra):
        body = {"orderId": order.id, "action": action, "filters": list(stations), **extra}
        return self.client.post(self.url, json.dumps(body), content_type="application/json")

    def statuses(self, order):
        return Order.objects.get(pk=order.pk).station_status_map()

    def test_screen_lists_active_orders_with_their_customizations(self):
        create_order(lines=[self.lunch(), self.dish(self.burger, cust={"parts": {str(self.dc_onion.id): 0}})],
                     channel="store", initial_status=1, cashier=self.staff)
        response = self.client.get(self.url, {"filter": "kitchen"})
        self.assertEqual(response.status_code, 200)
        order = response.context["orders"][0]
        self.assertEqual(order["cashier"], "cook")
        burger = next(d for d in order["dishes"] if d["combo"])
        self.assertEqual((burger["name"], burger["combo"], burger["slot"], burger["station"]), ("Burger", "Lunch combo", "Main", "kitchen"))
        self.assertIn([{"text": "No onion", "kind": "no"}], [d["tags"] for d in order["dishes"]])

    def test_filters_come_from_the_query(self):
        def filters(query):
            return self.client.get(self.url + query).context["filters"]
        codes = list(Station.objects.values_list("code", flat=True))
        self.assertEqual(filters("?filter=bar&filter=kitchen"), ["kitchen", "bar"])
        self.assertEqual(filters("?filter=bar,nope"), ["bar"])
        self.assertEqual(filters(""), codes)

    def test_mod_tags_say_what_kind_of_change_each_mod_is(self):
        cust = {"parts": {str(self.dc_onion.id): 0, str(self.dc_patty.id): 2},
                "options": {self.temp_key: self.well.id, self.addons_key: {str(self.avocado_addon.id): 1}},
                "condiments": {str(self.ketchup_side.id): 2}, "allergies": ["Peanuts"]}
        create_order(lines=[self.dish(self.burger, cust=cust), self.lunch()],
                     channel="store", initial_status=1)
        dishes = self.client.get(self.url).context["orders"][0]["dishes"]
        self.assertEqual([(t["text"], t["kind"]) for t in dishes[0]["tags"]],
                         [("2× patty", "add"), ("No onion", "no"), ("+ Avocado", "add"), ("Well done", "opt"), ("2× Ketchup", "side")])
        self.assertEqual(dishes[0]["allergies"], ["Peanuts"])
        self.assertEqual(dishes[1]["combo_tags"], [])
        self.assertNotEqual(dishes[0]["line"], dishes[1]["line"])

    def test_confirm_ready_pickup(self):
        order = self.online_order()
        self.assertEqual(self.send(order, "confirm", ["kitchen"]).json()["all_confirmed"], False)
        self.assertEqual(self.statuses(order), {"kitchen": 1, "bar": 0})
        self.send(order, "confirm", ["bar"])
        self.assertEqual(self.send(order, "ready", ["kitchen"]).json()["all_ready"], False)
        self.assertEqual(self.send(order, "pickup", ["kitchen"]).status_code, 409)
        self.assertEqual(self.send(order, "ready", ["bar"]).json()["all_ready"], True)
        self.assertEqual(self.statuses(order), {"kitchen": 2, "bar": 2})
        self.assertFalse(Order.objects.get(pk=order.pk).picked_up)
        self.assertEqual(self.send(order, "pickup", ["bar"]).status_code, 200)
        self.assertTrue(Order.objects.get(pk=order.pk).picked_up)

    def test_stations_record_when_they_confirm_and_finish(self):
        order = self.online_order()
        feed = lambda: self.client.get(self.url).context["orders"][0]
        self.assertIsNone(feed()["in_progress_at"])
        with mock.patch("django.utils.timezone.now", return_value=order.timestamp + datetime.timedelta(minutes=2)):
            self.send(order, "confirm", ["kitchen"])
        with mock.patch("django.utils.timezone.now", return_value=order.timestamp + datetime.timedelta(minutes=5)):
            self.send(order, "confirm", ["bar"])
        # In progress from the last confirmation
        self.assertEqual(feed()["in_progress_at"], (order.timestamp + datetime.timedelta(minutes=5)).isoformat())
        self.assertEqual(feed()["ready_at"], {})
        with mock.patch("django.utils.timezone.now", return_value=order.timestamp + datetime.timedelta(minutes=9)):
            self.send(order, "ready", ["kitchen"])
        self.assertEqual(feed()["ready_at"], {"kitchen": (order.timestamp + datetime.timedelta(minutes=9)).isoformat()})

    def test_register_orders_are_in_progress_from_the_start(self):
        order = create_order(lines=[self.dish(self.burger)], channel="store", initial_status=OrderStation.STATUS_APPROVED)
        self.assertEqual(order.in_progress_since(), order.timestamp)
        later = order.timestamp + datetime.timedelta(minutes=30)
        Order.objects.filter(pk=order.pk).update(start_time=later)  # scheduled: counts from the start time
        self.assertEqual(Order.objects.get(pk=order.pk).in_progress_since(), later)

    def test_ready_waits_for_every_station_to_confirm(self):
        order = self.online_order()
        self.send(order, "confirm", ["kitchen"])
        self.assertEqual(self.send(order, "ready", ["kitchen"]).status_code, 409)
        self.assertEqual(self.statuses(order)["kitchen"], 1)

    def test_couriers_pick_up_delivery_orders(self):
        order = self.online_order(channel="delivery")
        self.send(order, "confirm", ["kitchen", "bar"])
        self.send(order, "ready", ["kitchen", "bar"])
        self.assertEqual(self.send(order, "pickup", ["kitchen"]).status_code, 409)
        self.assertFalse(Order.objects.get(pk=order.pk).picked_up)

    def test_unknown_action_and_method(self):
        order = self.online_order()
        self.assertEqual(self.send(order, "explode").status_code, 400)
        self.assertEqual(self.client.put(self.url, "{}", content_type="application/json").status_code, 405)

    @mock.patch("payments.square.capture_authorization")
    def test_payment_captured_once_every_station_confirms(self, capture):
        transaction = Transaction.objects.create(amount=Decimal("16.50"), successful=True)
        auth = PaymentAuthorization.objects.create(payment_id="p1", amount=1650, currency="CAD", transaction=transaction)
        order = self.online_order(authorization=auth)
        self.send(order, "confirm", ["kitchen"])
        capture.assert_not_called()
        self.send(order, "confirm", ["bar"])
        capture.assert_called_once_with(auth)

    @mock.patch("payments.square.void_authorization")
    def test_reject_refunds_and_deletes(self, void):
        card = GiftCard.objects.create(available_balance=Decimal("20.00"))
        transaction = Transaction.objects.create(amount=Decimal("5.00"), successful=True)
        auth = PaymentAuthorization.objects.create(payment_id="p2", amount=500, currency="CAD", transaction=transaction)
        order = self.online_order(authorization=auth, gift_card_payments=[(card.number, "6.50")])
        response = self.send(order, "reject", ["kitchen"], reasons=["stock", "capacity"], text="  No buns left ")
        self.assertEqual(response.status_code, 200)
        void.assert_called_once_with(auth)
        self.assertFalse(Order.objects.filter(pk=order.pk).exists())
        self.assertEqual(RejectedOrder.objects.get(order_id=order.id).reason,
                         "Item out of stock; Kitchen at capacity: No buns left")
        card.refresh_from_db()
        self.assertEqual(card.available_balance, Decimal("20.00"))

    def test_reject_needs_a_known_reason(self):
        order = self.online_order()
        self.assertEqual(self.send(order, "reject", reasons=[], text=" ").status_code, 400)
        self.assertEqual(self.send(order, "reject", reasons=["bored"]).status_code, 400)
        self.assertEqual(self.send(order, "reject", text="Fryer is down").status_code, 200)
        self.assertEqual(RejectedOrder.objects.get(order_id=order.id).reason, "Fryer is down")

    @mock.patch("payments.square.void_authorization", side_effect=Exception("Square down"))
    def test_reject_keeps_order_if_refund_fails(self, void):
        transaction = Transaction.objects.create(amount=Decimal("16.50"), successful=True)
        auth = PaymentAuthorization.objects.create(payment_id="p3", amount=1650, currency="CAD", transaction=transaction)
        order = self.online_order(authorization=auth)
        response = self.send(order, "reject", ["kitchen"], reasons=["closing"])
        self.assertEqual(response.status_code, 502)
        self.assertTrue(Order.objects.filter(pk=order.pk).exists())


@override_settings(ENFORCE_DEVICE_CHECK=False)
class RegisterTests(MenuFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_menu()
        cls.staff = User.objects.create_user("cashier", password="x", is_staff=True, first_name="Dana")
        cls.offer = Discount.objects.create(name="Student", kind="pct", value=Decimal("10"))

    def setUp(self):
        self.client.force_login(self.staff)

    def post(self, name, body):
        return self.client.post(reverse(name), json.dumps(body), content_type="application/json")

    def test_page_and_catalog(self):
        response = self.client.get(reverse("pos"))
        self.assertEqual(response.status_code, 200)
        catalog = self.client.get(reverse("register_catalog")).json()
        self.assertEqual({i["title"] for i in catalog["items"]}, {"Burger", "Coffee", "Tea", "Lunch combo"})
        self.assertEqual(next(i for i in catalog["items"] if i["type"] == "combo")["ask"], "Choose a drink")
        kit = catalog["dishes"][str(self.burger.id)]["kit"]
        self.assertEqual([p["title"] for p in kit["parts"]], ["Bun", "Patty", "Onion"])
        self.assertEqual({g["title"] for g in kit["groups"]}, {"Temperature", "Bun", "Add-ons"})
        parts = {p["title"]: (p["standard"], p["max"]) for p in kit["parts"]}
        self.assertEqual(parts["Patty"], (1, 11))  # up to ten more
        self.assertEqual(next(g for g in kit["groups"] if g["title"] == "Add-ons")["pick"], "any")

    def test_quote(self):
        data = self.post("register_quote", {"lines": [self.dish(self.burger, discount={"offer": self.offer.id}), self.lunch()],
                                            "orderDiscount": {"code": "NOPE"}}).json()
        self.assertEqual((data["subtotal"], data["discount"], data["total"]), (3050, 145, 2905))
        self.assertEqual(data["orderDiscountError"], "That code isn't valid.")
        self.assertEqual(self.post("register_quote", {"lines": [{"type": "dish", "id": 0}]}).status_code, 400)

    def test_cash_sale_needs_an_open_drawer(self):
        body = {"lines": [self.dish(self.burger)], "payments": [{"method": "cash", "amount": "14.50", "tendered": "20.00"}],
                "total": "14.50", "name": "Taras", "toGo": True}
        self.assertEqual(self.post("pos", body).status_code, 400)
        self.post("cash_drawer", {"action": "open", "amount": "200.00"})
        response = self.post("pos", body)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["change"], 550)
        order = Order.objects.get()
        self.assertEqual((order.cashier, order.name, order.to_go_order), (self.staff, "Taras", True))

    def test_split_payment_with_a_gift_card(self):
        card = GiftCard.objects.create(available_balance=Decimal("5.00"))
        response = self.post("pos", {"lines": [self.dish(self.burger)], "total": "14.50", "payments": [
            {"method": "gift", "number": card.number, "amount": "5.00"}, {"method": "card", "amount": "9.50"}]})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(sorted(Order.objects.get().payments.values_list("method", flat=True)), ["card", "gift"])

    def test_manual_discount_needs_the_permission(self):
        body = {"lines": [self.dish(self.tea)], "orderDiscount": {"manual": {"kind": "amt", "value": "1.00"}},
                "total": "1.00", "payments": [{"method": "card", "amount": "1.00"}]}
        self.assertEqual(self.post("pos", body).status_code, 400)
        self.staff.user_permissions.add(Permission.objects.get(codename="apply_manual_discount"))
        self.assertEqual(self.post("pos", body).status_code, 200)

    @mock.patch("payments.square.gather_terminal_checkout")
    def test_card_checkout_checks_the_cart_first(self, checkout):
        checkout.return_value = mock.Mock(is_success=lambda: True, body={"checkout": {"id": "c1", "status": "PENDING"}})
        body = {"lines": [self.dish(self.burger)], "total": "14.50", "amount": "14.50", "payments": []}
        self.assertEqual(self.post("register_card", {**body, "total": "9.00"}).status_code, 400)
        self.assertEqual(self.post("register_card", {**body, "amount": "20.00"}).status_code, 400)
        checkout.assert_not_called()
        self.assertEqual(self.post("register_card", body).json()["checkoutId"], "c1")

    def test_drawer_open_move_close(self):
        self.assertEqual(self.client.get(reverse("cash_drawer")).status_code, 200)
        self.post("cash_drawer", {"action": "open", "amount": "200.00"})
        self.assertEqual(self.post("cash_drawer", {"action": "open", "amount": "1"}).status_code, 409)
        self.post("pos", {"lines": [self.dish(self.burger)], "total": "14.50", "payments": [{"method": "cash", "amount": "14.50"}]})
        self.post("cash_drawer", {"action": "move", "amount": "-10.00", "reason": "Ice"})
        closed = self.post("cash_drawer", {"action": "close", "counted": "200.00"}).json()["closed"]
        self.assertEqual((closed["cashSales"], closed["paidOut"], closed["expected"]), (1450, 1000, 20450))
        drawer = CashDrawerSession.objects.get()
        self.assertEqual(drawer.difference, Decimal("-4.50"))
        self.assertFalse(drawer.is_open)


@override_settings(ENFORCE_DEVICE_CHECK=False)
class LockScreenTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("dana", password="x", is_staff=True, first_name="Dana")
        self.staff = Staff.objects.create(user=self.user)
        self.staff.set_pin("1234")
        self.staff.save()
        RegisterSignIn.record(None, self.user)  # Dana used this register before

    def unlock(self, pin):
        return self.client.post(reverse("register_lock"), json.dumps({"staff": self.staff.id, "pin": pin}),
                                content_type="application/json")

    def test_pin_signs_in_and_lock_signs_out(self):
        page = self.client.get(reverse("register_lock"))
        self.assertEqual(page.context["staff"], [{"id": self.staff.id, "name": "Dana"}])
        self.assertNotEqual(self.staff.pin, "1234")  # stored hashed
        self.assertEqual(self.unlock("1111").status_code, 403)
        response = self.unlock("1234")
        self.assertEqual(response.json()["name"], "Dana")
        self.assertEqual(self.client.get(reverse("pos")).status_code, 200)
        self.client.post(reverse("register_lock"), {"action": "lock"})
        self.assertRedirects(self.client.get(reverse("pos")), f'{reverse("register_lock")}?next=%2Fen%2Frestaurant%2Fregister',
                             fetch_redirect_response=False)

    def test_pins_are_quick_to_check_and_old_hashes_upgrade(self):
        import time
        from django.contrib.auth.hashers import make_password
        self.assertTrue(self.staff.pin.startswith("pin_pbkdf2_sha256$"))
        started = time.perf_counter()
        self.assertTrue(self.staff.check_pin("1234"))
        self.assertLess(time.perf_counter() - started, 0.2)
        # A PIN stored the old way (a full password hash) still works and is re-hashed the fast way
        self.staff.pin = make_password("4321")
        self.staff.save()
        self.assertFalse(self.staff.check_pin("1111"))
        self.assertTrue(self.staff.check_pin("4321"))
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.pin.startswith("pin_pbkdf2_sha256$"))
        self.assertTrue(self.staff.check_pin("4321"))

    def make_staff(self, name, pin="1111"):
        user = User.objects.create_user(name.lower(), password="x", is_staff=True, first_name=name)
        staff = Staff.objects.create(user=user)
        if pin:
            staff.set_pin(pin)
            staff.save()
        return staff

    def test_lists_the_last_three_users_with_a_pin(self):
        for name in ("Ana", "Ben", "Cal", "Dee"):
            RegisterSignIn.record(None, self.make_staff(name).user)
        RegisterSignIn.record(None, self.make_staff("Nopin", pin="").user)
        names = [s["name"] for s in self.client.get(reverse("register_lock")).context["staff"]]
        self.assertEqual(names, ["Dee", "Cal", "Ben"])  # most recent first; Dana and Ana fell off

    def test_only_listed_staff_can_use_a_pin(self):
        other = self.make_staff("Eve", pin="5555")  # has a PIN, never used this register
        response = self.client.post(reverse("register_lock"), json.dumps({"staff": other.id, "pin": "5555"}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 403)

    def test_opening_the_register_lists_you(self):
        eve = self.make_staff("Eve", pin="5555")
        self.client.force_login(eve.user)
        self.client.get(reverse("pos"))
        self.client.logout()
        self.assertEqual(self.client.get(reverse("register_lock")).context["staff"][0]["name"], "Eve")

    def test_empty_list_points_to_the_password_sign_in(self):
        RegisterSignIn.objects.all().delete()
        page = self.client.get(reverse("register_lock"))
        self.assertEqual(page.context["staff"], [])
        self.assertContains(page, f'{reverse("login_view")}?next=%2Fen%2Frestaurant%2Fregister')

    def test_wrong_pins_lock_the_person_out(self):
        for _ in range(5):
            self.unlock("0000")
        self.assertEqual(self.unlock("1234").status_code, 429)

    def test_customers_cannot_use_a_pin(self):
        self.user.is_staff = False
        self.user.save()
        self.assertEqual(self.unlock("1234").status_code, 403)


class ActiveOrdersFeedTests(MenuFixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build_menu()

    def test_anonymous_feed_is_redacted(self):
        create_order(lines=[self.dish(self.tea)], channel="web", initial_status=0, phone=6045550100, name="Iryna")
        data = self.client.get(reverse("active_orders")).json()
        self.assertEqual(data[0]["name"], "Iryna")
        self.assertNotIn("phone", data[0])
        self.assertNotIn("payment_id", data[0])
        self.assertNotIn("lines", data[0])
        self.assertEqual(data[0]["stations"], {"bar": 0})
        self.assertEqual(data[0]["dishes"], [{"name": "Tea", "quantity": 1}])


@override_settings(ENFORCE_DEVICE_CHECK=False)
class AccessTests(MenuFixture, TestCase):
    """Customers can register accounts, so staff pages must check is_staff, not just login."""

    @classmethod
    def setUpTestData(cls):
        cls.build_menu()
        cls.customer = User.objects.create_user("customer", password="x")

    def test_customer_cannot_reach_staff_pages(self):
        self.client.force_login(self.customer)
        for name, kwargs in [("pos", {}), ("cash_drawer", {}), ("order_marking", {}), ("pos_output", {}), ("create_menu", {}),
                             ("component_availability", {}), ("crafting", {}), ("utils_get_image_colors", {}),
                             ("admin_home", {})]:
            response = self.client.get(reverse(name, kwargs=kwargs))
            self.assertIn(response.status_code, (302, 403), name)

    def test_customer_cannot_use_staff_apis(self):
        self.client.force_login(self.customer)
        for name in ("register_catalog",):
            self.assertEqual(self.client.get(reverse(name), HTTP_ACCEPT="application/json").status_code, 401, name)
        for name in ("register_quote", "register_card", "pos"):
            response = self.client.post(reverse(name), "{}", content_type="application/json", HTTP_ACCEPT="application/json")
            self.assertEqual(response.status_code, 401, name)


class SeedDemoTests(TestCase):
    """seed_demo must keep working on a fresh database (CLAUDE.md: update it with every model change)."""

    def test_seed_demo_runs(self):
        from django.core.management import call_command
        from io import StringIO
        with self.settings(MEDIA_ROOT=__import__("tempfile").mkdtemp()):
            call_command("seed_demo", allow_production=True, history_days=6, stdout=StringIO())
        self.assertTrue(Order.objects.filter(channel="delivery").exists())
        self.assertEqual(Menu.active().title, "Lunch")
        self.assertTrue(Order.objects.filter(items__children__isnull=False).exists())  # a combo was sold
        self.assertTrue(Staff.objects.get(user__username="cashier").check_pin("1234"))


class RealtimeTests(MenuFixture, TestCase):
    """Screens don't poll: whatever changes the register's catalog must send an "inventory" event."""

    @classmethod
    def setUpTestData(cls):
        cls.build_menu()

    def published(self, change):
        from django.db import connection
        # The fixture's saves queued events in the test's outer transaction that never commit; publish() sends one
        # event per topic per transaction, so drop them to see what `change` sends on its own.
        connection.run_on_commit = []
        with mock.patch("pavilion.realtime.get_broker") as broker, self.captureOnCommitCallbacks(execute=True):
            change()
        return [call.args[0] for call in broker.return_value.publish.call_args_list]

    def test_running_out_sends_an_event(self):
        def run_out():
            self.gf_bun.inventory = 0
            self.gf_bun.save()
        self.assertEqual(self.published(run_out), ["inventory"])

    def test_menu_edits_send_an_event(self):
        for change in (lambda: Option.objects.create(group=self.medium.group, title="Rare"),
                       lambda: self.avocado_addon.delete(),
                       lambda: ComboSlotOption.objects.filter(dish=self.tea).delete()):
            self.assertIn("inventory", self.published(change))
