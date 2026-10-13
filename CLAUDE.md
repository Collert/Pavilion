# CLAUDE.md

Pavilion is a Django restaurant-management app (POS, kitchen display, online ordering, deliveries, gift cards,
inventory). It was built for a small church-community café in New Westminster, BC. Solo hobby project. Many
features are half-finished, so **verify behaviour in the code before trusting docstrings, the README, or
`.github/copilot-instructions.md`.** Those are partly aspirational, and the docstrings are mostly AI-generated.

**Full deep-dive: [docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md)**. It covers every app, the flows, the
real-time history, all known bugs with file locations, and branches/stashes. Read the relevant section before
working on a feature, and update it when you change how something works or fix a listed bug.

## Running locally (Windows)

- Use **`venv\`** (complete), **not `.venv\`** (missing `whitenoise`, so every request 500s).
  `venv\Scripts\python manage.py runserver` → `http://127.0.0.1:8000/en/apps/`
- All page URLs carry a language prefix: `/en/`, `/uk/`, `/zh-hk/` (`i18n_patterns`). Only `/jsi18n/` doesn't.
- `manage.py check` passes. There is **no test suite** on main (`*/tests.py` are empty stubs).
- Quick smoke test without a server: `django.test.Client(HTTP_HOST='localhost')` + `force_login(superuser)`.
- Settings switch on `ENVIRONMENT=production` (Postgres, DEBUG off) vs anything else (SQLite `db.sqlite3`, DEBUG on).
  Secrets live in `pavilion/.env`. Never print or commit its values.
- Translations: `make_translations.bat` (makemessages uk, zh_HK) → `manage.py compilemessages`.
- Avoid `start.bat`/`start.sh` unless asked. They start ngrok and **rewrite `ALLOWED_HOSTS` inside settings.py**.

## ⚠️ Migrations trap

`*/migrations/` is **gitignored**, so migrations exist only on this machine. The local DB has migrations from
`stash@{0}` applied (Menu fonts/style, `MenuStationPosition`, `new_admin.Font`, modeltranslation fields) that the
current code lacks, so `makemigrations` will generate **drops** for them. Ask the user before running
`makemigrations`/`migrate`, and never run them against `db.sqlite3` casually. It holds real order history.

## Architecture map

| App | Prefix | Role |
|---|---|---|
| `pos_server` | `/restaurant/` | **Core.** Models: Menu, Dish, Component, Ingredient, Order, OrderDish, Station, EligibleDevice. POS register, KDS, customer display, status board, Square webhook. |
| `online_store` | `/` | Customer PWA: menu, cart, checkout, order status/history. Models: PromoContent, RejectedOrder. |
| `payments` | `/payments/` | Square: Terminal checkout (`square.py`), Web Payments iframe popup (`cardPaymentPopup.js`, see `payments/README.MD`). |
| `gift_cards` | `/gift-cards/` | GiftCard, GiftCardAuthorization, presets; SendGrid email; barcode page; `card-api`. |
| `inventory` | `/inventory/` | Crafting, shopping log, recipes, availability toggles. `craft_component()` lives here. |
| `deliveries` | `/deliveries/` | Courier PWA, Google ETA, web push. |
| `events` | — | `Event` decides "business day"; `online_store/context_processors.py` gates online ordering on it. |
| `new_admin` | `/admin/` | Custom superuser dashboard (orders, menus, promos, events). Django admin is at `/advanced-admin/`. |
| `users` | `/users/` | Login (`LOGIN_URL`), register, profile; Staff/Customer profiles. |
| `misc_tools` | `/utils/` | Image colour extraction (menu theming), TTS, SendGrid helper. |
| `app_switcher`, `webrtc` | `/apps/`, `/webrtc/` | Tile launcher; abandoned WebRTC experiment. |

Frontend: Django templates plus vanilla ES-module JS in `<app>/static/<app>/`. No build step. Shared CSS kit is
`static/css/AD-Elements/`. Each app has its own `layout.html`, `layers.css`, and `project-styles.css`. JS strings use `gettext()`.

## Domain essentials

- **Menu → Dish → DishComponent → Component → ComponentIngredient → Ingredient.** A Component with
  `child_dishes` is a *choice* (e.g. "Drink" → Coffee/Tea). A Dish made only of choice components is skipped
  when creating OrderDish rows. `Component.save()` cascades stock into `Dish.in_stock`.
- **Stations are hard-coded:** `Dish.station` ∈ `kitchen|bar|gng`, and `Order.kitchen_status|bar_status|gng_status`.
  The `Station` model, `Dish.new_station`, and `Order.status_N_stations` exist but **nothing uses them yet**.
- **Station status codes:** 0 pending approval · 1 approved/in progress · 2 done · 3 rejected · 4 not required.
  POS orders start at 1. Online/delivery orders start at 0 and must be approved on the KDS (`order-marking`).
- `Order.picked_up` defaults True and is set False when any station is needed. "Done" means picked up.
- `Order.save()` refreshes the `active_orders` cache, which is what all screens poll (`/restaurant/active_orders`).
- Rejecting an order **deletes** it and stores an `online_store.RejectedOrder` with the reason.
- Exactly one `Menu` should have `is_active=True`. Many views use `.get(is_active=True)`.

## Real-time: read before touching screens

Screens **poll**: KDS every 11 s, status board every 5 s, and the POS polls Square card status every 1 s. The
original SSE design used module-level Python lists and broke under multi-process deployment.
POS ↔ customer display talk via `BroadcastChannel` (same browser only).
Still per-process today: `LocMemCache` (Square checkout status), `deliveries/globals.active_couriers`, and the
leftover SSE stream in `inventory/crafting.html`. **Don't add new module-level/global state for cross-request data.**
Unmerged `origin/copilot/upgrade-order-display-flow` adds Channels WebSockets but uses `InMemoryChannelLayer`, which
needs Redis to work in production. Details in the overview, §7.

## Conventions in this codebase

- Function-based views that switch on `request.method` (GET page, POST/PUT/DELETE JSON from `fetch`). JSON bodies
  are read with `json.loads(request.body)`. CSRF goes via the `X-CSRFToken` header read from a `{% csrf_token %}` input.
- Superuser gates: `@login_required` + `@user_passes_test(lambda u: u.is_superuser)`.
- Cross-app imports are common (`online_store` imports `pos_server.views.collect_order`, `prettify_dish`, etc.).
  `collect_order()` is the canonical order → dict serializer for screens.
- Wrap user-facing strings in `gettext_lazy as _` / `{% trans %}`.
- Prices: `Dish.price` is a float, gift cards are Decimal. Convert with `Decimal(str(x))` when mixing them.

## Known landmines (full list: overview §9)

- **Media**: dev serves `./media` at `/media/` (stored names are `files/<kind>/<name>`, so physical path is `media/files/...`). Production uses `storages.backends.s3.S3Storage` against RustFS (`RUSTFS_*` env vars, see `settings.py`). Never build media URLs by hand or read them from disk; use `field.url` / `default_storage`. The old catch-all that served `.env` is fixed, but the keys in `.env` were exposed while it existed, so rotate them.
- `misc_tools/classes.py` `len(str)` crashes gift-card emails. Gift-card charges with cents crash (`Decimal -= float`).
- `/deliveries/`, `/restaurant/dashboard`, `/restaurant/day_stats` (no date), and `/restaurant/pair-terminal` return 500.
- The status board JS uses removed fields (`kitchen_done`, `kitchen_needed`, `bar_done`).
- The device allowlist (`authorizedDeviceCheck.js`) is client-side only. The server never checks tokens.
- Docker runs `runserver` and has no migrations in the image. Media isn't served with DEBUG off.

## Git

- Work on `main` is solo. Branch before larger changes. Commit only when asked.
- Unmerged work: three `origin/copilot/*` branches (WebSockets, dynamic stations, N+1 fixes) and `stash@{0}`
  (menu fonts/styles). `websockets` and `Translations` local branches are fully merged.
