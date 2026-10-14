from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from .models import Staff, Customer


class StaffForm(forms.ModelForm):
    """The register PIN is stored hashed: managers type a new one here, the old one is never shown."""
    new_pin = forms.CharField(label="New register PIN", required=False, max_length=4, min_length=4,
                              widget=forms.PasswordInput(render_value=False, attrs={"inputmode": "numeric", "autocomplete": "off"}),
                              help_text="4 digits. Leave blank to keep the current PIN.")
    remove_pin = forms.BooleanField(label="Remove the PIN", required=False)

    class Meta:
        model = Staff
        exclude = ("pin",)

    def clean_new_pin(self):
        pin = self.cleaned_data["new_pin"]
        if pin and not pin.isdigit():
            raise forms.ValidationError("A PIN is 4 digits.")
        return pin

    def save(self, commit=True):
        staff = super().save(commit=False)
        if self.cleaned_data.get("remove_pin"):
            staff.set_pin("")
        elif self.cleaned_data.get("new_pin"):
            staff.set_pin(self.cleaned_data["new_pin"])
        if commit:
            staff.save()
        return staff


# Define an inline admin descriptor for Staff model
class EmployeeInline(admin.StackedInline):
    model = Staff
    form = StaffForm
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
