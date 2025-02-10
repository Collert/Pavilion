const CACHE_NAME = 'your-app-cache-v1.1';
const OFFLINE_URL = '/static/online_store/offline.html';

const cartExpiryTimer = 15 * 60 * 1000 // In milliseconds, the first number is minutes.

const CartDB = {
  save: (cart) => saveCartToIndexedDB(cart),
  load: () => getCartFromIndexedDB(),
};

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

self.addEventListener('activate', (event) => {
  console.log('Service Worker activated.');

  // Claim all clients immediately
  event.waitUntil(self.clients.claim().then(() => {
      console.log('All clients claimed.');
  }));
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

self.addEventListener('sync', (event) => {
  if (event.tag === 'reset-cart-payments') {
      event.waitUntil(
          (async () => {
              await new Promise(resolve => setTimeout(resolve, cartExpiryTimer));

              // Call the resetCartPayments function
              try {
                  console.log('Running background sync for cart reset...');
                  await resetCartPayments();
              } catch (error) {
                  console.error('Error during background sync:', error);
              }
          })()
      );
  }
});

/**
 * Resets the partial payments in the cart if the cart's expiration time is within a 1-second margin of the current time.
 * 
 * This function retrieves the cart data from IndexedDB, checks if the cart's expiration time is within a 1-second margin
 * of the current time, and if so, resets the partial payments in the cart.
 * 
 * @async
 * @function resetCartPayments
 * @returns {Promise<void>} A promise that resolves when the cart payments have been reset or if no cart data is found.
 * @throws Will log an error message if there is an issue retrieving or saving the cart data.
 */
async function resetCartPayments() {
  try {
      // Retrieve cart data from IndexedDB
      const cartDBObject = await CartDB.load();
      const cart = cartDBObject.data
      if (!cart) {
          console.log('No cart data found for resetting payments.');
          return;
      }

      console.log('Resetting payments for cart:', cart);

      const cartExpires = cart.lastUpdate + cartExpiryTimer
      const now = Date.now()
      console.log({
        now:now,
        cartExpires:cartExpires,
        past:now - 1000,
        future: now + 1000,
        greater:now - 1000 <= cartExpires,
        less:cartExpires <= now + 1000
      })
      if (now - 1000 <= cartExpires && cartExpires <= now + 1000) { //Give it a 1 sec margin of error
        cart.partialPayments = []
        console.log('Cart payments reset successfully.');
        CartDB.save(cart)
      }
  } catch (error) {
      console.error('Error resetting cart payments:', error);
  }
}

self.addEventListener('push', function(event) {
    const data = event.data.json();
    /**
     * Options for the notification.
     * @typedef {Object} NotificationOptions
     * @property {string} body - The body text of the notification.
     * @property {string} icon - The URL of the icon to be displayed with the notification.
     * @property {string} [badge] - The URL of the badge to be displayed with the notification. Defaults to the icon URL if not provided.
     * @property {Array<Object>} [actions] - An array of actions to be displayed with the notification. Defaults to an empty array if not provided.
     * @property {Object} data - Additional data for the notification.
     * @property {string} data.url - The URL to be opened when the notification is clicked.
     */
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
  
/**
 * Saves the provided cart data to IndexedDB.
 *
 * This function opens an IndexedDB database named 'CartDB' with version 1.
 * If the database is being created or upgraded, it ensures that an object store
 * named 'cart' exists with a keyPath of 'id'. Once the database is successfully
 * opened, it stores the provided cart data in the 'cart' object store with a key
 * of 'currentCart'.
 *
 * @param {Object} cart - The cart data to be saved to IndexedDB.
 */
function saveCartToIndexedDB(cart) {
  const request = indexedDB.open('CartDB', 1); // Ensure the version number is correct (1 in this case)

  request.onupgradeneeded = function (event) {
      const db = event.target.result;
      if (!db.objectStoreNames.contains('cart')) {
          db.createObjectStore('cart', { keyPath: 'id' }); // Create the 'cart' object store
          console.log('Object store "cart" created.');
      }
  };

  request.onsuccess = function (event) {
      const db = event.target.result;
      const transaction = db.transaction(['cart'], 'readwrite');
      const store = transaction.objectStore('cart');
      store.put({ id: 'currentCart', data: cart });
  };
}

/**
 * Retrieves the current cart from IndexedDB.
 *
 * This function opens the 'CartDB' database, accesses the 'cart' object store,
 * and retrieves the 'currentCart' item. It returns a promise that resolves
 * with the cart data or rejects with an error message if the operation fails.
 *
 * @returns {Promise<Object>} A promise that resolves with the cart data or rejects with an error message.
 */
function getCartFromIndexedDB() {
  return new Promise((resolve, reject) => {
      const request = indexedDB.open('CartDB', 1);

      request.onsuccess = function (event) {
          const db = event.target.result;
          const transaction = db.transaction(['cart'], 'readonly');
          const store = transaction.objectStore('cart');
          const getRequest = store.get('currentCart');

          getRequest.onsuccess = function () {
              resolve(getRequest.result);
          };

          getRequest.onerror = function () {
              reject('Failed to retrieve cart from IndexedDB');
          };
      };

      request.onerror = function () {
          reject('Failed to open IndexedDB');
      };
  });
}