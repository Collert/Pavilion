// The printable receipt (admin_portal/receipt.html): print once it's laid out. It's loaded in the page's hidden
// print frame (pos_server/_print_frame.html), so this prints the frame, not the page around it. With Chrome started
// with --kiosk-printing, it goes straight to the default printer without the dialog.
window.addEventListener("load", () => window.print());
