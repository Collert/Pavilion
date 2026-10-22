from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from .layout import validate_layout

hex_color = RegexValidator(r"^#[0-9a-fA-F]{6}$", _("A colour like #F4F1EA"))


class Screen(models.Model):
    """
    A screen a restaurant builds from pre-made widgets (status boards, menus, signage), shown at /screens/<slug>.

    `layout` is a tree of widget nodes (see screens/layout.py). Sizes in widgets are in pixels of the design canvas
    (`width` × `height`); the page scales them to whatever display it's on.
    """
    name = models.CharField(_("name"), max_length=100)
    slug = models.SlugField(_("address"), unique=True, help_text=_("The screen opens at /screens/<address>"))
    layout = models.JSONField(_("layout"), null=True, blank=True, validators=[validate_layout],
                              help_text=_("A tree of widgets; the widget reference is below"))
    public = models.BooleanField(_("public"), default=False,
                                 help_text=_("Anyone with the link can open it (e.g. a pickup board). Otherwise staff only."))
    width = models.PositiveIntegerField(_("design width"), default=1920, validators=[MinValueValidator(320), MaxValueValidator(7680)])
    height = models.PositiveIntegerField(_("design height"), default=1080, validators=[MinValueValidator(320), MaxValueValidator(7680)])
    background = models.CharField(_("background"), max_length=7, default="#F4F1EA", validators=[hex_color])
    padding = models.PositiveIntegerField(_("padding"), default=36, validators=[MaxValueValidator(400)],
                                          help_text=_("Space around the edge, in design pixels"))
    sessions = models.BooleanField(_("sessions"), default=False, help_text=_(
        "Each display opens its own session, which a register can pair with by its code (an order summary then "
        "shows only on that display). Off: every display shows the same screen."))
    popups = models.JSONField(_("pop-ups"), default=list, blank=True,
                              help_text=_('Pop-up widgets shown over the screen: [{"widget": "order_ready", "params": {...}}]'))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


def _new_code():
    import secrets
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # nothing that reads two ways (0/O, 1/I)
    while True:
        code = "".join(secrets.choice(letters) for _n in range(3)) + "-" + "".join(secrets.choice(letters) for _n in range(3))
        if not ScreenSession.objects.filter(code=code).exists():
            return code


class ScreenSession(models.Model):
    """
    One display showing a screen that supports sessions (e.g. "Register 1 display"), at /screens/<slug>/s/<code>. A
    register pairs with it by typing its code, and from then on its order summary shows only here.
    """
    screen = models.ForeignKey(Screen, on_delete=models.CASCADE, related_name="screen_sessions")
    name = models.CharField(_("name"), max_length=60)
    code = models.CharField(_("pairing code"), max_length=7, unique=True, default=_new_code)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(null=True, blank=True, help_text=_("When the display last loaded it"))
    paired_by = models.CharField(_("paired with"), max_length=120, blank=True, help_text=_("Who paired a register with it"))
    paired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.screen.name} · {self.name}"

    @property
    def online(self) -> bool:
        from django.utils import timezone
        return bool(self.last_seen and (timezone.now() - self.last_seen).total_seconds() < 600)
