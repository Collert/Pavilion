# CLAUDE.md

Pavilion is a Django restaurant-management app (POS, kitchen display, online ordering, deliveries, gift cards,
inventory). It was built for a small church-community café in New Westminster, BC. Solo hobby project. It was
overhauled on 2026-10-13 (branch `fix-all-problems-for-real`), but **verify behaviour in the code before trusting
docstrings, the README, or `.github/copilot-instructions.md`.** The docstrings are mostly AI-generated and the other two
files are partly out of date.

**Full deep-dive: [docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md)**. It covers every app, the flows, the
real-time design, open issues, and branches/stashes. Read the relevant section before working on a feature, and update
it when you change how something works.

## Running locally (Windows)

- Use **`venv\`**, not `.venv\` (incomplete). `venv\Scripts\python manage.py runserver` (ASGI via daphne) →
  `http://127.0.0.1:8000/en/apps/`
- All page URLs carry a language prefix: `/en/`, `/uk/`, `/zh-hk/` (`i18n_patterns`). Only `/jsi18n/` and `/events/<topic>` don't.
- Fresh DB: `manage.py migrate` then `manage.py seed_demo` (logins admin/admin, cashier/cashier, courier/courier,
  customer/customer). `seed_demo` refuses to run with DEBUG off or on a DB that already has dishes/orders.
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
| `pos_server` | `/restaurant/` | **Core.** Menu, Dish, Component, Ingredient, Station, Order, OrderStation, OrderDish, EligibleDevice. POS register, KDS, customer display, status board, Square webhook. `orders.py` = order creation/pricing/serialization; `devices.py` = access decorators. |
| `online_store` | `/` | Customer PWA: menu, cart, checkout (`place_order`), order status/history. PromoContent, RejectedOrder. |
| `payments` | `/payments/` | Square: Terminal checkout and pairing, Web Payments popup, capture/void (`square.py`). Transaction, PaymentAuthorization, SquareTerminal. |
| `gift_cards` | `/gift-cards/` | GiftCard, GiftCardAuthorization, presets; SendGrid email; barcode page; `card-api`. |
| `inventory` | `/inventory/` | Crafting, shopping log, recipes, availability toggles. `craft_component()` lives here. |
| `deliveries` | `/deliveries/` | Courier PWA, CourierShift, Google ETA, web push. |
| `events` | — | `Event` decides "business day"; online ordering is gated on it (context processor + server check). |
| `new_admin` | `/admin/` | Custom superuser dashboard. Django admin is at `/advanced-admin/` (stations, devices, dishes...). |
| `users` | `/users/` | Login (`LOGIN_URL`), register, profile; Staff/Customer profiles. |
| `misc_tools` | `/utils/` | Image colour extraction (menu theming), TTS, SendGrid helper. |
| `app_switcher` | `/apps/` | Tile launcher. |
| `pavilion` | `/events/` | Project package; `realtime.py` = SSE. |

Frontend: Django templates plus vanilla ES-module JS in `<app>/static/<app>/`. No build step. Shared CSS kit is
`static/css/AD-Elements/`, shared JS in `static/js/` (`live.js`). Each app has its own `layout.html`, `layers.css`,
and `project-styles.css`. JS strings use `gettext()`. Build DOM from data with `textContent`, never `innerHTML` with
customer-supplied text.

## Domain essentials

- **Menu → Dish → DishComponent → Component → ComponentIngredient → Ingredient.** A Component with `child_dishes` is a
  *choice* (e.g. "Drink" → Coffee/Tea). A Dish made only of choices is never prepared itself; its picks are.
- **Stations:** `Dish.station` → `Station` (code, name, icon, menu_heading, position). Per-order status is
  `OrderStation(order, station, status)`: 0 pending approval · 1 being made · 2 done · 3 rejected; no row = not involved.
  POS orders start at 1, online/delivery at 0 (approved on the KDS). Change statuses only via `Order.set_station_status()`.
- **Create orders only via `pos_server.orders.create_order()`.** It prices the cart from the DB (never trust browser
  prices/totals), checks stock, takes stock per quantity, charges gift cards with a `GiftCardAuthorization`, and rolls
  back on `OrderError`. `collect_order()` is the order → dict serializer for screens.
- **Money is Decimal** (`Dish.price`, `Order.total`, gift cards, transactions). Square wants integer cents: `square.to_cents()`.
  Send `float()` only in JSON for JS.
- **Online payments:** only the server marks a `Transaction` paid; `Transaction.consume()` spends it once. Online card
  payments are authorize-only, captured when every station approves, voided/refunded on rejection.
- `Order.picked_up` defaults True and is set False when any station is involved. "Done" means picked up.
- Rejecting an order **deletes** it and stores an `online_store.RejectedOrder` with the reason.
- One active menu: saving an active `Menu` deactivates the others. Use `Menu.active()` (may be None), never `.get(is_active=True)`.

## Access control

Customers can register accounts, so **staff pages need `staff_required` / `superuser_required` / `api_staff_required`
from `pos_server/devices.py`, never bare `login_required`.** POS/KDS views also use `device_required` (signed cookie
for a registered `EligibleDevice`; enforced when `ENFORCE_DEVICE_CHECK`, default on in production). Redirects to a
`next` URL go through `safe_next_url()`.

## Real-time: read before touching screens

Screens update over **SSE** (`pavilion/realtime.py`). Model signals call `publish("orders"|"inventory"|"checkout")`
after commit; `/events/<topic>` streams nudges; screens refetch their JSON endpoint via `onLiveUpdate()` in
`static/js/live.js`, with a slow fallback poll. Redis (`REDIS_URL`) carries events and the cache between workers in
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
