from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from .models import Staff, Customer

# Define an inline admin descriptor for Staff model
class EmployeeInline(admin.StackedInline):
    model = Staff
    can_delete = False
    verbose_name_plural = "staff"
    extra = 1

class CustomerInline(admin.StackedInline):
    model = Customer
    can_delete = False
    verbose_name_plural = "staff"
    extra = 1

# Define a new User admin
class UserAdmin(BaseUserAdmin):
    inlines = [EmployeeInline, CustomerInline]

# Re-register UserAdmin
admin.site.unregister(User)
admin.site.register(User, UserAdmin)