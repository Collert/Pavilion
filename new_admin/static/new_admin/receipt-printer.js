document.addEventListener('DOMContentLoaded', function() {
    function printReceipt(id) {
        var iframe = document.createElement('iframe');
        iframe.style.display = 'none';
        iframe.src = `orders/retail/${id}/receipt`;
        iframe.onload = function() {
            iframe.contentWindow.print();
        };
        document.body.appendChild(iframe);
    }

    // Example usage: replace `123` with the actual order ID
    printReceipt(123);
});