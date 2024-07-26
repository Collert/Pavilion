from django.db import models

# Create your models here.

class PromoContent(models.Model):
    title = models.CharField(max_length=30)
    tagline = models.CharField(max_length=200)
    call_to_action = models.CharField(max_length=20)
    image = models.ImageField(upload_to='files/store_promos')    
    color = models.CharField(max_length=7)
    accent = models.CharField(max_length=7)
    link = models.TextField()