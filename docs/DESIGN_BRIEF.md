# Pavilion: what the app does

Pavilion is self-hosted restaurant management software. One installation runs a restaurant's point of sale, kitchen
screens, customer-facing displays, online ordering, deliveries, gift cards and inventory. Each restaurant sets up its
own menus, stations, opening days and colours.

## Who uses it

| Who | Device | What they do |
| --- | --- | --- |
| Cashiers | Touchscreen tablet or computer at the counter | Take in-person orders and payments |
| Kitchen / bar / prep staff | Tablet or monitor at their station, touch or a numeric keypad | See, approve and complete orders |
| Customers in the restaurant | Screen facing the counter; large TV in the dining area | Watch their order being rung up; see when it's ready |
| Online customers | Mostly phones (installable web app) | Order ahead for pickup or delivery, buy gift cards |
| Couriers | Their own phone | Take and complete deliveries |
| Managers | Computer | Set up menus, stations, events, promotions; review sales |

## Screens

### Staff

1. **Register (POS).**
   - Dishes are shown as buttons grouped into menu sections, one per station. Sold-out dishes can't be selected.
   - Some dishes ask for a choice first, e.g. pick a drink.
   - The cart runs alongside the dish buttons.
   - Checkout collects the customer's name, for-here or to-go, notes and an optional discount.
   - Payment: cash with a change calculator, card on a payment terminal with live status while the customer pays, or gift card (scanned with the camera or typed in). Payments can be split.
   - Other controls: set the cash drawer starting amount, switch the active menu, lock the register.
2. **Kitchen display.**
   - A list of order cards, filtered to the stations this screen covers.
   - Each card shows: customer name, order number, time since the order started, channel (in person, online pickup, delivery with phone number), for here or to go, this station's items, notes, and a progress indicator for every station on the order.
   - Order states: waiting for approval, waiting on another station, in progress, ready to hand out.
   - Actions: approve, reject with reasons, mark done, mark picked up.
   - Works by touch (tap, swipe) or keyboard/numpad. Has a stock-availability overlay and per-screen preferences (stations shown, automatic advance timers).
3. **Customer-facing display.** The active menu, plus the live cart and total while the cashier rings up an order.
4. **Order status board.**
   - Two columns, "in progress" and "ready for pickup", with customer names or order numbers.
   - Newly ready orders are announced out loud.
   - Also shows a clock, the local weather and an optional music player.
5. **Inventory.**
   - Making batches of prepared items (deducts their ingredients).
   - Toggling items in or out of stock.
   - Logging shopping trips with a receipt photo, and shopping history.
   - Recipes.
6. **Courier app.**
   - Clock in with a transport mode.
   - List of ready deliveries with travel-time estimates.
   - Delivery detail: address, phone, instructions; claim it, then complete it.
   - Push notifications when a delivery is ready.
7. **Launcher.** Tiles linking to the register, kitchen display, admin and other tools.
8. **Device registration.** A manager authorizes a tablet or computer to run the register or kitchen display.

### Customers

1. **Online store.**
   - Home: promotion banners and upcoming opening days.
   - Menu by section; dish page with description and allergens.
   - Cart and checkout: pickup or delivery, a time within today's opening hours, address for delivery, gift card payments, card payment in a popup.
   - "Closed today" state showing the next opening day.
   - Order status tracker: scheduled → being reviewed → being prepared → ready → picked up / delivered, plus a rejected state with the reason.
   - Order history.
2. **Gift cards.**
    - Buy: choose a preset card or a design, amount, recipient name and email, gift or for yourself, pay.
    - Confirmation page.
    - Card page: card artwork, balance and a barcode that is scanned at the register.

### Managers

 1. **Admin dashboard.**
    - Orders: list, detail and printable receipt.
    - Menus: create, edit and activate. Each menu has a header and footer image and its own colours; customer-facing screens use them.
    - Promotion banners for the online store.
    - Events: the days and hours the restaurant is open, and which channels (in person, online, delivery) are open.
    - Calendar of past business days, each opening that day's sales statistics: items sold, station load, ingredient use, revenue and prep times by time of day.
 2. **Settings managed in the advanced admin.** Dishes and their ingredients, stations (name, icon, order), registered devices, users and staff.
