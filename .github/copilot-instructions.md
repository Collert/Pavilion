# Copilot instructions for Pavilion

The accurate, maintained guides are [CLAUDE.md](../CLAUDE.md) (working rules) and
[docs/PROJECT_OVERVIEW.md](../docs/PROJECT_OVERVIEW.md) (how every part works). Read them first; when they disagree
with docstrings or older notes, they win.

Key rules:

- Create orders only through `pos_server.orders.create_order()`; never trust prices or totals from the browser.
- Money is `Decimal`; Square wants integer cents (`payments.square.to_cents`).
- Stations are `Station` rows; per-order status is `OrderStation`. Change it with `Order.set_station_status()`.
- Staff pages use `staff_required` / `superuser_required` / `api_staff_required` (`pos_server/devices.py`), not bare
  `login_required`: customers can register accounts.
- Live screen updates go through `pavilion.realtime.publish()` and `/events/<topic>` (SSE). No module-level state for
  cross-request data.
- Media goes through `field.url` / `default_storage` (local in dev, RustFS S3 in production).
- Every model change needs a migration and an update to `pos_server/management/commands/seed_demo.py`.
- Run `python manage.py test` and add tests with fixes.
