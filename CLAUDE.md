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
- Tests: `venv\Scripts\python manage.py test` (pavilion, pos_server, online_store, gift_cards). Square and listmonk are
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
| `online_store` | `/` | Customer store. Drawn by a **theme** (see "Store themes" below): home, menu + dish sheet, checkout (`pages.py`, session cart in `cart.py`), the placed order, catering (`catering/views.py`), sign in/join and the customer account (`account.py`; the account pages use Pavilion's own look), and events and services. RejectedOrder. |
| `payments` | `/payments/` | Square: Terminal checkout and pairing, Web Payments popup, capture/void (`square.py`). Transaction, PaymentAuthorization, SquareTerminal. |
| `gift_cards` | `/gift-cards/` | GiftCard, GiftCardAuthorization, presets; email; barcode page; `card-api`. |
| `inventory` | `/inventory/` | Cookery (`crafting`: batches from Recipe/RecipeStep/RecipeIngredient, shared CookingSessions, Transformations such as freeze/thaw; `cookery.py`), Availability screen, shopping log. `craft_component()` lives here. |
| `deliveries` | `/deliveries/` | Courier PWA (Cookery-style templates): shifts, accept/pass, steps to the door with a proof photo (`Delivery.phase()`), Google travel times (`routes.py`), web push. Delivery orders reach the KDS only once a courier accepts (`hidden_order_ids()`). |
| `events` | — | Store hours: `StoreHours` (one row: the week as minute ranges, past 1440 = after midnight; pickup/delivery cutoffs, last call, ordering ahead, `paused_until`) and `SpecialHours` (holidays), edited on the admin's Store hours page and worked out in `events/hours.py` (`Hours`): the store's open status, hours and checkout schedule all come from it. `Event` is left for the Events page. |
| `services` | — | Service (staff, rentals, venue, delivery, classes, extras: pricing, details, deposit, booking rules; `quote()`) and Booking. Edited in the admin's Services page. |
| `catering` | `/catering/` | Catering orders (CateringOrder/Line/Message, CateringSettings): `flow.py` does every step (accept charges the card on file, propose changes, the customer's counter-proposals and cancelling, reject/cancel refunds, deposit holds; each version kept as a CateringVersion), `pricing.py` the money, `store.py` the store's quote builder; answered in the admin's Catering page; `/catering/` builds a quote, `<token>` is the customer's request page. Daily cron: `manage.py catering_deposits`. Never on the register or KDS. |
| `screens` | `/screens/` | Screens built from pre-made widgets (status boards, signage): `Screen.layout` is a JSON tree rendered on the server. See "Screens and widgets" below. |
| `admin_portal` | `/admin/` | The back office, rebuilt from the canvas's `Admin*` artboards page by page (rail in `nav.py`). `StoreSettings.load()` = store name. Django admin is at `/advanced-admin/` (everything not rebuilt yet). |
| `users` | `/users/` | Staff login (`LOGIN_URL`), register, profile; Staff/Customer profiles, a customer's SavedCard and SavedAddress (the store's account pages edit them). |
| `misc_tools` | `/utils/` | Image colour extraction (menu theming), TTS, email (`email.py`: listmonk tx templates, one per kind; unset `LISTMONK_*` = log only). |
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

**Designs:** newer mockups live in the Claude Design canvas "Pavilion POS Register"
(<https://claude.ai/artifact/2cp1HSdvDDNgDfVxkTsdDv>), not in `docs/mockups/`. Read it with the Artifact tool:
`list` with `scope: "files"`, then `read` the `project/<Name>.dc.html` artboards you need (`project/canvas.json`
is the index). The courier app (`deliveries`) is `project/Courier-y2s3.dc.html`; the `Courier<State>-*.dc.html` files
only import it at one step (`start="list"`, `"proof"`, …). The canvas also has the register (`Main`), `Lock`, `Drawer`,
`Cookery*`, `Availability`, `Stations`, `OrderBoard-*`, `Apps`, `RailFoot`, the `Admin*` screens and menu boards. Treat
the artboards as a visual spec and rebuild them as Django templates; their markup and fake data aren't ours to copy.

## Domain essentials

- **Menu → Dish → DishComponent → Component → ComponentIngredient → Ingredient.** `DishComponent.adjustable` parts can
  be left out or asked for up to `max_extra` (10) more steps (a piece for whole pieces, else the whole amount; each costs
  `Component.extra_price`). A part can have substitutes (`PartChoice`, named by `DishComponent.choice_title`, e.g.
  "Bun": sesame bun, lettuce wrap +$0): the one picked (cart `swaps`) replaces the component for stock, steps and
  extra price, set on each dish's recipe. `OptionGroup`/`Option` are modifier groups, *pick one* or *pick any* (any
  option many times: add-ons), `required` or not, owned by a Component (offered by every dish using it, like patty
  temperature; edited in the Components editor, listed read-only on the dish) or by a Dish (size, add-ons). `Extra` = what a dish or combo offers on the side (counted). A `Condiment` (1:1 with a component counted in
  servings: house-made from its recipe, or bought as one ingredient whose pack is the case) is priced per order:
  `free_per_order` servings across the whole cart are free, then `price` each (`pricing._charge_condiments`); other
  extras cost `Extra.price` each.
- **Catering** orders are separate from `Order`: lines are dishes (catering price for the quantity), services
  (`Service.quote`), condiments and disposables (priced by hand), each keeping its price. Nothing is charged until
  the order is accepted (or the customer agrees to proposed changes); equipment deposits are card holds placed close
  to the event. Change them only through `catering/flow.py`.
- **Combo → ComboSlot → ComboSlotOption(dish, upcharge, is_default).** A combo is a set price + upcharges or a percent
  off the picks (`Combo.unit_price`); each slot takes `picks` dishes and is required (starts on its default) or optional.
  Cart lines list every pick (`slots: {id: [{dish, cust}]}`). Each picked dish is made at its own station. Variants (S/M/L) are a "Size" option group.
- **Stations:** `Dish.station` → `Station` (code, name, icon, menu_heading, position). Per-order status is
  `OrderStation(order, station, status)`: 0 pending approval · 1 being made · 2 done · 3 rejected; no row = not involved.
  POS orders start at 1, online/delivery at 0 (approved on the KDS). Change statuses only via `Order.set_station_status()`.
- **Create orders only via `pos_server.orders.create_order()`.** It prices the cart *lines* with
  `pricing.price_cart()` (never trust browser prices/totals; line format is in `pricing.py`'s docstring), checks stock,
  takes stock for what was actually picked, counts disposables (packaging) off stock for the order type and adds their
  fees (`OrderFee`, after tax: total = subtotal − discount + tax + fees + tip; `tip` is the store's "Tip the team"), records `OrderItem`/`OrderDiscount`/`OrderPayment` rows, charges gift cards
  with a `GiftCardAuthorization`, and rolls back on `OrderError`. `collect_order()` is the order → dict serializer for
  screens (`dishes` = what stations make, `lines` = what was sold).
- **Discounts:** `Discount` without a code is an *offer* listed on the register; with a code it's a *coupon*
  (`online=True` to allow it in the store). Conditions (dates, weekdays, time window, min subtotal/items, eligible
  dishes/combos, max uses) are checked in `pricing.py`. Typed-in discounts need `pos_server.apply_manual_discount`.
  Tax is `settings.TAX_RATE` (env, default 0), charged after discounts.
- **Costs come from ingredients**, the things the restaurant buys: `Ingredient.cost` = `pack_price / pack_size`
  (set on save). A component's cost and ingredients (`pos_server/dish_info.py`) come from its prep recipe's
  ingredients (batch over yield), else its per-unit ComponentIngredients, else the component a transformation makes
  it from. Never store a cost on a component or dish.
- **Allergens and diets** come from ingredients (`Ingredient.allergens` from `ALLERGENS`, plus `meat`/`fish`/`animal`
  flags); dishes work theirs out in `pos_server/dish_info.py` (also plate cost and food cost %). Don't store them on dishes.
- **Quantities are in the smallest unit**: ingredients and components are counted in `g`, `ml` or `ea` only (no kg/l), and every
  amount measured in them (recipes, dish parts, stock, par) uses that unit. Screens show kg / L when numbers get big.
- **Money is Decimal** (`Dish.price`, `Order.total`, gift cards, transactions). Square wants integer cents: `square.to_cents()`.
  Send `float()` only in JSON for JS.
- **Online payments:** only the server marks a `Transaction` paid; `Transaction.consume()` spends it once. Online card
  payments are authorize-only, captured when every station approves, voided/refunded on rejection.
- `Order.picked_up` defaults True and is set False when any station is involved. "Done" means picked up.
- Rejecting an order **deletes** it and stores an `online_store.RejectedOrder` with the reason.
- Menus: several can be live at once (`pos_server/menu_schedule.py`): switched by hand (`manual_on`) or automatic (`MenuRule`s:
  weekly times, date ranges, "when another menu starts"); drafts never are. `Menu.live_menus()` by priority (`position`),
  `Menu.active()` = the top one (may be None). Menus that aren't live never show online; each register picks among live ones.

## Screens and widgets

Admins build screens (`/screens/<slug>`, e.g. the pickup `order-board`) out of pre-made widgets; edit them in
`/advanced-admin/` → Screens (the form lists every widget and its params). `Screen.layout` is one node,
`{"widget": "name", "params": {...}, "children": [...]}`; only `rows`/`columns` take `children` (one per slot, `null`
= empty), so screens are grids nested as deep as needed. `width`/`height` are the design canvas: every size in a
widget is a design pixel, written `calc(var(--u) * 24)` in CSS (`{{ 24|u }}` in templates), so a screen scales to
any display. The server renders the whole tree (`screens/layout.py`); `live-page.js` refetches on the widgets' SSE
topics and swaps the leaf widgets whose HTML changed. Editing a Screen publishes `screens`, which reloads open ones.

**Adding a widget** (all four files share the widget's name, lowercase with `_`):
- `screens/widgets/<name>.json`: `title`, `description`, `template`, optional `context` (dotted path to
  `fn(request, params) -> dict`, given to the template as `data`; put it in `screens/providers.py`), `live` (SSE
  topics), `css`/`js` (static paths, loaded once per screen), `slots` (int param giving the number of children; containers
  only) and `params`: `{name: {type, default, label, help, options|item|min|max}}`. Types: `string`, `text`, `url`
  (`/...` or `https://` only), `int`, `number`, `bool`, `choice` (`options`), `color` (`#hex`), `size` (number = fr,
  or `"132px"`/`"40%"`/`"auto"`), `list` (`item` = the item's param spec; `min`/`max` = length).
- `screens/templates/screens/widgets/<name>.html`: a partial with `params` (validated, defaults filled in), `data`,
  `children` (rendered HTML, containers only), `node_id`, `widget`, `request`. One root element; it's wrapped in
  `<div class="widget widget--<name>" data-region>`. Use the `.screen-card` look and the `:root` colours in
  `screens/static/screens/screens.css`. Params are autoescaped; only `color`/`size`/`choice` values go in `style`.
- `screens/static/screens/widgets/<name>.css` (and `.js` only for what HTML can't do, e.g. the ticking clock).
- A test in `screens/tests.py` (`WidgetDefinitionTests` already checks every definition's files and defaults).

Providers run on every refetch and on public screens: keep them cheap, cache outside calls (weather is fetched on
the server and cached 15 min, so the key never reaches the browser) and return only what a public board may show
(order lists show "Priya S." and the number). A widget that needs a refetch at a set time (a highlight ending, a
cache expiring) renders `<span hidden data-ends="<epoch ms>">`. A bad saved param falls back to its default and a
missing or failing widget renders a placeholder, so one widget never takes a screen down.

**Menu templates** are a special kind of widget (`"kind": "menu_template"`, `screens/widgets/menu_<name>.json`): a
menu-board layout from the canvas's `Menu*` artboards (Showcase is built; Chalk, Editorial, Poster, Grid, Letterboard,
Triptych, Departures, Carousel and Neon are still to do). Screens place the `menu_board` widget (param: a menu id);
the template, pages, group slots, timing and the template's params (its look) are set per menu in the admin's Display
tab and kept on `Menu.display` (`screens/menu_boards.py`). A template's JSON adds `orientation`, `width`/`height`,
`groups`, `per_group`, `featured` and `fonts`; its partial gets `b` (`menu_boards.board()`), renders every
`.mb-page` and each slot's `.mb-screen`s, and `screens/widgets/menu_board.js` turns them over.

## Store themes

The online store is drawn by a theme, built like widgets: `online_store/themes/<name>.json` (title, fonts, css,
`thumb`, `contrast` pairs and `groups` of params: string, text, url, bool, int, number, choice, color, image, with
`control`, `presets`, `show_if`...; see `online_store/themes.py`), templates in
`online_store/templates/themes/<name>/` (`base.html` + one per page) and CSS in `online_store/static/themes/<name>/`.
Views render through `online_store.views.theme_page(request, "home.html", context)`; templates get `t` (the theme's
values), `store` (name, logo, address, city, phone, directions), `links`, `status`, `hours` and the like; colours,
sizes and fonts arrive as CSS variables (`themes.css_vars`). What pages show is worked out theme-independently in
`online_store/storefront.py`. The admin's Branding page edits a draft per theme (`admin_portal.StoreTheme`, store
details in `StoreSettings.draft`) that its preview iframe shows (`themes.previewing`: a superuser's iframe request
while Branding is open); Publish makes it live. Only "neighbourhood" (the canvas's `Store*` artboards) exists so far.

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
- Square popup/Terminal, listmonk email and web push have only been tested with mocks; Docker + Redis not yet run end-to-end.

## Git

- Solo project. Current work is on `fix-all-problems-for-real`. **Don't create new branches unless asked.**
- Commit each fix as its own commit with a descriptive message; **don't push unless asked**.
- The three `origin/copilot/*` branches are superseded by the overhaul; `stash@{0}` (menu fonts/styles) is unapplied.
