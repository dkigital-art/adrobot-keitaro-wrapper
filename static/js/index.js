document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("create-form");
  const offerInput = document.getElementById("offer-input");
  const offerMenu = document.getElementById("offer-menu");
  const listEl = document.getElementById("campaigns-list");

  // выбранный ID живёт в dataset.offerId и сбрасывается при любой правке текста
  bindOfferAutocomplete(offerInput, offerMenu, () => {});

  async function loadCampaigns() {
    const rows = await api("/api/campaigns");
    if (!rows.length) {
      listEl.innerHTML = `<div class="empty">Кампаний пока нет — создайте первую слева.</div>`;
      return;
    }
    listEl.innerHTML = rows
      .map(
        (c) => `
      <div class="list-item" data-campaign-id="${c.id}">
        <div>
          <h3>${escapeHtml(c.name)}</h3>
          <div class="meta">
            local #${c.id} · KT #${c.keitaro_campaign_id} · geo ${escapeHtml(c.geo)}
            ${c.has_draft ? ' · <span class="draft-badge">draft</span>' : ""}
          </div>
        </div>
        <div class="list-item-actions">
          <a class="btn btn-sm btn-primary" href="/campaigns/${c.id}">Открыть</a>
          <button
            type="button"
            class="icon-btn icon-btn-danger"
            data-action="delete-campaign"
            data-id="${c.id}"
            data-name="${escapeHtml(c.name)}"
            data-kt-id="${c.keitaro_campaign_id}"
            title="Удалить кампанию"
            aria-label="Удалить кампанию «${escapeHtml(c.name)}»"
          >×</button>
        </div>
      </div>`
      )
      .join("");
  }

  function confirmDeleteCampaign(name, ktId) {
    return window.confirm(
      `Удалить кампанию «${name}»?\n\n` +
        `Она будет удалена в Keitaro (KT #${ktId}) и из локального списка Wrapper.\n` +
        `Это необратимо.`
    );
  }

  listEl.addEventListener("click", async (e) => {
    const btn = e.target.closest('button[data-action="delete-campaign"]');
    if (!btn || btn.disabled) return;
    const id = Number(btn.dataset.id);
    const name = btn.dataset.name || `#${id}`;
    const ktId = btn.dataset.ktId || "?";
    if (!confirmDeleteCampaign(name, ktId)) return;

    btn.disabled = true;
    try {
      await api(`/api/campaigns/${id}`, { method: "DELETE" });
      toast(`Кампания «${name}» удалена`, "ok");
      await loadCampaigns();
    } catch (err) {
      toast(err.message, "error");
      btn.disabled = false;
    }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = document.getElementById("name").value.trim();
    const geo = document.getElementById("geo").value.trim();
    const offerId = Number(offerInput.dataset.offerId || 0);
    if (!offerId) {
      toast("Выберите оффер из автокомплита", "error");
      return;
    }
    const btn = form.querySelector('button[type="submit"]');
    btn.disabled = true;
    try {
      const campaign = await api("/api/campaigns", {
        method: "POST",
        body: JSON.stringify({ name, geo, offer_id: offerId }),
      });
      toast(`Кампания «${campaign.name}» создана`, "ok");
      form.reset();
      offerInput.dataset.offerId = "";
      window.location.href = `/campaigns/${campaign.id}`;
    } catch (err) {
      toast(err.message, "error");
    } finally {
      btn.disabled = false;
    }
  });

  const syncBtn = document.getElementById("sync-kt-btn");
  syncBtn.addEventListener("click", async () => {
    syncBtn.disabled = true;
    syncBtn.textContent = "Fetching…";
    try {
      const result = await api("/api/campaigns/sync-from-kt", { method: "POST" });
      toast(result.message || "Синхронизация завершена", "ok");
      await loadCampaigns();
    } catch (err) {
      toast(err.message, "error");
    } finally {
      syncBtn.disabled = false;
      syncBtn.textContent = "FETCH FROM KT";
    }
  });

  loadOffersCatalog().catch(() => {});
  loadCampaigns().catch((err) => toast(err.message, "error"));
});
