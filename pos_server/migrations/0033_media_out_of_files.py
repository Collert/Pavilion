"""
Media used to be kept under a "files/" folder in the media storage (media/files/dish_images/x.png, or the same key
in the S3 bucket), which did nothing. Its folders now sit at the storage's root (dish_images/x.png).

This moves every file under files/ to the same place without it, then renames what the database keeps: every
FileField/ImageField name, and media names or addresses inside JSON and text fields (theme pictures, screen images,
drafts). The old files are deleted once the renames are committed. Running it again does nothing.
"""
import logging

from django.core.files.storage import default_storage
from django.db import migrations, models, transaction
from django.db.models.functions import Substr

logger = logging.getLogger(__name__)

OLD = "files/"


def _walk(path):
    dirs, files = default_storage.listdir(path)
    for name in files:
        yield f"{path}/{name}"
    for name in dirs:
        yield from _walk(f"{path}/{name}")


def _media_bases():
    """Where media names turn into addresses: /media/ in development, the bucket's address in production."""
    probe = OLD + "x"
    url = default_storage.url(probe)
    return {url[: -len(probe)], "/media/"} if url.endswith(probe) else {"/media/"}


def _rename(value, bases, folders):
    if isinstance(value, str):
        if value.startswith(OLD) and value[len(OLD):].split("/", 1)[0] in folders:
            value = value[len(OLD):]
        for base in bases:
            value = value.replace(base + OLD, base)
        return value
    if isinstance(value, list):
        return [_rename(v, bases, folders) for v in value]
    if isinstance(value, dict):
        return {k: _rename(v, bases, folders) for k, v in value.items()}
    return value


def forwards(apps, schema_editor):
    try:
        names = list(_walk(OLD.rstrip("/")))
    except (FileNotFoundError, OSError):
        names = []
    except Exception:
        logger.exception("Listing the old media folder failed; only the database names are changed")
        names = []
    # the folders media lives in, so a JSON or text value is only renamed when it's one of ours
    folders = {name[len(OLD):].split("/", 1)[0] for name in names} | {"branding", "screens", "calls", "gift-cards", "deliveries", "music"}
    copied = []
    for name in names:
        target = name[len(OLD):]
        if not default_storage.exists(target):
            with default_storage.open(name, "rb") as fh:
                saved = default_storage.save(target, fh)
            if saved != target:
                logger.warning("Media %s was saved as %s", name, saved)
                continue
        copied.append(name)

    bases = _media_bases()
    for model in apps.get_models():
        if model._meta.proxy or not model._meta.managed:
            continue
        for field in model._meta.concrete_fields:
            if isinstance(field, models.FileField):
                if isinstance(field.upload_to, str):
                    folders.add(field.upload_to.split("/", 1)[0])
                model.objects.filter(**{f"{field.name}__startswith": OLD}).update(**{field.name: Substr(field.name, len(OLD) + 1)})
    folders.discard("")
    folders.discard(OLD.rstrip("/"))
    for model in apps.get_models():
        if model._meta.proxy or not model._meta.managed:
            continue
        for field in model._meta.concrete_fields:
            if isinstance(field, models.FileField) or not isinstance(field, (models.CharField, models.TextField, models.JSONField)):
                continue
            for row in model.objects.filter(**{f"{field.name}__icontains": OLD}).only("pk", field.name):
                value = getattr(row, field.name)
                renamed = _rename(value, bases, folders)
                if renamed != value:
                    model.objects.filter(pk=row.pk).update(**{field.name: renamed})

    def delete_old():
        for name in copied:
            try:
                default_storage.delete(name)
            except Exception:
                logger.exception("Couldn't delete the old copy %s", name)
        try:  # the emptied folders (a bucket has none to remove)
            import os
            root = default_storage.path(OLD.rstrip("/"))
            for path, _dirs, _files in sorted(os.walk(root), key=lambda entry: -len(entry[0])):
                if not os.listdir(path):
                    os.rmdir(path)
        except (NotImplementedError, OSError):
            pass

    transaction.on_commit(delete_old)


class Migration(migrations.Migration):
    dependencies = [
        ("pos_server", "0032_alter_combo_image_alter_dish_image_and_more"),
        ("admin_portal", "0006_alter_storesettings_logo"),
        ("audio", "0004_alter_track_artwork_alter_track_file"),
        ("catering", "0003_no_full_service"),
        ("deliveries", "0004_courier_app"),
        ("events", "0005_delivery_fee"),
        ("gift_cards", "0007_alter_cardpreset_image_alter_giftcard_image_and_more"),
        ("inventory", "0007_alter_restock_receipt_alter_stockupdate_receipt"),
        ("misc_tools", "0001_email_templates"),
        ("online_store", "0003_alter_promocontent_image"),
        ("payments", "0006_phone_checkouts"),
        ("screens", "0005_audio"),
        ("services", "0001_initial"),
        ("team", "0004_shift_offers"),
        ("users", "0014_alter_customer_profile_picture_alter_staff_photo"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
