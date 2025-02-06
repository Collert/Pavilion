# Creating a paid form

## 1. Set up the template

Import the `js` file into the template as well as create a dialog with an `id="payment-popup"`.

```html
<dialog id="payment-popup"></dialog>
<script>const webPaymentWindowURL = '{% url "web-payment-window" %}';</script>
<script src="{% static 'payments/cardPaymentPopup.js' %}"></script>
```

## 2. Set up the form

Give the form `class="attach-card-payment"`. If you want to confirm payments after before charging customer, add `confirmation-needed` to the class list as well. Only 1 form allowed per template. Also, make sure the form has an input with `id="transaction-amount"` with its value being an accurate amount to charge in a float format.

Pop up triggers on form submit (will not work with `.submit()` method!)

## 3. Set up the view

In the view add the form resubmit protection:

```python
        uuid = request.POST["transaction_uuid"]
        # Other form fields

        try:
            transaction = Transaction.objects.get(uuid=uuid)
            transaction.delete()
            # Successful transaction logic ie creating an order / etc.
        except:
            # Repeat transaction logic ie looking up an order / etc.
```

## 4. Post processing

You can listen to `transactionSuccessful` message to implement a post processing routine.

```js
window.addEventListener("message", e => {
    try {
        if (e.data.transactionSuccessful) {
            // do something...
        }
    } catch (error) {
        console.error(error)
    }
})
```
