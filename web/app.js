// His-Story frontend: loads data/checkins.json once and filters everything in the browser.
(async function () {
  const $ = (id) => document.getElementById(id);
  const LIST_LIMIT = 300;
  const ROUTE_LIMIT = 3000;
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

  // ---- data ----------------------------------------------------------------
  const raw = await (await fetch("data/checkins.json")).json();
  const all = raw.rows.map((r) => {
    const o = Object.fromEntries(raw.columns.map((c, i) => [c, r[i]]));
    // Local wall-clock time of the check-in, read with getUTC* methods.
    o.local = new Date((o.t + o.tz * 60) * 1000);
    o.day = o.local.toISOString().slice(0, 10);
    o.text = fold([o.venue, o.cat, o.city, o.country, o.shout, (o.people || []).join(" ")].join(" "));
    return o;
  });
  const byId = new Map(all.map((c) => [c.id, c]));

  function fold(s) {
    return (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  }
  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }
  function fmt(d) {
    return d.toLocaleString("en-GB", { timeZone: "UTC", weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  // ---- state + filtering -----------------------------------------------------
  let filtered = all;
  let visits = null, filteredVisits = [];
  function applyFilters() {
    const terms = fold($("q").value).split(/\s+/).filter(Boolean);
    const from = $("from").value, to = $("to").value;
    filtered = all.filter((c) =>
      !c.removed &&
      (!from || c.day >= from) && (!to || c.day <= to) &&
      terms.every((t) => c.text.includes(t)));
    filteredVisits = $("visits").checked && visits ? visits.filter((v) =>
      (!from || v.day >= from) && (!to || v.day <= to) &&
      terms.every((t) => v.text.includes(t))) : [];
    if (popup) popup.remove();
    render();
  }

  // ---- map -----------------------------------------------------------------
  const map = new maplibregl.Map({
    container: "map",
    style: `https://tiles.openfreemap.org/styles/${dark ? "dark" : "positron"}`,
    center: [10, 35],
    zoom: 1.6,
    attributionControl: { compact: true },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  let globe = true;

  map.on("style.load", () => {
    map.setProjection({ type: globe ? "globe" : "mercator" });
    const accent = cssVar("--accent");
    map.addSource("checkins", { type: "geojson", data: geojson(), cluster: true, clusterRadius: 40, clusterMaxZoom: 14 });
    map.addSource("visits", { type: "geojson", data: visitsGeojson() });
    map.addLayer({
      id: "visits", type: "circle", source: "visits",
      paint: {
        "circle-color": cssVar("--muted"), "circle-opacity": 0.55,
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 2, 2, 12, 5],
        "circle-stroke-width": 1, "circle-stroke-color": cssVar("--panel"),
      },
    });
    map.addSource("route", { type: "geojson", data: routeGeojson() });
    map.addLayer({ id: "route", type: "line", source: "route", paint: { "line-color": accent, "line-width": 2, "line-opacity": 0.6 } });
    map.addLayer({
      id: "clusters", type: "circle", source: "checkins", filter: ["has", "point_count"],
      paint: {
        "circle-color": accent, "circle-opacity": 0.75,
        "circle-radius": ["step", ["get", "point_count"], 12, 25, 16, 250, 22, 2500, 30],
        "circle-stroke-width": 2, "circle-stroke-color": "#fff",
      },
    });
    map.addLayer({
      id: "cluster-count", type: "symbol", source: "checkins", filter: ["has", "point_count"],
      layout: { "text-field": ["get", "point_count_abbreviated"], "text-size": 12, "text-font": ["Noto Sans Bold"] },
      paint: { "text-color": "#fff" },
    });
    map.addLayer({
      id: "points", type: "circle", source: "checkins", filter: ["!", ["has", "point_count"]],
      paint: { "circle-color": accent, "circle-radius": 6, "circle-stroke-width": 2, "circle-stroke-color": "#fff" },
    });
  });

  map.on("click", "clusters", async (e) => {
    const f = e.features[0];
    const zoom = await map.getSource("checkins").getClusterExpansionZoom(f.properties.cluster_id);
    map.easeTo({ center: f.geometry.coordinates, zoom });
  });
  map.on("click", "points", (e) => {
    // Several check-ins at the same venue stack on one point: show them all.
    const ids = e.features.map((f) => f.properties.id);
    showPopup(e.features[0].geometry.coordinates, ids.map((id) => byId.get(id)));
  });
  map.on("click", "visits", (e) => {
    // Check-ins sit on top; only show a visit popup when no check-in was clicked.
    if (map.queryRenderedFeatures(e.point, { layers: ["clusters", "points"] }).length) return;
    const v = filteredVisits[e.features[0].properties.i];
    const mins = v.end ? Math.round((v.end - v.start) / 60) : null;
    const dur = mins == null ? "" : mins >= 90 ? ` · ${Math.round(mins / 60)} h` : ` · ${mins} min`;
    if (popup) popup.remove();
    popup = new maplibregl.Popup({ maxWidth: "320px" }).setLngLat(e.features[0].geometry.coordinates).setHTML(
      `<div class="popup"><h3>${esc(v.label || v.city || "Detected visit")}</h3>
       <div class="meta">Detected visit, no check-in${v.label && v.city ? " · " + esc(v.city) : ""}</div>
       <p><strong>${esc(fmt(v.local))}</strong>${dur}</p></div>`).addTo(map);
  });
  for (const layer of ["clusters", "points", "visits"]) {
    map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
  }

  function geojson() {
    return {
      type: "FeatureCollection",
      features: filtered.map((c) => ({ type: "Feature", properties: { id: c.id }, geometry: { type: "Point", coordinates: [c.lng, c.lat] } })),
    };
  }
  function visitsGeojson() {
    return {
      type: "FeatureCollection",
      features: filteredVisits.map((v, i) => ({ type: "Feature", properties: { i }, geometry: { type: "Point", coordinates: [v.lng, v.lat] } })),
    };
  }
  function routeGeojson() {
    const on = $("route").checked && filtered.length > 1 && filtered.length <= ROUTE_LIMIT;
    return {
      type: "FeatureCollection",
      features: on ? [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: filtered.map((c) => [c.lng, c.lat]) } }] : [],
    };
  }

  let popup;
  function showPopup(lngLat, items) {
    items.sort((a, b) => b.t - a.t);
    const first = items[0];
    const html = `<div class="popup">
      <h3>${esc(first.venue)}${first.closed ? '<span class="tag">closed</span>' : ""}</h3>
      <div class="meta">${esc([first.cat, first.city, first.country].filter(Boolean).join(" · "))}</div>
      ${items.slice(0, 8).map((c) => `
        <p><strong>${esc(fmt(c.local))}</strong>${c.private ? '<span class="tag">private</span>' : ""}
        ${c.shout ? `<br>${esc(c.shout)}` : ""}
        ${c.people ? `<br><span class="meta">with ${esc(c.people.join(", "))}</span>` : ""}</p>
        ${c.photos ? `<div class="photos">${c.photos.map((p) => `<a href="${esc(p)}" target="_blank" rel="noopener"><img src="${esc(p)}" loading="lazy" alt=""></a>`).join("")}</div>` : ""}`).join("")}
      ${items.length > 8 ? `<p class="meta">+ ${items.length - 8} more visits here</p>` : ""}
    </div>`;
    if (popup) popup.remove();
    popup = new maplibregl.Popup({ maxWidth: "320px" }).setLngLat(lngLat).setHTML(html).addTo(map);
  }

  // ---- list + stats ----------------------------------------------------------
  function render() {
    // Count by venue id: different places can share a name ("Starbucks").
    const venues = new Set(filtered.map((c) => c.vid || c.venue)).size;
    const countries = new Set(filtered.map((c) => c.cc).filter(Boolean)).size;
    const cities = new Set(filtered.map((c) => c.city).filter(Boolean)).size;
    $("stats").textContent = `${filtered.length.toLocaleString()} check-ins · ${venues.toLocaleString()} venues · ${cities} cities · ${countries} countries` +
      ($("visits").checked ? ` · ${filteredVisits.length.toLocaleString()} visits` : "");

    const items = filtered.slice(-LIST_LIMIT).reverse();
    $("results").innerHTML = items.map((c) => `
      <li data-id="${c.id}">
        <div class="venue">${esc(c.venue)}</div>
        <div class="meta">${esc(fmt(c.local))}${c.city || c.country ? " · " + esc([c.city, c.country].filter(Boolean).join(", ")) : ""}</div>
        ${c.shout ? `<div class="shout">${esc(c.shout)}</div>` : ""}
      </li>`).join("") +
      (filtered.length > LIST_LIMIT ? `<li class="more">Showing latest ${LIST_LIMIT} of ${filtered.length.toLocaleString()}; narrow the search to see more</li>` : "");

    if (map.getSource("checkins")) {
      map.getSource("checkins").setData(geojson());
      map.getSource("route").setData(routeGeojson());
      map.getSource("visits").setData(visitsGeojson());
    }
    drawTimeline();
  }

  $("results").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-id]");
    if (!li) return;
    const c = byId.get(li.dataset.id);
    map.flyTo({ center: [c.lng, c.lat], zoom: Math.max(map.getZoom(), 14) });
    showPopup([c.lng, c.lat], [c]);
  });

  function fitToFiltered() {
    if (!filtered.length && !filteredVisits.length) return;
    const b = new maplibregl.LngLatBounds();
    filtered.forEach((c) => b.extend([c.lng, c.lat]));
    filteredVisits.forEach((v) => b.extend([v.lng, v.lat]));
    map.fitBounds(b, { padding: 60, maxZoom: 14, duration: 800 });
  }

  // ---- timeline (monthly histogram, drag to select) ---------------------------
  const canvas = $("hist");
  const firstMonth = monthIndex(all[0].day), lastMonth = monthIndex(all[all.length - 1].day);
  const nMonths = lastMonth - firstMonth + 1;
  let drag = null;

  function monthIndex(day) { return +day.slice(0, 4) * 12 + (+day.slice(5, 7) - 1); }
  function monthStart(i) { return `${Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}-01`; }
  function monthEnd(i) {
    const d = new Date(Date.UTC(Math.floor(i / 12), (i % 12) + 1, 0));
    return d.toISOString().slice(0, 10);
  }
  function xToMonth(x) {
    return firstMonth + Math.min(nMonths - 1, Math.max(0, Math.floor((x / canvas.clientWidth) * nMonths)));
  }

  function drawTimeline() {
    const dpr = devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    const totals = new Array(nMonths).fill(0), sel = new Array(nMonths).fill(0);
    all.forEach((c) => { if (!c.removed) totals[monthIndex(c.day) - firstMonth]++; });
    filtered.forEach((c) => sel[monthIndex(c.day) - firstMonth]++);
    const max = Math.max(...totals), bw = w / nMonths, top = 14;

    const from = $("from").value, to = $("to").value;
    if (from || to) {
      const a = from ? monthIndex(from) - firstMonth : 0, b = to ? monthIndex(to) - firstMonth : nMonths - 1;
      ctx.fillStyle = cssVar("--accent-soft");
      ctx.fillRect(a * bw, 0, (b - a + 1) * bw, h);
    }
    for (let i = 0; i < nMonths; i++) {
      const th = ((h - top) * totals[i]) / max, sh = ((h - top) * sel[i]) / max;
      ctx.fillStyle = cssVar("--bar");
      ctx.fillRect(i * bw, h - th, Math.max(1, bw - 0.5), th);
      ctx.fillStyle = cssVar("--accent");
      ctx.fillRect(i * bw, h - sh, Math.max(1, bw - 0.5), sh);
    }
    ctx.fillStyle = cssVar("--muted");
    ctx.font = "10px -apple-system, sans-serif";
    for (let m = firstMonth; m <= lastMonth; m++) {
      if (m % 12 === 0) ctx.fillText(String(m / 12), (m - firstMonth) * bw + 2, 10);
    }
  }

  canvas.addEventListener("pointerdown", (e) => {
    drag = { start: xToMonth(e.offsetX) };
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const a = Math.min(drag.start, xToMonth(e.offsetX)), b = Math.max(drag.start, xToMonth(e.offsetX));
    $("from").value = monthStart(a);
    $("to").value = monthEnd(b);
    applyFilters();
  });
  canvas.addEventListener("pointerup", (e) => {
    if (!drag) return;
    const m = xToMonth(e.offsetX);
    if (m === drag.start) { $("from").value = monthStart(m); $("to").value = monthEnd(m); }
    drag = null;
    applyFilters();
    fitToFiltered();
  });

  // ---- controls --------------------------------------------------------------
  let t;
  $("q").addEventListener("input", () => { clearTimeout(t); t = setTimeout(applyFilters, 150); });
  $("q").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    clearTimeout(t);
    applyFilters();
    fitToFiltered();
  });
  for (const id of ["from", "to"]) $(id).addEventListener("change", () => { applyFilters(); fitToFiltered(); });
  $("route").addEventListener("change", render);
  $("visits").addEventListener("change", async () => {
    if ($("visits").checked && !visits) {
      // Loaded only when first switched on.
      const d = await (await fetch("data/visits.json")).json();
      visits = d.rows.map((r) => {
        const v = Object.fromEntries(d.columns.map((c, i) => [c, r[i]]));
        v.local = new Date((v.start + v.tz * 60) * 1000);
        v.day = v.local.toISOString().slice(0, 10);
        v.text = fold([v.label, v.city].join(" "));
        return v;
      });
    }
    applyFilters();
  });
  $("clear").addEventListener("click", () => {
    $("q").value = $("from").value = $("to").value = "";
    applyFilters();
  });
  $("projection").addEventListener("click", () => {
    globe = !globe;
    if (popup) popup.remove();
    map.setProjection({ type: globe ? "globe" : "mercator" });
    $("projection").textContent = globe ? "Flat map" : "Globe";
  });
  addEventListener("resize", drawTimeline);

  applyFilters();
})();
