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
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
