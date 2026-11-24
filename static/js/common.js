async function api(path, options = {}) {
  const opts = {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  };
  const res = await fetch(path, opts);
  let data = null;
  const text = await res.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const detail =
      (data && data.detail) ||
      (typeof data === "string" ? data : null) ||
      res.statusText;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
          : detail && detail.message
            ? detail.message
            : JSON.stringify(detail);
    const err = new Error(message);
    err.status = res.status;
    err.detail = detail;
    throw err;
  }
  return data;
}

function toast(message, type = "ok") {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.className = `toast show ${type}`;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.remove("show"), 4200);
}

function escapeHtml(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

/** Кэш каталога офферов в браузере — поиск без повторных запросов к Keitaro. */
let _offersCatalog = null;
let _offersCatalogPromise = null;

async function loadOffersCatalog(force = false) {
  if (!force && _offersCatalog) return _offersCatalog;
  if (!force && _offersCatalogPromise) return _offersCatalogPromise;
  _offersCatalogPromise = api(`/api/offers?q=&limit=500${force ? "&refresh=1" : ""}`)
    .then((rows) => {
      _offersCatalog = rows;
      _offersCatalogPromise = null;
      return rows;
    })
    .catch((err) => {
      _offersCatalogPromise = null;
      throw err;
    });
  return _offersCatalogPromise;
}

function filterOffersLocal(catalog, q, limit = 25) {
  const query = (q || "").trim().toLowerCase();
  if (!query) return catalog.slice(0, limit);
  const out = [];
  for (const o of catalog) {
    const name = String(o.name || "").toLowerCase();
    const id = String(o.id);
    if (name.includes(query) || id === query || id.startsWith(query)) {
      out.push(o);
      if (out.length >= limit) break;
    }
  }
  return out;
}

/**
 * Автокомплит офферов (локальная фильтрация после одной загрузки каталога).
 * @param {HTMLInputElement} input
 * @param {HTMLElement} menu
 * @param {(offer: {id:number,name:string}) => void} onSelect
 */
function bindOfferAutocomplete(input, menu, onSelect) {
  if (input.dataset.acBound === "1") return;
  input.dataset.acBound = "1";

  let items = [];
  let active = -1;
  let ready = false;

  const render = () => {
    if (!items.length) {
      menu.hidden = true;
      menu.innerHTML = "";
      return;
    }
    menu.hidden = false;
    menu.innerHTML = items
      .map(
        (o, i) => `
      <div class="ac-item ${i === active ? "active" : ""}" data-idx="${i}">
        <div>${escapeHtml(o.name)}</div>
        <div class="id">ID ${o.id}</div>
      </div>`
      )
      .join("");
  };

  const applyFilter = () => {
    if (!ready || !_offersCatalog) return;
    const q = input.value.trim();
    // если в поле уже выбран оффер вида "Name (#id)" — не фильтруем по всей строке
    const selectedId = input.dataset.offerId;
    if (selectedId && /\(#\d+\)$/.test(q)) {
      items = _offersCatalog.filter((o) => String(o.id) === selectedId).slice(0, 1);
    } else {
      items = filterOffersLocal(_offersCatalog, q, 25);
    }
    active = items.length ? 0 : -1;
    render();
  };

  const ensureCatalog = async () => {
    if (ready) {
      applyFilter();
      return;
    }
    menu.hidden = false;
    menu.innerHTML = `<div class="ac-item"><div class="id">Загрузка офферов…</div></div>`;
    try {
      await loadOffersCatalog();
      ready = true;
      applyFilter();
    } catch (err) {
      menu.hidden = true;
      toast(err.message, "error");
    }
  };

  input.addEventListener("input", () => {
    input.dataset.offerId = "";
    if (!ready) ensureCatalog();
    else applyFilter();
  });

  input.addEventListener("focus", () => {
    ensureCatalog();
  });

  input.addEventListener("keydown", (e) => {
    if (menu.hidden || !items.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      active = (active + 1) % items.length;
      render();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      active = (active - 1 + items.length) % items.length;
      render();
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (active >= 0) select(items[active]);
    } else if (e.key === "Escape") {
      menu.hidden = true;
    }
  });

  menu.addEventListener("mousedown", (e) => {
    const item = e.target.closest(".ac-item");
    if (!item || item.dataset.idx == null) return;
    e.preventDefault();
    select(items[Number(item.dataset.idx)]);
  });

  // mousedown по пункту меню делает preventDefault, поэтому blur — это клик вне автокомплита
  input.addEventListener("blur", () => {
    menu.hidden = true;
  });

  function select(offer) {
    if (!offer) return;
    input.value = `${offer.name} (#${offer.id})`;
    input.dataset.offerId = String(offer.id);
    menu.hidden = true;
    onSelect(offer);
  }
}
