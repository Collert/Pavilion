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
`RESTAURANT_ADDRESS` (env, defaults to "501 4th Ave, New Westminster, BC") is where couriers pick up. Emails go
through listmonk (2026-10-20, replacing SendGrid); their wording lives in listmonk templates.

| Area | What it is | Status |
| --- | --- | --- |
| **POS register** | Touch-screen cashier UI, cash/card/gift-card tender, Square Terminal | ✅ Works |
| **Kitchen display (KDS)** | "Order marking" screen per set of stations: confirm, mark ready, hand out, reject; keyboard only | ✅ Works, live (SSE) |
| **Customer-facing display** | Second screen mirroring the cart, linked to the POS by `BroadcastChannel` | ✅ Works (same browser only) |
| **Order status board** | TV screen with in-progress/ready columns, TTS announcements, weather, YouTube player | ✅ Works, live (SSE) |
| **Online store** | Customer PWA: menu, cart, pickup/delivery ordering, Square web payments, order history | ✅ Works (Square popup not re-tested against sandbox) |
| **Store hours** | Online ordering only inside the store's hours after the pickup/delivery cutoffs (`events/hours.py`), not while paused | ✅ Enforced on the server |
| **Deliveries** | Courier app: start a shift, accept or pass on orders (before the kitchen sees them), steps to the door with a proof photo, ETAs via Google Distance Matrix, web push | ✅ Works (push and Google untested end-to-end) |
| **Gift cards** | Buy online (Square), email via listmonk, barcode card page, scan/charge at POS | ✅ Works |
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
  `square.client.Client`), listmonk transactional email (`misc_tools/email.py`), Google Distance Matrix, OpenWeather, Web Push (VAPID), gTTS +
  ffmpeg, OpenCV + scikit-learn, python-barcode, QuaggaJS, FullCalendar.
- **i18n:** `i18n_patterns`, so page URLs carry `/en/`, `/uk/` or `/zh-hk/`. `LANGUAGE_CODE` must be one of `LANGUAGES`
  (`en`), or `reverse()` outside a request builds 404ing `/en-us/` URLs. JS strings use `gettext()` via `/jsi18n/`.

---

## 4. URL map

All URLs below are under the language prefix (`/en/`) except `/jsi18n/` and `/events/<topic>`.

| Prefix | App | Key routes |
| --- | --- | --- |
| `/` | `online_store` | `""` home, `menu`, `dish/<id>`, `cart`, `services`, `order-status/<id>`, `account/...`, `catering/` (catering app) |
| `/restaurant/` | `pos_server` | `register` (**the POS**), `order-marking` (KDS), `pos-output` (customer display), `orders-progress` (status board), `active_orders` (JSON), `check_inventory` (JSON), `check-device`, `component-choice`, `menu_select`, `create-menu`, `pair-terminal`, `webhook/square`, `webhook/square/check_card_status`, `check-su` |
| `/admin/` | `admin_portal` | `""` dashboard home, `reports[/<day>[.csv]]`, `orders[/<id>[/note\|send\|refund\|print\|receipt]]`, `catering[/settings\|/<id>]`, `branding`, `hours`, `stations`, `menus[/new\|/<id>[/action]]`, `dishes[/new\|/<id>]`, `combos[/new\|/<id>]`, `recipes[/new\|/<id>]`, `services[/new\|/<id>]`, `components[/new\|/<id>]`, `ingredients[/new\|/<id>]`, `condiments[/new\|/<id>]`, `disposables[/new\|/<id>]`, `restocks[/new\|/<id>]`. Being rebuilt page by page from the canvas's `Admin*` artboards (see §6.9) |
| `/catering/` | `catering` | `<token>`: a customer's catering order (the link in its emails): reply, accept or decline proposed changes |
| `/advanced-admin/` | Django admin | Dishes, components, stations, devices, everything else |
| `/inventory/` | `inventory` | `log-shopping`, `shopping-history`, `day-display/<id>`, `crafting` (Cookery), `crafting/data` (its JSON + POST actions), `component-availability` (page), `availability/data` (its JSON + POST changes) |
| `/deliveries/` | `deliveries` | `""` start shift / orders looking for a courier (`?end=1` end-shift sheet), `shift` (POST start/end), `order/<id>` (accept/pass), `current` (your delivery), `done/<id>`, `eta` (store checkout), `vapid-public-key/`, `save-subscription/` |
| `/gift-cards/` | `gift_cards` | `card/<number>`, `get-card`, `special`, `card-confirm`, `card-api/<number>`, `email_card-confirm` (preview) |
| `/payments/` | `payments` | `web-payment-window` (iframe), `process-web-payment` |
| `/users/` | `users` | `login` (= `LOGIN_URL`), `logout`, `register`, `profile` |
| `/screens/` | `screens` | `""` list of screens + widget catalogue (staff), `<slug>` a screen (public or staff, per screen) |
| `/apps/` | `app_switcher` | staff launcher (see §6.2) |
| `/utils/` | `misc_tools` | `get-image-colors/` (staff), `tts?text=` (public, 300 chars max) |
| `/events/<topic>` | `pavilion.realtime` | SSE streams: `orders`, `screens` and `catering` (public), `inventory` and `cookery` (staff), `checkout` (staff + registered device) |

**Access rules.** Customers can register accounts, so staff pages use `staff_required` / `superuser_required` /
`api_staff_required` (`pos_server/devices.py`), never bare `login_required`. POS/KDS views also use `device_required`.

---

## 5. Domain model

### 5.1 Menu → Dish / Combo → Component → Ingredient (`pos_server/models.py`)

Redesigned 2026-10-13 (after the overhaul) for the register mockups in `docs/mockups/`.

```
Menu (position, draft, auto, manual_on; MenuRule schedules; Menu.live_menus() / Menu.active(), see pos_server/menu_schedule.py)
 ├─ Dish (M2M menu; price Decimal; station FK; ticket_name; prep_minutes; archived;
 │   │      in_stock / availability / blocked / visible_in_menu; catering + catering_min ─ CateringPrice breaks)
 │   ├─ DishComponent (quantity per portion; adjustable; max_extra; choice_title ─ PartChoice substitutes) ─ Component (inventory; par; category; storage; keeps_days; extra_price; extra_label; archived; …)
 │   │                                                                     └─ ComponentIngredient ─ Ingredient
 │   ├─ OptionGroup (dish-owned; pick one|any; required) ─ Option (price; extra component + quantity; multiplier)
 │   └─ Extra (condiment: component; price; max_count)
 └─ Combo (M2M menu; pricing fixed|percent; price; percent_off; ticket_name; active; archived)
         │   └─ ComboSlot (Main/Side/Drink; picks; required) ─ ComboSlotOption (dish; upcharge; is_default)
         └─ Extra (combo condiments)
Component ─ OptionGroup (component-owned, e.g. Patty temperature, Milk) ─ Option (component = swap it for this one)
```

- **A dish's customization sheet** = its adjustable parts, its own modifier groups, the groups of each of its
  components (hidden while that part is left out), condiments, allergy flags (the `ALLERGENS` list) and a note.
  **Parts** count in steps (`DishComponent.step` / `.standard`): one piece when the dish uses whole pieces (8 pierogies
  go from 0 to 18), else the whole amount (20 g lettuce: none, standard, double...), up to `max_extra` (default 10)
  above the standard; each step above costs the component's `extra_price`. **Modifier groups** (`OptionGroup.pick`):
  *pick one* (a `required` one starts on its first option, the standard; an optional one on nothing and can be
  cleared) or *pick any* (each option any number of times up to 99, e.g. add-ons; `required` = at least one, and the
  register opens the sheet when such a dish is added). Any option can use a component from stock. Charged add-ons
  are the dish's built-in Add-ons group (`OptionGroup.addons`: pick any, each option a component; 2026-10-21; the
  `Extra` add-on kind is gone, Extras are condiments).
- **Variants** (small/regular/large) are a dish option group whose options change the price and `multiplier`.
- **Combos** (2026-10-21) cost `Combo.unit_price(picks)` (set price + each pick's upcharge, or the picks' own prices
  less `percent_off`) + each pick's customizations + combo condiments. Each choice (`ComboSlot`) takes `picks` dishes
  (the same one may be picked twice): a required one starts on its default then the next options (skipping sold-out
  ones), an optional one on nothing. Cart lines list every pick: `"slots": {"<slot id>": [{"dish", "cust"}]}`.
  The register walks through every choice with something to choose when a combo is added (the choice sheet), and
  the combo sheet changes picks and customizes each one; the online store asks for the first required one-pick
  choice. Kitchen tickets call it `ticket_name`; `OrderItem.upcharge` records each pick's upcharge (for the admin's
  "upgraded" stats). Hidden (`active` off) and archived combos aren't sold. A combo is listed in the menu section of
  its first slot's default dish. Every picked dish is made at its own station.
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
- **Allergens and diets** (2026-10-21): `Ingredient.allergens` is a list of `ALLERGENS` (Gluten, Dairy, Egg, Soy,
  Peanuts, Tree nuts, Sesame, Mustard, Fish, Shellfish, Sulphites; also what customers can flag on the register), and
  `meat` / `fish` / `animal` flags; Dairy or Egg implies `animal`, Fish or Shellfish implies `fish` (`Ingredient.flags`).
  A dish's allergens, the `DIETS` it fits (vegetarian, vegan, pescatarian, gluten-free) and its food cost are worked
  out from its recipe in `pos_server/dish_info.py`; nothing is stored on the dish.
- **Kitchen tickets** use `Dish.ticket_name` when it's set (`orders._kitchen_rows`). `prep_minutes` is stored for
  later (estimates). `Dish.catering_price(qty)` is the biggest `CateringPrice` break reached, else retail; catering
  orders use it (§6.12). Archived dishes are never `available` and come off every menu.

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
- `events.StoreHours` (one row: `week` = Monday-first `{open, r: [[from, to]]}` in minutes, `to` past 1440 = after midnight;
  `pickup_after/before`, `delivery_after/before`, `last_call`, `around_breaks`, `ahead`, `ahead_days`, `lead`, `paused_until`),
  `SpecialHours(date, closed, opens, closes, note)` replacing the week on a date; `events/hours.py` (`Hours`: ranges per
  day and channel, spans as datetimes, current/next, pause). `events.Event` and `EventTemplate` are unused until the Events page.
- `deliveries.Delivery(order, order_number, customer_name, destination, address_2, instructions, phone, courier,
  passed_by, accepted_at, picked_up_at, arrived_at, delivered_at, handoff, photo, declined_reason, distance_m,
  travel_seconds)`; `phase()` says which step it's at. `CourierShift(user, mode = walking|bicycling|driving,
  started_at)` (who is on shift), `PushSubscription` (web push, `deliveries/notifications.py`).
- `inventory`: `StockUpdate` + `UpdateIngredient` (shopping trips with a receipt upload), `Recipe` (`yield_quantity`,
  `notes`) with `RecipeIngredient` (per batch) and `RecipeStep` (title, text, optional timer, scene, ingredients it uses),
  `Transformation`, `CookingSession` + `YieldEntry` (§6.10), `Packaging`,
  `CondimentUse`, `IngredientUsageBuffer` (TODO), and `Waste*` models (no UI).
- `services.Service(title, description, category = staff|equipment|venue|delivery|experience|extra, pricing =
  flat|hour|person|day|unit, price, minimum, tax = store|none, details JSON (per category: roster size and gratuity,
  units owned, capacity / food and drink minimum / included hours, radius and $ per extra km, group size and length),
  includes (venues), deposit + deposit_amount + deposit_per unit|booking (equipment), lead_days, cancellation,
  needs_approval, at_counter, online, catering, active, archived)`; `Service.quote(amount, count)` prices a booking
  (minimum, gratuity, tax, refundable deposit). `services.Booking(service, name, customer, date, amount, count, status =
  pending|confirmed|declined|cancelled, channel, total, deposit, notes, created_at)`. Confirmed catering orders keep one
  Booking per service line (`CateringLine.booking`); the register and the store don't book services yet.
- `catering`: `CateringSettings` (one row: on/off, minimum notice in hours or days, book-ahead limit, more notice for
  big orders = one day per `big_hours` of kitchen time from dishes' `prep_minutes`, less-notice requests, minimum
  subtotal, event days and hours, pickup/delivery, when deposits are held, policy, auto-reply),
  `CateringOrder` (token for the customer's page, contact, event date/time, fulfilment and address, note, status
  new → waiting (changes proposed) → confirmed → done, or rejected / cancelled; `previous` = the lines before the
  proposed changes; the Square card on file, `charged`/`refunded`, the deposit hold), `CateringLine` (dish, service,
  condiment or disposable; quantity, hours/days, price each kept on the line) and `CateringMessage` (the conversation
  and every step, with the changes and totals). See §6.12.
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
  it **picked up** (`picked_up = True`, the order leaves every screen). Delivery orders wait for the courier instead,
  and only reach the KDS once a courier has accepted them (§6.6); the card names the courier, or says "No courier".
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
works at its URL until it's removed. The customer display isn't on the app switcher either (Deliveries took its
tile); open it at its URL.

- The customer display shows the active menu grouped by station heading, plus the live cart from the POS via
  `BroadcastChannel`, so **it must be a second window in the same browser profile on the same machine**.
- The status board is public. It reads the redacted `active_orders` feed (no phone, address, payment ID, notes or
  prices), diffs it into "In progress" / "Ready" (ready = every involved station done), and announces newly ready
  orders by TTS. Clock, weather (OpenWeather) and a YouTube player are on the same screen.

### 6.5 Online ordering (`/`)

The store is being moved onto **themes** (CLAUDE.md "Store themes"; design: the canvas's `Store*` artboards, theme
"Neighbourhood"). Done:

- **Home** (`index`): hero, menus on now and coming up from the menu schedule, this week's best sellers, a catering
  teaser from `CateringSettings`, three online services, visit us with this week's hours (special dates show their note) from `events/hours.py`; the
  theme's header (open-now banner, the signed-in customer's order in progress, cart count) and footer.
- **Menu** (`online_store/pages.py: menu_page`): menus live now or starting within a day as tabs, sections by
  station, the cart beside them, and the dish sheet (`?dish=` / `?combo=`): a form built from `pricing.DishKit`
  (substitutes, modifier groups, leave off / extra for adjustable parts, condiments on the side, a note) that
  re-prices as it changes and adds a line to the **session cart** (`online_store/cart.py`; the old IndexedDB cart is
  only used by the pages still on the old layout).
- **Catering** (`/catering/` and `/catering/<token>`, see §6.12): build a quote and send it with a card on file;
  then the request page with its versions and messages.
- **Checkout** (`/cart`, `checkout`): quantities, "goes well with", pickup or delivery (address and driver note),
  as soon as possible (while an event is open) or scheduled up to 14 days ahead inside an open event and while a
  menu with every dish in the cart is live (`_schedule`), name/phone/email, promo or gift-card code, tip
  (`Order.tip`, added after fees), and Place order: gift cards first, then Square's card form in a sheet
  (`payments/web-payment-window`, authorize-only; `checkout.js` places the order once paid). Orders start pending
  like before. No delivery fee or saved cards yet (saved cards come with the account pages).
- **The placed order** (`order-status/<id>`, `order_page`): steps, ETA, where, "need a change", receipt; follows
  the kitchen over SSE.

- **Sign in and join** (`/account/sign-in`, `/account/join`, `online_store/account.py`, drawn by the theme): email
  (or username) and password; Apple/Google buttons are shown but not set up, email codes come later. Staff still
  use `users/login`.
- **The account** (`/account/`, Pavilion's own look like the mockup, not the theme): orders (the one in progress,
  reorder into the cart from each item's saved selection, a printable receipt), catering requests (waiting ones
  first-class, linked by customer or email), profile and wallet (`users.Customer`: phone, company, order texts,
  catering emails, newsletter; `users.SavedCard`: Square cards on file, added through `payments/save-card`, a
  default, removed with `square.disable_card`; `users.SavedAddress`; gift cards sent to their email, used
  automatically at checkout). Checkout offers saved cards (authorized like the card form's payments, by
  `pages._authorize_saved`) and saved addresses; the catering send sheet offers saved cards.

- **Events and services** (`/services`, `pages.services_page`): every service booked online, the venue first,
  then experiences, staff, equipment and extras, with "Add to a request" starting a catering quote with it
  (`/catering/?add=<id>`; a quote also takes services that are booked online but not switched on for catering).

Every page of the Neighbourhood theme is built; the old store layout, its IndexedDB cart, `cart-quote`,
`submit-order`, `order-history` and the service worker are gone.

Placing an order (`pages._place`, checkout's `op=place`): the schedule is checked again (ASAP only inside the pickup or
delivery window and not while paused; scheduled only when ordering ahead is on, up to `ahead_days`), gift cards are used first, then a saved card (`_authorize_saved`) or the card
sheet: `payments/web-payment-window?amount=` in an iframe creates a `Transaction`, `process-web-payment` charges
**the transaction's amount** via Square (authorize-only) and only then marks it paid, and `checkout.js` posts the
form again with `transaction_uuid`. `create_order` runs with `initial_status=PENDING`; `verify_payment` requires a
paid, unused transaction covering what's left and links its `PaymentAuthorization` to the order. Delivery orders get
a `Delivery`. The session keeps the last 20 order ids (`session["orders"]`).

### 6.6 Deliveries (`/deliveries/`)

Staff-only courier PWA for phones (design: the Courier screens of the "Pavilion POS Register" Claude Design canvas).
Server-rendered on `staff_layout.html` like Cookery (template per screen, POST forms that redirect, `live-page.js`
refetching on SSE `orders`); the nav rail is the bottom bar in portrait. `courier.js` only does the photo preview,
the start button's label and push sign-up.

1. **Start shift** (`/deliveries/`): pick walking, cycling or driving (`CourierShift.mode`, used for travel times and
   Maps directions). Starting asks for notifications. **End shift** (`?end=1` sheet) is refused while you have a
   delivery.
2. **Orders looking for a courier**: every delivery order nobody has accepted, oldest first, with the travel time for
   your mode, distance, item count, "Large order" (5+), a scheduled time and its age. A new one pushes "New delivery"
   to everyone on shift (`deliveries.signals`).
3. **Order detail** (`order/<id>`): travel time for each mode, address and note, items. **Accept** takes it (one at a
   time; a row lock stops two couriers taking the same order). **Pass** hides it from you.
4. **The kitchen sees it only now.** `deliveries.models.hidden_order_ids()` keeps unaccepted delivery orders out of
   `active_orders_queryset()` while some courier on shift could still take them. If nobody is on shift, or everyone on
   shift has passed, the order shows on the KDS as "No courier" so staff can reject it.
5. **Your delivery** (`current`), one step at a time (`Delivery.phase()`): waiting for the restaurant (KDS confirm) →
   preparing (countdown from the median `prep_time` of recent orders) → ready (push to the courier) → **I've picked it
   up** (`picked_up_at`, `Order.picked_up = True`, off the KDS) → on the way (Navigate in Maps) → **I've arrived** →
   drop-off: left at the door (photo required) or handed over. The photo is turned upright, shrunk to 1600 px and
   stripped of metadata, stored under a random name; staff see it in `/advanced-admin/` → Deliveries. →
   **Delivered** (`delivered_at`; `progress_status()` 6) with pickup-to-door time and a link back to the restaurant.
6. **Declined:** rejecting the order on the KDS deletes it as usual, but the courier's `Delivery` stays (order empty,
   `declined_reason` from the `RejectedOrder`) until they tap "Back to orders"; they get a push too.

Travel times (`deliveries/routes.py`): Google Distance Matrix from `RESTAURANT_ADDRESS`, one request per mode for all
new deliveries, stored on the `Delivery` (`distance_m`, `travel_seconds`) so each is looked up once; a failed lookup
waits 10 minutes. Without `GOOGLE_API_KEY` the app shows "–". `RESTAURANT_NAME` names the restaurant in the app. The
store's checkout estimate (`eta`) is unchanged. The order detail's header is a drawing of streets, not a real map.

### 6.7 Gift cards (`/gift-cards/`)

- `get-card`: pick a preset or personal design (images listed from storage under `files/gift-cards/{presets,personal}`),
  pay through the same popup. On POST the card is created **only from a paid, unused transaction, for the amount paid**,
  then emailed via listmonk (an email failure is logged; the confirmation page still shows the card link).
- `card/<number>` renders a code128 barcode. `card-api/<number>`: GET is public (balance, image; email for staff only),
  POST charges and needs staff + CSRF.

### 6.8 Device registration

POS/KDS views use `device_required`: when `ENFORCE_DEVICE_CHECK` is on (default in production), the browser needs a
signed HttpOnly `pavilion_device` cookie for a registered `EligibleDevice`. Unregistered browsers are redirected to
`check-device`, where a superuser registers them; a browser that already has the token in `localStorage` re-sends it
automatically and gets the cookie back. `seed_demo` creates a demo device; open any POS page with
`?device-token=00000000-0000-4000-8000-000000000001` once to register a dev browser.

### 6.9 Admin

`admin_portal` (`/admin/`, superuser only for now) replaced `new_admin` on 2026-10-20. It's rebuilt from the Claude
Design canvas's `Admin*` artboards, one page at a time, in the rail's order (`AdminRail`; groups and pages in
`admin_portal/nav.py`: a page shows on the rail once its url name is filled in). Pages are Django templates on
`admin_portal/layout.html` (the rail, `<main id="page">`, Django-message toasts) kept current by `live-page.js`;
shared styles in `admin_portal/static/admin_portal/admin.css`, one CSS file per page next to it.

- `StoreSettings` (one row, `StoreSettings.load()`): the store's name, shown at the top of the rail with "Pavilion"
  under it. The Branding page will edit it.
- **Dashboard home** (`admin_home`, `dashboard.py`): today by hour against the same weekday last week up to the same
  time, or 7/30 days against the period before (calendar: the same weekdays a whole number of weeks earlier; "Open
  days only": days with orders). KPIs, an SVG chart drawn on the server with CSS-only hover tips, "Right now" (open
  orders by channel, open tickets per station, average wait, ready for pickup, couriers out), top sellers, sales by
  channel, and alerts (sold-out dishes, components under par, drawers over/short, online orders waiting). Refetches
  on "orders" and "inventory" nudges.
- **Reports** (`admin_reports`, `reports.py`): a month of business days (days with orders) as a table or a
  calendar coloured by sales, and a day report (`admin_report_day`, `reports/<YYYY-MM-DD>`, `reports/latest`):
  KPIs, dishes sold (combos split across their picks' stations by price), components and ingredients from the
  standard recipes, station mix donut, orders and average prep per 15 minutes against a 10-minute target, and a
  CSV export (`reports/<day>.csv`). Replaces the old `day_stats` page. `Station.color` (new) colours stations.
- **Retail orders** (`admin_orders`, `orders.py`): every Order (register, online, delivery) with date/status
  filters, search and pages; the order page shows items, totals, a timeline, customer, payments (card brand and
  last 4), details, receipts and a staff note. Sheets (`?sheet=send|refund`) are GET links/submits, so the server
  works out the refund total; the final button POSTs. Receipts: print (a printable page in a new tab) or email
  through listmonk; Text/WhatsApp/Telegram/Messenger are disabled placeholders (`ReceiptLog`).
  **Refunds** (`pos_server/refunds.py`, `Refund`/`RefundItem`): an item's share after discounts and with tax, or
  everything left; back to the card through Square (`payments.square.refund_payment`), onto gift cards, or as cash
  from an open drawer (a `CashMovement`). A Square refusal rolls everything back.
  **Card details**: the register sends the Terminal checkout id with a card payment; after the order,
  `square.fill_card_details` stores the Square payment id, brand, last 4 and entry method (tap/chip/...). Online
  payments copy them from the web payment (`PaymentAuthorization.card_brand/last4`). Orders also record the register
  (`device`), the signed-in `customer`, `email`, `staff_note` and `voided_at/by` (nothing voids orders yet).
- **Menus** (`admin_menus`, `menus.py`): the list with priority arrows, live/scheduled/draft/off status, rule
  chips and the board template; the editor is one form with Save (2026-10-21): the whole draft travels in a hidden
  `state` field (JSON), the tab on screen merges its fields and buttons post an `op` (`tab:`, `toggle:d:5`,
  `day:0:3`, `page:1`, `sec:group:-5`...). Tabs: items (tick dishes and combos, filter by station, search, add/remove
  all shown), schedule (automatic switch with on/off rules, or on/off by hand; a week chart from the unsaved rules,
  date notes and overlaps) and display (the menu board: template, pages with a featured item and group slots, sold-out
  items shown or hidden, page and group transitions and timing with the design's checks, the template's look, and a
  live preview that is the real board drawn small at 5×). Save publishes a draft. `admin_menu_action` only keeps the
  list's priority arrows. Publishes "menus" and "inventory". The register's menu picker lists live menus and remembers the
  pick in the session (`register.register_menu`).
- **Dishes** (`admin_dishes`, `dishes.py`): the list by station, with filters (sold out or off, food cost over
  35%, not on a menu, archived), diet badges, allergens, food cost and availability. The design's "menu category"
  was dropped on purpose. The editor is **one form with a Save button** (`admin_dish` GET/POST, `op` = the button):
  the page's state is a draft (`draft_from_dish` / `draft_from_post`, rows as numbered fields); row buttons (+ break,
  × remove, + option...) and changed fields (`dish-editor.js` posts `op=preview`) re-render the draft with the sums
  worked out, unsaved; Save writes it all (`save_draft`), Back asks when there are unsaved changes, Duplicate saves
  the edited copy as a new dish, Archive takes it off every menu. Sections: basics and photo (previewed in the
  browser, sent with Save), catering price breaks with warnings, the recipe (amounts in the smallest unit,
  adjustable, cost), modifier groups (pick one/any, required), availability, menus, allergens and diets from the
  ingredients, and the last 7 days (sold includes combos; sales and profit count it sold on its own).
  `live-page.js`: while you type in a region only its `[data-calc]` parts are swapped; a form with `data-queue`
  (and `data-search`) sends its last submit after one in flight instead of dropping it; `data-keep` regions are left
  alone by background answers.
- **Combos** (`admin_combos`, `combos.py`): 30-day KPIs (sold, average saving against the default picks bought
  separately, upcharge revenue, best seller), All/Active/Hidden(/Archived) filters, search by combo or dish, rows
  with choices, price, saving, sold and % upgraded. The editor is one form like the dish editor: choices with picks
  −/+, required switch, move, remove, default dish and upcharge (or "in combo" price when percent off), the price
  card with the saving bar and warnings, a register preview and the menus with an active switch. Editors share
  `editor.js` (`form[data-editor]`, `[data-preview]`) and the editor styles in `admin.css`.
- **Recipes** (`admin_recipes`, `recipes.py`, 2026-10-21): sections by what a recipe is attached to (component,
  transformation, dish, not attached), with processes, steps and total time. The editor (one form with Save) has the
  name, what it's attached to (attaching takes over the target's old recipe), batch size, total time (at least the
  longest timer; "Use timers" fills it in), ingredients and components with amounts, and steps (title, text,
  process, optional timer; reorder, add, remove), with a dark "In Cookery" preview of the selected step (the real
  `cook_scene`). `Recipe` gained `title` and `total_minutes`; `RecipeIngredient` can be a component (Cookery takes it
  out of component stock when a batch starts); `Transformation.recipe` gives Cookery's Transform screen its steps,
  time (`Transformation.takes`) and default batch size. Dish recipes are stored but not shown on the KDS yet.
- **Services** (`admin_services`, `services.py`, 2026-10-21): sections by category with chips and search; each row shows
  the price and minimum, tags (units owned and deposit, roster, capacity, needs approval, long notice), bookings in
  the last 30 days and Active/Hidden. The editor (one form with Save) has name, description and category (picking one
  starts its details and pricing over), pricing (flat, per hour/guest/day/item, minimum, tax), the category's details
  (venues: what's included; equipment: a security deposit per unit or per booking), booking rules (notice,
  cancellation, needs approval), where it can be booked, a dark quote preview (try hours/guests/days and people/units;
  never saved) and the next three bookings. Duplicate and archive/restore like combos. The design's tax choices
  (GST, GST + PST) became "store tax rate" or "no tax", since the store has one rate (`TAX_RATE`).
- **Components** (`admin_components`, `components.py`, 2026-10-21): KPIs (count, stock value at cost, under par, extras
  sold in 30 days with margin), filters (sold as extras, under par, no recipe yet, archived) and rows grouped by
  category with unit cost, the extra's price and margin, on hand against par (bar with a par mark) and dishes using it.
  The editor (one form with Save) has name, unit (g/ml/ea) and category; the prep recipe (pick one; its batch with
  costs, or the per-unit ingredients; a warning when there are neither); stock (where it's kept, how long it
  keeps, on hand; the components its transformations connect to are listed, linked, since each storage state is its
  own component; par with a bar); a dark cost card with allergens; "Sell as an extra" (label, price, most extras per
  dish, margin, which dishes offer it: it sets `adjustable` / `max_extra` on those dishes' parts, `max_extra` 0 when
  not offered); and the dishes using it. Stock is only written when changed in the editor (`inventory_was`), so
  counts made elsewhere survive a save. Archiving is blocked while dishes use it (parts or add-ons); archived
  components leave Cookery, Availability and the pickers. Costs always come from ingredient prices:
  `dish_info.component_cost` takes the recipe's ingredients (batch over yield) first, then per-unit ingredients,
  then the component a transformation makes it from. A component's cost is never set by hand.
- **Ingredients** (`admin_ingredients`, `ingredients.py`, 2026-10-21): what the restaurant buys. KPIs (count and
  suppliers, stock value, under par, price went up), filters (under par, price went up, has allergens, vegan,
  gluten-free, archived), rows by category with cost per kg/L/piece and its change, allergens, diet badges (V, VG, P,
  GF), on hand against par and uses. The editor (one form with Save) has name, unit and category; allergens and the
  meat / fish / animal / gluten switches (the gluten switch is the Gluten allergen; Fish/Shellfish and Dairy/Egg lock
  fish and animal on) with the diets they fit; buying (supplier, picked or typed, which creates a `Supplier`; pack
  size and pack price, which set `Ingredient.cost`); stock (on hand, par, and where it is stored: a `StorageLocation`, picked or typed like suppliers and shared with the Components editor); the dark cost card with the
  change since the last price (`previous_cost`, set when the price changes); and the recipes and components using it.
  Saving publishes `menus`/`inventory`, since component and dish costs, allergens and diets follow it.
- **Condiments** (`admin_condiments`, `condiments.py`, 2026-10-21): `Condiment` (served as, serving, free per order,
  price each, dine-in mode, ask on takeout) sits on a component counted in servings, which holds the stock, par,
  storage and shelf life. House-made: the component's prep recipe (batch table, cost and allergens from its
  ingredients). Bought: the component uses one ingredient whose pack is the case, so the editor's supplier, servings
  per case, case price, allergens and switches edit that ingredient. "Suggest with" adds or removes the dishes'
  Extra rows. The list (house-made, bought) has KPIs (handed out and charged in 30 days, from the orders'
  selections), filters and the rule ("3 free, then $0.10"). Pricing per order is `pricing._charge_condiments`;
  the register's sheet says "3 free per order, then +$0.10". Dine-in mode and ask-on-takeout are saved but the
  register doesn't prompt before bagging yet. Components that are only condiments leave the Components list.
- **Substitutes and component modifiers** (2026-10-21): each recipe row in the dish editor has "+ Substitute";
  once a part has substitutes it gets a name ("Bun") and chips with an upcharge each. The register shows the part as a
  pick-one row and counts its steps with the substitute (two lettuce wraps, each extra at the wrap's extra price);
  `pricing.price_dish` reads `swaps`, the KDS shows the substitute as an option. The dish's Modifiers card lists the
  groups its components own ("Patty temperature · from Beef patty") with a link, and the Components editor has a
  Modifiers card to edit them. seed_demo's old "Bun" and "Milk" component groups became substitutes on every dish.
- **Restocks** (`admin_restocks`, `restocks.py`, 2026-10-21): purchase orders (`inventory.Restock` + `RestockLine`:
  cases of an ingredient at a case price, with the price last paid and the case size at the time). Draft, placed,
  delivered: the first save as delivered adds each line's cases to stock and makes its case price the ingredient's pack
  price (`previous_cost` keeps the old one, so "since last delivery" is real); `received_at` locks it (only prices,
  dates, notes, fee, tax and the receipt can change, and a fixed price follows to the ingredient when it's the latest
  delivery). The list has KPIs (on the way, spent and price changes in 30 days, drafts and missing receipts), a band of
  suppliers with items under par that aren't on an open order ("Draft order" makes one), filters and rows; the editor
  has the status steps, supplier and dates, items (cases stepper, price per case with the change from last time, "Add N
  under par", add from this or other suppliers), notes, the total with delivery fee and tax, the receipt (kept as soon
  as it's picked: `editor.js` posts `[data-upload]` files at once) and the price changes with what they affect. The
  old shopping log (`/inventory/log-shopping`, `StockUpdate`) is still there.
- **Disposables** (`admin_disposables`, `disposables.py`, 2026-10-21): `Disposable` (size, category, material, bin,
  order types, counted per dish / per order / per guest / on request, dishes, count, fee label and price) sits on an
  ingredient counted in ea that holds the buying (supplier, units per case, case price) and the stock. When an order
  is created, `pricing.disposables_for` picks the per-dish and per-order ones for its type (`models.order_type`:
  delivery, pickup = to go, else dine-in), `orders.use_disposables` counts them off stock (`DisposableUse`) and fees
  become `OrderFee` lines (`Order.fees`, after tax, on the register's and store's totals and the receipts). The
  register sends `toGo` and the store `method`/`toGo` with their quotes. On-request items (the register's "Need
  cutlery?" prompt, the store's checkbox) and per-guest items (catering) aren't counted yet. The list has KPIs
  (packaging per takeout order, stock value, compostable share, under par), filters and days of stock left; the
  editor follows the mockup. Disposables' ingredients leave the Ingredients list and the recipe picker.
- `seed_demo` adds nine weeks of finished orders (closed Tuesdays) so the dashboard and reports have history.

- **Branding** (`admin_branding`, `branding.py`, 2026-10-21): theme cards, the theme's settings (from its JSON) plus
  the store details (name, logo, address, phone, map link), with contrast warnings, and the real store in a scaled
  iframe (desktop 1440 or phone 390) showing the draft. Every change is kept as a draft at once; Publish copies it
  live (and the store details to `StoreSettings`); "Reset to theme defaults" resets the draft.
- **Store hours** (`admin_hours`, `hours.py`, 2026-10-21): the week with switches, periods (breaks between them),
  "Copy to…" and per-day checks; the week at a glance (open hours, and pickup and delivery windows after cutoffs, with
  a now line); holidays and special hours with a note for the store; "Right now" (open, kitchen last call, pickup and
  delivery windows, what the store shows today); pause online orders (15/30/60 min or the rest of the day, acts at
  once; the store shows "Back soon"); order cutoffs (around breaks or not) and ordering ahead (days, soonest pickup).
  One form with Save; the draft drives the chart and "Right now".
- **Stations** (`admin_stations`, `stations.py`, 2026-10-22): the list in ticket order (arrows reorder) and the
  selected station: name, ticket code, icon (the 18 drawings in `pos_server/station_icons.py`, also drawn on the order
  marking screen), colour (taken ones ringed), target ticket time (the order marking screen's timers turn amber at
  the strictest involved station's target and red at double), taking tickets (off makes its dishes sold out), its
  dishes, a ticket preview, the last 30 days (tickets, average and red share from `OrderStation` confirmed → ready)
  and archiving (its dishes move to another station). One form with Save; archived stations leave every list.
  There's no ticket printing, so the mockup's printer picker isn't there.
- **Catering** (`admin_catering`, `catering.py`, 2026-10-21): an inbox by status (New, Waiting on customer,
  Upcoming, Past; the rail badge counts new ones) and the open order: event facts, notice against the settings,
  kitchen time, the customer's note, the lines (tagged against the lines before proposed changes), totals and the
  deposit, the conversation with a reply box, and an action bar per status. `?edit=1` turns the order into a draft
  (steppers, hand-set prices for condiments and disposables, add a dish/service/condiment/disposable, a required
  reason) sent with "Send to …"; `?sheet=accept|reject` are the sheets. After the event, "Equipment back" / "Keep
  deposit" (or "Mark completed") close it. The Settings tab (`admin_catering_settings`) is one form with Save; its
  service switches are `Service.catering`. Catering orders never reach the register, the KDS or the boards (§6.12).

Django admin at `/advanced-admin/` handles everything not built yet.

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

- **Menu boards** (2026-10-21): menu templates are widgets of kind `menu_template` (`registry.menu_templates()`,
  not placeable directly). The `menu_board` widget shows one menu (its `menu` param) with the template and layout
  from `Menu.display` (`screens/menu_boards.py`: `clean()`, `board()`, `groups()`: a group is a station's dishes on
  the menu or its combos). Every page and every slot screen is rendered; `menu_board.js` shows one at a time from
  the clock (so boards stay in step across refetches and screens). Showcase (`menu_showcase`, portrait, 3 groups of
  6 + featured, best sellers as cards) is built; the other nine `Menu*` artboards are next. `seed_demo` adds a
  `menu-board` screen showing Lunch.

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

### 6.12 Catering (`catering/`)

Orders come in through the store's quote page (`/catering/`, `catering/views.py: quote_page`, state and checks in
`catering/store.py`: notice incl. big-order days, event days and hours, book-ahead limit, minimum subtotal and per-dish
minimums, pickup or delivery; choosing delivery adds the store's delivery service (`store.delivery_service()`: one on
for catering first, else one booked online; none = no charge), adding a delivery service from the services page picks
delivery, pickup takes delivery services off; staff and setup are booked as services) and `catering.flow.place()`, with a Square card on file saved first (`payments/save-card`: Square's card form
in an iframe, `create_customer` + `save_card`; in DEBUG a "Use a test card" button saves a `demo:` card). Nothing is
held on the card when it's sent (holds expire in about 7 days); it's charged when both sides agree. Every set of
lines is kept as a `CateringVersion` (1 = the request, then each proposal by the store or the customer). `flow` does every step and adds a
`CateringMessage` for it, emailing the customer (`catering_*` listmonk kinds): `accept` charges the card in full
(`catering/payments.py`), `propose` rewrites the lines (keeping the first `previous`) and waits for the customer,
who `agree`s (charged), suggests their own version (`counter`: the order goes back to New for the store, shown
against the version before) or cancels (`customer_cancel`) on `/catering/<token>` (the themed request page: versions
as tabs, what changed, messages); `reject` rejects a new order or cancels a later one
(refund, hold let go); `finish` closes it after the event, letting the deposit hold go or keeping it. Money is in
`catering/pricing.py` on line dicts: dishes at their catering price for the quantity, services through
`Service.quote` (with the price kept on the line), condiments and disposables at the price set on the line; tax is
`TAX_RATE` except no-tax services; deposits are held, never added to the total. **Deposits**: card holds last about
7 days, so `manage.py catering_deposits` (run daily by cron) holds them `hold_days` before the event and completes
past orders with nothing to settle; accepting an order whose hold day has come holds it at once. Cards whose ids
start with `demo:` (seed_demo's) are never sent to Square.

---

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
`LISTMONK_URL/API_USER/API_TOKEN/FROM_EMAIL`, `GOOGLE_API_KEY`, `WEATHER_API_KEY`, `VAPID_PUBLIC_KEY/PRIVATE_KEY/CONTACT_EMAIL`.
`BUGSINK_DSN` (error reporting; defaults to the project's public DSN, set empty to disable; not sent during tests) and
`BUGSINK_SEND_PII` (`true` attaches usernames, emails and IPs to error reports; default off).

### Scheduled jobs

- `manage.py catering_deposits`, once a day: holds catering deposits that are due and completes past catering orders.

### Local dev (Windows)

- Use **`venv/`**. `venv\Scripts\python manage.py runserver` (ASGI via daphne), then `http://127.0.0.1:8000/en/apps/`.
- Fresh database: `manage.py migrate` then `manage.py seed_demo` (admin/admin, cashier/cashier, courier/courier, customer/customer).
- Tests: `manage.py test` (Square and listmonk are mocked). `pip install -r requirements-dev.txt` adds `fakeredis` for the Redis broker test.
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
   Terminal pairing and checkout, listmonk gift card email, web push to couriers. All are covered by mocked tests only.
3. Run the stack once with Docker + Redis (`docker compose up`) to check multi-worker live updates; locally only the
   in-process broker and a fakeredis test were exercised.

### Known limits (not bugs, but worth knowing)

- POS card payments: the server trusts the POS that the Terminal checkout completed (the POS is staff-only and device-gated).
- Customer order pages use sequential order IDs, so anyone can view any order's dish list and progress (no names, phones or addresses are shown).
- `card-api` GET is public: the 16-digit card number is the only secret for checking a balance.
- Receipt name/address are hard-coded for the cathedral café (`RESTAURANT_ADDRESS` is an env
  setting now, but defaults to the café's address).
- The store's order status page doesn't show the courier's proof photo yet (waiting for the store redesign), and
  checkout still takes delivery orders when no courier is on shift (they show on the KDS as "No courier").
- Old migrations were discarded, so `db-old-2026-10-13.sqlite3` can't be migrated forward. Its data would need a manual import.
- The admin portal is mid-rebuild (§6.9); the `Waste*` inventory models have no UI.
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

1. Rotate keys; test the Square/listmonk/push flows and the Docker stack (§9).
2. Merge `fix-all-problems-for-real` into `main` and deploy.
3. Register redesign follow-ups: the customization sheet in the online store; a manager UI for dishes, combos,
   options and discounts (today: `/advanced-admin/`);  translations for the
   new strings (`make_translations`); Orders/Inventory screens in the register's look.
4. Product work: finish the admin portal rebuild (§6.9), make restaurant address configurable, decide on `stash@{0}` (menu fonts/styles), add UI for waste tracking.
