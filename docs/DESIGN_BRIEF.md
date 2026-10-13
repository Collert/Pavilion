# Pavilion: design brief

## What it is

Pavilion runs a small volunteer-staffed café at a Ukrainian Catholic cathedral in New Westminster, BC. It is open
around church events, mostly Sundays after the service and the occasional fundraiser such as a perogy dinner.
Pavilion is one system for the whole operation:

- staff ring up orders at a touch-screen register
- the kitchen and bar see orders on screens
- customers watch a TV board for "your order is ready"
- people order ahead online for pickup or delivery
- the café sells gift cards and tracks ingredients and stock

Everything works today but looks rough and inconsistent. Each part was built at a different time, with its own styling.
The goal is **one coherent visual system across all screens**, then a redesign of each screen in that system.

## People and places

| Who | Where / device | Situation |
| --- | --- | --- |
| **Cashiers** (volunteers, all ages, little training) | Touchscreen tablet or laptop at the counter | Line of people after the service, needs speed, big targets, hard to make mistakes |
| **Kitchen & bar staff** | Tablet or monitor near the stove/espresso machine, often a numeric keypad instead of touch | Hands busy or wet, glancing from a distance, noisy, bright |
| **Customers in the hall** | Large TV on the wall | Reading names from across a room |
| **Customers at the counter** | Second screen facing them | Watching their order total build up |
| **Online customers** | Phones mostly (installable web app) | Parishioners, often older; ordering ahead for pickup or delivery |
| **Couriers** | Their own phone | Driving/walking, glanceable, one hand |
| **Manager / admin** | Laptop | Setting up menus, events, promos, looking at sales history |

The interface is in **English, Ukrainian and Cantonese** (Hong Kong). Layouts must survive longer Ukrainian words.

## Screens to design

### Staff, in the café

1. **Register (POS).** A grid of dish buttons grouped into sections (Hot food, Drinks and sweets, Grab & Go). Sold-out
   dishes are disabled. Some dishes open a "pick your drink" choice dialog. The cart shows on the side. Checkout is a
   summary dialog with the customer's name, a for-here/to-go toggle, notes and discounts, then a tender: cash
   (change calculator), card (waits for the card terminal, shows progress), or gift card (scan with the camera or type
   the number). There is also a cash-drawer start amount, a menu switcher, and a "lock" button.
2. **Kitchen display (KDS).** A scrolling list of order cards. Each card shows the customer's name, order number, a
   running timer, channel (in person / online pickup / delivery, with phone), for-here or to-go, the items for *this*
   station, notes, and a small progress icon per station: waiting for approval, being made, done. Card colour shows
   the state: needs approval, waiting on another station, in progress, or ready to hand out. Staff move through cards
   with arrows or a numpad, press one key for the next step, swipe for more actions, and reject with reasons. There is a
   stock-availability overlay and a preferences dialog (which stations this screen shows, auto-advance timers).
3. **Customer-facing display.** The menu, themed with the active menu's colours and header/footer images, plus the live
   cart and total while the cashier rings things up.
4. **Order status board (TV).** Two big columns, "In progress" and "Ready for pickup", with customer names. The bottom
   bar shows a clock, the weather, and a small music player. New ready orders are announced out loud.
5. **Inventory tools.**
   - Crafting: tap to "make" a batch of perogies or soup, with ingredient counters that animate down. This screen is
     playful on purpose.
   - Stock availability toggles.
   - Shopping-trip logging with a receipt photo.
   - Recipes.
6. **Courier app.** A list of ready deliveries with ETAs, the delivery screen (address, phone, instructions, complete
   button), and a profile page to clock in by transport mode.
7. **App switcher.** The launcher tiles: Register, Kitchen, Admin, and so on.

### Customers

8. **Online store (phone-first).**
   - Home: promo banners and the next event.
   - Menu by section, and a dish page with description and allergens.
   - Cart and checkout: pickup or delivery, a time inside today's opening hours, address, gift cards, then a card
     payment popup.
   - Order status, as a progress tracker: scheduled → reviewing → preparing → ready → picked up / delivered, plus a
     "rejected, here's why" state.
   - Order history.
   - A "closed today, next event is…" state.
9. **Gift cards.**
   - Buy flow: pick a preset or a personal design, amount, recipient, gifted or for yourself, pay.
   - Confirmation.
   - The card page itself: card art and a barcode that gets scanned at the register.

### Admin

10. **Manager dashboard.** Orders list and detail with a printable receipt, menus (edit, activate, theme colours), promo
    banners, events (opening days/hours), and a calendar of past business days that opens a day's sales stats.

## Current look (what exists)

- Fonts: Noto Sans Display; Satisfy / Kaushan Script for accents. Icons: Google Material Symbols.
- A small in-house CSS kit ("AD-Elements": buttons, inputs, dialogs, colours) shared loosely by all screens.
- **Menus are themed:** each menu has a header image, a footer image, and four colours (background plus three
  accents) extracted automatically from the header image. The customer display, online menu and status board use these.
  Keep that idea: the café dresses up differently for Christmas, Easter, fundraisers…
- Heavy use of native `<dialog>` modals.

## Constraints for the redesign

- **Implementation is plain HTML/CSS and small vanilla JavaScript modules, with no build step and no framework.**
  Designs should map to simple, reusable CSS components such as buttons, cards, dialogs, toggles, lists and badges.
- Big, forgiving touch targets on the register and kitchen screens; keyboard/numpad-friendly focus states on the KDS.
- High contrast and legibility from a distance (KDS, TV board). Must work in a bright hall.
- Status needs more than colour: kitchen and order states must also be readable from icons and text.
- Light theme required; dark theme welcome (TV board and kitchen especially).
- Number of stations is configurable (not always three), so station icons/colours must come from data.
- Accessible: WCAG AA contrast, visible focus, no information only in colour, works with long translated strings.

## Tone

Warm, community, homemade, a parish hall rather than a fast-food chain. Ukrainian heritage can show (embroidery-like
patterns, sunflowers, warm reds and golds) without becoming kitsch. Staff screens should feel calm and fast;
customer screens friendly and simple.
