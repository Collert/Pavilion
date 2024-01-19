from django.http import JsonResponse
from django.shortcuts import render
import cv2
import numpy as np
from sklearn.cluster import KMeans
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

# Create your views here.

def index(request):
    if request.method == "GET":
        return render(request, "misc_tools/get-image-colors.html")
    elif request.method == "POST":
        num_colors = 3
        print(request.FILES)
        uploaded_image = request.FILES['image']
        image_path = default_storage.save('temp_image.jpg', ContentFile(uploaded_image.read()))
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

        # Create a dictionary with the detected colors
        color_dict = {
            f"Color {i+1}": sorted_colors[i].tolist() for i in range(num_colors)
        }
        color_dict["Background Color"] = background_color.tolist()

        default_storage.delete(image_path)

        return JsonResponse(color_dict)