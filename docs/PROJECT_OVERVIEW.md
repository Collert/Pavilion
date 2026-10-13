# Pavilion — Full Project Overview

> Written 2026-10-04 from a full read-through of the code, git history, branches, stashes, and the local dev DB.
> This file is about what the code **actually does**, not what it was meant to do. Where those differ, this file says so.
> The older [README.md](../README.md) and [.github/copilot-instructions.md](../.github/copilot-instructions.md) are partly wrong (see §10).

---

## 1. What this is

**Pavilion** is a Django restaurant-management system for a small community café. It was originally built for the
café at **Holy Eucharist Ukrainian Catholic Cathedral** in New Westminster, BC. The project was called `heucc_pos`,
was renamed to `pavilion` on 2025-02-06 (commit `cd409c5`), and has Ukrainian and Cantonese translations.
`RESTAURANT_ADDRESS = "501 4th Ave, New Westminster, BC"` is hard-coded in `deliveries/views.py`, and SendGrid emails
go out as "Holy Eucharist Cathedral".

It started as a simple web POS: a cashier rings in an order, and the order shows up on a kitchen screen. It then grew into:

| Area | What it is | Status |
|---|---|---|
| **POS register** | Touch-screen cashier UI, cash/card/gift-card tender, Square Terminal | ✅ Works (core) |
| **Kitchen/bar display (KDS)** | "Order marking" screen per station: approve, complete, pick up | ✅ Works (polling) |
| **Customer-facing display** | Second screen mirroring the cart, linked to the POS by `BroadcastChannel` | ✅ Works (same browser only) |
| **Order status board** | TV screen with in-progress/ready columns, TTS announcements, weather, YouTube player | ⚠️ Half-broken (stale field names) |
| **Online store** | Customer PWA: menu, cart, pickup/delivery ordering, Square web payments, order history | ✅ Mostly works |
| **Events / business days** | Online ordering is only open during an `Event` with `restaurant_open=True` | ✅ Works |
| **Deliveries** | Courier app: clock in, claim delivery, ETA via Google Distance Matrix, web push | ⚠️ List page crashes, in-memory state |
| **Gift cards** | Buy online (Square), email via SendGrid, barcode card page, scan/charge at POS | ⚠️ Email sending broken by latest commit |
| **Inventory** | Ingredients → Components → Dishes, crafting, shopping log, recipes, availability toggles | ⚠️ Works, but crafting page still uses SSE |
| **New admin** | Custom superuser dashboard: orders, receipts, menus, promo content, events, calendar | ⚠️ Partial, several stubs |
| **Users** | Login/register/profile, `Staff`/`Customer` profiles | ✅ Basic |
| **Misc tools** | K-means image-color extractor (menu theming), gTTS announcement endpoint | ✅ Works |
| **App switcher** | Landing tile page: POS / Order marking / Admin | ✅ Trivial |
| **WebRTC** | Phone↔laptop camera signalling experiment | ❌ Abandoned/broken |
| **Stations (dynamic)** | `Station` model to replace hard-coded kitchen/bar/gng | ❌ Models only, unused |

---

## 2. Timeline (from git)

| When | What happened |
|---|---|
| 2023-12 → 2024-03 | Initial build: POS, kitchen/bar screens, Square terminal, dashboard, inventory. ~115 commits. |
| 2024-05 → 2024-06 | Deployment onto own hardware. **SSE broke in production → switched to polling** (`8502037` "Switched to polling for new orders", `4e447ab` order status screen, `7170e2c` inventory). |
| 2024-07 → 2024-08 | Online store + online payment processing, gift cards (PR #11), date/time constraints for online ordering. |
| 2024-10 → 2024-11 | Thread-safety attempt on active orders, gift card scanner. Started a `screens` app (branch `screens`, abandoned). |
| 2025-02 (79 commits) | Big push: whitenoise, rename to Pavilion, mostly-AI docstrings, new_admin rebuild, translations (PR #25), events, promo content, Station model added/disabled/re-enabled. |
| 2025-03 → 2025-04 | Menu management in new_admin, dashboard history calendar, Square checkout caching. Menu fonts/styles WIP left in **stash@{0}**. |
| 2025-07 | Docker/Postgres setup, Copilot instructions file, `classes.py` edit (introduced a crash, see §9). |
| 2025-11-26 | Three **Copilot agent branches** pushed to origin (unmerged): WebSockets, dynamic stations, N+1 query fixes. |

---

## 3. Tech stack

- **Django 5.1** (settings comment says 4.2), Python 3.11 in Docker, 3.12 locally.
- Plain Django templates plus vanilla JS ES modules. No frontend build step and no npm.
- **DB:** SQLite in dev (`db.sqlite3`), PostgreSQL 16 in Docker when `ENVIRONMENT=production`.
- **Cache:** `LocMemCache`, which is per-process (this matters, see §7).
- **Static:** whitenoise. The custom CSS kit `static/css/AD-Elements/` is shared by all apps. Each app also has its own `layers.css` and `project-styles.css`. Fonts are served locally (Noto Sans, Material Symbols).
- **Integrations:** Square (Terminal API, Web Payments SDK, webhooks), SendGrid dynamic templates, Google Distance Matrix, OpenWeather (status board), Web Push (VAPID / pywebpush), gTTS + pydub/ffmpeg, OpenCV + scikit-learn (image colours), python-barcode, QuaggaJS (barcode scan in browser), FullCalendar (admin history).
- **i18n:** `i18n_patterns`, so every page URL is prefixed with a language code (`/en/...`, `/uk/...`, `/zh-hk/...`). `locale/uk`, `locale/zh_HK`. JS strings use `gettext()` via `/jsi18n/`.

---

## 4. URL map

All URLs below are under the language prefix (`/en/`) except `/jsi18n/`.

| Prefix | App | Key routes |
|---|---|---|
| `/` | `online_store` | `""` home, `menu`, `dish/<id>`, `submit-order`, `order-status/<id>[/<from_placing>]`, `order-history`, `service-worker.js` |
| `/restaurant/` | `pos_server` | `register` (**the POS**), `order-marking` (KDS), `pos-output` (customer display), `orders-progress` (status board), `active_orders` (polling JSON), `check_inventory` (polling JSON), `check-device`, `component-choice`, `menu_select`, `create-menu`, `dashboard`, `day_stats`, `pair-terminal`, `webhook/square`, `webhook/square/check_card_status`, `check-su` |
| `/admin/` | `new_admin` | `dashboard/home`, `dashboard/history[/api]`, `menus/list`, `menus/<id>`, `orders/retail[/<id>[/receipt]]`, `store/branding`, `store/promo-content[/<id>\|/new]`, `store/events[/<id>\|/new]` |
| `/advanced-admin/` | Django admin | |
| `/inventory/` | `inventory` | `log-shopping`, `shopping-history`, `day-display/<id>`, `crafting`, `inventory-updates` (**SSE**), `component-availability`, `recipes`, `recipes/<id>` |
| `/deliveries/` | `deliveries` | `""` ready orders, `profile` (clock in/out), `eta`, `order/<id>`, `vapid-public-key/`, `save-subscription/` |
| `/gift-cards/` | `gift_cards` | `card/<number>`, `get-card`, `special`, `card-confirm`, `card-api/<number>` (GET balance / POST charge, csrf-exempt) |
| `/payments/` | `payments` | `web-payment-window` (iframe), `process-web-payment`, `capture-payment` |
| `/users/` | `users` | `login` (= `LOGIN_URL`), `logout`, `register`, `profile` |
| `/apps/` | `app_switcher` | tile page |
| `/utils/` | `misc_tools` | `get-image-colors/`, `tts?text=` |
| `/webrtc/` | `webrtc` | `post_offer`, `get_answer` (broken) |

---

## 5. Domain model

### 5.1 Menu → Dish → Component → Ingredient (`pos_server/models.py`)

```
Menu (is_active: exactly one is expected to be active; header/footer images; 4 theme colours)
 └─ Dish (M2M menu; price float; station "kitchen"|"bar"|"gng"; in_stock / force_in_stock / visible_in_menu)
     └─ DishComponent (quantity) ─ Component (inventory float; unit; type food|beverage;
                                              crafting_option craft|buy|auto; child_dishes M2M → Dish)
                                      └─ ComponentIngredient (quantity) ─ Ingredient (inventory int; allergen; unlimited; cost)
```

- **Choice components:** a `Component` with `child_dishes` acts as a choice, e.g. "Drink" with children Coffee and Tea.
  A Dish whose components are *all* choice components is an "only-choices" dish (`check_if_only_choice_dish`). It
  is skipped when creating `OrderDish` rows, and its chosen child dishes are added instead.
- **Stock cascade:** `Component.save()` sets `in_stock` from `inventory`, then walks `dishcomponent_set` and flips
  `Dish.in_stock`. Passing `save(force_update_stock=True)` skips the auto-recalculation.
  Note that `super().save()` is called without args, so `update_fields` etc. are dropped.
- **Crafting:** `inventory.views.craft_component(id, qty)` adds to the component and subtracts its ingredients
  (unless `unlimited`). Components with `crafting_option="auto"` are crafted automatically when a dish using them is sold.
- Ordering a dish decrements each component's inventory by `DishComponent.quantity` × **1**. The order quantity is
  not used: `pos()` loops over unique dishes, not quantities. **Bug:** stock under-counts when quantity > 1.
- `pos_server/translation.py` registers `Dish.title/description` with **django-modeltranslation**, but that package
  is not installed or in `INSTALLED_APPS`. The file is dead code, and the real WIP lives in stash@{0}.

### 5.2 Orders

`Order` fields that matter:

| Field | Meaning |
|---|---|
| `channel` | `store` (POS), `web` (online pickup), `delivery` |
| `kitchen_status`, `bar_status`, `gng_status` | Per-station status, see codes below. **These are the live fields.** |
| `status_0_stations` … `status_4_stations` | M2M to `Station`, intended to replace the three fields above. **Unused.** |
| `picked_up` | Defaults `True`. It is set `False` when any station is needed, and the order is "done" when `True`. |
| `approved` | Unused. |
| `start_time` | Scheduled time. Online orders pick a time, and the KDS hides orders until `start_time <= now`. |
| `prep_time` | Auto-set in `save()` when all stations are 2/4. |
| `name` | Customer name / table. A 2025-02 commit renamed it to `table` and back. |
| `authorization` | FK `payments.PaymentAuthorization`, used for delayed-capture Square payments. |
| `gift_cards` | M2M through `gift_cards.GiftCardAuthorization` (`related_name="gift_card_auth"`). |
| `phone`, `special_instructions`, `to_go_order`, `final_revenue` (unused) | |

**Station status codes:** `0` pending approval · `1` approved / in progress · `2` completed · `3` rejected · `4` not required (default).

- POS orders start at **1** for needed stations, which means auto-approved.
- Online and delivery orders start at **0** and must be approved on the KDS.

**`Order.progress_status()`** (customer-facing, web/delivery only):
1 scheduled → 2 not started → 3 in progress → 4 ready → 5 picked up → 6 delivered. It also contains three
leftover `print()` calls.

`Order.save()`/`delete()` call `update_active_orders_cache()`. That function puts the IDs of active orders
(start_time ≤ now AND (any station 0/1 OR not picked up)) into the cache key `active_orders` with a 60 s TTL.

### 5.3 Other models

- `EligibleDevice(token uuid, name)` is the device allowlist (see §6.8).
- `Station(friendly_name, code, icon)` lives in `pos_server`. Icons come from `settings.AVAILABLE_ICONS` (Material Symbols names). The local DB has 3 stations.
- `payments.Transaction(uuid, amount, successful)` and `payments.PaymentAuthorization(payment_id, amount cents, currency, status cap|del, transaction FK)`.
- `gift_cards.GiftCard(number 16 digits, available_balance, email, image)`, `GiftCardAuthorization`, `CardPreset`, `SpecialCard(CardPreset)`.
- `online_store.PromoContent` (home-page banners) and `online_store.RejectedOrder(order_id, reason)`. A rejected order is **deleted** and replaced by this record.
- `events.Event(start, end, restaurant_open, in_person_open, online_open, delivery_open, location…)` and `EventTemplate(Event)` (unused).
- `deliveries.Delivery(order FK related_name="delivery", destination, courier FK User, eta, completed, phone)`.
- `inventory`: `StockUpdate` + `UpdateIngredient` (shopping trips with a receipt upload), `Recipe` (markdown table), `Packaging`, `CondimentUse`, `IngredientUsageBuffer` (TODO), and `Waste*` models. The waste models have no UI.
- `users.Staff` (minutes_worked, allowance) and `users.Customer` (auto-created for **every** new User by a post_save signal).
- There are **two `PushSubscription` models**, in `online_store/notifications.py` and `deliveries/notifications.py`. Both are declared outside `models.py`, both are registered, and both use `related_name="web_notifications_sub"`. Only the deliveries one is routed.
- `webrtc.WebRTCSession` is part of the abandoned experiment.

---

## 6. Core flows

### 6.1 In-store POS (`/restaurant/register`)

Template `pos_server/order.html`, JS `pos-order.js` + `pos-utils.js` (`Cart`, `CashPayment`, `GiftCardPayment`, `CashDrawer`).

1. Before any item can be added, a cash drawer amount must be set (`localStorage.cashDrawer`). This needs superuser auth, done via `POST check-su` with a username and password.
2. Tapping a dish adds it to the cart. A choice dish opens `component-choice` as a dialog. The cart is broadcast on `BroadcastChannel('cart_channel')` to the customer display.
3. **Tender:**
   - **Cash:** `POST register` creates the order directly.
   - **Card:** `PUT register` checks gift-card balances and then calls `square.terminal_checkout()`. That creates a Square Terminal checkout and stores `checkout_card_status` + `current_checkout_id` in the cache. The JS then polls `GET webhook/square/check_card_status` every 1 s for up to 120 s. Square calls `POST webhook/square`, which verifies the HMAC and updates the cache status to COMPLETED/CANCELED. After COMPLETED, the JS does `POST register`.
   - **Gift card (partial payment):** the card is looked up via `card-api/<n>` or scanned with the camera (QuaggaJS, code128). `POST register` then charges it with `GiftCard.charge_card()`. **No `GiftCardAuthorization` is created for POS orders.**
4. `POST register` creates the `Order` (channel `store`) and an `OrderDish` per dish, sets the needed station statuses to 1, and decrements inventory.
5. `pair-terminal` is meant to pair a Square Terminal, but it **crashes**: it reads `pos_server/config.cfg`, which doesn't exist (`*.cfg` is gitignored), and the GET and PUT paths use different section names.

### 6.2 Kitchen / bar display (`/restaurant/order-marking?filter=kitchen&filter=bar`)

Template `order-marking.html`, JS `kitchen-bar.js`.

- `filter` query params choose which stations this screen covers. `auto-done` / `auto-collect` query params (minutes) auto-advance cards.
- **Polls `GET active_orders` every 11 s** (an interval chosen so it isn't a factor of 10, to dodge a race with the auto-actions). It diffs against the previous state to add and remove cards.
- **POST** with action `approve` sets the screen's stations to 1. If the order has a Square authorization and all stations are approved, the JS calls `POST payments/capture-payment` to capture it.
- **POST** with action `delete` (reject) creates a `RejectedOrder` with a reason, refunds the gift cards, deletes the order, and calls `DELETE capture-payment`.
- **PUT** marks the screen's stations as completed (2). **DELETE** marks the order picked up.
- Swipe gestures, keyboard selection, a reject-reason dialog, and a preferences dialog are built in.
- `timeouts.js` reloads the page after 5 min of inactivity.

### 6.3 Customer-facing display (`/restaurant/pos-output`)

Shows the active menu (`compile_menu()` / `prettify_dish()` build human-readable component strings like
"2 eggs, a cup of coffee (choice of: Latte/Tea)") plus the live cart. It receives the cart from the POS via
`BroadcastChannel`, so **it must be a second tab/window in the same browser profile on the same machine**.

### 6.4 Order status board (`/restaurant/orders-progress`)

TV display. It polls `active_orders` every 5 s, announces ready orders with randomised phrases
(browser TTS / `utils/tts`), and shows a clock, weather (OpenWeather key passed into the template), and a YouTube
player (`YTPlayer.js`). **Partly broken:** `orders-status.js` and `orders-status.html` still read
`kitchen_done`, `kitchen_needed`, and `bar_done`, fields that were replaced by `*_status` in 2024. The server view also
only looks at `kitchen_status`.

### 6.5 Online ordering (`/`)

`online_store/layout.html` (942 lines) holds most of the logic: cart in JS, gift-card lookup, delivery ETA, service-worker registration, and push subscription.

1. A `business_day` context processor (`online_store/context_processors.py`) checks for an `Event` ending today with `restaurant_open=True`. The pickup time input is limited to that event's start/end.
2. The finalize form has `class="attach-card-payment"`, so `payments/cardPaymentPopup.js` intercepts submit and opens `payments/web-payment-window?amount=` in an iframe (Square Web Payments SDK). The iframe creates a `Transaction`, and `process-web-payment` charges the card. When `confirmation_needed`, it uses `autocomplete=False` and creates a `PaymentAuthorization`. After a `postMessage`, the form is resubmitted with `transaction_uuid` (see [payments/README.MD](../payments/README.MD)).
3. `submit-order` creates the `Order` with channel `web` or `delivery`, station statuses 0, the gift-card charges + `GiftCardAuthorization`, and a `Delivery` if needed. It then redirects to `order-status/<id>/True`.
4. The customer's order IDs are kept in `localStorage.orders` and are what `order-history?orders=[...]` shows.
5. **Important:** the online form does **not** have `confirmation-needed`, so card payments are captured immediately and `order.authorization` is `None`. **Rejecting an online order refunds gift cards but not the card payment.** The delayed-capture path exists but isn't wired up.
6. Any exception in `place_order` returns the text "Don't do this. I will get to implementing a better page soon".

### 6.6 Deliveries (`/deliveries/`)

Staff-only courier PWA (`deliveries/service-worker.js`, manifest).
- A courier "clocks in" on `profile` with a transport mode. **The active couriers list is a module-level Python `set`** (`deliveries/globals.py`), so it is lost on restart and not shared between processes.
- A ready delivery order shows in the list. The courier claims it with `PUT order/<id>` plus an ETA, which also marks the order picked up. `DELETE` completes it.
- `get_eta` calls the Google Distance Matrix API for each active courier's transport mode.
- `deliveries/signals.py` sends web push "New delivery!" on `Delivery` save, but only if the order is already ready at that moment. That never happens for a new delivery, so in practice no push is sent.
- **The list page (`/deliveries/`) currently 500s:** `orders.html:30` has `{% trans "... "Job" tab." %}`, and the nested quotes are a template syntax error.

### 6.7 Gift cards (`/gift-cards/`)

- `get-card`: choose a preset (`CardPreset`) or a personal design (images in `files/gift-cards/{presets,personal}`). Payment goes through the same `attach-card-payment` popup. On POST, `create_card()` makes a `GiftCard` and sends a SendGrid dynamic-template email (hard-coded template IDs, from `restaurant@uahelp.ca`).
- `card/<number>` renders a code128 barcode (base64 PNG).
- `card-api/<number>`: GET returns the balance, POST charges the card. It is `csrf_exempt` with **no auth**, so anyone who knows a card number can drain it.
- **Currently broken:** `misc_tools/classes.py` → `SendGridEmailData.add_recipient` does `if len(str) > 100`. `str` is the builtin type, so this raises `TypeError` on every call. The card is still created, but the email fails, and `get_card`'s bare `except:` then looks up the card by email/image/amount instead.

### 6.8 Device authorisation

`authorizedDeviceCheck.js` is included on the POS and KDS. It reads `deviceToken` from `localStorage` or `?device-token=` and `PUT`s it to `/restaurant/check-device`. If the token is missing or wrong, it redirects to a page where a superuser logs in, which creates an `EligibleDevice` and shows the new UUID.
**This is only a client-side redirect.** No server view checks the token, so the copilot-instructions claim
("always validate device tokens") is aspirational.

### 6.9 Admin

- `new_admin` (`/admin/`) is the custom superuser UI. Orders list + detail + printable receipt (restaurant name/address are placeholders), menu list/edit/activate, promo content CRUD, event CRUD, and a calendar of business days. `dashboard_home` and `store_branding` are essentially empty. `menus_list`, `menu_instance`, `dashboard_history`, and `get_business_dates` have **no auth decorators**.
- `pos_server.dashboard` renders a template that doesn't exist (`pos_server/dashboard.html`), and `day_stats` crashes without `?date=`. Both are older versions of what new_admin is replacing.
- Django admin at `/advanced-admin/` is used for everything else (dishes, components, stations, devices).

---

## 7. The real-time story (SSE → polling), and how to do it properly

**What existed (before June 2024):** `pos_server/signals.py` appended `Order` objects to module-level Python lists
(`globals.new_data_queue`, `kitchen_update_queue`, …). Views like `order_updates` returned a `StreamingHttpResponse`
wrapping an infinite generator that popped from those lists and slept with `time.sleep(1)`. You can see it in
`git show 8502037`.

**Why it worked locally and died when deployed:**
1. **Per-process memory.** `runserver` is one process, so everything shared the same lists. Production
   (gunicorn with several workers) gives every worker its own copy of `globals`. An order saved in worker A
   never reaches a stream held open by worker B.
2. **Each SSE connection pins a worker.** A sync WSGI worker stuck in `while True: sleep()` can't serve anything
   else. With a few screens open, all workers are busy and the site hangs.
3. **The queues were consumed destructively** (`queue.remove(data)`). With two screens connected, whichever stream read an
   event first "stole" it from the other.
4. **Proxy buffering** (nginx/Cloudflare) holds streamed responses. `X-Accel-Buffering: no` only fixes nginx.

**What exists now:** all screens poll small JSON endpoints (`active_orders`, `check_inventory`,
`check_card_status`). That is robust and honestly fine for a one-location café.

**The same root cause is still present in three places:**
- `LocMemCache` is per-process. The Square webhook may land in worker A while the POS polls worker B, so the POS
  never sees COMPLETED. It only works today because Docker runs `manage.py runserver` (a single process).
- `deliveries/globals.active_couriers` is an in-memory set.
- `inventory/crafting.html` still opens an `EventSource` to `inventory/inventory-updates`. That is an infinite
  generator driven by `inventory/globals.new_inventory_updates`, so it has all four problems above.

**Proper fix options:**
- **Keep polling (simplest):** move shared state into the DB or a shared cache (Redis, or Django's DB cache
  backend). Then polling works with any number of workers. Convert the crafting page to polling as well.
- **Real push:** use Django Channels with **`channels_redis.core.RedisChannelLayer`**, run under daphne/uvicorn (ASGI),
  and send group messages from `post_save` signals. Branch `origin/copilot/upgrade-order-display-flow` already
  implements this (`pos_server/consumers.py`, `routing.py`, reworked `kitchen-bar.js` and `orders-status.js`), **but it
  uses `InMemoryChannelLayer`**. That reproduces bug #1 as soon as there is more than one process. Switch it to
  Redis, add a Redis service to docker-compose, and serve with daphne before relying on it.

---

## 8. Config, running, deploying

### Environment (`pavilion/.env`, loaded by python-dotenv)
`SQUARE_ACCESS_TOKEN`, `SQUARE_ENVIRONMENT` (sandbox|production), `SQUARE_DEVICE_ID`, `SQUARE_LOCATION_ID`,
`SQUARE_APPLICATION_ID`, `SQUARE_WEBHOOK_SIGNATURE_KEY`, `ENVIRONMENT` (production → Postgres, DEBUG off, file logging
to `logs/debug.log`), `DB_USER`/`DB_PASSWORD` (unused; Postgres reads `POSTGRES_*`, `DB_HOST`, `DB_PORT`),
`WEATHER_API_KEY`, `SENDGRID_API_KEY`, `GOOGLE_API_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `APP_DOMAIN`.
`pavilion/.env.example` is incomplete and has an unterminated quote at the end.

### Local dev (Windows)
- Use **`venv/`** (Python 3.12.2, has every dependency). **`.venv/` is incomplete** because it's missing `whitenoise`, so every request 500s with it.
- `venv\Scripts\python manage.py runserver`, then go to `http://127.0.0.1:8000/en/apps/`.
- `start.bat` / `start.sh` start ngrok, **rewrite `ALLOWED_HOSTS` inside `settings.py`**, collectstatic, compilemessages, and runserver. The rewrite modifies a tracked file. Better to put the ngrok host in `APP_DOMAIN` instead.
- `make_translations.(bat|sh)` → `makemessages -l uk -l zh_HK`, then `compilemessages` (`.mo` files are gitignored).
- Dev `STATIC_ROOT` is hard-coded to `/home/collert/POS/static/`, a leftover from the self-hosted Linux box.

### Migrations — read before running `makemigrations`
- **`*/migrations/` is gitignored.** Migrations exist only on your machine, so a fresh clone or the Docker build has to run `makemigrations` itself.
- Your local `pos_server` migrations 0045–0047 and `new_admin` 0001 came from **stash@{0}** (modeltranslation fields, Menu fonts/style, `MenuStationPosition`, `new_admin.Font`) and **are applied to `db.sqlite3`**. The current code doesn't have those fields. Running `makemigrations` now generates `0048_remove_dish_description_en…`, which would drop them. Decide whether to `git stash pop` that work or let the drop happen.
- The local DB has real data: 364 orders, 2 menus, 14 dishes, 3 stations.

### Docker (`Dockerfile`, `docker-compose.yml`)
- python:3.11 slim + ffmpeg + libgl + libpq. It copies `prod.env` (which contains only `ENVIRONMENT=production`) over `pavilion/.env`, and compose also loads `pavilion/.env`.
- **Runs `manage.py runserver 0.0.0.0:8000` in production**, not gunicorn. It does run `migrate`, but with no migrations in git and no `makemigrations`, a clean build has **no tables for the project apps**.
- Postgres credentials are hard-coded in compose (`pavilion_pass`), and port 5432 is published to the host.
- **(Fixed: media now goes to a RustFS S3 bucket in production, `./media` in dev.)** Previously: media wasn't served when DEBUG is off. `MEDIA_URL=''`, and `static()` returns nothing in production, so dish images, gift-card images, and promo images 404 in Docker. The `media_volume` mounts `/app/media`, but uploads go to `/app/files/`.

---

## 9. Known bugs (verified), most important first

**Security**
1. **FIXED (code): dev no longer serves the whole project folder.** Media is `MEDIA_URL='/media/'` / `MEDIA_ROOT=BASE_DIR/'media'` in dev and a RustFS bucket in prod. **Still to do: rotate the Square/SendGrid/Google/VAPID keys in `.env`**, since they were reachable via ngrok while the catch-all existed.
2. **FIXED:** `SECRET_KEY` now comes from the `SECRET_KEY` env var (required in production, dev fallback otherwise). The old key is in git history, so it must never be reused.
3. **FIXED:** `gift-cards/card-api/<n>` POST now needs a logged-in staff user plus CSRF, validates the amount and charges atomically. GET stays public (checkout needs it) but only staff see the owner's email. Card numbers are still the only secret for GET, so keep them unguessable.
4. **FIXED:** `check_card_status` and `check_inventory` need a login (401 JSON otherwise). `active_orders` returns a redacted list (no phone, address, payment ID, notes, prices) to anonymous callers, since the public status board polls it. The `CORS_*` settings were removed: `django-cors-headers` was never installed, so they did nothing.
5. The device allowlist is client-side only (§6.8).
6. **FIXED:** `CSRF_TRUSTED_ORIGINS` now builds `https://<APP_DOMAIN>` with a real f-string and no longer lists the stale ngrok URL (add your current ngrok origin there if you POST through ngrok in dev).

**Broken right now**
7. `misc_tools/classes.py:10` has `len(str)`, which crashes every gift-card email (introduced by the latest commit `9a09a27`).
8. `/deliveries/` 500s (template quote error, `deliveries/templates/deliveries/orders.html:30`).
9. `/restaurant/dashboard` 500s (missing template). `/restaurant/day_stats` 500s without `?date=` and references a missing `error.html`.
10. `/restaurant/pair-terminal` 500s (missing `config.cfg`, mismatched section names, and it writes `config.cfg` to the CWD).
11. Order status board uses removed `kitchen_done`/`kitchen_needed`/`bar_done` fields.
12. `webrtc/get_answer` URL has no `session_id`, and `phone.html` uses undefined URL names `get_offer`/`post_answer`.
13. `payments.capture_web_payment` sets `status = "del"` on capture (should be `"cap"`). If the status isn't `""`, `captured_payment` is undefined, which causes a NameError that gets swallowed into an error response. DELETE never cancels the payment with Square.

**Logic bugs**
14. Rejected online orders don't refund card payments (§6.5 point 5).
15. POS gift-card charges create no `GiftCardAuthorization`, so they can't be refunded.
16. Inventory decrement ignores order quantity (§5.1).
17. **Gift-card partial payments with cents crash** (verified: `Decimal -= float` → `TypeError`). `pos()` and `place_order()` pass the raw JSON amount to `GiftCard.charge_card`. Whole-dollar amounts arrive as `int` and happen to work, but `3.50` doesn't. At the POS this happens *after* the Square terminal has already charged the rest. Online, the order row is already saved when it crashes, and the customer gets the "Don't do this" page. `card_api` does it correctly with `Decimal(...)`.
18. `kitchen-bar.js` mixes `data-orderid` and `data-order-id` selectors, so the duplicate-card check can miss.
19. `deliveries` push notification only fires if the order is already ready at creation.
20. `gift_cards/urls.py`: `special` route points at `get_card`, not `special`.
21. `Menu.objects.get(is_active=True)` is used in several views and crashes when zero or two menus are active. `new_admin` lets you toggle any number of menus active.
22. Prices are a `FloatField` (Dish) mixed with `DecimalField` (gift cards, Ingredient). Square amounts are computed as `amount * 100` on floats; `gather_terminal_checkout` sends a float, not an int.

**Cruft**
- Debug `print()` calls throughout (including in `Order.progress_status`).
- Duplicate login/logout views in `pos_server/views.py` (unrouted; `users` has the live ones).
- `pos_server/globals.py`, `pos_server/signals.py` (all commented out), and `pos_server/translation.py` (dead).
- `new_admin/templates/new_admin/store_promo_content-instance copy.html`, and a `receipt-printer.js` that prints a hard-coded order 123.
- The local `screens/` folder (untracked, only `__pycache__`/migrations) is a leftover of the `screens` branch.
- The docstrings were "mostly AI generated so beware" (commit message, 2025-02-10).

---

## 10. Branches, stashes, and other in-flight work

| Ref | Contents | Recommendation |
|---|---|---|
| `main` | Everything above | |
| `origin/copilot/upgrade-order-display-flow` (5 commits, 2025-11-26) | Django Channels WebSockets: `consumers.py`, `routing.py`, ASGI router, signal broadcasts, rewritten KDS/status JS | Best starting point for real-time, **after switching to the Redis channel layer** |
| `origin/copilot/upgrade-station-management-system` (4 commits) | Dynamic `Station` system replacing kitchen/bar/gng, admin support, 238 lines of tests | Review carefully. It touches models, views, filters, and templates. |
| `origin/copilot/improve-inefficient-code` (3 commits) | `select_related`/`prefetch_related` N+1 fixes | Low risk; likely conflicts with the station branch |
| `stash@{0}` "Idk" (2025-04-26) | Menu `style` + fonts (`new_admin.Font`), `MenuStationPosition`, modeltranslation enabled, customer-display redesign | **Matches migrations already applied to your local DB** |
| `stash@{1}` "Calendar" (2025-02-24) | Early dashboard-history calendar | Superseded by main; probably drop |
| `stash@{2}` "Started screens" (2024-11-16) | On the old `heucc_pos/` layout | Obsolete |
| `screens` (local) | Abandoned digital-signage app, old layout | Obsolete |
| `websockets` (local), `Translations` | Fully merged into main (0 commits ahead) | Safe to delete |
| `origin/recovery` | Unrelated history (no merge base), 2024 gift-card work | Archive only |

The Copilot branches are newer than `main`'s last commit and have **not** been merged.
`.github/copilot-instructions.md` describes some of them as if they were done. It also says `webrtc` serves kitchen
displays (it doesn't) and that device tokens are validated server-side (they aren't).

---

## 11. Suggested path to "finished"

1. **Stop the bleeding:** fix the media catch-all, rotate the keys, move `SECRET_KEY` to env, fix `len(str)`, the deliveries template, and the gift-card Decimal crash.
2. **Make it deployable:** commit migrations (un-ignore them) after deciding on stash@{0}; use gunicorn (or daphne) instead of runserver; add Redis (cache + channel layer); serve media properly.
3. **Pick the real-time strategy:** either polling + shared cache, or Channels + Redis via the Copilot branch. Then convert the inventory crafting SSE page and fix the status board fields.
4. **Finish stations:** decide between the `Station` model and the hard-coded three. The Copilot branch is a head start. Then remove the dead fields.
5. **Money correctness:** use Decimal everywhere, wire up delayed capture or refunds for rejected online orders, create `GiftCardAuthorization` at the POS, and lock down `card-api`.
6. **Tests:** only the Copilot station branch has any. Add tests around order creation, station status transitions, and payments first.
