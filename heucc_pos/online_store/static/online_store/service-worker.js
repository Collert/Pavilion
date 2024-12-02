const CACHE_NAME = 'your-app-cache-v1.1';
const OFFLINE_URL = '/static/online_store/offline.html';

self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function(cache) {
      return cache.addAll([
        // '/',
        '/static/online_store/project-styles.css',
        '/static/online_store/icon-192x192.png',
        '/static/online_store/icon-512x512.png',
        OFFLINE_URL
      ]);
    })
  );
});

self.addEventListener('fetch', function(event) {
  event.respondWith(
    fetch(event.request).catch(function() {
      return caches.match(event.request).then(function(response) {
        return response || caches.match(OFFLINE_URL);
      });
    })
  );
});

self.addEventListener('push', function(event) {
    const data = event.data.json();
    const options = {
      body: data.body,
      icon: data.icon,
      badge: data.badge || data.icon,
      actions: data.actions || [],
      data: {
        url: data.data.url
      }
    };
    
    event.waitUntil(
      self.registration.showNotification(data.title, options)
    );
});
  
self.addEventListener('notificationclick', function(event) {
    event.notification.close();
  
    if (event.action === 'open_url') {
      event.waitUntil(
        clients.openWindow(event.notification.data.url)
      );
    } else {
      event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(clientList) {
          if (clientList.length > 0) {
            return clientList[0].focus();
          }
          return clients.openWindow(event.notification.data.url);
        })
      );
    }
});
  