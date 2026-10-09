"use strict";
// A tiny practice shop for the gherkin-copilot demo. All state lives in
// sessionStorage, nothing is sent anywhere, and the page structure mirrors
// saucedemo.com so the same test suite can target either site.

const PRODUCTS = [
  { id: "backpack", name: "Sauce Labs Backpack", price: 29.99 },
  { id: "bike-light", name: "Sauce Labs Bike Light", price: 9.99 },
  { id: "bolt-t-shirt", name: "Sauce Labs Bolt T-Shirt", price: 15.99 },
  { id: "onesie", name: "Sauce Labs Onesie", price: 7.99 },
];
const PASSWORD = "secret_sauce"; // public practice password, not a secret
const USERS = { standard_user: "ok", locked_out_user: "locked" };

const store = {
  get user() { return sessionStorage.getItem("user"); },
  set user(v) { v ? sessionStorage.setItem("user", v) : sessionStorage.clear(); },
  get cart() { return JSON.parse(sessionStorage.getItem("cart") || "[]"); },
  set cart(ids) { sessionStorage.setItem("cart", JSON.stringify(ids)); },
};

function el(tag, attrs = {}, text = "") {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  node.textContent = text; // textContent only: no HTML injection
  return node;
}

function showError(message) {
  const box = document.querySelector("[data-test=error]");
  box.textContent = message;
  box.hidden = false;
}

function initLogin() {
  document.getElementById("login-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const username = document.getElementById("user-name").value.trim();
    const password = document.getElementById("password").value;
    if (!username) return showError("Epic sadface: Username is required");
    if (!password) return showError("Epic sadface: Password is required");
    if (!(username in USERS) || password !== PASSWORD) {
      return showError("Epic sadface: Username and password do not match any user in this service");
    }
    if (USERS[username] === "locked") return showError("Epic sadface: Sorry, this user has been locked out.");
    store.user = username;
    store.cart = [];
    location.href = "inventory.html";
  });
}

function renderBadge() {
  const badge = document.querySelector(".shopping_cart_badge");
  const count = store.cart.length;
  badge.textContent = String(count);
  badge.hidden = count === 0;
}

function toggleCart(id) {
  const cart = store.cart;
  store.cart = cart.includes(id) ? cart.filter((x) => x !== id) : [...cart, id];
}

function initInventory() {
  const list = document.querySelector(".inventory_list");
  const render = () => {
    list.replaceChildren(...PRODUCTS.map((p) => {
      const inCart = store.cart.includes(p.id);
      const item = el("li", { class: "inventory_item", "data-test": "inventory-item" });
      item.append(
        el("span", { class: "inventory_item_name", "data-test": "inventory-item-name" }, p.name),
        el("span", { class: "inventory_item_price" }, `$${p.price.toFixed(2)}`),
      );
      const button = el("button", { "data-test": `${inCart ? "remove" : "add-to-cart"}-${p.id}` }, inCart ? "Remove" : "Add to cart");
      button.addEventListener("click", () => { toggleCart(p.id); render(); });
      item.append(button);
      return item;
    }));
    renderBadge();
  };
  render();
}

function initCart() {
  const list = document.querySelector(".cart_list");
  const render = () => {
    const items = PRODUCTS.filter((p) => store.cart.includes(p.id));
    list.replaceChildren(...items.map((p) => {
      const row = el("li", { class: "cart_item", "data-test": "cart-item" });
      row.append(
        el("span", { class: "inventory_item_name", "data-test": "inventory-item-name" }, p.name),
        el("span", { class: "inventory_item_price" }, `$${p.price.toFixed(2)}`),
      );
      const remove = el("button", { "data-test": `remove-${p.id}` }, "Remove");
      remove.addEventListener("click", () => { toggleCart(p.id); render(); });
      row.append(remove);
      return row;
    }));
    const total = items.reduce((sum, p) => sum + p.price, 0);
    document.querySelector("[data-test=total]").textContent = `Total: $${total.toFixed(2)}`;
    renderBadge();
  };
  render();
}

document.addEventListener("DOMContentLoaded", () => {
  const page = document.body.dataset.page;
  if (page !== "login" && !store.user) { location.href = "index.html"; return; }
  document.getElementById("logout")?.addEventListener("click", () => { store.user = null; location.href = "index.html"; });
  ({ login: initLogin, inventory: initInventory, cart: initCart })[page]();
});
