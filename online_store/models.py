from django.db import models

# Create your models here.

class PromoContent(models.Model):
    """
    PromoContent model represents promotional content for the online store.

    Attributes:
        title (CharField): The title of the promotional content, with a maximum length of 30 characters.
        tagline (CharField): The tagline or subtitle of the promotional content, with a maximum length of 200 characters.
        call_to_action (CharField): The call-to-action text, with a maximum length of 20 characters.
        image (ImageField): The image associated with the promotional content, uploaded to 'files/store_promos'.
        color (CharField): The primary color of the promotional content, represented as a hex code with a maximum length of 7 characters.
        accent (CharField): The accent color of the promotional content, represented as a hex code with a maximum length of 7 characters.
        link (TextField): The URL or link associated with the promotional content.
    """
    title = models.CharField(max_length=30)
    tagline = models.CharField(max_length=200)
    call_to_action = models.CharField(max_length=20)
    image = models.ImageField(upload_to='files/store_promos')    
    color = models.CharField(max_length=7)
    accent = models.CharField(max_length=7)
    link = models.TextField()

class RejectedOrder(models.Model):
    """
    Model representing a rejected order.

    Attributes:
        order_id (int): Unique identifier for the order.
        reason (str): Reason for the order rejection.
        timestamp (datetime): Timestamp when the order was rejected. Can be null.
    """
    order_id = models.PositiveIntegerField(unique=True)
    reason = models.TextField()
    timestamp = models.DateTimeField(null=True)