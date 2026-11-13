# Pavilion

**Self-hosted restaurant software that runs the whole place: the till, the kitchen, the online store, the couriers,
the TVs on the wall and the music in the dining room.**

![Pavilion](https://github.com/user-attachments/assets/a5b7b5e8-17d0-422f-96b7-feb51789cf3c)

Pavilion is one Django app that replaces the half-dozen subscriptions a small restaurant usually pays for. It's built
for touch screens and kitchen TVs, it updates live everywhere at once, and you host it yourself: your data, your
domain, your colours.

---

## Highlights

- 🧾 **A real POS register.** Touch-first cashier screen with combos, modifiers, substitutions, discounts and gift
  cards. Staff sign in with a 4-digit PIN, and each register pairs its own **Square Terminal**. No Terminal? It shows a
  QR code and the customer pays on their own phone.
- 👩‍🍳 **Kitchen display per station.** Every dish goes to the station that makes it (grill, bar, pastry…). Tickets
  turn amber, then red, as they run late. Works on portrait TVs and is driven entirely by keyboard.
- 🛍️ **An installable online store.** Customers order pickup or delivery from a themeable PWA with push
  notifications, saved cards and addresses, catering quotes, gift cards and services. Every page is drawn by a theme
  that you edit and preview live before publishing.
- 🎨 **Store themes** *(work in progress)*. The store's look comes from a theme, and you set its colours, fonts,
  images and sections from the admin. The plan works like Shopify: designers publish themes, and store owners install
  one and use it with their own store. For now there's one built-in theme, "Neighbourhood". Sharing and installing
  themes is still being built.
- 🚲 **A courier app.** Couriers start a shift, accept or pass on deliveries before the kitchen even sees them, follow
  the steps to the door and snap a proof photo. Travel times come from your own OpenStreetMap routing
  (Valhalla + Nominatim), so there's no Google bill.
- 📺 **A screen builder.** Build order boards, signage and menu boards out of widgets (order lists, weather, clocks,
  QR codes, promo slides, "order ready" pop-ups) and lay them out on a grid. There are **ten menu-board templates**:
  Chalkboard, Neon, Departures (a split-flap board), Letterboard, Triptych, Carousel and more. A Raspberry Pi plugged
  into a TV pairs with a six-digit code and shows whichever screen you pick.
- 🎵 **Music and walkie-talkies.** Upload your music library and run radio-style channels: every screen in the
  building plays the same song at the same moment. There's push-to-talk voice too, and spoken "order ready" calls that
  quietly duck the music.
- 🍳 **Real inventory and costing.** Ingredients → components → dishes. Costs, allergens and diet flags are worked
  out from the ingredients you buy, never typed in by hand. **Cookery** walks cooks through recipes step by step,
  tracks batches, and handles freezing and thawing. Running out takes the dish off every menu automatically.
- 🎁 **Gift cards and staff cards.** Sell gift cards on the register or online with seasonal designs. Card numbers are
  never stored, only a hash. Staff cards top themselves up on a schedule or give a staff discount.
- 🥂 **Catering.** Customers build a quote. You accept it, or propose changes and go back and forth with them. Every
  version is kept, the card is charged only on acceptance, and equipment deposits are held close to the event.
- 🗓️ **Team scheduling and a time clock.** Shifts, staffing pars by hour, availability, time off and shift swaps
  ("up for grabs"). A PIN-based time clock runs on a shared tablet, and a manager approves early clock-ins.
- 🏷️ **Discounts that make sense.** Automatic offers, coupons, happy-hour windows, first-order deals, per-channel
  rules and stacking. Typed-in discounts need a manager's PIN.
- ⚡ **Live everywhere.** Server-Sent Events push every change (a new order, a dish selling out, a song skipping) to
  every open screen. There's no polling and no frontend build step.
- 🌍 **Multilingual.** English, Ukrainian (complete) and Cantonese. Each person's language follows them across the
  store, their emails and their push notifications.
- 🔐 **Locked down by default.** Group-based permissions down to view, edit and delete on each admin page. Only
  registered devices can open the register and kitchen screens. Prices and totals are always worked out on the server,
  and payments are only ever marked paid by the server.

## What's inside

| App | What it does |
| --- | --- |
| **Register** (`/restaurant/register`) | POS, lock screen, cash drawer, order history with receipts and refunds |
| **Order marking** (`/restaurant/order-marking`) | Kitchen display: approve, mark ready, hand out, reject |
| **Online store** (`/store/`) | Themed customer storefront and PWA: menu, checkout, account, catering, gift cards, events |
| **Admin** (`/admin/`) | Back office: menus, dishes, combos, recipes, stations, discounts, staff, groups, store hours, events, reports, screens, devices, email and notifications |
| **Cookery** and **Availability** (`/inventory/`) | Guided cooking and batches, stock, shopping log, what's on and off |
| **Courier** (`/deliveries/`) | Installable delivery app with web push |
| **Screens** (`/screens/`) | Widget-built status boards, signage and menu boards |
| **Audio** (`/audio/`) | Music channels and push-to-talk voice |
| **Time clock** (`/team/clock`) | Clock in and out, breaks |
| **Apps** (`/apps/`) | The staff launcher that ties it all together |

### Integrations

- **Square**: Terminal, Web Payments, saved cards, refunds. Connect your own account with OAuth from the admin.
- **listmonk**: every transactional email (order updates, account emails, gift cards), newsletters and event
  announcements. It also works as a push-notification messenger for campaigns.
- **Web Push** (VAPID keys are generated for you), **Valhalla** and **Nominatim** (OpenStreetMap), **weatherapi.com**,
  **Gotenberg** (screenshots for the store app's install sheet) and **Bugsink** (error reporting).

## Tech stack

Django 6.1 on Python 3.12+, served over ASGI (daphne in development, uvicorn in Docker). Plain Django templates and
vanilla ES-module JavaScript, with no npm and no build step. PostgreSQL and Redis in production, SQLite in
development. Media goes in any S3-compatible bucket (RustFS in the compose file).

## Getting started

### Local development

```bash
git clone https://github.com/Collert/POS.git pavilion && cd pavilion
python -m venv venv
venv/Scripts/activate            # Windows; on macOS/Linux: source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp pavilion/.env.example pavilion/.env

python manage.py migrate
python manage.py seed_demo       # optional: a demo restaurant with menus, orders and staff
python manage.py runserver
```

Open <http://127.0.0.1:8000/apps/>. The demo logins are `admin/admin`, `cashier/cashier`, `courier/courier` and
`customer/customer`. The register PINs are Alex `0000`, Dana `1234` and Priya `2468`.

If you skip `seed_demo`, Pavilion opens a **first-time setup** screen instead. It asks for the store's name, address,
tax rate and time zone, then creates the first owner account.

### Production (Docker)

```bash
cp pavilion/.env.example pavilion/.env   # set SECRET_KEY, Square, listmonk, RUSTFS_* …
docker compose up -d
docker compose exec web python manage.py setup_code   # the code the setup screen asks for
```

The compose file runs the web app, Postgres, Redis, a scheduler for the daily jobs (catering deposits, gift-card
cleanup, Square token refresh), Valhalla, Nominatim and the screenshot service. The database password is generated
for you. Put a TLS proxy (Caddy, nginx, Cloudflare) in front, and don't buffer `/events/`, because it's the live
stream.

Most settings live in the app, not in environment variables. The store's name, address, tax, time zone, languages,
theme, payment account and email server are all set from the admin.

### Tests

```bash
python manage.py test
```

Square and listmonk are mocked. Any Django deprecation warning fails the run.

## Documentation

- **[docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md)**: the deep dive into every app, the domain model, the core
  flows, the real-time design, deployment and the known open issues.
- **[CLAUDE.md](CLAUDE.md)**: a dense map of the conventions and where things live in the code.

## Contributing

Issues and pull requests are welcome. A few house rules:

- Render pages with Django templates and keep them live with SSE. Don't build screens in JavaScript.
- Staff screens must work in portrait as well as landscape (kitchen TVs are often mounted vertically).
- Every model change comes with its migration and an update to `seed_demo`.
- Every new UI string is wrapped for translation and translated to Ukrainian.
- Add tests with every fix.
