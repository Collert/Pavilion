# Pavilion — Full Project Overview

> First written 2026-10-04 from a full read-through of the code, git history, branches, stashes, and the local dev DB.
> Rewritten 2026-10-13 after the overhaul on branch `fix-all-problems-for-real` (dynamic stations, server-side pricing,
> payment fixes, SSE, Docker, tests). This file is about what the code **actually does**. Where the code and older docs
> ([README.md](../README.md), [.github/copilot-instructions.md](../.github/copilot-instructions.md)) disagree, trust this file and the code.

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
| **Kitchen display (KDS)** | "Order marking" screen per set of stations: approve, complete, hand out, reject | ✅ Works, live (SSE) |
| **Customer-facing display** | Second screen mirroring the cart, linked to the POS by `BroadcastChannel` | ✅ Works (same browser only) |
| **Order status board** | TV screen with in-progress/ready columns, TTS announcements, weather, YouTube player | ✅ Works, live (SSE) |
| **Online store** | Customer PWA: menu, cart, pickup/delivery ordering, Square web payments, order history | ✅ Works (Square popup not re-tested against sandbox) |
| **Events / business days** | Online ordering only during an `Event` with `restaurant_open` + `online_open`/`delivery_open` | ✅ Enforced on the server |
| **Deliveries** | Courier app: clock in, claim delivery, ETA via Google Distance Matrix, web push | ✅ Works (push untested end-to-end) |
| **Gift cards** | Buy online (Square), email via SendGrid, barcode card page, scan/charge at POS | ✅ Works |
| **Inventory** | Ingredients → Components → Dishes, crafting, shopping log, recipes, availability toggles | ✅ Works, crafting page live (SSE) |
| **New admin** | Custom superuser dashboard: orders, receipts, menus, promo content, events, calendar | ⚠️ Partial (`dashboard_home`, `store_branding` are stubs) |
| **Users** | Login/register/profile, `Staff`/`Customer` profiles | ✅ Basic |
| **Misc tools** | K-means image-colour extractor (menu theming), gTTS announcement endpoint | ✅ Works |
| **App switcher** | Landing tile page: POS / Order marking / Admin | ✅ Trivial |
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
| 2026-10-13 | **Overhaul** on `fix-all-problems-for-real`: security fixes, DB reset with committed migrations, Station model, shared order service, payment fixes, SSE over Redis, Docker with uvicorn, tests, `seed_demo`. |

---

## 3. Tech stack

- **Django 5.1**, Python 3.12 (Docker and local).
- Plain Django templates plus vanilla JS ES modules. No frontend build step and no npm.
- **Server:** ASGI. `manage.py runserver` runs daphne (it's first in `INSTALLED_APPS`); Docker runs uvicorn with several workers.
- **DB:** SQLite in dev (`db.sqlite3`), PostgreSQL 16 when `ENVIRONMENT=production`.
- **Redis** (`REDIS_URL`, required in production): Django cache + pub/sub for live updates. Without it (dev) the cache is
  `LocMemCache` and live updates use an in-process broker.
- **Static:** whitenoise, collected to `staticfiles/`. Shared CSS kit `static/css/AD-Elements/`; shared JS in `static/js/`.
- **Media:** `./media` served by Django in dev; a RustFS (S3-compatible) bucket via `django-storages` in production.
- **Integrations:** Square (Terminal API, Web Payments SDK, webhooks) on `squareup==38.x` (later versions replaced
  `square.client.Client`), SendGrid dynamic templates, Google Distance Matrix, OpenWeather, Web Push (VAPID), gTTS +
  pydub/ffmpeg, OpenCV + scikit-learn, python-barcode, QuaggaJS, FullCalendar.
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
| `/inventory/` | `inventory` | `log-shopping`, `shopping-history`, `day-display/<id>`, `crafting`, `inventory-updates` (JSON stock), `component-availability`, `recipes`, `recipes/<id>` |
| `/deliveries/` | `deliveries` | `""` ready orders, `profile` (clock in/out), `eta`, `order/<id>`, `vapid-public-key/`, `save-subscription/` |
| `/gift-cards/` | `gift_cards` | `card/<number>`, `get-card`, `special`, `card-confirm`, `card-api/<number>`, `email_card-confirm` (preview) |
| `/payments/` | `payments` | `web-payment-window` (iframe), `process-web-payment` |
| `/users/` | `users` | `login` (= `LOGIN_URL`), `logout`, `register`, `profile` |
| `/apps/` | `app_switcher` | tile page |
| `/utils/` | `misc_tools` | `get-image-colors/` (staff), `tts?text=` (public, 300 chars max) |
| `/events/<topic>` | `pavilion.realtime` | SSE streams: `orders` (public), `inventory` (staff), `checkout` (staff + registered device) |

**Access rules.** Customers can register accounts, so staff pages use `staff_required` / `superuser_required` /
`api_staff_required` (`pos_server/devices.py`), never bare `login_required`. POS/KDS views also use `device_required`.

---

## 5. Domain model

### 5.1 Menu → Dish → Component → Ingredient (`pos_server/models.py`)

```
Menu (is_active: saving an active menu deactivates the others; Menu.active() returns it or None)
 └─ Dish (M2M menu; price Decimal; station FK → Station; in_stock / force_in_stock / visible_in_menu)
     └─ DishComponent (quantity) ─ Component (inventory float; unit; type food|beverage;
                                              crafting_option craft|buy|auto; child_dishes M2M → Dish)
                                      └─ ComponentIngredient (quantity) ─ Ingredient (inventory int; allergen; unlimited; cost)
```

- **Choice components:** a `Component` with `child_dishes` is a choice, e.g. "Drink" with children Coffee and Tea. A Dish
  whose components are *all* choices is an "only-choices" dish: it is never prepared itself, its picked children are.
  Pricing rules are in `pos_server/orders.py` (see §6.1).
- **Stock:** `Component.save()` recalculates `in_stock` (unless `save(force_update_stock=True)`, used by the manual
  availability toggle) and updates dishes: a dish goes out of stock when a component can't cover one portion, and comes
  back when that component is restocked and all its other components are available. `force_in_stock` keeps a dish
  sellable regardless. Self-crafting (`auto`) components and choice components never limit a dish by their own inventory.
- **Crafting:** `inventory.views.craft_component(id, qty)` adds to the component and subtracts its ingredients (unless
  `unlimited`). `auto` components are crafted on demand when a dish using them is sold.
- Selling `n` of a dish takes `DishComponent.quantity × n` of each component.

### 5.2 Stations and orders

- `Station(name, code, icon, menu_heading, position)`. `code` is a unique slug used in KDS filters (`?filter=kitchen`).
  `menu_heading` titles the menu section for that station's dishes ("Hot food"). A data migration creates
  Kitchen / Bar / Grab & Go. Edit or add stations in `/advanced-admin/`.
- `Order` fields: `channel` (`store` POS, `web` online pickup, `delivery`), `start_time` (scheduled time; screens hide
  orders until it passes), `prep_time` (set when the last station finishes), `name`, `phone`, `special_instructions`,
  `to_go_order`, `picked_up`, `total` (server-computed, after discount), `discount`, `authorization` (Square payment),
  `gift_cards` (through `GiftCardAuthorization`).
- `OrderStation(order, station, status)`, one row per station that has work on the order. **Status codes:** `0` pending
  approval · `1` being made · `2` done · `3` rejected. A station with no row isn't involved.
  POS orders start at 1; online/delivery orders start at 0 and must be approved on the KDS.
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
- `inventory`: `StockUpdate` + `UpdateIngredient` (shopping trips with a receipt upload), `Recipe` (markdown), `Packaging`,
  `CondimentUse`, `IngredientUsageBuffer` (TODO), and `Waste*` models (no UI).
- `users.Staff` (minutes_worked, allowance) and `users.Customer` (auto-created for every new User by a post_save signal).

---

## 6. Core flows

### 6.1 Creating orders (`pos_server/orders.py`)

Both the POS and the online store call `create_order()`. Inside one DB transaction it:

1. Prices the cart from the database (`price_cart`). The browser sends items like `{"pk": dish, "parent": dish}`, and
   its prices and totals are ignored. A plain dish costs its price; a dish with choices costs its price and each pick is
   free (one free pick per choice component per parent); picks for an only-choices dish cost their own price. Unknown,
   out-of-stock, or invalid picks raise `OrderError`.
2. Applies POS discounts (amount, then percent; staff-only input).
3. Locks and checks gift cards, and calls `verify_payment(remaining)` (online: the card payment must cover the rest).
4. Creates the order, `OrderDish` rows, `OrderStation` rows, takes stock, charges gift cards with a `GiftCardAuthorization`.

Any `OrderError` rolls everything back and is shown to the cashier/customer. `collect_order()` serializes an order for
the screens (`stations` is `{code: status}`); `orders_for_screens()` prefetches what it needs.

### 6.2 In-store POS (`/restaurant/register`)

Template `pos_server/order.html`, JS `pos-order.js` + `pos-utils.js` (`Cart`, `CashPayment`, `GiftCardPayment`).

1. A cash drawer amount must be set first (`localStorage.cashDrawer`), which needs a superuser password via `POST check-su`.
2. Tapping a dish adds it to the cart; a choice dish opens `component-choice`. The cart is broadcast on
   `BroadcastChannel('cart_channel')` to the customer display.
3. **Tender:** cash → `POST register`. Card → `PUT register` (re-checks gift card balances) starts a Square Terminal
   checkout on the paired terminal (`SquareTerminal`, falling back to `SQUARE_DEVICE_ID`); the JS polls
   `check_card_status` until the webhook reports COMPLETED, then `POST register`. Gift cards are looked up via `card-api`
   or scanned (QuaggaJS) and charged inside `create_order`.
4. If the server refuses the order, the POS keeps the cart and shows the reason. Dish buttons follow stock live (SSE `inventory`).
5. `pair-terminal` (superuser) creates Square device codes and activates a terminal once Square reports it PAIRED.

### 6.3 Kitchen display (`/restaurant/order-marking?filter=kitchen&filter=bar`)

Template `order-marking.html`, JS `kitchen-bar.js`. Cards are rendered by JS from server data (`json_script`), with
`textContent` only. The screen refreshes from `active_orders` on every SSE `orders` event (30 s fallback poll).

- `filter` params choose the stations this screen covers; the preferences dialog lists all stations. `auto-done` /
  `auto-collect` (minutes) auto-advance cards.
- **POST** `approve` moves this screen's pending stations to 1. When no station is pending anymore and the order has a
  Square authorization, the server **captures** the payment.
- **POST** `delete` (reject) **voids or refunds** the card payment on the server (the order is kept if Square fails),
  refunds gift cards, records a `RejectedOrder`, and deletes the order.
- **PUT** moves this screen's in-progress stations to 2. **DELETE** marks the order picked up.
- Keyboard (Enter/5 advances, arrows/2/8 move, Delete rejects, 1 opens availability), swipe, reject reasons, preferences.

### 6.4 Customer display (`/restaurant/pos-output`) and status board (`/restaurant/orders-progress`)

- The customer display shows the active menu grouped by station heading, plus the live cart from the POS via
  `BroadcastChannel`, so **it must be a second window in the same browser profile on the same machine**.
- The status board is public. It reads the redacted `active_orders` feed (no phone, address, payment ID, notes or
  prices), diffs it into "In progress" / "Ready" (ready = every involved station done), and announces newly ready
  orders by TTS. Clock, weather (OpenWeather) and a YouTube player are on the same screen.

### 6.5 Online ordering (`/`)

`online_store/layout.html` holds most of the logic: cart, gift-card lookup, delivery ETA, service worker, push.

1. `check_business_day` (context processor) exposes today's open `Event`; `place_order` re-checks it on the server,
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
  live in one place. `static/js/live.js` (`onLiveUpdate(topic, refresh)`) adds a debounce and a slow fallback poll.
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
3. Product work: finish `new_admin` (dashboard home, branding, station management), make restaurant name/address
   configurable, decide on `stash@{0}` (menu fonts/styles), add UI for waste tracking.
