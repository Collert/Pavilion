# Pavilion — Full Project Overview

> First written 2026-10-04 from a full read-through of the code, git history, branches, stashes, and the local dev DB.
> Rewritten 2026-10-13 after the overhaul on branch `fix-all-problems-for-real` (dynamic stations, server-side pricing,
> payment fixes, SSE, Docker, tests). This file is about what the code **actually does**. Where the code and older docs
> ([README.md](../README.md)) disagree, trust this file and the code.

---

## 1. What this is

**Pavilion** is a Django restaurant-management system for a small community café. It was originally built for the
café at **Holy Eucharist Ukrainian Catholic Cathedral** in New Westminster, BC. The project was called `heucc_pos`,
was renamed to `pavilion` on 2025-02-06 (commit `cd409c5`), and has Ukrainian and Cantonese translations.
`RESTAURANT_ADDRESS = "501 4th Ave, New Westminster, BC"` is hard-coded in `deliveries/views.py`, and SendGrid emails
go out as "Holy Eucharist Cathedral".

| Area | What it is | Status |
| --- | --- | --- |
| **POS register** | Touch-screen cashier UI, cash/card/gift-card tender, Square Terminal | ✅ Works |
| **Kitchen display (KDS)** | "Order marking" screen per set of stations: confirm, mark ready, hand out, reject; keyboard only | ✅ Works, live (SSE) |
| **Customer-facing display** | Second screen mirroring the cart, linked to the POS by `BroadcastChannel` | ✅ Works (same browser only) |
| **Order status board** | TV screen with in-progress/ready columns, TTS announcements, weather, YouTube player | ✅ Works, live (SSE) |
| **Online store** | Customer PWA: menu, cart, pickup/delivery ordering, Square web payments, order history | ✅ Works (Square popup not re-tested against sandbox) |
| **Events / business days** | Online ordering only during an `Event` with `restaurant_open` + `online_open`/`delivery_open` | ✅ Enforced on the server |
| **Deliveries** | Courier app: clock in, claim delivery, ETA via Google Distance Matrix, web push | ✅ Works (push untested end-to-end) |
| **Gift cards** | Buy online (Square), email via SendGrid, barcode card page, scan/charge at POS | ✅ Works |
| **Inventory** | Ingredients → Components → Dishes, Cookery (recipes, guided cooking, transforms), shopping log, Availability screen (Off / Auto / Always) | ✅ Works, Cookery and availability live (SSE) |
| **New admin** | Custom superuser dashboard: orders, receipts, menus, promo content, events, calendar | ⚠️ Partial (`dashboard_home`, `store_branding` are stubs) |
| **Users** | Login/register/profile, `Staff`/`Customer` profiles | ✅ Basic |
| **Misc tools** | K-means image-colour extractor (menu theming), gTTS announcement endpoint | ✅ Works |
| **App switcher** | Staff launcher: device overview (clock, who's signed in, today's stats for this device) + app tiles | ✅ Done |
| **Stations** | `Station` model; any number of stations, configured in `/advanced-admin/` | ✅ Done |

---

## 2. Timeline (from git)

| When | What happened |
| --- | --- |
| 2023-12 → 2024-03 | Initial build: POS, kitchen/bar screens, Square terminal, dashboard, inventory. ~115 commits. |
| 2024-05 → 2024-06 | Deployment onto own hardware. **SSE broke in production → switched to polling** (`8502037`, `4e447ab`, `7170e2c`). |
| 2024-07 → 2024-08 | Online store + online payment processing, gift cards (PR #11), date/time constraints for online ordering. |
| 2024-10 → 2024-11 | Thread-safety attempt on active orders, gift card scanner. Started a `screens` app (branch `screens`, abandoned). |
| 2025-02 (79 commits) | Whitenoise, rename to Pavilion, mostly-AI docstrings, new_admin rebuild, translations (PR #25), events, promo content, Station model added/disabled/re-enabled. |
| 2025-03 → 2025-04 | Menu management in new_admin, dashboard history calendar, Square checkout caching. Menu fonts/styles WIP left in **stash@{0}**. |
| 2025-07 | Docker/Postgres setup, Copilot instructions file, `classes.py` edit (introduced the gift-card email crash). |
| 2025-11-26 | Three **Copilot agent branches** pushed to origin (unmerged): WebSockets, dynamic stations, N+1 query fixes. |
| 2026-10-13 | **Overhaul** on `fix-all-problems-for-real`: security fixes, DB reset with committed migrations, Station model, shared order service, payment fixes, SSE over Redis, Docker with uvicorn, tests, `seed_demo`. Then Django 5.1 → 6.1. |

---

## 3. Tech stack

- **Django 6.1**, Python 3.12+ (Docker and local). Upgraded from 5.1 on 2026-10-13 with no deprecated APIs in use:
  the test suite runs with every `RemovedInDjango…Warning` turned into an error.
- Plain Django templates plus vanilla JS ES modules. No frontend build step and no npm.
- **Server:** ASGI. `manage.py runserver` runs daphne (it's first in `INSTALLED_APPS`); Docker runs uvicorn with several workers.
- **DB:** SQLite in dev (`db.sqlite3`), PostgreSQL 16 when `ENVIRONMENT=production`.
- **Redis** (`REDIS_URL`, required in production): Django cache + pub/sub for live updates. Without it (dev) the cache is
  `LocMemCache` and live updates use an in-process broker.
- **Static:** whitenoise, collected to `staticfiles/`. Shared CSS kit `static/css/AD-Elements/`; shared JS in `static/js/`.
- **Media:** `./media` served by Django in dev; a RustFS (S3-compatible) bucket via `django-storages` in production.
- **Integrations:** Square (Terminal API, Web Payments SDK, webhooks) on `squareup==38.x` (later versions replaced
  `square.client.Client`), SendGrid dynamic templates, Google Distance Matrix, OpenWeather, Web Push (VAPID), gTTS +
  ffmpeg, OpenCV + scikit-learn, python-barcode, QuaggaJS, FullCalendar.
- **i18n:** `i18n_patterns`, so page URLs carry `/en/`, `/uk/` or `/zh-hk/`. `LANGUAGE_CODE` must be one of `LANGUAGES`
  (`en`), or `reverse()` outside a request builds 404ing `/en-us/` URLs. JS strings use `gettext()` via `/jsi18n/`.

---

## 4. URL map

All URLs below are under the language prefix (`/en/`) except `/jsi18n/` and `/events/<topic>`.

| Prefix | App | Key routes |
| --- | --- | --- |
| `/` | `online_store` | `""` home, `menu`, `dish/<id>`, `submit-order`, `order-status/<id>[/<from_placing>]`, `order-history`, `service-worker.js` |
| `/restaurant/` | `pos_server` | `register` (**the POS**), `order-marking` (KDS), `pos-output` (customer display), `orders-progress` (status board), `active_orders` (JSON), `check_inventory` (JSON), `check-device`, `component-choice`, `menu_select`, `create-menu`, `dashboard` (→ admin history), `day_stats`, `pair-terminal`, `webhook/square`, `webhook/square/check_card_status`, `check-su` |
| `/admin/` | `new_admin` | `dashboard/home`, `dashboard/history[/api]`, `menus/list`, `menus/<id>`, `orders/retail[/<id>[/receipt]]`, `store/branding`, `store/promo-content[/<id>\|/new]`, `store/events[/<id>\|/new]` |
| `/advanced-admin/` | Django admin | Dishes, components, stations, devices, everything else |
| `/inventory/` | `inventory` | `log-shopping`, `shopping-history`, `day-display/<id>`, `crafting` (Cookery), `crafting/data` (its JSON + POST actions), `component-availability` (page), `availability/data` (its JSON + POST changes) |
| `/deliveries/` | `deliveries` | `""` ready orders, `profile` (clock in/out), `eta`, `order/<id>`, `vapid-public-key/`, `save-subscription/` |
| `/gift-cards/` | `gift_cards` | `card/<number>`, `get-card`, `special`, `card-confirm`, `card-api/<number>`, `email_card-confirm` (preview) |
| `/payments/` | `payments` | `web-payment-window` (iframe), `process-web-payment` |
| `/users/` | `users` | `login` (= `LOGIN_URL`), `logout`, `register`, `profile` |
| `/screens/` | `screens` | `""` list of screens + widget catalogue (staff), `<slug>` a screen (public or staff, per screen) |
| `/apps/` | `app_switcher` | staff launcher (see §6.2) |
| `/utils/` | `misc_tools` | `get-image-colors/` (staff), `tts?text=` (public, 300 chars max) |
| `/events/<topic>` | `pavilion.realtime` | SSE streams: `orders` and `screens` (public), `inventory` and `cookery` (staff), `checkout` (staff + registered device) |

**Access rules.** Customers can register accounts, so staff pages use `staff_required` / `superuser_required` /
`api_staff_required` (`pos_server/devices.py`), never bare `login_required`. POS/KDS views also use `device_required`.

---

## 5. Domain model

### 5.1 Menu → Dish / Combo → Component → Ingredient (`pos_server/models.py`)

Redesigned 2026-10-13 (after the overhaul) for the register mockups in `docs/mockups/`.

```
Menu (is_active: saving an active menu deactivates the others; Menu.active() returns it or None)
 ├─ Dish (M2M menu; price Decimal; station FK; in_stock / availability / blocked / visible_in_menu)
 │   ├─ DishComponent (quantity per portion; adjustable; max_portions) ─ Component (inventory; extra_price; …)
 │   │                                                                     └─ ComponentIngredient ─ Ingredient
 │   ├─ OptionGroup (dish-owned, e.g. Size, Dressing) ─ Option (price change; extra component + quantity; multiplier)
 │   └─ Extra (kind addon | condiment; component; price; max_count)
 └─ Combo (M2M menu; price) ─ ComboSlot (Main/Side/Drink; ask_on_add; allow_none + none_price)
         │                       └─ ComboSlotOption (dish; price change; is_default)
         └─ Extra (combo condiments/add-ons)
Component ─ OptionGroup (component-owned, e.g. Patty temperature, Milk) ─ Option (component = swap it for this one)
```

- **A dish's customization sheet** = its adjustable parts (0 = "No onion", 2 = double; extra portions cost the
  component's `extra_price`), its own option groups, the option groups of each of its components (hidden while that
  part is left out), add-ons, condiments, allergy flags (the `Ingredient.allergens` list) and a note.
- **Variants** (small/regular/large) are a dish option group whose options change the price and `multiplier`.
- **Combos** cost `combo.price + each pick's price change + each pick's customizations + combo extras`. A combo is
  listed in the menu section of its first slot's standard dish. Every picked dish is made at its own station.
- **Stock:** `Component.save()` recalculates `in_stock` and updates the dishes that use it; adding or removing a
  `DishComponent` re-checks its dish.
- **Availability** (`availability` on Dish and Component, `AVAILABILITY` in `pos_server/models.py`): **Off** stops
  selling now, **Auto** follows inventory, **Always** keeps selling past zero (inventory goes negative). A component
  that's Off makes its dishes `blocked` (unavailable even when they're set to Always). `Dish.available` combines it all;
  `Option`/`Extra` availability follows their component's `in_stock`. The **Availability screen**
  (`/inventory/component-availability`, design `docs/mockups/Availability-html`) is a partial,
  `inventory/availability_panel.html` + `inventory/static/inventory/availability.js` (`mountAvailability`), fed by
  `inventory/availability.py` through `availability/data`; it is also a popup on the KDS. Tabs: dishes (active menu
  and its combos, by station), components & add-ons, condiments (components only used as condiments). Keys: arrows
  move, Enter cycles Off → Auto → Always, Tab switches tabs, typing searches, Ctrl+Z undoes, Esc clears the search
  and then closes the popup. Sold dishes take what was actually picked (portions × multipliers, swapped
  components, add-ons, condiments). `craft_component` and `auto` components work as before.
- Choice components (`Component.child_dishes`) and "only-choice" dishes were removed: use combos or option groups.

### 5.2 Stations and orders

- `Station(name, code, icon, menu_heading, position)`. `code` is a unique slug used in KDS filters (`?filter=kitchen`).
  `menu_heading` titles the menu section for that station's dishes ("Hot food"). A data migration creates
  Kitchen / Bar / Grab & Go. Edit or add stations in `/advanced-admin/`.
- `Order` fields: `channel` (`store` POS, `web` online pickup, `delivery`), `start_time` (scheduled time; screens hide
  orders until it passes), `prep_time` (set when the last station finishes), `name`, `phone`, `special_instructions`
  (+ `note_for_kitchen`), `to_go_order`, `picked_up`, `subtotal`/`discount`/`tax`/`total` (server-computed),
  `cashier`, `authorization` (Square payment), `gift_cards` (through `GiftCardAuthorization`).
- `OrderItem`: one per cart line (dish or combo; `unit_price`, `quantity`, line `discount`, `mods` as readable lines,
  `note`, `selection` JSON). A combo line has a child row per filled slot; rows with a dish carry the station.
- `OrderDiscount` (line or whole order; label, kind, value, amount), `OrderPayment` (cash/card/gift; `tendered` for
  cash; linked to the register's `CashDrawerSession`).
- `OrderStation(order, station, status)`, one row per station that has work on the order. **Status codes:** `0` pending
  approval · `1` being made · `2` done · `3` rejected. A station with no row isn't involved.
  POS orders start at 1; online/delivery orders start at 0 and must be approved on the KDS. Each row records
  `confirmed_at` and `ready_at`; `Order.in_progress_since()` is the last confirmation (or the scheduled start).
- `picked_up` defaults True and is set False when any station is involved; "done" means picked up.
- `Order.set_station_status(codes, status, only_from=...)` is the only way statuses change. It saves the order (which
  publishes an SSE `orders` event) and sends `pos_server.signals.order_ready` when the last station finishes.
- `Order.progress_status()` (customer-facing, web/delivery only): 1 scheduled → 2 not started → 3 in progress → 4 ready → 5 picked up → 6 delivered.
- Rejecting an order **deletes** it and stores an `online_store.RejectedOrder` with the reason.

### 5.3 Other models

- `EligibleDevice(token, name)`: registered POS/KDS devices (§6.8).
- `payments.Transaction(uuid, amount, successful, used)`: one online card payment. Only the server marks it `successful`;
  `Transaction.consume()` spends it once. `PaymentAuthorization(payment_id, amount cents, currency, status)`: `""`
  authorized, `cap` captured, `del` voided/refunded. `SquareTerminal`: paired Terminal devices.
- `gift_cards.GiftCard(number 16 random digits, available_balance, email, image)`, `GiftCardAuthorization` (charge per
  order, refunded on rejection), `CardPreset`, `SpecialCard(CardPreset)`.
- `online_store.PromoContent` (home-page banners), `online_store.RejectedOrder(order_id, reason)`.
- `events.Event(start, end, restaurant_open, in_person_open, online_open, delivery_open, location…)`, `EventTemplate` (unused).
- `deliveries.Delivery(order, destination, courier, eta, completed, phone)`, `CourierShift(user, mode)` (who is clocked
  in), `PushSubscription` (web push, `deliveries/notifications.py`).
- `inventory`: `StockUpdate` + `UpdateIngredient` (shopping trips with a receipt upload), `Recipe` (`yield_quantity`,
  `notes`) with `RecipeIngredient` (per batch) and `RecipeStep` (title, text, optional timer, scene, ingredients it uses),
  `Transformation`, `CookingSession` + `YieldEntry` (§6.10), `Packaging`,
  `CondimentUse`, `IngredientUsageBuffer` (TODO), and `Waste*` models (no UI).
- `users.Staff` (minutes_worked, allowance) and `users.Customer` (auto-created for every new User by a post_save signal).

---

## 6. Core flows

### 6.1 Pricing and creating orders (`pos_server/pricing.py`, `pos_server/orders.py`)

Carts are lists of lines (`{"type": "dish"|"combo", "id", "qty", "cust", "slots", "discount"}`; the full format is
in the `pricing.py` docstring). `price_cart()` validates every customization, combo pick and stock, applies at most
one discount per line and one on the order (after line discounts), then tax (`TAX_RATE`, default 0). It is used by:

- the register's live quote (`register/quote`, lenient: a discount that stops applying is reported, not fatal),
- the online store's quote (`cart-quote`, coupons only),
- `create_order()` (strict), which also takes payments, counts coupon uses (`max_uses`, row-locked), records
  `OrderItem`/`OrderDiscount`/`OrderPayment`, takes stock and charges gift cards. Register payments must add up to the
  total exactly, cash needs an open drawer, and `expected_total` refuses the order if the price changed under the
  cashier. Any `OrderError` rolls everything back.

`Discount`: an *offer* (no code, listed on the register) or a *coupon* (code; `online=True` to accept it in the
store). Conditions: `starts_on`/`ends_on`, `weekdays` ("12345"), `start_time`/`end_time`, `min_subtotal`,
`min_items`, eligible `dishes`/`combos` (item discounts), `max_uses`. Typed-in ("manual") discounts need the
`pos_server.apply_manual_discount` permission (superusers have it; `seed_demo` gives it to Priya).

### 6.2 Register, lock screen and cash drawer (`pos_server/register.py`)

Templates `register.html`, `drawer.html`, `lock.html` on `staff_layout.html` (nav rail); CSS `staff.css` plus one
file per screen; JS `register.js`, `drawer.js`, `lock.js` with the shared `staff-ui.js`. Designs: `docs/mockups/`.
Every rail ends with `rail_foot.html`: Apps, Lock (register screens only), a divider and the clock (ticked by
`staff-page.js`, which every staff screen loads). The **app switcher** (`/apps/`, `app_switcher`, design
`docs/mockups/App switcher-html`) is staff-only and almost query-free (session, user, and the device's name): its
"Today on this device" numbers (sales and orders rung up, KDS tickets moved, time since the first screen) live in the
tab's `sessionStorage` (`device-stats.js`, reset daily), bumped by the register and the KDS.
In portrait the rail is a row along the bottom (`staff.css`) and each screen stacks its panels: the register's menu
above the order, the drawer's and lock screen's panels top to bottom, the KDS header above the list.

1. **Lock screen** (`/restaurant/lock`): lists the last 3 staff with a PIN who used this register
   (`RegisterSignIn`, updated whenever someone opens the register on the device). Pick your name, type your 4-digit
   PIN (`Staff.pin`, cheap salted hash; set in `/advanced-admin/` → Users → Staff); only listed people can use a PIN.
   Everyone else taps "Someone else? Sign in with a password", which signs them in (`users.login_view`: email or
   username, "stay signed in") and lands them on the register, so they're listed next time. 5 wrong PINs lock that
   person out for a minute on that device. PINs are register-only; other screens use the password sign-in.
2. **Register** (`/restaurant/register`): sections are stations; tap a dish to add it; a combo that asks ("Choose a
   drink") opens a picker. Tap or swipe left on a line for Discount / Customize; swipe right removes it. The cart is
   kept in `sessionStorage`, priced live by the quote endpoint and broadcast to the customer display
   (`BroadcastChannel('cart_channel')`). Order note with a "show on the kitchen ticket" switch. Stock changes arrive by
   SSE `inventory` (the register refetches `register/catalog`).
3. **Pay:** cash (quick amounts, change due; blocked until the drawer is open), card (`register/card` checks the whole
   cart first, then starts a Square Terminal checkout; the result arrives by webhook → SSE `checkout`), gift card
   (typed, keyboard-wedge scanner, or camera via QuaggaJS). "Split payment" takes part now with one method and the
   rest with another. The order is posted once everything is paid.
4. **Cash drawer** (`/restaurant/drawer`): open with a starting amount (keypad), see cash/card sales and paid in/out
   for the session, record paid in/out, close by counting (the expected amount stays hidden until then) and see the
   difference. One open `CashDrawerSession` per registered device (one shared session when the device check is off).
5. `pair-terminal` (superuser) pairs Square Terminals as before.

### 6.3 Kitchen display (`/restaurant/order-marking?filter=kitchen,bar`)

Template `order-marking.html` on `staff_layout.html`, JS `order-marking.js`, CSS `order-marking.css`. Design:
`docs/mockups/Order marking-html` (plus the keyboard and "ready for pickup" changes below). Cards are rendered by JS
from server data (`json_script`), with `textContent` only. The screen refreshes from `active_orders` on every SSE
`orders` event (no polling).

- `?filter=` picks the stations the screen works for: repeat it or comma-separate codes; unknown codes are dropped and
  no filter means every station. The filter sheet (rail button, `F`) changes it and rewrites the URL, so the screen can
  be bookmarked per station.
- Each order shows only the dishes for this screen's stations: combos grouped, mods coloured by kind (`tags`: left
  out / extra / option / condiment, worked out in `orders._mod_tags`), allergies as red flags plus an alert banner,
  the order note, where it came from, and every involved station's status.
- Flow, per station (`OrderStation`): **confirm** (0 → 1) → once every station has confirmed, the order is
  *in progress* → **ready** (1 → 2) → once every station is ready, the order is *ready for pickup* → any station marks
  it **picked up** (`picked_up = True`, the order leaves every screen). Delivery orders wait for the courier instead.
  A station that has confirmed or finished early sees "waiting on <stations>".
- **POST** `{orderId, action, filters}` with `action` = `confirm` (captures the Square payment once nobody is pending),
  `ready` (409 until every station confirmed), `pickup` (409 until every station is ready; never for delivery), or
  `reject` with `reasons` (codes from `views.REJECT_REASONS`) and/or `text` (≤ 200 chars). Reject **voids or refunds**
  the card payment (the order is kept if Square fails), refunds gift cards, records a `RejectedOrder` with the
  reason the customer sees, and deletes the order.
- **Auto** (rail button, `A`): `?auto-ready=<min>` marks this screen's part ready that long after the order went in
  progress (`in_progress_at`); `?auto-pickup=<min>` marks it picked up that long after this screen marked it ready
  (`ready_at`), and while another station is still on it, tries again every minute. Not for deliveries. The screen
  runs these (every 5 s, on the server's clock), so they only happen while a screen with them on is open. Cards show
  the countdown.
- `Enter` keeps the order open; a card only moves on when its order leaves the screen.
- Keyboard only (hints are shown as `<kbd>` labels): `↑`/`↓` open the previous/next order (scrolled into view),
  `PgUp`/`PgDn` scroll, `Enter` = the open order's next step (confirm / mark ready / picked up), `Esc` rejects an
  unconfirmed order, `F` filter, `A` auto, `S` the Availability popup (it takes the keyboard until closed). In the reject sheet `Ctrl`+`1…6` toggle quick reasons, typing goes into the reason box, `Enter`
  rejects, `Esc` cancels. In the filter sheet `1…9` toggle stations, `A` selects all, `Enter`/`Esc` close.
  Browsers may keep `Ctrl`+digit for switching tabs: run the KDS in kiosk/app mode, or use `Alt`+digit.

### 6.4 Customer display (`/restaurant/pos-output`) and old status board (`/restaurant/orders-progress`)

The old status board is superseded by the `order-board` screen (§6.11) and no longer on the app switcher; it still
works at its URL until it's removed.

- The customer display shows the active menu grouped by station heading, plus the live cart from the POS via
  `BroadcastChannel`, so **it must be a second window in the same browser profile on the same machine**.
- The status board is public. It reads the redacted `active_orders` feed (no phone, address, payment ID, notes or
  prices), diffs it into "In progress" / "Ready" (ready = every involved station done), and announces newly ready
  orders by TTS. Clock, weather (OpenWeather) and a YouTube player are on the same screen.

### 6.5 Online ordering (`/`)

`online_store/layout.html` holds most of the logic: cart, gift-card lookup, delivery ETA, service worker, push.

0. The menu lists dishes and combos; a combo that asks for a pick opens a picker. The cart
   (`online_store/static/online_store/cart.js`, kept in IndexedDB) is priced by `cart-quote`; checkout has a coupon
   field. Dishes are added as standard (no customization sheet online yet).
1. `check_business_day` (context processor) exposes today's open `Event` (lazily: only pages that use it query it); `place_order` re-checks it on the server,
   including `online_open`/`delivery_open` and that the pickup time is inside the event.
2. The checkout form (`attach-card-payment confirmation-needed`) opens `payments/web-payment-window?amount=` in an iframe.
   That creates a `Transaction` for the amount; `process-web-payment` charges **the transaction's amount** via Square
   (authorize-only) and only then marks it paid. The form is resubmitted with `transaction_uuid`.
3. `place_order` calls `create_order` with `initial_status=PENDING`. `verify_payment` requires a paid, unused transaction
   covering the remainder after gift cards and links its `PaymentAuthorization` to the order. Delivery orders get a
   `Delivery` (phone required). Errors render `order-error.html`.
4. The customer's order IDs are kept in `localStorage.orders` for `order-history`.

### 6.6 Deliveries (`/deliveries/`)

Staff-only courier PWA. A courier clocks in on `profile` (creates a `CourierShift`). Ready delivery orders (every
station done) show in the list; the courier claims one with `PUT order/<id>` plus an ETA (marks it picked up),
`DELETE` completes it. `get_eta` uses Google Distance Matrix per clocked-in transport mode. When a delivery order becomes
ready, `order_ready` → `deliveries.signals` sends a web push to clocked-in couriers.

### 6.7 Gift cards (`/gift-cards/`)

- `get-card`: pick a preset or personal design (images listed from storage under `files/gift-cards/{presets,personal}`),
  pay through the same popup. On POST the card is created **only from a paid, unused transaction, for the amount paid**,
  then emailed via SendGrid (an email failure is logged; the confirmation page still shows the card link).
- `card/<number>` renders a code128 barcode. `card-api/<number>`: GET is public (balance, image; email for staff only),
  POST charges and needs staff + CSRF.

### 6.8 Device registration

POS/KDS views use `device_required`: when `ENFORCE_DEVICE_CHECK` is on (default in production), the browser needs a
signed HttpOnly `pavilion_device` cookie for a registered `EligibleDevice`. Unregistered browsers are redirected to
`check-device`, where a superuser registers them; a browser that already has the token in `localStorage` re-sends it
automatically and gets the cookie back. `seed_demo` creates a demo device; open any POS page with
`?device-token=00000000-0000-4000-8000-000000000001` once to register a dev browser.

### 6.9 Admin

`new_admin` (`/admin/`, superuser): orders list + detail + printable receipt (restaurant name/address are placeholders),
menu list/edit/activate, promo content CRUD, event CRUD, and a calendar of business days that opens `day_stats` per day.
`dashboard_home` and `store_branding` are empty stubs. Django admin at `/advanced-admin/` handles everything else.

---

### 6.10 Cookery (`/inventory/crafting`)

Design `docs/mockups/cookery`. **Server-rendered**: each screen is a Django template in
`inventory/templates/inventory/cookery/` with its own URL (`crafting`, `crafting/transforms`, `crafting/recipe/<id>`,
`crafting/batch/<id>` (guided or counting), `crafting/done/<id>`, `crafting/transform/<id>`, `crafting/transforming/<id>`),
views in `inventory/cookery_views.py`, the work in `inventory/cookery.py` (`act()`). Every button is a form that POSTs
and redirects back (works without JS); toasts are Django messages; the animated scenes are the `{% cook_scene %}`
inclusion tag and formatting lives in `inventory/templatetags/cookery_tags.py`. `static/js/live-page.js` (generic, for
any such page) submits forms in the background and swaps in only the `data-region`s whose HTML changed, refetches the
page on SSE `cookery`/`inventory` nudges (identical HTML changes nothing on screen), and ticks `data-ends` countdowns.
A background POST that stays on the same page gets the page back directly (`Sec-Fetch-Mode` isn't `navigate`)
instead of a redirect.

- **Craft** lists components with crafting option *Craftable*. With a `Recipe` they cook in batches: the recipe's
  ingredients × batch size are **reserved** when the batch starts (`IngredientReservation`; they leave inventory right
  away so no count offers them, the recipe screen shows what other batches hold; unlimited ingredients aren't counted;
  shortages are allowed and go negative). Guided cooking walks the `RecipeStep`s with per-step timers, then the yield
  is counted and added to the component on Finish. Short of `yield_quantity × batch`, the unused share of each
  reservation becomes `WasteIngredient` (linked to the batch); the done screen shows it.
  Without a recipe a component is made one at a time: count as you go; its `ComponentIngredient` amounts per unit
  leave inventory on Finish. `Component.par` drives the stock bar and "Low".
- **Transform**: a `Transformation` turns `from_component` into `to_component` (× `ratio`) over `duration`, e.g. fresh
  ↔ frozen pierogi. Frozen versions are their own components (crafting option *Made by a transform*, hidden from
  Craft), so they keep their own stock and par and dishes keep using the fresh one. The amount leaves the source when
  it starts; Mark as done adds it to the target; Cancel puts it back.
- **Ongoing**: every unfinished `CookingSession` (step, phase, timers as epoch ms, `YieldEntry` counts) is shared;
  anyone on shift can Pick up a batch (`holder`). Finished sessions stay as the record of what was made (`made`,
  `expected`). Timers ring with a short chime on the screen that has Cookery open.
- Recipes and transforms are edited in `/advanced-admin/` (Recipe has inline ingredients and steps). Counting and
  transform amounts show friendlier units (kg for g, L for ml, pcs for ea) and convert back. `manage.py seed_recipes`
  adds the mockup's recipes (pierogi, beef stock, brioche, smash sauce, caramelized onion, freeze/thaw) to any DB.

### 6.11 Screens (`/screens/`)

Design `docs/mockups/screens/OrderBoard-html`. Restaurants build their own displays from **pre-made widgets**; the
format for adding widgets is in CLAUDE.md ("Screens and widgets").

- `Screen(name, slug, layout, public, width, height, background, padding)`. `layout` is a JSON tree of widget nodes;
  `rows`/`columns` hold child slots sized by `weights` (numbers are fr, or `"132px"`), so a screen is a nested grid.
  It's validated on save (`screens/layout.py`, `layout_errors()` names the slot and param at fault) and edited as
  JSON in `/advanced-admin/`, which shows the widget reference under the field. There's no visual editor yet.
- Widgets are defined by `screens/widgets/<name>.json` (title, template, params with types and defaults, optional
  data provider, SSE topics, CSS/JS) plus a template partial and CSS. Built in: `rows`, `columns`, `order_list`
  (preparing or ready, live on `orders`), `clock`, `weather` (weatherapi.com, fetched server-side and cached),
  `now_playing` (placeholder showing typed-in tracks; no player yet), `text_card` (specials/announcements), `image`.
- Rendering is server-side: `render_screen()` renders the tree children-first, collects each widget's CSS/JS and
  topics, and wraps every leaf in a `data-region`. `static/js/live-page.js` refetches the page on those topics and
  swaps the leaves that changed; containers aren't regions, so one list updating doesn't restart animations elsewhere.
  Node ids include a hash of the layout, so editing a screen (which publishes `screens`) reloads open copies.
- Sizes are design pixels of `width` × `height`; `--u` scales them to the display (`min(100vw/W, 100vh/H)`). Design
  portrait screens (vertical TVs) as their own 1080×1920 screens. `seed_demo` adds `pickup-portrait` as an example.
- `order_list` shows pickup orders only (delivery is for couriers): preparing = a station still pending or making it
  (oldest first), ready = every station done and not picked up (newest first). Names are shortened to "Priya S.";
  a just-ready tile is highlighted for `fresh_seconds` and asks the page to refetch when that ends.
- Migration `screens/0002` creates the `order-board` screen (public). The old board's TTS announcements, YouTube
  player and the mockup's promo slideshow aren't widgets yet.

## 7. Real-time: history and current design

**What existed before June 2024:** `pos_server/signals.py` appended orders to module-level Python lists, and views
returned a `StreamingHttpResponse` wrapping an infinite `while True: sleep(1)` generator. It worked under `runserver` and
died in production because (1) every worker process had its own lists, (2) each stream pinned a sync worker, (3) the
queues were consumed destructively so two screens stole events from each other, and (4) proxies buffered the stream.
The app then switched to polling, but the same per-process problem remained in `LocMemCache` (Square checkout status),
the in-memory courier set, and the inventory crafting page's SSE.

**Now (`pavilion/realtime.py`, SSE as decided):**

- Model signals call `publish("orders" | "inventory")` (and the Square code `publish("checkout")`). Events are sent
  after the DB transaction commits, at most one per topic per transaction.
- With `REDIS_URL`, events go through Redis pub/sub, so every worker's streams receive them. Without it (dev, one
  process) an in-process broker is used.
- `/events/<topic>` is an async view, so open streams don't hold worker threads (daphne in dev, uvicorn in production).
  Heartbeats every 15 s; `X-Accel-Buffering: no` for nginx.
- Events are only nudges ("orders changed"). Screens refetch their normal JSON endpoint, so access rules and redaction
  live in one place. `static/js/live.js` (`onLiveUpdate(topic, refresh)`) adds a debounce, refetches on every (re)connect, and reopens the stream if the 15 s `ping` heartbeat stops. No screen polls.
- Redis is also the cache in production, so the Square checkout status is shared between workers. Couriers are in the
  `CourierShift` table.

The Copilot WebSockets branch is superseded by this.

---

## 8. Config, running, deploying

### Environment (`pavilion/.env`, see `pavilion/.env.example`)

`ENVIRONMENT` (production → Postgres, DEBUG off, Redis, S3 media, file logging), `SECRET_KEY` (required in production),
`APP_DOMAIN`, `EXTRA_HOSTS` (comma-separated, e.g. an ngrok domain), `POSTGRES_DB/USER/PASSWORD`, `DB_HOST`, `DB_PORT`,
`REDIS_URL`, `RUSTFS_ENDPOINT_URL/ACCESS_KEY/SECRET_KEY/BUCKET/REGION/PUBLIC_URL/PUBLIC_PROTOCOL`,
`ENFORCE_DEVICE_CHECK`, `SQUARE_ACCESS_TOKEN/ENVIRONMENT/APPLICATION_ID/LOCATION_ID/DEVICE_ID/WEBHOOK_SIGNATURE_KEY`,
`SENDGRID_API_KEY`, `GOOGLE_API_KEY`, `WEATHER_API_KEY`, `VAPID_PUBLIC_KEY/PRIVATE_KEY/CONTACT_EMAIL`.

### Local dev (Windows)

- Use **`venv/`**. `venv\Scripts\python manage.py runserver` (ASGI via daphne), then `http://127.0.0.1:8000/en/apps/`.
- Fresh database: `manage.py migrate` then `manage.py seed_demo` (admin/admin, cashier/cashier, courier/courier, customer/customer).
- Tests: `manage.py test` (Square and SendGrid are mocked). `pip install -r requirements-dev.txt` adds `fakeredis` for the Redis broker test.
- `start.bat` / `start.sh` start ngrok and pass its domain to Django via `EXTRA_HOSTS`. This exposes a DEBUG server; don't leave it running.
- Translations: `make_translations.(bat|sh)` → `makemessages -l uk -l zh_HK`, then `compilemessages` (`.mo` files are gitignored).

### Migrations

Committed. The DB was reset on 2026-10-13; every app starts at `0001_initial` (the old DB is kept locally as
`db-old-2026-10-13.sqlite3`). Run `makemigrations` with every model change, commit the migration with it, and update
`seed_demo`. `stash@{0}` (menu fonts/styles) was **not** applied.

### Docker (`Dockerfile`, `docker-compose.yml`)

- Python 3.12 slim + ffmpeg, libgl, libpq, gettext. Collects static and compiles translations at build time; the
  container runs `migrate`, then `uvicorn pavilion.asgi:application --workers ${WEB_CONCURRENCY:-3}`.
- Compose: `web` + `db` (Postgres 16, healthchecked, port not published) + `redis`. Secrets come from `pavilion/.env`.
  `.dockerignore` keeps `.env`, databases and media out of the image.
- Media lives in the RustFS bucket, which must allow anonymous reads (images are embedded in emails and cached by the PWA).
- Put a TLS-terminating proxy in front (nginx, Caddy, Cloudflare). For nginx, don't buffer `/events/`.

---

## 9. Open issues and limits

### Needs action from you

1. **Rotate the Square, SendGrid, Google, VAPID and RustFS keys.** The old dev server served the whole project folder
   (including `.env`) and was exposed through ngrok. The old hard-coded `SECRET_KEY` is in git history; never reuse it.
2. Re-test against real services: Square Web Payments popup (authorize → capture on approval, void on reject), Square
   Terminal pairing and checkout, SendGrid gift card email, web push to couriers. All are covered by mocked tests only.
3. Run the stack once with Docker + Redis (`docker compose up`) to check multi-worker live updates; locally only the
   in-process broker and a fakeredis test were exercised.

### Known limits (not bugs, but worth knowing)

- POS card payments: the server trusts the POS that the Terminal checkout completed (the POS is staff-only and device-gated).
- Customer order pages use sequential order IDs, so anyone can view any order's dish list and progress (no names, phones or addresses are shown).
- `card-api` GET is public: the 16-digit card number is the only secret for checking a balance.
- `RESTAURANT_ADDRESS`, receipt name/address and the SendGrid sender are hard-coded for the cathedral café.
- Old migrations were discarded, so `db-old-2026-10-13.sqlite3` can't be migrated forward. Its data would need a manual import.
- `new_admin` `dashboard_home` and `store_branding` are stubs; the `Waste*` inventory models have no UI.
- `Staff.allowance` is still a float.

**Fixed in the 2026-10-13 overhaul** (details in the commit messages on `fix-all-problems-for-real`): media catch-all
serving `.env`; hard-coded `SECRET_KEY`; open gift card API; unauthenticated POS endpoints; client-side-only device
check; staff pages open to any logged-in customer; open redirect on `next`; `CSRF_TRUSTED_ORIGINS` f-string; KDS
script injection via names/instructions; browser-set prices and totals; browser-marked payments and reusable
transactions; free gift cards; gift-card cents crash; no `GiftCardAuthorization` at the POS; rejected online orders not
refunded; payment capture endpoint that never worked; stock ignoring quantity; self-crafting components blocking dishes;
multiple active menus crashing views; `/deliveries/`, dashboard, `day_stats`, `pair-terminal`, email preview and template
syntax 500s; status board using removed fields; courier push never firing; missing `users` migrations; `en-us`
URLs; per-process state (cache, couriers, SSE); Docker running `runserver` without migrations; start scripts editing
`settings.py`; webrtc, debug prints and other dead code.

---

## 10. Branches, stashes, and other in-flight work

| Ref | Contents | Recommendation |
| --- | --- | --- |
| `fix-all-problems-for-real` | The 2026-10-13 overhaul (everything in this file) | Merge into `main` once tested |
| `origin/copilot/upgrade-order-display-flow` | Channels WebSockets with `InMemoryChannelLayer` | Superseded by SSE (§7); close |
| `origin/copilot/upgrade-station-management-system` | Dynamic stations alongside the old fields | Superseded by the Station model (§5.2); close |
| `origin/copilot/improve-inefficient-code` | `select_related`/`prefetch_related` fixes | Mostly superseded (`orders_for_screens`); cherry-pick leftovers if wanted |
| `stash@{0}` "Idk" (2025-04-26) | Menu `style` + fonts (`new_admin.Font`), `MenuStationPosition`, modeltranslation, customer-display redesign | Not applied. Would need porting to the new models and fresh migrations |
| `stash@{1}`, `stash@{2}`, `screens`, `websockets`, `Translations` | Superseded / obsolete / merged | Safe to drop |
| `origin/recovery` | Unrelated history, 2024 gift-card work | Archive only |

---

## 11. What's next

1. Rotate keys; test the Square/SendGrid/push flows and the Docker stack (§9).
2. Merge `fix-all-problems-for-real` into `main` and deploy.
3. Register redesign follow-ups: the customization sheet in the online store; a manager UI for dishes, combos,
   options and discounts (today: `/advanced-admin/`); refunds (the drawer has no refund row yet); translations for the
   new strings (`make_translations`); Orders/Inventory screens in the register's look.
4. Product work: finish `new_admin` (dashboard home, branding, station management), make restaurant name/address
   configurable, decide on `stash@{0}` (menu fonts/styles), add UI for waste tracking.
