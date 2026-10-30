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
The store's address on the admin's Store info page is where couriers pick up. Emails go
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
  `square.client.Client`), listmonk transactional email (`misc_tools/email.py`), Valhalla and Nominatim (OpenStreetMap routing and address lookup), weatherapi.com, Web Push (VAPID), gTTS +
  ffmpeg, OpenCV + scikit-learn, python-barcode, QuaggaJS, FullCalendar.
- **i18n:** no language prefixes (since 2026-10-30): the language is the cookie the pickers set (`/i18n/setlang/`),
  else the browser's (`LocaleMiddleware`); a signed-in person's saved one is looked up once a session. Old prefixed
  addresses (`/uk/menu`) redirect and switch to that language (`pavilion/redirects.py`). `LANGUAGE_CODE` must be one of
  `LANGUAGES` (`en`). JS strings use `gettext()` via `/jsi18n/<lang>/` (one address per language, cached a day).

---

## 4. URL map

No URL has a language prefix. The online store lives under `/store/` so the installed store app can be scoped to it;
old store addresses at the root (`/menu`, `/order-status/12`, `/`) redirect there for good (`pavilion/redirects.py`,
last in the URLconf).

| Prefix | App | Key routes |
| --- | --- | --- |
| `/store/` | `online_store` | `""` home, `menu`, `dish/<id>`, `cart`, `services`, `gift-cards`, `order-status/<id>` (only for whoever placed it), `account/...` (incl. `account/notifications`), `install` (get the app), `manifest.webmanifest`, `icon-<size>.png`, `icon-maskable.png`, `sw.js`, `push/sync\|forget\|order/<id>\|renew`; `catering/` (catering app) |
| `/restaurant/` | `pos_server` | `register` (**the POS**), `order-marking` (KDS), `pos-output` (customer display), `orders-progress` (status board), `active_orders` (JSON), `check_inventory` (JSON), `check-device`, `component-choice`, `menu_select`, `create-menu`, `pair-terminal`, `webhook/square`, `register/card/<checkout id>`, `register/terminal`, `check-su` |
| `/admin/` | `admin_portal` | `""` dashboard home, `reports[/<day>[.csv]]`, `orders[/<id>[/note\|send\|refund\|print\|receipt]]`, `catering[/settings\|/<id>]`, `branding`, `hours`, `stations`, `events`, `screens[/<id>[/preview]]`, `audio`, `library[/upload]`, `staff`, `shifts[/pars]`, `requests`, `roles`, `menus[/new\|/<id>[/action]]`, `dishes[/new\|/<id>]`, `combos[/new\|/<id>]`, `recipes[/new\|/<id>]`, `services[/new\|/<id>]`, `components[/new\|/<id>]`, `ingredients[/new\|/<id>]`, `condiments[/new\|/<id>]`, `disposables[/new\|/<id>]`, `restocks[/new\|/<id>]`. Being rebuilt page by page from the canvas's `Admin*` artboards (see §6.9) |
| `/store/catering/` | `catering` | `""` the quote builder, `<token>`: a customer's catering order (the link in its emails): reply, accept or decline proposed changes |
| `/advanced-admin/` | Django admin | Dishes, components, stations, devices, everything else |
| `/inventory/` | `inventory` | `log-shopping`, `shopping-history`, `day-display/<id>`, `crafting` (Cookery), `crafting/data` (its JSON + POST actions), `component-availability` (page), `availability/data` (its JSON + POST changes) |
| `/deliveries/` | `deliveries` | `""` start shift / orders looking for a courier (`?end=1` end-shift sheet), `shift` (POST start/end), `order/<id>` (accept/pass), `current` (your delivery), `done/<id>`, `eta` (store checkout), `manifest.webmanifest`, `sw.js`, `push/renew`, `save-subscription/` |
| `/gift-cards/` | `gift_cards` | `card/<number>`, `get-card`, `special`, `card-confirm`, `card-api/<number>`, `email_card-confirm` (preview) |
| `/payments/` | `payments` | `web-payment-window` (iframe), `process-web-payment` |
| `/users/` | `users` | `login` (= `LOGIN_URL`), `logout`, `register`, `profile` |
| `/screens/` | `screens` | `""` list of screens + widget catalogue (staff), `<slug>` a screen (public or staff, per screen) |
| `/audio/` | `audio` | `""` all channels (staff), `music/<id>` and `voice/<id>` (the Audio app), `channels/<id>/now` (a music channel's song, for screens) and `/control` (JSON), `talk/<id>` (old address of `voice/<id>`); `/ws/voice/<id>` is the voice WebSocket |
| `/apps/` | `app_switcher` | staff launcher (see §6.2) |
| `/utils/` | `misc_tools` | `get-image-colors/` (staff), `tts?text=` (public, 300 chars max) |
| `/events/<topic>` | `pavilion.realtime` | SSE streams: `orders`, `screens` and `catering` (public), `inventory` and `cookery` (staff), `checkout` (staff + registered device) |

**Access rules.** Customers can register accounts, so staff pages use `app_required("app.…")` / `api_app_required`
(or `staff_required` / `superuser_required` / `api_staff_required`; all in `pos_server/devices.py`), never bare
`login_required`. POS/KDS views also use `device_required`. What staff may open and do comes from their groups
(`users/access.py`, §6.9 Groups): apps, actions (typed-in discounts, cash in and out, rejecting orders, refunds) and
admin pages with view / edit / delete. Staff in no group open nothing; Owners (superusers) everything. Without an
app, staff get `pos_server/no_access.html` (a 403) and the app switcher and staff rail leave it out.

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
  "upgraded" stats). Hidden (`active` off) and archived combos aren't sold. A combo whose choices are all made at one
  station is listed in that station's menu section (`Combo.own_station()`; a "Choice of drink" of bar drinks goes
  with the bar's dishes); one that mixes stations goes under Combos on menu boards and under its first slot's default
  dish in the store and on the register (`section_station()`). `Combo.contents()` says what's in it in words
  (`combo_contents`: "Plate of Perogies and choice of drink (Coffee, Tea)"); the combo editor fills the description
  in with it while it's empty or still the generated text, and stops once someone writes their own. Every picked dish
  is made at its own station.
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
  `menu_heading` titles the menu section for that station's dishes ("Hot food"; empty = the name) in the store, on the register and on menu boards. A data migration creates
  Kitchen / Bar / Grab & Go. Edit them on the admin's Stations page ("Menu section" is `menu_heading`).
- `Order` fields: `channel` (`store` POS, `web` online pickup, `delivery`), `start_time` (scheduled time; screens hide
  orders until it passes), `prep_time` (set when the last station finishes: from when it was placed, or its scheduled time if later; none for a scheduled order done early), `name`, `phone`, `special_instructions`
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
  authorized, `cap` captured, `del` voided/refunded. `SquareTerminal`: a Terminal and the register (`EligibleDevice`) it takes
  card payments for. `TerminalCheckout`: one card payment asked of a Terminal (register, amount, Square's status, the order
  that used it).
- `gift_cards.GiftCard(number_hash, last4, design, available_balance, original, email, recipient, message, note, source
  online/pos/staff, buyer, issued_at/by, frozen, empty_since)`: the 16-digit number is never stored (since 2026-10-23;
  `GiftCard.find` hashes what's typed), `GiftCardEvent` (its history: issue, use, refund, adjust, note),
  `GiftCardAuthorization` (charge per order, refunded on rejection; kept when the card is deleted), `GiftCardDesign`
  (picture or colour, ink, amounts, custom range, message, online/register, seasonal dates, hidden), `CardPreset`,
  `SpecialCard(CardPreset)` (no longer used: the old `/gift-cards/get-card` page sends people to the store's
  `/gift-cards`). `GiftCard.sold_on`: the order that bought it. Carts take `gift_sales` (pricing "Gift cards sold").
- `online_store.PromoContent` (home-page banners), `online_store.RejectedOrder(order_id, reason)`.
- `events.StoreHours` (one row: `week` = Monday-first `{open, r: [[from, to]]}` in minutes, `to` past 1440 = after midnight;
  `pickup_after/before`, `delivery_after/before`, `last_call`, `around_breaks`, `ahead`, `ahead_days`, `lead`, `paused_until`),
  and `events/hours.py` (`Hours`: ranges per day and channel, spans as datetimes, current/next, pause).
  `events.Event(kind open|closed, title, description, start_date, end_date, all_day, opens, closes, dine_in, pickup,
  delivery, banner, banner_days)` is laid over that by `Hours.ranges`: openings add hours for the channels ticked,
  closures (holidays too) take hours from the channels ticked, closing wins, cutoffs after; the store counts as closed
  only when every channel is. `EventTemplate` is unused.
- `deliveries.Delivery(order, order_number, customer_name, destination, address_2, instructions, phone, courier,
  passed_by, accepted_at, picked_up_at, arrived_at, delivered_at, handoff, photo, declined_reason, distance_m,
  travel_seconds)`; `phase()` says which step it's at. `CourierShift(user, mode = walking|bicycling|driving,
  started_at)` (who is on shift). Couriers' push subscriptions are `push.PushSubscription` (app `deliveries`).
- `push`: `PushSubscription(user or none for a guest, app = store|deliveries, endpoint, keys, device, language,
  order_updates, event_updates, seen_at, last_success_at, failures)`, `VapidKeys` (one row, made on first use; the
  private key encrypted), `NotificationSwitch(kind, enabled)` (the admin's Notifications page). See §6.14.
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
one discount per line and one on the order (after line discounts), then tax (Store info's rate, default 0). It is used by:

- the register's live quote (`register/quote`, lenient: a discount that stops applying is reported, not fatal),
- the online store's quote (`cart-quote`, coupons only),
- `create_order()` (strict), which also takes payments, counts coupon uses (`max_uses`, row-locked), records
  `OrderItem`/`OrderDiscount`/`OrderPayment`, takes stock and charges gift cards. Register payments must add up to the
  total exactly, cash needs an open drawer, and `expected_total` refuses the order if the price changed under the
  cashier. Any `OrderError` rolls everything back.

`Discount`: an *offer* (no code, picked by the cashier on the register), an *automatic* discount (no code,
`automatic`: `price_cart` applies the one saving the most by itself, as `auto_discount`; with an order discount too
only if it `stacks`, else the bigger saving wins and the other gets `order_discount_error`; `audience` everyone,
signed-in customers or staff, which needs a staff card on the order) or a *coupon* (code; `channels` says where it
works). Conditions: `starts_on`/`ends_on`, `weekdays` ("12345"), `start_time`/`end_time`, `min_subtotal`,
`min_items`, eligible `dishes`/`combos` (item discounts), `max_uses`. Typed-in ("manual") discounts need the
"Give discounts" action (`act.discount`: Managers have it by default, so `seed_demo`'s Priya does; Front of house
doesn't). The old `pos_server.apply_manual_discount` permission was dropped on 2026-10-22. Cashiers without it can
still type one in and press **Ask a manager**: anyone with the action and a register PIN picks their name and types
it (`register/approve`, `pos_server/approvals.py`, rate-limited like the lock screen). The server signs an approval
for that discount (kind and value), for that cashier, good for 3 hours; the register sends it inside the manual spec
(`{"manual": {kind, value, approval}}`), pricing accepts it (`price_cart(approve=…)`), and the order records the
approver on `OrderDiscount.approved_by` (shown on the admin's order page).

**Staff cards** at the register: the order panel's Staff card button opens a sheet to scan (camera or reader) or type
a card's number. The register sends `staffCards: [numbers]` with every quote; the quote answers with each card (type,
holder, balance and what a balance card can pay, or why it can't be used, and then it's taken off) and the discount
card's `staffDiscount`. A card with "own order" puts the holder's name on the order. A balance card is a pay method
("Staff", picked first when there is one) paying up to what it covers; place_order passes its payments as
`staff_card_amount`. **Online**, a signed-in staff member's cards that work for the channel apply by themselves
(`staff_cards.cards_for`, used by `online_store/cart.py: priced` and `pages._staff_cover`): the discount card's
discount, then the balance card paying after any gift cards, each shown as a line at checkout.

### 6.2 Register, lock screen and cash drawer (`pos_server/register.py`)

Templates `register.html`, `drawer.html`, `lock.html` on `staff_layout.html` (nav rail); CSS `staff.css` plus one
file per screen; JS `register.js`, `drawer.js`, `lock.js` with the shared `staff-ui.js`. Designs: `docs/mockups/`.
**History** (`/restaurant/history`, `history.py` + `history.html`, design: History; on the rail after Orders): past
orders by Today / Yesterday / This week and All / Paid / Refunds / Voided, searched by order #, name or card, 8 to a
page, and the picked order's receipt with Print receipt (`history/<id>/print` logs it and opens the printable receipt),
Send (email; text messages aren't set up) and Refund (whole order or picked items, a reason, back to the card or cash
from the drawer). It reuses the admin's Orders data (`admin_portal/orders.py`) and `pos_server/refunds.py`; refunding
needs `act.refund`, and an order with nothing refundable (only gift cards sold) offers no refund. Server-rendered with
live-page.js; sheets open from the address.
**The Orders dot**: the kitchen screen (`order-marking.js`) keeps, in the device's localStorage
(`pavilion.ordersWatch`), the stations it shows and the newest order it has shown. On that device, every screen with
the rail (`staff-page.js`) opens Orders on those stations and, on each `orders` nudge, asks `orders/attention`
(`views.orders_attention`: newest active order and how many wait for a station to accept them) and shows an amber
dot when there's an order newer than the last one seen there, or one waiting for approval. No kitchen screen opened
on the device yet: no dot.
Every rail ends with `rail_foot.html`: Apps, Lock (register screens only), a divider and the clock (ticked by
`staff-page.js`, which every staff screen loads). The **app switcher** (`/apps/`, `app_switcher`, design
`docs/mockups/App switcher-html`) is staff-only and almost query-free (session, user, and the device's name): its
"Today on this device" numbers (sales and orders rung up, KDS tickets moved, time since the first screen) live in the
tab's `sessionStorage` (`device-stats.js`, reset daily), bumped by the register and the KDS.
In portrait the rail is a row along the bottom (`staff.css`) and each screen stacks its panels: the register's menu
above the order, the drawer's and lock screen's panels top to bottom, the KDS header above the list. On phones
(portrait, 640px wide or less) the rail drops its logo and clock and its items share the row, sheets rise from the
bottom edge, and the register's order folds into a bar above the rail (count, total; `register.js: renderCartBar`)
that opens the order in the menu's place; paying keeps it open, and a finished order folds it again. Landscape phones
(560px tall or less) keep the side-by-side layout with a narrow rail, and the order panel scrolls as a whole.

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
5. **The store's Square account** (October 2026): connected with OAuth on the admin's Payments page (Settings). Pavilion
   is one Square app (`SQUARE_APPLICATION_ID`/`SECRET`); "Connect with Square" sends the manager to Square's authorize page
   with a `state` kept in the session; Square returns to `/square/oauth/callback` (no language prefix), which swaps the code
   for tokens (`payments/square_oauth.py`), stored encrypted on `SquareConnection` with the business name and the location
   payments go to. Every call goes through `square.square_client`, built for that account each time; access tokens (30 days)
   are renewed with a week left and daily (`square_refresh`); Disconnect revokes them. Without a connection,
   `SQUARE_ACCESS_TOKEN`/`SQUARE_LOCATION_ID` are used (development).
6. **Terminals** (October 2026): each register pairs its own from its Screen sheet ("Card terminal": a device code to type
   on the Terminal; Square's `device.code.paired` webhook or Check finishes it). A card payment is a `TerminalCheckout` row;
   `terminal.checkout.updated` updates it and publishes `checkout`; the register asks `register/card/<id>` for its own
   ("Check with Square" asks Square directly when no webhook arrives; Cancel cancels on the Terminal). Placing the order
   claims the checkout once. In Square's sandbox nothing can be paired: the sheet offers Square's test device ids instead
   (approved, approved with a tip, Interac, buyer cancels, times out, offline; approvals up to $25).
   **No Terminal: the customer's phone.** A register with no Terminal of its own shows "Show QR code" for
   card: a `TerminalCheckout` of kind `phone` (amount, a summary of the lines, a link token). The QR code opens
   `/payments/pay/<link>` with the store's look, what's being paid and the card form; `square.charge_phone` charges the
   register's amount once (the checkout id is Square's idempotency key) and publishes `checkout`. Cancel closes the link;
   links last 15 minutes. The card form (`_card_form.html`, `card-form.js`, `card-form.css`) also replaced the old
   "Pay by card" page in the store's sheet; Square's script follows `SQUARE_ENVIRONMENT`, not DEBUG.
   `pair-terminal` (superuser) lists every Terminal and its register. Before this, one "active"
   Terminal took every register's payments and the checkout lived under two global cache keys.

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

### 6.5 Online ordering (`/store/`)

The store is being moved onto **themes** (CLAUDE.md "Store themes"; design: the canvas's `Store*` artboards, theme
"Neighbourhood"). Done:

- **Home** (`index`): hero, menus on now and coming up from the menu schedule, this week's best sellers, a catering
  teaser from `CateringSettings`, three online services, visit us with this week's hours (with each day's events) from `events/hours.py`; the
  theme's header (open-now banner, the signed-in customer's order in progress, cart count) and footer.
  Avatars on customer pages (store header, tab bar, account) are `storefront.photo(user)` over the initials: the
  photo uploaded on the account's Profile (`Customer.profile_picture`), else the staff photo, else their Gravatar
  (`d=blank`: transparent when they have none, so the initials show).
  On phones (≤760px) the header keeps only the logo, name (wraps, never cut off), language and order-in-progress pill:
  Home, Order, Gift cards and Profile/Sign in sit in a tab bar along the bottom (`_dock.html`), with a "View your order"
  bar above it while the cart has something (`_cartbar.html`, a region; `cart.html` leaves it out). The row of links under
  the header is tablets only (phones reach Catering and Events & services from the footer).
- **Menu** (`online_store/pages.py: menu_page`): menus live now or starting within a day as tabs, sections by
  station, the cart beside them, and the dish sheet (`?dish=` / `?combo=`): a form built from `pricing.DishKit`
  (substitutes, modifier groups, leave off / extra for adjustable parts, condiments on the side, a note) that
  re-prices as it changes and adds a line to the **session cart** (`online_store/cart.py`; the old IndexedDB cart is
  only used by the pages still on the old layout).
- **Catering** (`/catering/` and `/catering/<token>`, see §6.12): build a quote and send it with a card on file;
  then the request page with its versions and messages.
- **Time estimates** (`pos_server/estimates.py`): the store's "ready in about N min" (`storefront.pickup_minutes`)
  is the median `prep_time` of the last 14 days; delivery (`storefront.delivery_minutes`, shown as N to N+10 min)
  adds the median drive of the last 30 days' deliveries (picked up → at the door). Anything over 2 hours is left out
  (a ticket nobody closed), both are rounded to 5 minutes, 20 each without history, and cached 10 minutes. The
  courier app's kitchen countdown uses the same median.
- **Checkout** (`/cart`, `checkout`): quantities, "goes well with", then **Your details** (name, phone, email),
  pickup (take out or eat here: `state["eat"]`, `to_go_order` and packaging follow it; eat here only while the
  dining room, the `dine` channel, is open then, `_dine_ok`) or delivery (a signed-in customer picks a saved address, the default
  first, or "A new address": the fields, and a switch on by default to save it under a name, `_save_address` once
  the order is placed; guests just get the fields; a driver note either way), as soon as possible (while an event is open) or scheduled up to
  14 days ahead inside an open event and while a menu with every dish in the cart is live (`_schedule`), promo or
  gift-card code (a signed-in customer's own gift cards, sent to their email, are used by themselves; Remove keeps
  one off in `cart["skipped_gifts"]` until its code is typed again), tip (`Order.tip`, added after fees), and Place order: gift cards first, then a saved card or
  Square's card form in a sheet (`payments/web-payment-window`, authorize-only; `checkout.js` places the order
  once paid). Orders start pending like before. **Guests whose email has an account** (`pages.account_for`;
  `checkout.js` posts the form when the email field loses focus) are offered sign-in (`/account/sign-in?email=&next=`,
  the email filled in); the cart and checkout choices are in the session, so they survive it, and `_state` drops
  the card and saved address picked by whoever was signed in before (`state["who"]`).
- **The placed order** (`order-status/<id>`, `order_page`): steps, ETA, where, "need a change", receipt; follows
  the kitchen over SSE.

- **Sign in and join** (`/account/sign-in`, `/account/join`, `online_store/account.py`, drawn by the theme): email
  (or username) and password; Apple/Google buttons are shown but not set up, email codes come later. Staff still
  use `users/login`.
- **The account** (`/account/`, Pavilion's own look like the mockup, not the theme): orders (the one in progress,
  reorder into the cart from each item's saved selection, a printable receipt), catering requests (waiting ones
  first-class, linked by customer or email), profile and wallet (`users.Customer`: photo, phone, company, order texts,
  catering emails, newsletter; `users.SavedCard`: Square cards on file, added through `payments/save-card`, a
  default, removed with `square.disable_card`; `users.SavedAddress`; gift cards sent to their email, used
  automatically at checkout). Checkout offers saved cards (authorized like the card form's payments, by
  `pages._authorize_saved`) and saved addresses; the catering send sheet offers saved cards. A new card paid at
  checkout is kept when "Save this card for next time" is ticked (signed in only, on by default): the card form gets
  `save_card`, and `process-web-payment` saves it on the customer's Square customer (`square.save_card`, created
  with `create_customer` if they have none) and charges the saved card (`views._save_and_charge`) instead of the
  one-time token. Card numbers stay in Square; `SavedCard` keeps the ids, brand, last 4 and expiry, and the same
  card saved again replaces the old one (`account.save_customer_card`, the old one disabled in Square).
- **The staff side of the account** (`online_store/work.py`, StoreAccount's staff views; only `is_staff` people see
  the "Work" group in the side panel). The profile adds their emergency contact (name, relationship, phone; their
  phone is `Staff.phone`), and a Work card with their roles and staff cards; the company field is hidden.
  **Shifts**: the next shift, hours this week, upcoming published shifts with Give away / Swap (a dialog from
  `?give=` / `?swap=&who=`: give lists who sees it; swap picks a coworker who can work it, then one of their shifts
  or "just hand it over"), the state of each (up for grabs, swap pending, waiting for a manager, picked up) with Take
  it back / Cancel swap, pending time off, and the time clock's last two weeks (late over 2 minutes).
  **Up for grabs**: what coworkers asked of you (Decline / Take it or Swap), open shifts and shifts given away, each
  with why it may not suit (needs a role, overlaps, outside your availability, back to back); "Only shifts I can
  work" hides other roles. The side panel's badge counts what you could take plus asks. **Availability and time
  off**: the week in effect, their requests (withdraw while waiting) and a new request: time off (dates, all day or
  times, a reason, a note; `account-work.js` shows the shifts it touches) or a new week of availability (one window
  a day, from a date, optionally until one). The moves are `team/swaps.py`: a given-away shift stays theirs until
  grabbed; reassigned shifts keep `published_as` in step so they don't wait for Publish; emails `shift_swap`,
  `shift_taken`, `shift_swap_decided`. Shift, ShiftOffer, TimeOff and Availability saves publish `schedule`.

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

Travel times (`deliveries/routes.py`, since 2026-10-26 instead of Google's paid Distance Matrix): the store's address
and each delivery address are turned into map points by a Nominatim server (cached 30 days per address), then a Valhalla
server's matrix (`/sources_to_targets`, costing auto / bicycle / pedestrian) gives one request per mode for all new
deliveries. Results are stored on the `Delivery` (`distance_m`, `travel_seconds`) so each is looked up once; a failed
lookup waits 10 minutes. Both servers are set on Store info (`VALHALLA_URL`/`GEOCODER_URL` fill in); docker-compose.yml's
`valhalla` and `nominatim` services start with the stack and build from an OpenStreetMap extract (`OSM_EXTRACT`, British Columbia by default;
the first start builds for a while). OpenStreetMap's public Nominatim may be used within its policy
(https://operations.osmfoundation.org/policies/nominatim/): Pavilion names itself, sends the store's email, keeps to one
lookup a second across workers and at most 3 per page, and caches every answer. Without the servers the app shows "–".
Directions still open Google Maps as a plain link (no API). The store's name names the restaurant in the app. The old
public `eta` endpoint (the old store's checkout estimate) is gone. The order detail's header is a drawing of streets, not a real map.

### 6.7 Gift cards (`/gift-cards/`)

- `get-card`: pick a preset or personal design (images listed from storage under `gift-cards/{presets,personal}`),
  pay through the same popup. On POST the card is created **only from a paid, unused transaction, for the amount paid**,
  then emailed via listmonk (an email failure is logged; the confirmation page still shows the card link).
- `card/<number>` renders a code128 barcode. `card-api/<number>`: GET is public (balance, image; email for staff only),
  POST charges and needs staff + CSRF.

### 6.8 Device registration

October 2026: the setup page (`/restaurant/check-device`) is redesigned like the sign-in page ("Which device is this?":
someone with `admin.devices.edit` names it or picks an existing one, or a device code is pasted; a browser that remembers
its token reconnects by itself). `?device-token=<token>` works on any link (`DeviceTokenMiddleware`: sets the cookie, then
reloads without it), for kiosks that wipe cookies. The admin's Devices page (Settings) lists devices with their code and
kiosk links, adds, renames, gives a new code (old links stop) and removes them. `EligibleDevice.token` is unique and
`created_at` records when it was added.

POS/KDS views use `device_required`: when `ENFORCE_DEVICE_CHECK` is on (default in production), the browser needs a
signed HttpOnly `pavilion_device` cookie for a registered `EligibleDevice`. Unregistered browsers are redirected to
`check-device`, where a superuser registers them; a browser that already has the token in `localStorage` re-sends it
automatically and gets the cookie back. `seed_demo` creates a demo device; open any POS page with
`?device-token=00000000-0000-4000-8000-000000000001` once to register a dev browser.

### 6.9 Admin

`admin_portal` (`/admin/`) replaced `new_admin` on 2026-10-20. Each view is wrapped in
`admin_portal/access.py: admin_page(page, edit=…, delete=…)`: people need the Admin app and the page's view to open
it (otherwise they're sent to the first page they can see), and the ops that write need edit or delete (refused ones
come back with a message). The rail only lists pages they can view; the layout tags view-only pages and hides the
buttons they can't use. It's rebuilt from the Claude
Design canvas's `Admin*` artboards, one page at a time, in the rail's order (`AdminRail`; groups and pages in
`admin_portal/nav.py`: a page shows on the rail once its url name is filled in). Pages are Django templates on
`admin_portal/layout.html` (the rail, `<main id="page">`, Django-message toasts) kept current by `live-page.js`;
shared styles in `admin_portal/static/admin_portal/admin.css`, one CSS file per page next to it.

- `StoreSettings` (one row, `StoreSettings.load()`, cached in the shared cache and cleared on save): the store's name,
  shown at the top of the rail with "Pavilion" under it, and everything on **Store info** (Settings, `store_info.py`):
  details (name, phone, email, address, city, map link; the Online store page drafts the same ones and Store info keeps
  that draft in step), the tax rate, the time zone (applied when Pavilion starts, like languages; the page restarts it),
  the music library's space, the Valhalla and Nominatim servers for travel times, and the weatherapi.com key (encrypted
  with `payments/secrets.py`; `VALHALLA_URL`/`GEOCODER_URL`/`WEATHER_API_KEY` only fill in). Migration `admin_portal 0007` copied `TAX_RATE`,
  `MUSIC_STORAGE_GB` and `RESTAURANT_NAME/ADDRESS` from the environment once; those settings are gone.
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
  the edited copy as a new dish, Archive takes it off every menu. New (`<page>/new`, a link) opens the same editor
  on an unsaved object with the old starting values (`new_dish()`, `new_combo()`... in each page's module) and
  posts back to `/new`; nothing is created until Save, and Duplicate, Archive and Delete wait until it's saved
  (`views._saved_only`). Every editor (menus, dishes, combos, recipes, services, components, ingredients,
  condiments, disposables, restocks) works this way; a new restock takes its receipt once it's saved. Sections: basics and photo (previewed in the
  browser, sent with Save), catering price breaks with warnings, the recipe (amounts in the smallest unit,
  adjustable, cost), modifier groups (pick one/any, required), availability, menus, allergens and diets from the
  ingredients, and the last 7 days (sold includes combos; sales and profit count it sold on its own).
  `live-page.js`: while you type in a region only its `[data-calc]` parts are swapped; a form with `data-queue`
  (and `data-search`) sends its last submit after one in flight instead of dropping it; `data-keep` regions are left
  alone by background answers.
- **Combos** (`admin_combos`, `combos.py`): 30-day KPIs (sold, average saving against the default picks bought
  separately, upcharge revenue, best seller), All/Active/Hidden(/Archived) filters, search by combo or dish, rows
  with choices, price, saving, sold and % upgraded. The editor is one form like the dish editor: a photo
  (`Combo.image`, saved with Save like a dish's; menu boards and the store's combo sheet show it, the sheet falling
  back to the first default pick's photo), choices with picks
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
  (GST, GST + PST) became "store tax rate" or "no tax", since the store has one rate (Store info).
- **Components** (`admin_components`, `components.py`, 2026-10-21): KPIs (count, stock value at cost, under par, extras
  sold in 30 days with margin), filters (sold as extras, under par, no recipe yet, archived) and rows grouped by
  category with unit cost, the extra's price and margin, on hand against par (bar with a par mark) and dishes using it.
  The editor (one form with Save) has name, unit (g/ml/ea) and category; the prep recipe (pick one; its batch with
  costs, or the per-unit ingredients; a warning when there are neither); stock (where it's kept, how long it
  keeps, on hand; the components its transformations connect to are listed, linked, since each storage state is its
  own component; **Add state** adds a row (name, kept in, keeps for, on hand) that Save turns into a component of its
  own, "Beef patty, frozen", with a `Transformation` each way, "Beef patty · to frozen" and "· back from frozen", the
  scene and a placeholder time guessed from the name, for recipes to attach to (`components.add_state`); par with a bar); a dark cost card with allergens; "Sell as an extra" (label, price, most extras per
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

- **Online store** (was Branding, renamed 2026-10-24 for a separate Branding page to come; still `admin_branding`, `branding.py`, page id `branding`): theme cards, the theme's settings (from its JSON) plus
  the store details (name, logo, address, phone, map link), with contrast warnings, and the real store in a scaled
  iframe (desktop 1440 or phone 390) showing the draft. Every change is kept as a draft at once; Publish copies it
  live (and the store details to `StoreSettings`); "Reset to theme defaults" resets the draft.
- **Store hours** (`admin_hours`, `hours.py`, 2026-10-21): the week with switches, periods (breaks between them),
  "Copy to…" and per-day checks; the week at a glance (open hours, and pickup and delivery windows after cutoffs, with
  a now line); "Right now" (open, kitchen last call, pickup and
  delivery windows, what the store shows today); pause online orders (15/30/60 min or the rest of the day, acts at
  once; the store shows "Back soon"); order cutoffs (around breaks or not) and ordering ahead (days, soonest pickup).
  One form with Save; the draft drives the chart and "Right now". Holidays and one-off changes are Events.
- **Stations** (`admin_stations`, `stations.py`, 2026-10-22): the list in ticket order (arrows reorder) and the
  selected station: name, ticket code, icon (the 18 drawings in `pos_server/station_icons.py`, also drawn on the order
  marking screen), colour (taken ones ringed), target ticket time (the order marking screen's timers turn amber at
  the strictest involved station's target and red at double), taking tickets (off makes its dishes sold out), its
  dishes, a ticket preview, the last 30 days (tickets, average and red share from `OrderStation` confirmed → ready)
  and archiving (its dishes move to another station). One form with Save; archived stations leave every list.
  There's no ticket printing, so the mockup's printer picker isn't there.
- **Events** (`admin_events`, `events.py`, 2026-10-22): a month calendar over the usual hours (an empty day adds an
  event: a closure if the store is usually open, else an opening; a day with events steps through them), what's coming
  up (120 days), and the selected event: open for an event or close the store, name and what customers see, dates and
  times (moving the start keeps the length), what changes on its next three days against the usual hours, overlap
  warnings, the channels it opens or closes (the same dine-in/pickup/delivery toggles), and the online store's banner (from 1 day to 2 weeks before;
  `storefront.event_banner`). One form with Save; the old `Event` rows were converted (migration `0003_event_layers`).
  Saving a new event that hasn't ended emails customers about it (Email page, Events group; `events/announce.py`).
  `static/js/live-page.js` keeps scrolled boxes in a swapped region where they were (by id, or classes and position).
- **Screens** (`admin_screens`, `screens.py`, 2026-10-22): cards with a thumbnail of each layout (`boxes()` lays
  the tree out like the CSS grid), "New screen" (name and landscape or portrait), and the editor: the draft lives in
  the session (`screen_edit_<id>`, with a 40-step undo); the preview is the real screen rendered from the draft in an
  iframe (`admin_screen_preview`, `render_screen(editing=...)` marks every node with its path, empty slots invite a
  widget; `screens/static/screens/preview.js` tells the editor what was picked or dropped, `admin_portal/screens.js`
  posts it as an op); the layout tree and breadcrumbs; the panel shows the screen's settings (name, orientation,
  background, public link), the widget library (an empty slot; click or drag), rows/columns (count, weights, gap,
  turn around) or the widget's settings from its JSON params (text, choices, steppers, sliders, toggles, colours,
  uploads, menus and dishes to pick). Save validates and publishes "screens" so open screens reload.
  The Pop-ups tab lists the screen's pop-ups (`Screen.popups`): each opens to its settings, Preview shows it in the
  preview with sample data (`popups.SAMPLES`), and "Add a pop-up" lists the popup-kind widgets (one of each).
  Screen settings also turn on sessions and list the screen's sessions ("Showing on": who paired, last seen, the
  code; removing one sends its display back to the session list). Apps → Screens (`screens/views.py: index`) opens a
  screen, or for one with sessions its session picker (`sessions`: open one, or start a named one). The register's
  Screens sheet pairs with a session by code and sends staff announcements; while paying it posts the order to its
  session (`summary`), which the `order_summary` pop-up shows on that display only.
  The Audio tab (visual only for now) lists the screen's audio channels (`Screen.audio`, music or voice, top one
  wins): name, order, type, source, the level it drops to while a channel above plays, what uses it, and a "How they
  mix" chart; pop-ups' "Chime on"/"Play on" and Now playing pick from them. Playback comes with the audio backend.
- **Audio channels** (`admin_audio`, `audio.py`, 2026-10-22): the store's music and voice channels filtered by type,
  and the selected one: name, type, notes, the screens that use it (role and priority, from `Screen.audio`) and
  archiving (saving a type change or an archive takes it off those screens). A voice channel links to its
  page in the Audio app.
- **Staff** (`admin_staff`, `staff.py`, 2026-10-22): staff accounts (search by name, email or phone; filter by
  role; a warning on anyone without an emergency contact) and the picked profile: photo (saved straight away), name,
  email, phone, roles (`users.Role`), groups (Django `Group`s, `users.GroupInfo`), emergency contact, can sign in
  (`is_active`), staff since, last signed in (password or register PIN), and remove from staff (`is_staff` off; the
  account and its orders stay). One form with Save (the draft lives in the session, `admin_staff`); only edited
  profiles are checked (first name, a valid email no other account uses). Owners: members are superusers, the last
  active owner can't leave the group or be removed, and nobody can turn off or remove themselves. **Add staff**
  promotes an existing account or makes a new one (username = email, no password) and can email an invite
  (`staff_invite`) to `users/welcome/<uid>/<token>`, where they set a password (Django's reset token: one use, a few
  days).
- **Shifts** (`admin_shifts`, `shifts.py`, 2026-10-22): a week strip (shifts, hours and conflicts or open shifts
  per day) and one day's timeline, by staff or by role: open shifts on top, each person's shifts as blocks (dashed:
  open; ringed amber: unpublished; striped red: a conflict), their unavailability and time off hatched, hours this
  week (red over 40), the store's open hours and a now line. Coverage compares each hour with the pars (short hours
  link to a new shift). The drawer (`?shift=<id>` or `?new=1&u&role&start`) edits who, role, times, unpaid break,
  note and copies on other days, with the problems (time off, unavailable, overlaps, not their role, no meal break)
  worked out as you change it (op=preview). Shifts are saved as they're made, dragged (`shifts.js`: op=move) or
  deleted (a published one is kept as `deleted` until Publish); **Publish** makes them what staff see and emails
  everyone whose schedule changed.
- **Shift pars** (`admin_shift_pars`, `pars.py`): the weekly pars and seasonal ones (dates, weekdays, add on top or
  replace), each day's roles as an hour strip and time windows with a people stepper, copy a day to others. One form
  with Save (session draft).
- **Availability requests** (`admin_requests`, `requests.py`, `team/requests.py`): an inbox of time off and
  availability changes by status (Pending, the rail's badge; Approved; Declined; All) and kind, and the picked one:
  what's asked (dates, part day, or the week before and after for availability), their note, the shifts it clashes
  with, things to weigh (others off then, a seasonal par, short notice, a role nobody else covers), and Approve
  (moving the clashing shifts to open shifts, or leaving them as conflicts) or Decline, with a message they're
  emailed. Decided ones can be reopened unless they're in the past. Staff ask from their account. Swaps and
  hand-offs a coworker said yes to wait here too (kind "Swaps", keys `s<id>`; not in the mockup): who gives what to
  whom, any problem since, and Approve (the shifts move) or Decline, with a message to both; they can't be reopened.
- **Roles** (`admin_roles`, `roles.py`): the jobs people do (`users.Role`) with who has each, and the picked one's
  name, description, icon (`team.py: ROLE_ICONS`), colour, people (the same as ticking it on a Staff profile),
  starting groups (`Role.groups`: given to whoever gets the role, here or on the Staff page) and archiving (it comes
  off every profile; groups stay; archived roles stay on old shifts). One form with Save (session draft).
- **Coupons** (`admin_coupons`, `coupons.py`, 2026-10-23): discounts with a code. Tabs All, Active (paused too),
  Upcoming, Ended (used up too); each coupon's code (Generate makes an 8-letter one), what customers see, accepting it
  (paused), the discount (percent with an optional cap, or an amount), what it applies to (entire order, specific
  items from the stations' dishes and combos, specific menus, or the delivery fee, which locks it to delivery
  orders), conditions (minimum subtotal, order types, start and end dates), limits (total uses, per customer, first
  order only; those need a signed-in customer, so not the register), a checkout preview of a sample order (pickup or
  delivery) and its performance (uses, discount given, orders total). One form with Save (session draft). The
  delivery fee it needs is set on Store hours, by the delivery cutoffs.
- **Email** (`admin_email`, `email_settings.py`, 2026-10-23, Settings): connect listmonk (URL, API user and key, an optional
  From address; "Test and connect" checks it can read lists and templates), its facts, a test email, reload and
  disconnect, and "Add Pavilion’s templates" while some are missing (`misc_tools/listmonk.py: install`: each email ×
  each language that's on, a newsletter campaign template per language, and the "<store> customers" list). Emails by
  group (Accounts, Orders, Catering, Gift cards, Team): on/off (account emails are always on), the listmonk template
  per language (a template staff pick themselves is theirs: Reset to default makes Pavilion's again and never
  rewrites theirs), the data each template gets, and Reset to default. The newsletter list is synced when a
  customer's "Monthly menu email" changes (`users.models.newsletter_list`) or with "Update the list now".
  New emails with this page: ready for pickup (`pos_server/order_emails.py`, when the kitchen finishes an online
  pickup order), refund issued, a new catering request to the staff who answer catering, and the buyer's gift card
  receipt.
  **Events group (marketing, 2026-10-30)**: "Extra hours" (`event_open`) and "We’re closed" (`event_closed`) are
  listmonk *campaigns*, not transactional email (`Kind.campaign`). Saving a new event on the Events page that hasn't
  ended sends one (`events/announce.py: announce_later`, in the background after the save) to the customers list and
  the other listmonk lists ticked for that email on this page (`EmailSwitch.lists`; the users list is never offered).
  A campaign has one template for everyone, so each is one campaign template holding every language that's on
  (`EmailTemplate.ALL`): `{% bylang %}` (`templatetags/listmonk.py`) writes `{{ if eq (or .Subscriber.Attribs.language
  "en") "uk" }}…` branches, and the subject and Pavilion's content (`event_content.html`: the name, a date tile, times,
  the channels, a button to the menu or the hours) do the same. People with an account get their language, everyone
  else the store's default. The look (`_campaign.html`, palettes in `email_templates.LOOKS`) is tables and inline
  styles. The API user also needs to manage campaigns.
- **Languages** (`admin_languages`, `languages.py`, 2026-10-23, Settings): every supported language with how much of
  it is translated (from its .po file), on/off switches (the default stays on), search and an All/On filter, the
  default language, "match each person's device" and a preview of the picker. Saved values apply after a restart:
  the page shows a "Restart now" card (`languages.restart`: SIGHUP to uvicorn's worker supervisor, or touching the
  module under the reloading dev server; otherwise it says to restart by hand) and reloads itself once
  `languages/status` says the worker answering runs the saved languages. The restart is noted in the cache for two
  minutes (`languages.restarting`), so a worker that hasn't restarted yet shows "Restarting…" again. uvicorn swaps its
  workers one at a time and an old one waits for its open connections, which SSE streams never close: the Dockerfile's
  `--timeout-graceful-shutdown 5` cuts them (and `--timeout-worker-healthcheck 60` gives a new worker time to start).
- **Gift card designs** (`admin_gift_designs`, `giftdesigns.py`, 2026-10-23): the designs in the order buyers see
  them (up and down), each one's picture (kept as soon as it's picked), store name on or off, light or dark text, name,
  default message, amounts and the buyer's own range, where it's sold (online store, register) and when (all year or a
  season repeating every year), how it shows online and on the register, and its sales. One form with Save. Deleting
  one that sold cards archives it. They're sold on the register's Gift cards tab (tiles per amount, "Other amount";
  `register.with_gift_cards`) and the store's `/gift-cards` page (`online_store/pages.py: gift_cards_page`).
- **Gift cards** (`admin_gift_cards`, `giftcards.py`, 2026-10-23): stats (outstanding balance, active and frozen
  cards, sold or issued in 30 days, empty ones waiting to be deleted), the list (With balance, Empty, All; searched by
  last 4, name or email) and the picked card (its look, facts, message, Resend email: last 4 and a signed balance link,
  Freeze, Adjust balance with a reason, History). Issue gift card: design, amount (the design's or another whole
  amount up to $1,000), recipient, email (a matching customer account is named), message, internal note, and email it
  or show the code; the last step shows the number once (only in that response; `giftcards.js` copies and prints it,
  #gc-modal is data-keep until Done). Actions happen straight away (no Save); the page has view and edit levels.
  New email kinds: `gift_card_issued`, `gift_card_balance`.
- **Discounts** (`admin_discounts`, `discounts.py`, 2026-10-23): discounts without a code. Same list as Coupons
  (status Off, Scheduled, On now, Active, Ended) and the same discount, applies-to and preview parts (imported from
  `coupons.py`), plus how it's given (applies by itself, or the cashier picks it: then in store only and never off the
  delivery fee), a badge ("20% off" on its dishes in the store's menu and the Showcase menu board while it runs:
  `pricing.badges`/`badge_for`; boards only refresh on their usual nudges, not when a happy hour starts), when it runs
  (days with presets, all day or set hours, dates), who gets it (everyone, signed-in customers, staff with a staff
  card) and working with coupons. Performance is the last 90 days. One form with Save (session draft). Offers the
  register already used keep their item/order scope.
- **Customers** (`admin_customers`, `customers.py`, 2026-10-23): store accounts (users who aren't staff). The list
  (search name, email or phone; sort by last order, name, most orders, top spend, newest; filters Ordered this month,
  No orders, Gets emails, Signed out) and the picked customer: name, email, phone, staff notes, their stats (orders,
  lifetime spend after refunds, average, last order) and five recent orders (with what they usually get), cards from
  Square (refresh with `square.list_cards`, remove with a second press: `square.disable_card`), delivery addresses
  (label, street, note, default), can sign in and marketing emails, and account facts. One form with Save (session
  draft); a changed email isn't switched on Save: `users/accounts.py` sends the new address a confirmation link and
  the old one a heads-up. Password reset and verification emails, card changes and Make staff act straight away.
  Store sign-up now sends a confirmation link; the Orders search matches an order's or customer's email.
- **Time clock** (`/team/clock`, `team/clock.py`, `team/clock_views.py`, 2026-10-22; no mockup, designed after the
  register's lock screen). The dark side shows the time, who's on the clock (on break dimmed) and who's due in the next
  three hours (late in red). The panel: who's clocking in (staff with a register PIN, those on the clock and on today's
  schedule first; or email and password), which lasts one visit (`session["clock"]`, two idle minutes, never a
  sign-in); then their clock (today's shifts, status, Clock in / Start unpaid break / End break / Clock out, today's
  punches and time worked), and a done screen that goes back by itself. Rules (`clock.check_in`): from an hour before a
  published shift until it ends is fine; earlier, or with no shift, a manager approves with their PIN (someone with
  "Approve clock-ins", `act.clock`; Managers have it). People who have it themselves (owners too) clock straight in,
recorded as approving their own. Off the schedule, a published Shift is added (their first
  role, from when they clock in, ending when they clock out; `TimeEntry.added_shift`). Early, the entry is tied to
  the upcoming shift. Clocking out adds the minutes to `Staff.minutes_worked`. Punches publish "clock" (registered
  devices may listen) so every clock's board updates. Clocking in refills "every shift" staff cards, and a card type
  can work "only while clocked in". The admin's Shifts page shows what happened next to the plan: a thin bar
  under each person's lane for their punches (breaks as gaps), "In 8:57" / "Late 12m" / "No-show" on the shift
  (no-show from 15 minutes after a published shift starts), the punches in the shift's drawer, and hours worked in
  the footer.
- **Staff cards** (`admin_staff_cards`, `staffcards.py`, 2026-10-22): card types (`gift_cards.StaffCardType`) with the
  cards issued (`StaffCard`). A type is a *balance* card (an amount that refills every day, week or month at midnight,
  resetting or carrying over up to a limit, or never) or a *discount* card (percent with a per-order cap, or an
  amount; every order, once a day or once a week), with rules: their own order (the order goes under the holder's
  name), no alcohol (dishes made with an `Ingredient.alcohol` ingredient aren't covered), works with other discounts
  (else the bigger saving wins) and where it works (in store, pickup, delivery). The type is one form with Save
  (session draft); issuing a card (a new number, printed from `staff-cards/<id>/print` with a Code 128 barcode, or a
  pre-printed blank scanned in) and revoking one happen straight away. Deleting a type archives it and revokes its
  cards. The side shows the card and what it does to a sample order, and this month's use. How cards work on orders
  is `gift_cards/staff_cards.py` (refills are worked out when a card is read; `price_cart(staff_cards=…)` takes a
  discount card's discount, `create_order(staff_cards=…, staff_card_amount=…)` takes a balance card's payment as an
  `OrderPayment` "staff" and records `StaffCardUse`s; rejected orders and refunds put money back). "Every shift"
  balances refill when they clock in, and "Only while clocked in" needs an open time clock entry.
- **Groups** (`admin_groups`, `groups.py`, 2026-10-22): what people can open and change. The list (members and how
  much each grants), and the picked group's name, description, members (added on the Staff page), Duplicate and
  Delete, its **Apps** and **Actions** (switches) and **Admin pages**: a grid of the rail's pages by section with View,
  Edit (add and change) and Delete where the page has them (`users/access.py: PAGE_LEVELS`); edit and delete bring
  view, taking view takes the rest; column, row and section buttons (None, View only, Everything); Find and Granted
  only filter it (the boxes stay in the form). Owners are locked (every permission; only the description changes).
  One form with Save (session draft); members get the change on their next request. `users` migration 0006 gave the
  usual groups defaults (Managers: everything but deleting staff and changing roles and groups; Front of house:
  register, order marking, drawer, availability, screens, audio, cash in and out, rejecting; Kitchen: order marking,
  Cookery, availability, screens, audio, rejecting; Delivery: courier and audio) and put staff who were in no group in
  Front of house, Kitchen and Delivery, so nobody lost an app they had.
- **Library** (`admin_library`, `library.py`, 2026-10-22): the songs (All songs, Missing info: no artist, album or
  artwork) and playlists, searched and sorted, with a storage bar (Store info's space for music, default 10 GB). The view is in
  the address (`?view=all|needs|p<id>&q&sort`, `song=<id>` opens a song's details drawer, `add=1` a playlist's Add
  songs); selecting songs shows a bar (add to a playlist, a new playlist with them, remove, delete). Playlists are
  renamed as you type and dragged into order. **Add music** (`_upload_music.html`, `library.js`) reads each file's
  tags in the browser (`audio/static/audio/tags.js`: ID3, MP4, FLAC/Ogg comments, RIFF INFO; length from an
  `<audio>` element), lets you check and fill them in, and uploads the file untouched with what was read
  (`library/upload` → `audio.library.add_track`): the server never opens a song.
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
  from `Menu.display` (`screens/menu_boards.py`: `clean()`, `board()`, `groups()`: a group is a station's dishes (and
  its one-station combos), the combos that mix stations, or one of the menu's own sections, `display["sections"]`,
  slot key `m:<id>`, named and filled with dishes/combos in the Display tab, for boards only). The Display tab's "On
  the board" switches (`display["descriptions"]`, `display["contents"]`) show or hide descriptions and what's in each
  combo (Poster and Departures never show descriptions). Every page and every slot screen is rendered; `menu_board.js` shows one at a time from
  the clock (so boards stay in step across refetches and screens). All ten `Menu*` artboards are built (2026-10-27),
  each a JSON + partial in `screens/templates/screens/menu_templates/` + CSS: Showcase (best sellers as cards),
  Chalkboard, Editorial, Poster (first group as tiles), Grid (numbered rows, sideways group names), all portrait;
  Letterboard (sold-out items move to the side panel), Triptych (a coloured panel per group with its first picture),
  Departures (split-flap tiles via the `cells` filter, a live clock and ticker), Carousel (featured + best sellers
  taking turns in the spotlight) and Neon, all landscape. Most give the first slot more room (`per_group` as a
  list; the Display tab's notes and timing checks go slot by slot). Vegetarian marks come from `dish_info`
  (`items()` prefetches `DISH_PREFETCH`). `seed_demo` adds a
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
  Every leaf is also a CSS size container, so widgets lay themselves out by their slot's shape (side by side in wide
  slots, stacked and centred in tall or square ones) and cap sizes with `cqw`/`cqh`.
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
the store's tax rate except no-tax services; deposits are held, never added to the total. **Deposits**: card holds last about
7 days, so `manage.py catering_deposits` (run daily by cron) holds them `hold_days` before the event and completes
past orders with nothing to settle; accepting an order whose hold day has come holds it at once. Cards whose ids
start with `demo:` (seed_demo's) are never sent to Square.

---

### 6.13 Audio (`/audio/`)

The store's music and voice channels (`audio.AudioChannel`, set up in the admin's Audio channels page) are played
by screens (`Screen.audio`, `screens/static/screens/audio-player.js`) and run by staff in the **Audio app** (any
staff member; designs `AudioRail`, `AudioChannels`, `AudioMusic`, `AudioVoice`; `audio/views.py`, `pages.py`):

- **All channels** (`/audio/?type=all|music|voice&q`): a card per channel: a music one's song, Playing/Paused/Idle
  and a play/pause button; a voice one's Live/Quiet and who talked last.
- **A music channel** (`music/<id>`): Now playing (artwork, seek, shuffle, previous, play/pause, next, repeat
  off/all/one, the volume every screen plays it at) and the queue (jump to a song, play next, remove, drag into
  order, clear); Browse (playlists and songs from the Library: tap plays now; ⋯ or a long press opens the sheet with
  Play now / Play next / Add to end of queue); a playlist (Play, Shuffle, Play next, Add to queue); a mini player.
  Every button is an op on the one `#au-ctl` form (`toggle`, `jump:<entry key>`, `playnext:p<playlist>`,
  `playnow:t<song>`...) handled by `music.control()`. The queue lives on the channel (`queue`, `cur`, `repeat`,
  `shuffle`, `volume`, `source`, `started_at`, `paused_at`); `now_playing()` walks the timeline when asked.
- **A voice channel** (`voice/<id>`): hold to talk (or the space bar), the status (clear, you're live, someone
  talking, a call playing), a waveform, a chime for everyone, and this device's speaker on/off and volume (it plays
  the channel too). Recent lists the last hour: who talked and for how long (voices aren't recorded), and order
  calls, which play again on this device only (`voice.recent()`, kept in the cache; `voice.talking()` is who's on).

### 6.14 Web push and the installable apps (`push/`, `online_store/app.py`)

Two installable apps, each with its own scope, manifest and service worker served from the top of its scope
(`push/templates/push/sw.js`: shows notifications, opens their page on a tap, re-posts a renewed subscription to
`push/renew`, which trusts only the old endpoint):

- **The store** (`/store/`): the manifest is written per request from the store's name and the live theme's `app`
  colours (`online_store/themes.py`); the icons are drawn with Pillow from the logo (or Pavilion's cloche, `pavilion/brand.py`) and cached.
  `/store/install` explains the app (what it does and doesn't), shows the install button when the browser offers one
  (`beforeinstallprompt`) and walks iPhone people through Share → Add to Home Screen. **Screenshots** for Chrome's
  richer install sheet are taken by a Gotenberg server (`screenshots` in docker-compose.yml; `SCREENSHOT_URL`,
  `SCREENSHOT_SITE`) after each Publish on the admin's Online store page, in the background (one run at a time, one more
  if published again meanwhile; `online_store/screenshots.py`): new files under `store_app/` in the media storage, then
  the old set is deleted. `StoreSettings.app_screenshots` lists them.
- **The courier app** (`/deliveries/`): its manifest's id is `/deliveries/` (the old UUID id matched a stale app record Chrome syncs
  between devices, so installing said "already installed" and opening failed); its service
  worker used to sit in `/static/` (where it never reached the pages) and now has an offline page. Its mark is the bag
  (`deliveries/static/deliveries/courier.svg`; PNG icons, maskable and a monochrome status-bar badge drawn by
  `pavilion/brand.py`), and its screenshots for Chrome's richer install sheet are static, the same for every store
  (`deliveries/static/deliveries/screenshots/`, taken from seed_demo's data with headless Chrome; `views.SCREENSHOTS`).

**Keys**: `push/vapid.py` makes a VAPID pair the first time it's needed and keeps it in `push.VapidKeys` (private key
encrypted with a key from SECRET_KEY); a deployment that had `VAPID_PRIVATE_KEY` keeps that key. **Sending**
(`push/send.py`): after the commit, on a background thread, each subscription in its own language. 404/410 delete the
subscription at once; three other refusals in a row delete it; 429/5xx are left for next time. Guest subscriptions not
seen for 180 days are dropped when anyone subscribes. Browsers post their subscription whenever a store page finds one
(`push/sync`: a guest's gets the account when they sign in on it; notifications turned off → `push/forget`).

**Order updates** (`online_store/order_push.py`): on every save of an online order or its delivery, its stage is worked
out (accepted when every station took it, ready (pickup only), on its way (courier picked it up), picked up, delivered)
and a stage past `Order.push_stage` is sent once (a conditional update claims it; `Order.save()` never writes
`push_stage`). Rejections (pre_delete with the `RejectedOrder`'s reason) and refunds send their own. Recipients: the
browsers linked to the order (`Order.push_subscriptions`) plus, for a customer's order, their store subscriptions with
`order_updates`. **The order page** shows a card (`online_store/static/online_store/push.js`): just after placing
(`?placed=1`) a sheet offers notifications; a browser that already has them links itself (unless order updates are off
for it); blocked notifications get a red explanation (browsers can't ask again); iPhones outside the Home Screen app
get a pointer to `/store/install`. **Signed-in customers** are asked with the browser's own question once a visit (a browser session) on any store or
account page until they answer: Chromium browsers on load, Safari and Firefox (which need a tap) on the first tap that
isn't a link or a field. An account's devices get all three kinds (order, event, news); a guest's only order updates
until the device gets an account, when the other two turn on. **Event updates** go to store subscriptions with `event_updates` when a new event is
saved (`events/announce.py: notify_later`). The admin's **Notifications** page (Settings) turns each kind on or off
(`push/kinds.py`, `NotificationSwitch`) and sends you a test; the account's **Notifications** page lists your devices with
their switches and Remove, marks "This device" and offers turning them on here.

**Push campaigns from listmonk** (`push/listmonk.py`): Pavilion is a listmonk [messenger](https://listmonk.app/docs/messengers/)
named `pavilion-push`. listmonk posts each campaign message (its postback JSON: subject, body, content_type, recipients
with their attributes, campaign) to `/push/listmonk` with Basic auth (user `pavilion`, a password Pavilion makes and
keeps encrypted, `StoreSettings.push_messenger_password`); only HTTP 200 counts as delivered, so a recipient without
devices still gets a 200. The subject is the title, the body the text (HTML turned into text), the first link where a
tap goes (with `@TrackLink`, listmonk counts the click). Recipients are matched by their attributes: `pavilion_id` and
`pavilion_push` (named for Pavilion: other apps on the same listmonk can't reach these devices with an attribute of their own), the ids of that account's store devices with `news_updates` on (`misc_tools/listmonk.py: sync_user` writes them;
a signal syncs whenever a device's news switch or account changes), and the messenger re-checks both. The Notifications
page's Push campaigns card sets it up in listmonk (its settings API: the messenger entry, passwords it shows as dots sent
back empty so they're kept; and a "Pavilion push" campaign template), shows the sign-in and makes a new password; the
"News from listmonk" switch turns it off. The messenger's address skips the per-address rate limit (`pavilion/throttle.py`)
and sends on a small pool (`push.send.pooled`). Guests have no subscriber, so campaigns only reach accounts.

**Who sees an order**: a customer's order only shows to them (signed in, any device); a guest's to the browser that
placed it (a signed cookie, `online_store/guest_orders.py`, a year, the last 20). Everyone else gets "This order isn't
yours to see" (403).

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
  live in one place. `static/js/live.js` (`onLiveUpdate(topic, refresh)`) adds a debounce, refetches on every (re)connect, and reopens the stream if the 15 s `ping` heartbeat stops. No screen polls. Scheduled orders join screens at their `start_time`, when nothing in the database changes: the heartbeat of open "orders" streams calls `pos_server.models.nudge_started_orders` (one stream per beat, via `cache.add`), which publishes "orders" once orders have started since the last look, so they appear within ~15 s without a reload.
- Redis is also the cache in production, so the Square checkout status is shared between workers. Couriers are in the
  `CourierShift` table.

The Copilot WebSockets branch is superseded by this.

**Rate limits (`pavilion/throttle.py`, October 2026).** Django runs a worker's synchronous views on one shared thread
under ASGI. On 2026-10-23 one register tapped a cart up to 99 of four dishes (about 400 taps; each pause sent a
quote and none was cancelled), and the queue of quotes stalled the KDS, the music and every other screen until it
drained. Since then: the register keeps one quote in flight (changes made meanwhile go in the next one) after a
250 ms pause and has a quantity pad (tap the number); `ThrottleMiddleware`, first in `MIDDLEWARE`, counts every
request (not `/events/`, static or media) per browser cookie and per address in the cache and answers a 429 from
the event loop, so turning a flood away costs no database work or time on the shared thread; `register/quote` (10
per 2 s per register) and the store's menu-sheet and checkout posts (20 per 10 s per visitor; placing an order is
never skipped) have their own caps. Measured on the dev server with ten threads flooding quotes from one session:
the KDS stayed up (about 360 ms against 100 ms idle; 820 ms with no throttle). A proxy in front (Caddy or nginx
`limit_req`) would turn floods away before Python at all.

---

## 8. Config, running, deploying

### Environment (`pavilion/.env`, see `pavilion/.env.example`)

`ENVIRONMENT` (production → Postgres, DEBUG off, Redis, S3 media, file logging), `SECRET_KEY` (required in production),
`APP_DOMAIN`, `EXTRA_HOSTS` (comma-separated, e.g. an ngrok domain), `DATABASE_URL` (a connection string; else `POSTGRES_DB/USER/PASSWORD`, `DB_HOST`, `DB_PORT`),
`REDIS_URL`, `RUSTFS_ENDPOINT_URL/ACCESS_KEY/SECRET_KEY/BUCKET/REGION/PUBLIC_URL/PUBLIC_PROTOCOL`,
`ENFORCE_DEVICE_CHECK`, `SQUARE_ACCESS_TOKEN/ENVIRONMENT/APPLICATION_ID/APPLICATION_SECRET/LOCATION_ID/WEBHOOK_SIGNATURE_KEY` (Terminals are paired per register; there's no store-wide `SQUARE_DEVICE_ID` any more),
`LISTMONK_URL/API_USER/API_TOKEN/FROM_EMAIL`, `VALHALLA_URL`, `GEOCODER_URL`, `WEATHER_API_KEY` (all only when Store info has none), `OSM_EXTRACT` (compose), `SCREENSHOT_URL`/`SCREENSHOT_SITE` (the store app's screenshots; compose sets both), `VAPID_PRIVATE_KEY` (only to keep an older deployment's key; Pavilion makes its own) and `VAPID_CONTACT_EMAIL`. The store's name, address, tax, time zone and music space are on the admin's Store info page, not here.
`BUGSINK_DSN` (error reporting; defaults to the project's public DSN, set empty to disable; not sent during tests) and
`BUGSINK_SEND_PII` (`true` attaches usernames, emails and IPs to error reports; default off).

### Scheduled jobs

- `manage.py catering_deposits`, once a day: holds catering deposits that are due and completes past catering orders.
- `manage.py gift_cards_cleanup`, once a day: deletes gift cards that have been empty for 30 days.
- `manage.py square_refresh`, once a day: renews the store's Square OAuth tokens.

In Docker, the compose file's `scheduler` service runs all three once a day (same image).

### First start

A server with no accounts sends every page to the first-time setup screen (`/users/setup`, `users/setup.py` +
`users.views.setup`, drawn like the sign-in page): the store's name, phone, address, time zone and tax, then the first
owner (name, email as the username, password, an optional register PIN). Outside development it also asks for the setup
code, so whoever finds a freshly deployed URL first can't claim it: the code is made once (in the shared cache), written
to the server's log when the screen first opens (`users.setup` logs to the console in production) and shown again by
`manage.py setup_code` (`docker compose exec web python manage.py setup_code`). Finishing signs the owner in and opens
the admin (or Store info, to restart for a new time zone).

### Local dev (Windows)

- Use **`venv/`**. `venv\Scripts\python manage.py runserver` (ASGI via daphne), then `http://127.0.0.1:8000/apps/`.
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
- Compose: `web` + `scheduler` (the daily jobs) + `redis` + `db` (Postgres 18, healthchecked, port not published; its
  password is made once by the `db-password` service into the `db_password` volume and read through
  `POSTGRES_PASSWORD_FILE`, so it needs no settings; `DATABASE_URL` points Pavilion elsewhere instead) +
  `valhalla` and `nominatim` (delivery travel times; they build from `OSM_EXTRACT` on first start). Secrets come from `pavilion/.env`.
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

- POS card payments: the server checks the Terminal checkout itself (completed, this register's, that amount, once). Terminal
  tips aren't recorded on the order yet.
- Customer order pages use sequential order IDs, so anyone can view any order's dish list and progress (no names, phones or addresses are shown).
- `card-api` GET is public: the 16-digit card number is the only secret for checking a balance.
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
