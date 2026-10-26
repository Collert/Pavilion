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
- All page URLs carry a language prefix: `/en/`, `/uk/`, `/zh-hk/` (`i18n_patterns`), including the JS translation
  catalog (`/en/jsi18n/`, cached by browsers for a day). Only `/events/<topic>` and `/i18n/` (Django's
  `set_language`, which the language pickers post to) don't.
- **Languages**: `settings.SUPPORTED_LANGUAGES` is every language with translations; the store turns them on and off
  and picks the default on the admin's Languages page (`StoreSettings.languages/default_language/match_device`), and
  `admin_portal/languages.py` applies them to `LANGUAGES`/`LANGUAGE_CODE`/`LANGUAGE_MATCH_DEVICE` when Pavilion starts
  (`AdminPortalConfig.ready`), so saving asks for a restart (the page does it: SIGHUP to uvicorn's supervisor, or a
  touch for the reloading dev server). Never read `settings.LANGUAGES` at import time. A person's language is
  `users.Preference` (`language_of(user)` for emails; `users/middleware.py` starts signed-in people in it and saves the
  one they browse in). Pickers: the store header (`themes/neighbourhood/_language.html`, only with 2+ languages) and the
  account profile page (staff use it too; `/users/profile` redirects there). Language codes: compare with
  `users.models.enabled_language()`, Django lowercases them (`zh-hk` vs `zh-HK`).
- Fresh DB: `manage.py migrate` then `manage.py seed_demo` (logins admin/admin, cashier/cashier, courier/courier,
  customer/customer; register PINs Alex 0000, Dana 1234, Priya 2468). `seed_demo` refuses to run with DEBUG off or
  on a DB that already has dishes/orders. `manage.py seed_recipes` adds the Cookery mockup's recipes to any DB
  (safe on real data, re-runnable).
- **Every time a model is created or changed, update `pos_server/management/commands/seed_demo.py`** so it still runs
  on a fresh database (`migrate` then `seed_demo`) and fills in the new model/fields with realistic data.
- Tests: `venv\Scripts\python manage.py test` (pavilion, pos_server, online_store, gift_cards). Square and listmonk are
  mocked. Add tests with every fix. `requirements-dev.txt` adds `fakeredis`.
- Quick smoke test without a server: `django.test.Client(HTTP_HOST='localhost')` + `force_login(user)`.
- Daily crons in production: `manage.py catering_deposits`, `gift_cards_cleanup`, `square_refresh`.
- Settings switch on `ENVIRONMENT=production` (Postgres from `DATABASE_URL`, a connection string read by dj-database-url, or the `POSTGRES_*`/`DB_*` parts; DEBUG off, Redis + `SECRET_KEY` required, S3 media) vs anything
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
| `pos_server` | `/restaurant/` | **Core.** Menu, Dish, Combo, Component, OptionGroup/Option, Extra, Ingredient, Station, Order, OrderItem, OrderDiscount, OrderPayment, Discount, CashDrawerSession, EligibleDevice. Register + lock screen + cash drawer (`register.py`), the register's order History (`history.py`: receipt, send, refund), KDS, customer display, status board, Square webhook. `pricing.py` = cart pricing/discounts/tax; `orders.py` = order creation/serialization; `devices.py` = access decorators. |
| `online_store` | `/` | Customer store. Drawn by a **theme** (see "Store themes" below): home, menu + dish sheet, checkout (`pages.py`, session cart in `cart.py`), the placed order, catering (`catering/views.py`), sign in/join and the customer account (`account.py`; the account pages use Pavilion's own look), and events and services. RejectedOrder. |
| `payments` | `/payments/` | Square (`square.py`): Terminal payments, Web Payments popup, capture/void/refund, saved cards. Transaction, PaymentAuthorization. **The store's own Square account** is connected with OAuth on the admin's Payments page (`square_oauth.py`, `admin_portal/payment_settings.py`; `SquareConnection`: tokens encrypted with a key from SECRET_KEY, `secrets.py`; access renewed when under a week is left and daily by `manage.py square_refresh`; the location picked there). `square_client` builds a client for that account on every use (else `SQUARE_ACCESS_TOKEN`, development) and `square.location_id()` is its location. Pavilion's app is `SQUARE_APPLICATION_ID/SECRET`; Square calls `/square/oauth/callback` and `/square/webhook` (no language prefix). **Each register has its own Terminal** (`SquareTerminal.register`; paired from the register's Screen sheet with a device code, finished by Square's `device.code.paired` webhook or Check; there's no store-wide Terminal to fall back on; in the sandbox there's nothing to pair: the sheet offers Square's test device ids, `square.SANDBOX_TERMINALS`, approvals up to $25). **Each card payment is a `TerminalCheckout` row** (register, Terminal, amount, status): `terminal.checkout.updated` webhooks update the row and publish `checkout`, and the register asks for its own checkout (`register/card/<id>`; `?refresh=1` asks Square when no webhook arrives, POST cancel stops the Terminal). `create_order` claims a checkout once (`claim_checkout`: this register's, COMPLETED, that amount). **A register without a Terminal** takes cards on the customer's phone instead: a `TerminalCheckout` of kind `phone` with a QR code for `/payments/pay/<link>` (`views.phone_pay`, `square.charge_phone`: the register's amount, idempotent, 15 minutes); the register waits on it the same way. The card form (`payments/_card_form.html` + `card-form.js`, styled with the store theme) serves that page and the store's "Pay by card" sheet (`square-checkout.html`). Square's card fields need HTTPS (or `http://localhost` in development). Never keep payment state in the cache or memory. |
| `gift_cards` | `/gift-cards/` | GiftCard, GiftCardAuthorization, presets; email; barcode page; `card-api`. **A gift card's number is never stored**: only `number_hash` and `last4`; look numbers up with `GiftCard.find(number)` and pay with `(number or GiftCard, amount)`. The number exists only on the instance that made it (`card.number`, shown once: emailed, printed or read out); later emails send a signed balance link (`balance/<token>`). Each balance change is a `GiftCardEvent` (`charge_card`/`load_card` record them); `frozen` cards can't pay; empty cards are deleted 30 days after `empty_since` (daily cron: `manage.py gift_cards_cleanup`; orders keep their `GiftCardAuthorization` with the card blank). `GiftCardDesign` (the admin's Gift card designs page): a card's picture, amounts (and a custom range), where it's sold (`online`, `on_register`) and a season that repeats every year; deleted ones with cards are `archived`. Staff issue, freeze, adjust and resend on the admin's Gift cards page (`giftcards.py`). **Selling gift cards**: carts take `gift_sales` next to their lines (`price_cart`/`create_order(gift_sales=...)`; no tax or discounts, gift and staff cards can't pay for them, refunds leave them out: `refunds.gift_part`); `create_order` issues them (`GiftCard.sold_on`) and puts `[(card, number)]` on `order.new_gift_cards`. The register's Gift cards tab sells them (shows the numbers once after paying, emails those with an address); the store's `/gift-cards` page sells one at a time as its own order, charged straight away (the theme links it in the header between Menu and Catering and has a home section, `show_gifts`, while a design is sold online: `storefront.gift_designs`). Staff cards (`StaffCardType`, `StaffCard`, `StaffCardUse`; edited on the admin's Staff cards page): balance cards that refill on a schedule and pay like gift cards, and discount cards; rules, refills, the discount and putting money back are in `staff_cards.py`; `price_cart`/`create_order` take `staff_cards`. |
| `inventory` | `/inventory/` | Cookery (`crafting`: batches from Recipe/RecipeStep/RecipeIngredient, shared CookingSessions, Transformations such as freeze/thaw; `cookery.py`), Availability screen, shopping log. `craft_component()` lives here. |
| `deliveries` | `/deliveries/` | Courier PWA (Cookery-style templates): shifts, accept/pass, steps to the door with a proof photo (`Delivery.phase()`), travel times (`routes.py`: addresses found with Nominatim, times from a Valhalla matrix, both OpenStreetMap services set on Store info; compose profiles `geocoding`/`routing` run them; OpenStreetMap's public Nominatim only within its policy: one lookup a second, cached), web push. Delivery orders reach the KDS only once a courier accepts (`hidden_order_ids()`). |
| `events` | — | Store hours: `StoreHours` (one row: the week as minute ranges, past 1440 = after midnight; pickup/delivery cutoffs, last call, ordering ahead, `paused_until`) edited on the admin's Store hours page and worked out in `events/hours.py` (`Hours`): the store's open status, hours and checkout schedule all come from it. `Event`s (admin's Events page) are laid over the hours: open for an event or closed (holidays too), each for the channels ticked (dine-in, pickup, delivery; closing wins), with an optional banner on the store. |
| `services` | — | Service (staff, rentals, venue, delivery, classes, extras: pricing, details, deposit, booking rules; `quote()`) and Booking. Edited in the admin's Services page. |
| `audio` | `/audio/` | The music library (`Track`, `Playlist`: uploaded in the admin's Library; the browser reads the tags (`audio/static/audio/tags.js`) and the file is kept as is under `music/` in the media storage, `library.py`; the server never opens a song) and `AudioChannel`s. Music channels are radio stations playing a queue (`music.py`: one timeline, every screen plays the same song at the same moment straight from storage; play now/next/last, shuffle, repeat off/all/one, volume); voice channels are push-to-talk over a WebSocket (`voice.py`, routed in `pavilion/asgi.py`: one talker at a time, Opus packets relayed through the broker; who's talking and the last hour in the cache, voices never recorded) and carry spoken order calls (`calls.py`). Set up on the admin's Audio channels page; staff play music and talk in the **Audio app** (`/audio/`: `views.py`, `pages.py`, designs `AudioRail`/`AudioChannels`/`AudioMusic`/`AudioVoice`). |
| `team` | — | The schedule: `Shift` (who works which role when; minutes since midnight; unpublished until Publish, which emails staff `schedule_changed`), `ParSet` (how many people each role needs by hour: the weekly pars and seasonal ones that add or replace), `Availability` and `TimeOff` (decided on the admin's Availability requests: `requests.py`; approving can move the shifts they clash with to open shifts, and emails `request_decided`). `schedule.py` works out a day's needs and what's wrong with a shift. Edited on the admin's Shifts and Shift pars pages. Staff ask for time off and availability changes, and pass shifts on, from their store account (`online_store/work.py`: Shifts, Up for grabs, Availability and time off): `ShiftOffer` (`swaps.py`) gives a published shift away (the first coworker with the role to grab it gets it) or asks one coworker for a hand-off or a swap (they say yes, then a manager approves on Availability requests); changes publish `schedule`. The **time clock** (`/team/clock`, `clock.py` + `clock_views.py`; designed after the lock screen, no mockup) runs on a registered device with nobody signed in: people pick their name and type their PIN (or use email and password) for one short visit, then clock in, start or end an unpaid break, or clock out (`TimeEntry`, `TimeBreak`: what actually happened; a `Shift` is the plan). Clocking in more than an hour early or off the schedule needs a manager's PIN (`act.clock`); off the schedule it adds a published Shift that follows the punches. Clocking in refills "every shift" staff cards. |
| `catering` | `/catering/` | Catering orders (CateringOrder/Line/Message, CateringSettings): `flow.py` does every step (accept charges the card on file, propose changes, the customer's counter-proposals and cancelling, reject/cancel refunds, deposit holds; each version kept as a CateringVersion), `pricing.py` the money, `store.py` the store's quote builder; answered in the admin's Catering page; `/catering/` builds a quote, `<token>` is the customer's request page. Daily cron: `manage.py catering_deposits`. Never on the register or KDS. |
| `screens` | `/screens/` | Screens built from pre-made widgets (status boards, signage, menu boards): `Screen.layout` is a JSON tree rendered on the server, `Screen.popups` the pop-ups over it, `ScreenSession` one display of a screen with sessions (a register pairs with it). See "Screens and widgets" below. |
| `admin_portal` | `/admin/` | The back office, rebuilt from the canvas's `Admin*` artboards page by page (rail in `nav.py`). Who sees which page and what they can do there comes from their groups (`access.py`). `StoreSettings.load()` = the store's settings row, read from the cache (saving clears it; a queryset `.update()` doesn't, so call `StoreSettings.forget()` after one). **Store info** (Settings, `store_info.py`) holds what used to be env settings: name, address, phone, email, tax rate, time zone (applied at startup like languages, restart to change), music space, the Valhalla and Nominatim servers and the weather key (encrypted; the env settings only fill in); read them through `store_info`'s functions (`tax_rate`, `full_address`, `music_limit_bytes`, `routing_url`, `geocoder_url`, `weather_key`, `contact_email`). Django admin is at `/advanced-admin/` (everything not rebuilt yet). |
| `users` | `/users/` | Staff login (`LOGIN_URL`), register, profile, `welcome/<uid>/<token>` (a new staff member sets their password from the invite); Staff profiles (phone, photo, emergency contact, `Role`s: the jobs people do, with starting groups given to whoever gets them; edited on the admin's Roles page), `GroupInfo` (a Django `Group`'s description, colour, icon and `perms`, the `access.py` codes it grants; edited on the admin's Groups page; members of the `owners` one are superusers), Customer profiles (with staff notes, `email_verified` and a `pending_email` waiting for its link), a customer's SavedCard and SavedAddress (`is_default`; the store's account pages and the admin's Customers page edit them). `accounts.py` sends the account emails: confirm your email (on sign-up), confirm a new email (a staff member changed it; the old address is told), and a set-a-new-password link (`welcome/<uid>/<token>` serves customers too); `verify/<uid>/<token>` confirms. Staff are edited on the admin's Staff page (`admin_portal/staff.py`). |
| `misc_tools` | `/utils/` | Image colour extraction (menu theming), TTS, and email through listmonk: `emails.py` lists every email (key, group, recipient, data fields, subject), its template is `templates/emails/listmonk/<key>.html` (Django `{% trans %}` text plus listmonk's own code via `{% go %}` / `tx.<field>`, `templatetags/listmonk.py`), `listmonk.py` connects (the admin's Email page, kept on `StoreSettings.listmonk_*`; `LISTMONK_*` settings fill in) and installs them once per language that's on (`EmailTemplate`), plus a newsletter campaign template. People: everyone with an account is a listmonk subscriber in the private "<store> users" list (transactional only: never campaigns, so nobody can unsubscribe from it; listmonk sends transactional email even to blocklisted subscribers) and, with "Monthly menu email" ticked, in "<store> customers" (newsletters). Synced in the background when listmonk is connected and on every account, menu-email or language change (`users.models` signals → `listmonk.sync_later`); an address with no account becomes a list-less subscriber if listmonk refuses it (older versions ignore `subscriber_mode`). `email.send(kind, to, data, language=, user=, fallback=)` writes in the recipient's language (`language_for`); not connected = log only; `EmailSwitch` turns one off. **Adding an email**: a `Kind` in `emails.py`, its template, then `send`. |
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
- **Stations:** `Dish.station` → `Station` (code = the key screens filter on, never shown; name, `ticket_code`, icon from
  `pos_server/station_icons.py`, colour, menu_heading, position, `target_minutes` (tickets amber at it, red at double),
  `taking_tickets` (off = its dishes are sold out), `archived`; edited on the admin's Stations page). Per-order status is
  `OrderStation(order, station, status)`: 0 pending approval · 1 being made · 2 done · 3 rejected; no row = not involved.
  POS orders start at 1, online/delivery at 0 (approved on the KDS). Change statuses only via `Order.set_station_status()`.
- **Create orders only via `pos_server.orders.create_order()`.** It prices the cart *lines* with
  `pricing.price_cart()` (never trust browser prices/totals; line format is in `pricing.py`'s docstring), checks stock,
  takes stock for what was actually picked, counts disposables (packaging) off stock for the order type and adds their
  fees (`OrderFee`, after tax: total = subtotal − discount + tax + fees + tip; `tip` is the store's "Tip the team"), records `OrderItem`/`OrderDiscount`/`OrderPayment` rows, charges gift cards
  with a `GiftCardAuthorization`, and rolls back on `OrderError`. `collect_order()` is the order → dict serializer for
  screens (`dishes` = what stations make, `lines` = what was sold).
- **Discounts:** `Discount` without a code is edited on the admin's Discounts page: an *offer* the cashier picks on
  the register, or `automatic`, applied by itself to every order it fits (`PricedCart.auto_discount`: the one saving the
  most; next to a coupon or other order discount only if it `stacks`, else the bigger saving wins; `audience` everyone,
  signed-in customers, or staff = a staff card on the order; a `badge` shows "20% off" on its dishes in the store and on
  menu boards while it runs, `pricing.badges`). With a code it's a *coupon* (edited on the admin's Coupons page). `channels` (store, pickup, delivery) says where it works (`on_register`/`online`
  follow it). Conditions (dates, weekdays, time window, min subtotal/items, eligible dishes/combos, max uses, a
  percent's `max_amount`, `per_customer` and `first_order` for signed-in customers) are checked in `pricing.py`. An
  order-wide coupon's `target` decides what it comes off: the order, the picked dishes/combos, the dishes on `menus`,
  or the delivery fee (`events.StoreHours.delivery_fee`, a fee line on delivery orders; a coupon off it lowers the
  fees, not the taxed subtotal). Typed-in discounts need the `act.discount` action (a group gives it) or a manager's sign-off on the register (`pos_server/approvals.py`: someone with it types their PIN; the signed approval travels with the discount spec and `OrderDiscount.approved_by` keeps who).
  Tax is the store's rate from Store info (`admin_portal.store_info.tax_rate()`, a fraction), charged after discounts.
- **Costs come from ingredients**, the things the restaurant buys: `Ingredient.cost` = `pack_price / pack_size`
  (set on save). A component's cost and ingredients (`pos_server/dish_info.py`) come from its prep recipe's
  ingredients (batch over yield), else its per-unit ComponentIngredients, else the component a transformation makes
  it from. Never store a cost on a component or dish.
- **Allergens and diets** (and alcohol: `Ingredient.alcohol`, which staff cards can leave out) come from ingredients (`Ingredient.allergens` from `ALLERGENS`, plus `meat`/`fish`/`animal`
  flags); dishes work theirs out in `pos_server/dish_info.py` (also plate cost and food cost %). Don't store them on dishes.
- **Quantities are in the smallest unit**: ingredients and components are counted in `g`, `ml` or `ea` only (no kg/l), and every
  amount measured in them (recipes, dish parts, stock, par) uses that unit. Screens show kg / L when numbers get big.
- **Money is Decimal** (`Dish.price`, `Order.total`, gift cards, transactions). Square wants integer cents: `square.to_cents()`.
  Send `float()` only in JSON for JS.
- **Online payments:** only the server marks a `Transaction` paid; `Transaction.consume()` spends it once. Online card
  payments are authorize-only, captured when every station approves, voided/refunded on rejection. **Register card
  payments** come from the register's Terminal: the order needs a completed `TerminalCheckout` of that register for
  that amount, used once (`payments.square.claim_checkout`).
- `Order.picked_up` defaults True and is set False when any station is involved. "Done" means picked up.
- Rejecting an order **deletes** it and stores an `online_store.RejectedOrder` with the reason.
- Menus: several can be live at once (`pos_server/menu_schedule.py`): switched by hand (`manual_on`) or automatic (`MenuRule`s:
  weekly times, date ranges, "when another menu starts"); drafts never are. `Menu.live_menus()` by priority (`position`),
  `Menu.active()` = the top one (may be None). Menus that aren't live never show online; each register picks among live ones.

## Screens and widgets

Admins build screens (`/screens/<slug>`, e.g. the pickup `order-board`) out of pre-made widgets; edit them in
the admin's Screens page (`admin_portal/screens.py`: a live preview of the draft in an iframe, the layout tree, the
widget library and each widget's settings built from its JSON; `/advanced-admin/` → Screens still edits raw JSON). `Screen.layout` is one node,
`{"widget": "name", "params": {...}, "children": [...]}`; only `rows`/`columns` take `children` (one per slot, `null`
= empty), so screens are grids nested as deep as needed. `width`/`height` are the design canvas: every size in a
widget is a design pixel, written `calc(var(--u) * 24)` in CSS (`{{ 24|u }}` in templates), so a screen scales to
any display. The server renders the whole tree (`screens/layout.py`); `live-page.js` refetches on the widgets' SSE
topics and swaps the leaf widgets whose HTML changed. Editing a Screen publishes `screens`, which reloads open ones.

**Adding a widget** (all four files share the widget's name, lowercase with `_`):
- `screens/widgets/<name>.json`: `title`, `description`, `template`, `kind` (`visible`, the default: placed in the
  layout's slots; `popup`: shown over the screen when its `trigger` fires, one of order_ready, announcement,
  menu_change, event_reminder, order_summary; `audio`: a sound channel, `channel` music or voice), `icon` (24×24 SVG
  path) and `color` (how the admin's editor shows it), optional `context` (dotted path to
  `fn(request, params) -> dict`, given to the template as `data`; put it in `screens/providers.py`), `live` (SSE
  topics), `css`/`js` (static paths, loaded once per screen), `slots` (int param giving the number of children; containers
  only) and `params`: `{name: {type, default, label, help, options|item|min|max}}`. Types: `string`, `text`, `url`
  (`/...` or `https://` only), `int`, `number`, `bool`, `choice` (`options`), `color` (`#hex`), `size` (number = fr,
  or `"132px"`/`"40%"`/`"auto"`), `list` (`item` = the item's param spec; `min`/`max` = length), `image` (an uploaded
  picture's address). For the editor, a param may add `control` ("slider"), `unit`, `step`, `presets` (colours) and
  `source` ("menus", "dishes", "audio:music", "audio:voice": picked from a list).
- `screens/templates/screens/widgets/<name>.html`: a partial with `params` (validated, defaults filled in), `data`,
  `children` (rendered HTML, containers only), `node_id`, `widget`, `request`. One root element; it's wrapped in
  `<div class="widget widget--<name>" data-region>`. Use the `.screen-card` look and the `:root` colours in
  `screens/static/screens/screens.css`. Params are autoescaped; only `color`/`size`/`choice` values go in `style`.
- `screens/static/screens/widgets/<name>.css` (and `.js` only for what HTML can't do, e.g. the ticking clock).
- A test in `screens/tests.py` (`WidgetDefinitionTests` already checks every definition's files and defaults).

**Pop-ups** (`kind: popup`) are listed on `Screen.popups` and rendered after the layout, each as its own region that
holds its card only while it shows (`layout.render_popups`, `screens/popups.py`): moments (an order ready, a staff
announcement from the register's Screens sheet) are recorded in the cache by `popups.record()` and nudge "popups";
scheduled ones (a menu starting, an event coming up) are worked out at render time. A pop-up's context is
`fn(request, params, now)` returning `show`, `until` (hide) and `next` (look again), turned into a hidden data-ends.

**Audio** widgets (`kind: audio`: `music_channel`, `voice_channel`) are listed on `Screen.audio`, top priority first,
each with an `id` (pop-ups pick it with a `source: "audio:voice"` param) and a store channel (`audio.AudioChannel`,
`params.channel`). The Now playing widget shows one of them (its `channel` param picks the entry). The screen page plays them (`screens/static/screens/audio-player.js`, config from
`screens/views.py: audio_config`): music in step with the channel's timeline (`audio/music.py`) at its volume, straight from the
media storage, voice from its WebSocket, ducking lower channels while a voice speaks; "Tap to turn on sound" until a
tap (or Chrome's `--autoplay-policy=no-user-gesture-required`). Songs on S3 need CORS for the volume control.

**Sessions:** a screen with `sessions` on is opened per display: Apps → Screens → the screen → open or start a
`ScreenSession` (`/screens/<slug>/s/<code>`; the code shows in a corner until a register pairs). A register pairs in
its Screens sheet by typing the code (`screens_pair`; kept in the register's localStorage) and, while it takes
payment, posts its order (`screen_summary`, cents) to the cache for that session only; the `order_summary` pop-up
shows it there. The old same-device customer display (BroadcastChannel) still works alongside.

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
`online_store/storefront.py`. The admin's Online store page (id `branding`, so groups keep `admin.branding.*`; a separate Branding page is planned) edits a draft per theme (`admin_portal.StoreTheme`, store
details in `StoreSettings.draft`) that its preview iframe shows (`themes.previewing`: a superuser's iframe request
while that page is open); Publish makes it live. Only "neighbourhood" (the canvas's `Store*` artboards) exists so far.

## Access control

**Groups grant everything** (`users/access.py`, edited on the admin's Groups page): each `GroupInfo.perms` lists codes:
apps (`app.register`, `app.marking`, `app.drawer`, `app.cookery`, `app.availability`, `app.courier`, `app.screens`,
`app.audio`, `app.admin`), actions (`act.discount` typed-in discounts, `act.cash` paying cash in and out, `act.reject`
rejecting orders on order marking, `act.refund` admin refunds, `act.clock` approving early or unscheduled clock-ins) and admin pages (`admin.<page id>.view|edit|delete`, the
pages being `admin_portal/nav.py`'s rail; `PAGE_LEVELS` lists pages without every level). Check with
`access.can(user, code)` (templates: `{% if can.app_register %}`). Staff in no group open nothing. Being in the
**Owners** group (`users.GroupInfo.owners`) is what makes someone a superuser, who has every code (the admin's Staff
page keeps them in step, only owners make owners or change an owner's profile, and the store always keeps one active
owner). Customers can register accounts, so **staff pages need `app_required("app.…")` / `api_app_required` (or
`staff_required` / `superuser_required` / `api_staff_required` for the few that aren't an app) from
`pos_server/devices.py` (or `register_staff_required` in `register.py`, which sends people to the lock screen), never
bare `login_required`. Admin views use `admin_portal/access.py: admin_page(page, edit={ops}, delete={ops})`**: GET
needs view, posted ops listed in `edit`/`delete` need those (everything else only changes a draft); a new view must
list every op that writes. The layout hides Save/Delete-like buttons people can't use (`ro`/`nodel` body classes). On the register only, staff sign in with a hashed 4-digit `Staff.pin` (set in
`/advanced-admin/` → Users), rate-limited per device; the lock screen lists the device's last 3 users
(`RegisterSignIn`), and anyone else signs in with their password first. POS/KDS views also use `device_required` (signed cookie
for a registered `EligibleDevice`; enforced when `ENFORCE_DEVICE_CHECK`, default on in production). A browser becomes a device on the
device setup page (`/restaurant/check-device`, `views.device_elig`: someone with `admin.devices.edit` names it or picks one, or a device code is
pasted) or by opening any link with `?device-token=<token>` (`devices.DeviceTokenMiddleware`; kiosks keep it in their start URL). Devices, their
codes and kiosk links are on the admin's Devices page (`admin_portal/devices.py`). Redirects to a
`next` URL go through `safe_next_url()`.

## Real-time: read before touching screens

Screens update over **SSE** (`pavilion/realtime.py`). Model signals call `publish("orders"|"inventory"|"checkout")`
after commit; `/events/<topic>[,<topic>...]` streams nudges (a page's topics share ONE stream: browsers allow six
connections per server over HTTP/1.1, and a stream per topic hung pages); screens refetch their JSON endpoint via `onLiveUpdate()` in
`static/js/live.js` (no polling: screens refetch on each event and on every (re)connect, and reconnect if the 15 s `ping` heartbeat stops). Redis (`REDIS_URL`) carries events and the cache between workers in
production; without it an in-process broker is used. POS ↔ customer display talk via `BroadcastChannel` (same browser).
**Don't add module-level/global state for cross-request data**: per-process memory is what broke the original SSE.
Use the DB, the cache, or `publish()`.

**One client must never stall the app.** Under ASGI every synchronous view of a worker shares one thread, so a
flood from one browser queues in front of every screen (a register tapped into hundreds of quotes once stopped the
KDS and the music). `pavilion/throttle.py`: `ThrottleMiddleware` sits first and caps every request per browser
(raw session or device cookie, no database) and per address (`THROTTLE_REQUESTS` per `THROTTLE_WINDOW`), turning
the rest away with a 429 on the event loop; `throttle.allow(scope, who, limit, window)` gives tighter caps to
endpoints a page calls on every change (the register's quote; the store's sheet and checkout posts). Pages that
fetch on every change keep one request in flight and send the latest state when it answers (`register.js`
`runQuote`); a 429 waits `retryAfter`. The test runner sets `THROTTLE = False`; throttle tests turn it on.

## Media

Dev serves `./media` at `/media/` (stored names are `<kind>/<name>`, folders at the storage root: `media/dish_images/...`, or the bucket's top level; there used to be a `files/` folder above them, moved out by `pos_server` migration 0033).
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
