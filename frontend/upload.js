import { apiUpload } from "./client.js";

const dz = document.getElementById("dz");
const input = document.getElementById("file");
const title = dz.querySelector(".dz-title");
const sub = dz.querySelector(".dz-sub");

["dragenter", "dragover"].forEach((e) =>
  dz.addEventListener(e, (ev) => { ev.preventDefault(); dz.classList.add("drag"); }));
["dragleave", "dragend"].forEach((e) =>
  dz.addEventListener(e, (ev) => { ev.preventDefault(); dz.classList.remove("drag"); }));

dz.addEventListener("submit", (ev) => ev.preventDefault());
dz.addEventListener("drop", (ev) => {
  ev.preventDefault();
  dz.classList.remove("drag");
  const file = ev.dataTransfer?.files?.[0];
  if (file) send(file);
});
input.addEventListener("change", () => {
  if (input.files[0]) send(input.files[0]);
});

async function send(file) {
  title.textContent = `Uploading ${file.name}…`;
  if (sub) sub.textContent = "Handing it to the agents.";
  dz.classList.add("busy");
  try {
    await apiUpload("/api/invoices", file, "upload");
    // The pipeline runs in the background; the new invoice appears at the top of
    // the inbox (most recent first) and the inbox polls until it settles.
    location.href = "index.html";
  } catch (err) {
    dz.classList.remove("busy");
    title.textContent = "Upload failed.";
    if (sub) sub.textContent = err.message;
  }
}
