// The account's Profile card (profile.html): show a chosen photo in the avatar before Save changes keeps it, and
// let go of the file once a save has sent it, so the next save doesn't upload it again.
document.addEventListener("change", (event) => {
    const input = event.target.closest('#acct-profile input[name="photo"]');
    const avatar = input?.form.querySelector("[data-photo-preview]");
    if (!avatar || !input.files[0]) return;
    let img = avatar.querySelector("img");
    if (!img) {
        img = document.createElement("img");
        img.alt = "";
        avatar.append(img);
    }
    img.src = URL.createObjectURL(input.files[0]);
});

document.addEventListener("submit", (event) => {
    const input = event.target.querySelector?.('input[name="photo"]');
    if (input) setTimeout(() => { input.value = ""; });  // live-page.js has read the form by then
});
