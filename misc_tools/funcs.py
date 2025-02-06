import cv2
import numpy as np
from sklearn.cluster import KMeans
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from django.conf import settings
from python_http_client import exceptions 

def rgb_to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(rgb[0], rgb[1], rgb[2])

def get_image_colors(image_path:str, num_colors:int):
    # Load the image
    image = cv2.imread(image_path)
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    # Reshape the image to a list of pixels
    pixels = image.reshape(-1, 3)

    # Perform K-means clustering to find the dominant colors
    kmeans = KMeans(n_clusters=num_colors)
    kmeans.fit(pixels)
    colors = kmeans.cluster_centers_

    # Convert colors to 8-bit integers
    colors = colors.astype(int)

    # Sort the colors by frequency
    color_counts = np.bincount(kmeans.labels_)
    sorted_colors = [colors[i] for i in np.argsort(-color_counts)[:num_colors]]

    # Find the background color (the color with the maximum count)
    background_color = colors[np.argmax(color_counts)]

    # Create a dictionary with the detected colors as RGB hex strings
    color_dict = {
        f"Color {i+1}": rgb_to_hex(sorted_colors[i]) for i in range(num_colors)
    }

    return color_dict

def send_template_email(email_data):
    """
    Sends a templated email using SendGrid's dynamic templates.
    :param email_data: An instance of SendGridEmailData containing all email details.
    """
    print("send_template_email called")

    # Extract just the emails for recipients
    to_email_objects = [recipient["email"] for recipient in email_data.recipients]

    # Create the Mail object, passing only the email strings in `to_emails`
    message = Mail(
        from_email=email_data.from_email,  # Only the email address as a string
        to_emails=to_email_objects  # List of email strings
    )
    message.template_id = email_data.template_id

    # Add dynamic data for each recipient (SendGrid will apply based on matching index)
    for index, recipient in enumerate(email_data.recipients):
        # Set personalization data for each recipient
        message.personalizations[index].dynamic_template_data = recipient["dynamic_data"]

    print("email prepped with template:", email_data.template_id)

    # Initialize SendGrid client and send email
    try:
        sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
        response = sg.send(message)
        print(response.status_code)
        print(response.body)
        print(response.headers)
        print(f"Email sent! Status code: {response.status_code}")
    except exceptions.BadRequestsError as e:
        print("An error occurred while sending the email:")
        print(e.body)  # Display the detailed error
