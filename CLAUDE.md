# CLAUDE.md

Pavilion is a Django restaurant-management app (POS, kitchen display, online ordering, deliveries, gift cards,
inventory). It was built for a small church-community café in New Westminster, BC. Solo hobby project. It was
overhauled on 2026-10-13 (branch `fix-all-problems-for-real`), but **verify behaviour in the code before trusting
docstrings or the README.** The docstrings are mostly AI-generated and the README is partly out of date.

**Full deep-dive: [docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md)**. It covers every app, the flows, the
real-time design, open issues, and branches/stashes. Read the relevant section before working on a feature, and update
it when you change how something works.

## Running locally (Windows)

- Use **`venv\`**, not `.venv\` (incomplete). `venv\Scripts\python manage.py runserver` (ASGI via daphne) →
  `http://127.0.0.1:8000/en/apps/`
- All page URLs carry a language prefix: `/en/`, `/uk/`, `/zh-hk/` (`i18n_patterns`). Only `/jsi18n/` and `/events/<topic>` don't.
- Fresh DB: `manage.py migrate` then `manage.py seed_demo` (logins admin/admin, cashier/cashier, courier/courier,
  customer/customer; register PINs Alex 0000, Dana 1234, Priya 2468). `seed_demo` refuses to run with DEBUG off or
  on a DB that already has dishes/orders. `manage.py seed_recipes` adds the Cookery mockup's recipes to any DB
  (safe on real data, re-runnable).
- **Every time a model is created or changed, update `pos_server/management/commands/seed_demo.py`** so it still runs
  on a fresh database (`migrate` then `seed_demo`) and fills in the new model/fields with realistic data.
- Tests: `venv\Scripts\python manage.py test` (pavilion, pos_server, online_store, gift_cards). Square and SendGrid are
  mocked. Add tests with every fix. `requirements-dev.txt` adds `fakeredis`.
- Quick smoke test without a server: `django.test.Client(HTTP_HOST='localhost')` + `force_login(user)`.
- Settings switch on `ENVIRONMENT=production` (Postgres, DEBUG off, Redis + `SECRET_KEY` required, S3 media) vs anything
  else (SQLite `db.sqlite3`, DEBUG on). Secrets live in `pavilion/.env` (template: `pavilion/.env.example`). Never print
  or commit its values.
- Translations: `make_translations.bat` (makemessages uk, zh_HK) → `manage.py compilemessages`.
- `start.bat`/`start.sh` start ngrok and pass its domain via `EXTRA_HOSTS`. Avoid them unless asked: they expose a DEBUG server.

## Migrations

Committed. The DB was reset on 2026-10-13 and all apps restarted from `0001_initial` (old DB kept locally as
`db-old-2026-10-13.sqlite3`, not migratable). Run `makemigrations` with every model change and commit the migration
together with the change. `stash@{0}` (menu fonts/styles) was **not** applied. Don't reset or delete `db.sqlite3`
without asking.

## Architecture map

| App | Prefix | Role |
| --- | --- | --- |
| `pos_server` | `/restaurant/` | **Core.** Menu, Dish, Combo, Component, OptionGroup/Option, Extra, Ingredient, Station, Order, OrderItem, OrderDiscount, OrderPayment, Discount, CashDrawerSession, EligibleDevice. Register + lock screen + cash drawer (`register.py`), KDS, customer display, status board, Square webhook. `pricing.py` = cart pricing/discounts/tax; `orders.py` = order creation/serialization; `devices.py` = access decorators. |
| `online_store` | `/` | Customer PWA: menu, cart, checkout (`place_order`), order status/history. PromoContent, RejectedOrder. |
| `payments` | `/payments/` | Square: Terminal checkout and pairing, Web Payments popup, capture/void (`square.py`). Transaction, PaymentAuthorization, SquareTerminal. |
| `gift_cards` | `/gift-cards/` | GiftCard, GiftCardAuthorization, presets; SendGrid email; barcode page; `card-api`. |
| `inventory` | `/inventory/` | Cookery (`crafting`: batches from Recipe/RecipeStep/RecipeIngredient, shared CookingSessions, Transformations such as freeze/thaw; `cookery.py`), Availability screen, shopping log. `craft_component()` lives here. |
| `deliveries` | `/deliveries/` | Courier PWA, CourierShift, Google ETA, web push. |
| `events` | — | `Event` decides "business day"; online ordering is gated on it (context processor + server check). |
| `new_admin` | `/admin/` | Custom superuser dashboard. Django admin is at `/advanced-admin/` (stations, devices, dishes...). |
| `users` | `/users/` | Login (`LOGIN_URL`), register, profile; Staff/Customer profiles. |
| `misc_tools` | `/utils/` | Image colour extraction (menu theming), TTS, SendGrid helper. |
| `app_switcher` | `/apps/` | Tile launcher. |
| `pavilion` | `/events/` | Project package; `realtime.py` = SSE. |

Frontend: **render with Django templates and keep pages current with SSE**; don't build screens in JS. New screens
follow Cookery: each screen a template + URL, buttons are POST forms that redirect back, Django messages for toasts,
`static/js/live-page.js` for background submits, swapping changed `data-region`s and SSE refetches. JS is for what
HTML can't do (countdowns, keypads). Vanilla ES-module JS lives in `<app>/static/<app>/`. No build step, no frameworks (the
mockups in `docs/mockups/` use React only as a viewer; don't bring it in). The register, drawer, lock and order
marking (KDS) screens and the app switcher share `pos_server/staff_layout.html`, `staff.css` and `staff-ui.js`; every
rail ends with `rail_foot.html` (Apps, Lock on register screens, clock). **Staff screens must work in
portrait** (the KDS runs on vertical TVs): `staff.css` turns the nav rail into a bottom row, and each screen stacks its
panels in an `@media (orientation: portrait)` block placed last in its CSS. Check new screens at 1080×1920, 800×1280
and 1280×800. Shared CSS kit is
`static/css/AD-Elements/`, shared JS in `static/js/` (`live.js`). Each app has its own `layout.html`, `layers.css`,
and `project-styles.css`. JS strings use `gettext()`. Build DOM from data with `textContent`, never `innerHTML` with
customer-supplied text.

## Domain essentials

- **Menu → Dish → DishComponent → Component → ComponentIngredient → Ingredient.** `DishComponent.adjustable` parts can
  be left out or doubled (extra portions cost `Component.extra_price`). `OptionGroup`/`Option` are pick-one adjustments
  owned by a Component (offered by every dish using it; an option can swap the component, e.g. oat milk) or by a Dish
  (size, dressing). `Extra` = add-ons (charged) and condiments (free, counted) for a dish or combo.
- **Combo → ComboSlot → ComboSlotOption(dish, price change).** A slot may `ask_on_add` ("Choose a drink") or
  `allow_none`. Each picked dish is made at its own station. Variants (S/M/L) are a "Size" option group.
- **Stations:** `Dish.station` → `Station` (code, name, icon, menu_heading, position). Per-order status is
  `OrderStation(order, station, status)`: 0 pending approval · 1 being made · 2 done · 3 rejected; no row = not involved.
  POS orders start at 1, online/delivery at 0 (approved on the KDS). Change statuses only via `Order.set_station_status()`.
- **Create orders only via `pos_server.orders.create_order()`.** It prices the cart *lines* with
  `pricing.price_cart()` (never trust browser prices/totals; line format is in `pricing.py`'s docstring), checks stock,
  takes stock for what was actually picked, records `OrderItem`/`OrderDiscount`/`OrderPayment` rows, charges gift cards
  with a `GiftCardAuthorization`, and rolls back on `OrderError`. `collect_order()` is the order → dict serializer for
  screens (`dishes` = what stations make, `lines` = what was sold).
- **Discounts:** `Discount` without a code is an *offer* listed on the register; with a code it's a *coupon*
  (`online=True` to allow it in the store). Conditions (dates, weekdays, time window, min subtotal/items, eligible
  dishes/combos, max uses) are checked in `pricing.py`. Typed-in discounts need `pos_server.apply_manual_discount`.
  Tax is `settings.TAX_RATE` (env, default 0), charged after discounts.
- **Quantities are in the smallest unit**: ingredients and components are counted in `g`, `ml` or `ea` only (no kg/l), and every
  amount measured in them (recipes, dish parts, stock, par) uses that unit. Screens show kg / L when numbers get big.
- **Money is Decimal** (`Dish.price`, `Order.total`, gift cards, transactions). Square wants integer cents: `square.to_cents()`.
  Send `float()` only in JSON for JS.
- **Online payments:** only the server marks a `Transaction` paid; `Transaction.consume()` spends it once. Online card
  payments are authorize-only, captured when every station approves, voided/refunded on rejection.
- `Order.picked_up` defaults True and is set False when any station is involved. "Done" means picked up.
- Rejecting an order **deletes** it and stores an `online_store.RejectedOrder` with the reason.
- One active menu: saving an active `Menu` deactivates the others. Use `Menu.active()` (may be None), never `.get(is_active=True)`.

## Access control

Customers can register accounts, so **staff pages need `staff_required` / `superuser_required` / `api_staff_required`
from `pos_server/devices.py` (or `register_staff_required` in `register.py`, which sends people to the lock screen),
never bare `login_required`.** On the register only, staff sign in with a hashed 4-digit `Staff.pin` (set in
`/advanced-admin/` → Users), rate-limited per device; the lock screen lists the device's last 3 users
(`RegisterSignIn`), and anyone else signs in with their password first. POS/KDS views also use `device_required` (signed cookie
for a registered `EligibleDevice`; enforced when `ENFORCE_DEVICE_CHECK`, default on in production). Redirects to a
`next` URL go through `safe_next_url()`.

## Real-time: read before touching screens

Screens update over **SSE** (`pavilion/realtime.py`). Model signals call `publish("orders"|"inventory"|"checkout")`
after commit; `/events/<topic>` streams nudges; screens refetch their JSON endpoint via `onLiveUpdate()` in
`static/js/live.js` (no polling: screens refetch on each event and on every (re)connect, and reconnect if the 15 s `ping` heartbeat stops). Redis (`REDIS_URL`) carries events and the cache between workers in
production; without it an in-process broker is used. POS ↔ customer display talk via `BroadcastChannel` (same browser).
**Don't add module-level/global state for cross-request data**: per-process memory is what broke the original SSE.
Use the DB, the cache, or `publish()`.

## Media

Dev serves `./media` at `/media/` (stored names are `files/<kind>/<name>`, so files are at `media/files/...`).
Production uses `storages.backends.s3.S3Storage` against RustFS (`RUSTFS_*`). Never build media URLs by hand or read
media from disk; use `field.url` / `default_storage`.

## Django version

Django 6.1 (Python 3.12+). Don't use deprecated APIs: check new code against the release notes, and `manage.py test`
turns every `RemovedInDjango…Warning` into an error (`pavilion/test_runner.py`), so keep it green. Pass data to JavaScript with
`{{ value|json_script:"id" }}`, never by pasting JSON into a JS string (`JSON.parse('{{ x|safe }}')` broke when
Django 6.0's serializer started ending output with a newline, and it's an injection risk).

## Conventions in this codebase

- Function-based views that switch on `request.method` (GET page, POST/PUT/DELETE JSON from `fetch`). JSON bodies
  are read with `json.loads(request.body)`. CSRF goes via the `X-CSRFToken` header read from a `{% csrf_token %}` input.
- Cross-app imports are common (`online_store` imports from `pos_server.orders` and `pos_server.views`).
- Wrap user-facing strings in `gettext_lazy as _` / `{% trans %}`.
- Use `logging.getLogger(__name__)`, not `print()`.

## Open issues (full list: overview §9)

- The `.env` keys (Square, SendGrid, Google, VAPID) were exposed by the old media catch-all and must be rotated.
- Square popup/Terminal, SendGrid and web push have only been tested with mocks; Docker + Redis not yet run end-to-end.

## Git

- Solo project. Current work is on `fix-all-problems-for-real`. **Don't create new branches unless asked.**
- Commit each fix as its own commit with a descriptive message; **don't push unless asked**.
- The three `origin/copilot/*` branches are superseded by the overhaul; `stash@{0}` (menu fonts/styles) is unapplied.
