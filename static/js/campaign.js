document.addEventListener("DOMContentLoaded", () => {
  const campaignId = Number(location.pathname.split("/").pop());
  const titleEl = document.getElementById("campaign-title");
  const subEl = document.getElementById("campaign-sub");
  const streamsEl = document.getElementById("streams");
  const fetchBtn = document.getElementById("fetch-btn");
  const deleteBtn = document.getElementById("delete-campaign-btn");
  const openKtBtn = document.getElementById("open-kt-btn");
  const logsEl = document.getElementById("sync-logs");

  let campaign = null;
  // зеркало MIN_ACTIVE_SHARE из app/services/weights.py
  const MIN_ACTIVE_SHARE = 1;
  // Изменения одного потока уходят строго по очереди: параллельные PATCH на сервере
  // читают одно и то же состояние и затирают доли друг друга.
  const queues = new Map();
  const diffHtml = new WeakMap();

  function validatePinnedLocal(offers) {
    const active = offers.filter((o) => o.status === "active");
    const pinned = active.filter((o) => o.pinned);
    const unpinned = active.filter((o) => !o.pinned);
    const pinnedSum = pinned.reduce((s, o) => s + Math.max(0, o.share | 0), 0);
    if (pinned.some((o) => o.share < MIN_ACTIVE_SHARE)) {
      throw new Error(`Закреплённая доля должна быть не меньше ${MIN_ACTIVE_SHARE}%`);
    }
    if (pinnedSum > 100) throw new Error(`Сумма закреплённых долей (${pinnedSum}%) превышает 100%`);
    if (pinnedSum === 100 && unpinned.length) {
      throw new Error(
        `Сумма закреплённых долей уже 100%, но есть ещё ${unpinned.length} активных оффер(а) без доли`
      );
    }
    const remainder = 100 - pinnedSum;
    if (unpinned.length && remainder < MIN_ACTIVE_SHARE * unpinned.length) {
      throw new Error(
        `Свободно ${remainder}% на ${unpinned.length} незакреплённых оффер(а) — каждому нужен минимум ${MIN_ACTIVE_SHARE}%`
      );
    }
    if (pinned.length && !unpinned.length && pinnedSum !== 100) {
      throw new Error(`Все активные офферы закреплены, сумма = ${pinnedSum}% (нужно 100%)`);
    }
  }

  function redistributeOffers(offers) {
    for (const o of offers) {
      if (o.status !== "active") o.share = 0;
    }
    const active = offers.filter((o) => o.status === "active");
    if (!active.length) return;
    validatePinnedLocal(offers);

    const pinned = active.filter((o) => o.pinned);
    const unpinned = active.filter((o) => !o.pinned);
    const pinnedSum = pinned.reduce((s, o) => s + Math.max(0, o.share | 0), 0);
    const remainder = 100 - pinnedSum;
    if (!unpinned.length) return;
    const base = Math.floor(remainder / unpinned.length);
    const extra = remainder % unpinned.length;
    unpinned.forEach((o, i) => {
      o.share = base + (i < extra ? 1 : 0);
    });
    const total = active.reduce((s, o) => s + o.share, 0);
    if (total !== 100) unpinned[0].share += 100 - total;
  }

  function enqueue(streamId, task) {
    const q = queues.get(streamId) || { tail: Promise.resolve(), pending: 0 };
    queues.set(streamId, q);
    q.pending += 1;
    const run = q.tail.then(task).finally(() => {
      q.pending -= 1;
    });
    q.tail = run.catch(() => {});
    return run;
  }

  const hasPending = (streamId) => (queues.get(streamId)?.pending || 0) > 0;
  // 410 — кампанию удалили в Keitaro, локальная копия осталась без родителя
  const isCampaignMissing = (err) => err.status === 410;
  const findStream = (streamId) => campaign.streams.find((s) => s.id === streamId);
  const cardOf = (streamId) =>
    streamsEl.querySelector(`section.card[data-stream-id="${streamId}"]`);

  async function load() {
    campaign = await api(`/api/campaigns/${campaignId}`);
    render();
    loadLogs();
  }

  function storeStream(updated) {
    const idx = campaign.streams.findIndex((s) => s.id === updated.id);
    if (idx >= 0) campaign.streams[idx] = updated;
    else campaign.streams.push(updated);
  }

  function bindCard(card) {
    const input = card.querySelector(".offer-add-input");
    const menu = card.querySelector(".offer-add-menu");
    if (input && menu) bindOfferAutocomplete(input, menu, () => {});
  }

  function render() {
    titleEl.textContent = campaign.name;
    subEl.innerHTML = `
      local #${campaign.id} · Keitaro #${campaign.keitaro_campaign_id} · geo
      <strong>${escapeHtml(campaign.geo)}</strong>
    `;
    if (openKtBtn) {
      openKtBtn.href = `https://keitaro-for-tests.etolegko.com/admin/#!/campaign.editor/${campaign.keitaro_campaign_id}`;
    }

    const offerStreams = (campaign.streams || []).filter((s) => s.has_offers);
    if (!offerStreams.length) {
      streamsEl.innerHTML = `<div class="empty">Потоков с офферами нет. Нажмите FETCH STREAMS FROM KT.</div>`;
      return;
    }

    // geo/redirect-потоки без офферов не показываем — ими UI не управляет
    streamsEl.innerHTML = offerStreams.map(renderStream).join("");
    streamsEl.querySelectorAll("section.card[data-stream-id]").forEach(bindCard);
  }

  function renderCard(stream) {
    const card = cardOf(stream.id);
    if (!card) {
      render();
      return;
    }
    const tpl = document.createElement("template");
    tpl.innerHTML = renderStream(stream).trim();
    const fresh = tpl.content.firstElementChild;
    card.replaceWith(fresh);
    bindCard(fresh);
  }

  /** Показывает состояние потока, по возможности точечно, без пересоздания карточки. */
  function showStream(stream) {
    if (!stream.has_offers) return;
    const card = cardOf(stream.id);
    if (!card) {
      renderCard(stream);
      return;
    }
    const rowKey = (id, removed) => `${id}:${removed}`;
    const domRows = [...card.querySelectorAll(".offer-row")]
      .map((r) => rowKey(r.dataset.offerId, r.classList.contains("removed")))
      .join(",");
    const wantRows = (stream.offers || [])
      .map((o) => rowKey(o.id, o.status === "removed"))
      .join(",");
    if (domRows === wantRows) patchStreamChrome(card, stream);
    else renderCard(stream);
  }

  function applyServer(updated) {
    // в очереди есть более свежий запрос этого потока — показываем только его итог
    if (hasPending(updated.id)) return;
    storeStream(updated);
    showStream(updated);
  }

  function resync(streamId) {
    enqueue(streamId, () => api(`/api/streams/${streamId}`))
      .then(applyServer)
      .catch(() => {});
  }

  function renderDiff(stream) {
    if (!stream.is_draft || !stream.diff || stream.diff.empty) return "";
    const d = stream.diff;
    const lines = [];
    for (const a of d.added || []) {
      lines.push(`+ добавить «${escapeHtml(a.offer_name)}» (${a.share}%)`);
    }
    for (const r of d.removed || []) {
      lines.push(`− удалить «${escapeHtml(r.offer_name)}» (было ${r.share_before}%)`);
    }
    for (const s of d.share_changed || []) {
      lines.push(
        `~ доля «${escapeHtml(s.offer_name)}»: ${s.share_before}% → ${s.share_after}%`
      );
    }
    for (const p of d.pin_changed || []) {
      lines.push(
        `~ pin «${escapeHtml(p.offer_name)}»: ${p.pinned_before ? "on" : "off"} → ${p.pinned_after ? "on" : "off"}`
      );
    }
    if (!lines.length) return "";
    return `
      <div class="diff-box">
        <div class="diff-title">Было в KT → Станет после PUSH</div>
        <ul class="diff-list">${lines.map((l) => `<li>${l}</li>`).join("")}</ul>
      </div>`;
  }

  const activeSum = (stream) =>
    (stream.offers || []).filter((o) => o.status === "active").reduce((s, o) => s + o.share, 0);

  function renderStream(stream) {
    const draftClass = stream.is_draft ? "draft" : "";
    const badge = stream.is_draft ? `<span class="draft-badge">unpublished draft</span>` : "";
    const sum = activeSum(stream);
    const sumOk = sum === 100;
    const offersHtml = (stream.offers || []).map((o) => renderOfferRow(o)).join("");

    // diff — под списком: его высота меняется на каждый pin и не должна сдвигать строки офферов
    return `
      <section class="card ${draftClass}" data-stream-id="${stream.id}">
        <div class="stream-head">
          <div>
            <h2>${escapeHtml(stream.name)}</h2>
            <div class="muted" style="font-size:0.82rem;margin-top:4px">
              offer stream · KT #${stream.keitaro_stream_id}
            </div>
          </div>
          <div data-draft-slot>${badge}</div>
        </div>

        <div class="stream-actions">
          <button type="button" class="btn btn-success" data-action="push" ${stream.is_draft ? "" : "disabled"}>PUSH TO KT</button>
          <button type="button" class="btn btn-warn" data-action="cancel" ${stream.is_draft ? "" : "disabled"}>CANCEL</button>
        </div>

        <div class="add-offer-row">
          <div class="field autocomplete">
            <label>Добавить оффер</label>
            <input type="search" class="offer-add-input" placeholder="Поиск оффера…" autocomplete="off" />
            <div class="ac-menu offer-add-menu" hidden></div>
          </div>
          <button type="button" class="btn btn-secondary" data-action="add-offer">Добавить</button>
        </div>

        <div class="share-sum ${sumOk ? "ok" : "bad"}" data-share-sum>Сумма: ${sum}%</div>
        <div class="offers" data-offers>${offersHtml || '<div class="empty">Нет офферов</div>'}</div>
        <div data-diff-slot>${renderDiff(stream)}</div>
      </section>`;
  }

  function shareControl(o) {
    return o.status === "removed" || o.pinned
      ? `<div class="share-pill" data-share>${o.share}%</div>`
      : `<input type="number" class="share-input" min="${MIN_ACTIVE_SHARE}" max="100" step="1" value="${o.share}" data-share-input title="Доля %" />`;
  }

  function renderOfferRow(o) {
    const removed = o.status === "removed";
    return `
      <div class="offer-row ${removed ? "removed" : ""}" data-offer-id="${o.id}">
        <div>
          <div class="title">${escapeHtml(o.offer_name)}</div>
          <div class="sub">offer_id ${o.keitaro_offer_id}${removed ? " · removed" : ""}</div>
        </div>
        ${shareControl(o)}
        <label class="pin-toggle" title="Закрепить долю" data-pin-wrap>
          <input type="checkbox" class="pin-input" ${o.pinned ? "checked" : ""} ${removed ? "disabled" : ""} />
          <span class="pin-track"><span class="pin-thumb"></span></span>
          pin
        </label>
        ${
          removed
            ? `<button type="button" class="btn btn-sm btn-success" data-action="bring-back">BRING BACK</button>`
            : `<button type="button" class="btn btn-sm btn-danger" data-action="remove">REMOVE</button>`
        }
      </div>`;
  }

  function patchStreamChrome(card, stream) {
    card.classList.toggle("draft", !!stream.is_draft);
    const slot = card.querySelector("[data-draft-slot]");
    if (slot) {
      slot.innerHTML = stream.is_draft
        ? `<span class="draft-badge">unpublished draft</span>`
        : "";
    }
    const pushBtn = card.querySelector('[data-action="push"]');
    const cancelBtn = card.querySelector('[data-action="cancel"]');
    if (pushBtn) pushBtn.disabled = !stream.is_draft;
    if (cancelBtn) cancelBtn.disabled = !stream.is_draft;

    const sum = activeSum(stream);
    const sumEl = card.querySelector("[data-share-sum]");
    if (sumEl) {
      sumEl.textContent = `Сумма: ${sum}%`;
      sumEl.classList.toggle("ok", sum === 100);
      sumEl.classList.toggle("bad", sum !== 100);
    }

    for (const o of stream.offers || []) {
      const row = card.querySelector(`.offer-row[data-offer-id="${o.id}"]`);
      if (!row) continue;
      const wantPill = o.status === "removed" || o.pinned;
      const control = row.querySelector("[data-share], [data-share-input]");
      if (control && control.hasAttribute("data-share") !== wantPill) {
        control.outerHTML = shareControl(o);
      } else if (control && wantPill) {
        control.textContent = `${o.share}%`;
      } else if (control && document.activeElement !== control) {
        control.value = String(o.share);
      }
      const pin = row.querySelector(".pin-input");
      if (pin && pin.checked !== !!o.pinned) pin.checked = !!o.pinned;
    }

    const diffSlot = card.querySelector("[data-diff-slot]");
    if (diffSlot) {
      const html = renderDiff(stream);
      if (diffHtml.get(diffSlot) !== html) {
        // пока поток в draft, блок только растёт: если он сжимается внизу прокрученной
        // страницы, браузер поджимает прокрутку и весь список офферов прыгает
        diffSlot.style.minHeight = stream.is_draft ? `${diffSlot.offsetHeight}px` : "";
        diffSlot.innerHTML = html;
        diffHtml.set(diffSlot, html);
      }
    }
  }

  const LOG_ACTIONS = {
    push: "Публикация",
    fetch: "Загрузка",
    cancel: "Отмена",
    create_campaign: "Создание",
  };

  async function loadLogs() {
    if (!logsEl) return;
    try {
      const rows = await api(`/api/campaigns/${campaignId}/logs?limit=40`);
      if (!rows.length) {
        logsEl.innerHTML = `<div class="empty">Записей пока нет</div>`;
        return;
      }
      logsEl.innerHTML = `
        <div class="log-list">
          ${rows
            .map((r) => {
              const when = escapeHtml(String(r.created_at).replace("T", " ").slice(0, 19));
              const action = escapeHtml(LOG_ACTIONS[r.action] || r.action);
              const status = r.status === "ok" ? "OK" : "Ошибка";
              return `
            <div class="log-row ${r.status}">
              <div class="log-meta">
                <span class="log-time">${when}</span>
                <span class="log-action">${action}</span>
                <span class="log-status">${status}</span>
              </div>
              <div class="log-summary">${escapeHtml(r.summary)}</div>
            </div>`;
            })
            .join("")}
        </div>`;
    } catch (err) {
      logsEl.innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
    }
  }

  streamsEl.addEventListener("click", (e) => {
    if (e.target.closest("[data-pin-wrap]")) return;
    if (e.target.closest("[data-share-input]")) return;
    const card = e.target.closest("section.card[data-stream-id]");
    if (!card) return;
    const btn = e.target.closest("button[data-action]");
    if (!btn || btn.disabled) return;

    const streamId = Number(card.dataset.streamId);
    const action = btn.dataset.action;
    const offerRow = e.target.closest(".offer-row");
    const offerId = offerRow ? Number(offerRow.dataset.offerId) : null;
    const input = card.querySelector(".offer-add-input");

    let request;
    let okMessage;
    if (action === "push") {
      request = () => api(`/api/streams/${streamId}/push`, { method: "POST" });
      okMessage = "Опубликовано в Keitaro";
    } else if (action === "cancel") {
      request = () => api(`/api/streams/${streamId}/cancel`, { method: "POST" });
      okMessage = "Черновик отменён";
    } else if (action === "add-offer") {
      const oid = Number(input?.dataset.offerId || 0);
      if (!oid) {
        toast("Выберите оффер", "error");
        return;
      }
      request = () =>
        api(`/api/streams/${streamId}/offers`, {
          method: "POST",
          body: JSON.stringify({ offer_id: oid }),
        });
      okMessage = "Оффер добавлен (draft)";
    } else if (action === "remove") {
      request = () =>
        api(`/api/streams/${streamId}/offers/${offerId}/remove`, { method: "POST" });
      okMessage = "Оффер помечен removed (draft)";
    } else if (action === "bring-back") {
      request = () =>
        api(`/api/streams/${streamId}/offers/${offerId}/bring-back`, { method: "POST" });
      okMessage = "Оффер возвращён (draft)";
    } else {
      return;
    }

    const writesLog = action === "push" || action === "cancel";
    btn.disabled = true;
    enqueue(streamId, request)
      .then((updated) => {
        applyServer(updated);
        toast(okMessage, "ok");
        if (writesLog) loadLogs();
      })
      .catch((err) => {
        if (action === "push" && isCampaignMissing(err)) {
          offerDeleteMissingCampaign(err.message);
          return;
        }
        toast(err.message, "error");
        if (action === "push") loadLogs();
        resync(streamId);
      })
      .finally(() => {
        if (!btn.isConnected) return;
        const s = findStream(streamId);
        btn.disabled = writesLog ? !s?.is_draft : false;
      });
  });

  streamsEl.addEventListener("change", (e) => {
    const el = e.target;
    if (!(el instanceof HTMLInputElement) || !el.classList.contains("pin-input")) return;

    const card = el.closest("section.card[data-stream-id]");
    const offerRow = el.closest(".offer-row");
    if (!card || !offerRow) return;

    const streamId = Number(card.dataset.streamId);
    const offerId = Number(offerRow.dataset.offerId);
    const stream = findStream(streamId);
    const offer = stream?.offers?.find((o) => o.id === offerId);
    if (!stream || !offer) return;

    const wanted = el.checked;
    const snapshot = stream.offers.map((o) => ({ pinned: o.pinned, share: o.share }));

    offer.pinned = wanted;
    try {
      redistributeOffers(stream.offers);
    } catch (err) {
      stream.offers.forEach((o, i) => {
        o.pinned = snapshot[i].pinned;
        o.share = snapshot[i].share;
      });
      el.checked = !wanted;
      toast(err.message, "error");
      return;
    }

    stream.is_draft = true;
    patchStreamChrome(card, stream);

    enqueue(streamId, () =>
      api(`/api/streams/${streamId}/offers/${offerId}`, {
        method: "PATCH",
        body: JSON.stringify({ pinned: wanted }),
      })
    )
      .then(applyServer)
      .catch((err) => {
        toast(err.message, "error");
        resync(streamId);
      });
  });

  streamsEl.addEventListener("change", (e) => {
    const el = e.target;
    if (!(el instanceof HTMLInputElement) || !el.classList.contains("share-input")) return;

    const card = el.closest("section.card[data-stream-id]");
    const offerRow = el.closest(".offer-row");
    if (!card || !offerRow) return;

    const streamId = Number(card.dataset.streamId);
    const offerId = Number(offerRow.dataset.offerId);
    const offer = findStream(streamId)?.offers?.find((o) => o.id === offerId);
    if (!offer) return;

    const prev = offer.share;
    const value = Number(el.value);
    if (!Number.isFinite(value) || value < MIN_ACTIVE_SHARE || value > 100) {
      el.value = String(prev);
      toast(`Доля должна быть числом ${MIN_ACTIVE_SHARE}–100. Чтобы исключить оффер, нажмите REMOVE`, "error");
      return;
    }

    el.disabled = true;
    enqueue(streamId, () =>
      api(`/api/streams/${streamId}/offers/${offerId}`, {
        method: "PATCH",
        body: JSON.stringify({ share: Math.round(value) }),
      })
    )
      .then((updated) => {
        applyServer(updated);
        toast("Доля обновлена (draft)", "ok");
      })
      .catch((err) => {
        el.value = String(prev);
        toast(err.message, "error");
        resync(streamId);
      })
      .finally(() => {
        if (el.isConnected) el.disabled = false;
      });
  });

  async function doFetch(force = false) {
    fetchBtn.disabled = true;
    try {
      const q = force ? "?force=true" : "";
      campaign = await api(`/api/campaigns/${campaignId}/fetch${q}`, { method: "POST" });
      toast(force ? "Черновик перезаписан данными из KT" : "Потоки синхронизированы с Keitaro", "ok");
      render();
      loadLogs();
    } catch (err) {
      if (err.status === 409) {
        const ok = window.confirm(
          "В потоке есть неопубликованные изменения. Перезаписать данными из Keitaro?"
        );
        if (ok) {
          await doFetch(true);
          return;
        }
        toast("FETCH отменён — черновик сохранён", "ok");
      } else if (isCampaignMissing(err)) {
        offerDeleteMissingCampaign(err.message);
      } else {
        toast(err.message, "error");
        loadLogs();
      }
    } finally {
      fetchBtn.disabled = false;
    }
  }

  async function deleteCampaign(okMessage) {
    if (deleteBtn) deleteBtn.disabled = true;
    try {
      await api(`/api/campaigns/${campaignId}`, { method: "DELETE" });
      toast(okMessage, "ok");
      window.location.href = "/";
    } catch (err) {
      toast(err.message, "error");
      if (deleteBtn) deleteBtn.disabled = false;
    }
  }

  function offerDeleteMissingCampaign(message) {
    if (window.confirm(`${message}\n\nУдалить эту кампанию из локального списка Wrapper?`)) {
      deleteCampaign("Локальная копия удалена");
      return;
    }
    toast(message, "error");
    loadLogs();
  }

  function confirmDeleteCampaign() {
    if (!campaign) return;
    const ok = window.confirm(
      `Удалить кампанию «${campaign.name}»?\n\n` +
        `Она будет удалена в Keitaro (KT #${campaign.keitaro_campaign_id}) ` +
        `и из локального списка Wrapper.\nЭто необратимо.`
    );
    if (ok) deleteCampaign(`Кампания «${campaign.name}» удалена`);
  }

  fetchBtn.addEventListener("click", () => doFetch(false));
  if (deleteBtn) deleteBtn.addEventListener("click", confirmDeleteCampaign);

  load().catch((err) => toast(err.message, "error"));
  // каталог офферов грузим сразу, чтобы первый ввод в поиск не ждал сеть
  loadOffersCatalog().catch(() => {});
});
