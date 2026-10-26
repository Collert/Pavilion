"""
A manager's sign-off for a typed-in discount on the register, for cashiers whose groups don't give "Give discounts"
(`act.discount`, users/access.py).

The cashier types the discount; someone who has `act.discount` picks their name on the register and types their PIN
(`grant`, rate-limited like the lock screen). The answer is a signed approval for that discount (kind and value), for
that cashier, good for APPROVAL_HOURS. The register sends it with the discount (`{"manual": {..., "approval": …}}`)
and pricing accepts the discount when `check` says the approval matches; the order keeps who approved it
(OrderDiscount.approved_by).
"""
from decimal import Decimal, InvalidOperation

from django.contrib.auth.models import User
from django.core import signing
from django.core.cache import cache
from django.utils.translation import gettext as _

from users.access import can
from users.models import Staff

SALT = "pavilion.discount-approval"
APPROVAL_HOURS = 3
PIN_ATTEMPTS, PIN_LOCKOUT_SECONDS = 5, 60


def approvers():
    """Who can sign off here: active staff with a register PIN whose groups give "Give discounts"."""
    people = Staff.objects.filter(user__is_active=True, user__is_staff=True).exclude(pin="").select_related("user")
    return [s for s in people.order_by("user__first_name", "user__username") if can(s.user, "act.discount")]


def _value(value) -> str:
    try:
        return f"{Decimal(str(value)):.2f}"
    except (InvalidOperation, ValueError, TypeError):
        return ""


class ApprovalError(Exception):
    def __init__(self, message, status=403):
        super().__init__(message)
        self.status = status


def grant(device_id, staff_id, pin, kind, value, cashier) -> tuple[str, Staff]:
    """Check the approver's PIN and sign an approval for this discount. Raises ApprovalError."""
    if kind not in ("pct", "amt") or not _value(value) or Decimal(_value(value)) <= 0:
        raise ApprovalError(_("Type the discount first."), 400)
    key = f"discount-approval:{device_id}:{staff_id}"
    if cache.get(key, 0) >= PIN_ATTEMPTS:
        raise ApprovalError(_("Too many tries. Wait a minute, then try again."), 429)
    staff = next((s for s in approvers() if s.id == staff_id), None)
    if staff is None or not staff.check_pin(str(pin)):
        cache.set(key, cache.get(key, 0) + 1, timeout=PIN_LOCKOUT_SECONDS)
        raise ApprovalError(_("That PIN didn't match. Try again."))
    cache.delete(key)
    token = signing.dumps({"by": staff.user_id, "for": cashier.id, "kind": kind, "value": _value(value)}, salt=SALT, compress=True)
    return token, staff


def check(manual, cashier):
    """The approver's user id when `manual` ({kind, value, approval}) carries a good approval for this cashier."""
    token = manual.get("approval") if isinstance(manual, dict) else None
    if not token or not cashier:
        return None
    try:
        data = signing.loads(str(token), salt=SALT, max_age=APPROVAL_HOURS * 3600)
    except signing.BadSignature:
        return None
    if (data.get("for") != cashier.id or data.get("kind") != manual.get("kind")
            or data.get("value") != _value(manual.get("value"))):
        return None
    approver = User.objects.filter(pk=data.get("by"), is_active=True).first()
    return approver.id if approver and can(approver, "act.discount") else None
