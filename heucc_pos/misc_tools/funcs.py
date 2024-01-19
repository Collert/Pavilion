import cv2
import numpy as np
from sklearn.cluster import KMeans

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