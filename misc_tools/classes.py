import re

class SendGridEmailData:
    def __init__(self, template_id, from_email, from_name="Holy Eucharist Cathedral"):
        self.template_id = template_id
        self.from_email = (from_email, from_name)
        self.recipients = []

    def add_recipient(self, email, name=None, dynamic_data=None):
        # Simple email format validation
        if len(str) > 100:
            raise ValueError("Input too long")
        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            raise ValueError(f"Invalid email address: {email}")
        
        self.recipients.append({
            "email": email,
            "name": name or "",
            "dynamic_data": dynamic_data or {}
        })
