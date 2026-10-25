const $ = (id) => document.getElementById(id);
const regions = {
  AB: "Alberta",
  BC: "British Columbia",
  MB: "Manitoba",
  NB: "New Brunswick",
  NL: "Newfoundland & Labrador",
  NS: "Nova Scotia",
  NT: "Northwest Territories",
  NU: "Nunavut",
  ON: "Ontario",
  PE: "Prince Edward Island",
  QC: "Québec",
  SK: "Saskatchewan",
  YT: "Yukon",
};
const levels = {
  federal: "Federal",
  provincial_territorial: "Provincial & territorial",
  local: "Local",
};
const storageKey = "civic-circle-public-watchlist-v1";
const pageSize = 24;
let dataset,
  people = [],
  byId = new Map(),
  saved = new Set(),
  route = {},
  selected,
  lastFocus,
  mapLoaded = false;
let viewBox = [0, 0, 900, 480];
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}
function showNotice(message) {
  $("notice").textContent = message;
  $("notice").hidden = !message;
}
function showError(message) {
  $("error").textContent = message;
  $("error").hidden = !message;
}
function fold(value) {
  return String(value || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}
function safeUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}
function readSaved() {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey) || "[]");
    if (
      !Array.isArray(value) ||
      value.length > 25000 ||
      value.some((v) => typeof v !== "string")
    )
      throw new Error();
    saved = new Set(value);
  } catch {
    saved = new Set();
    showNotice(
      "Browser storage is unavailable or unreadable. Your watchlist will work for this visit.",
    );
  }
}
function savePerson(id) {
  if (saved.has(id)) saved.delete(id);
  else saved.add(id);
  try {
    localStorage.setItem(storageKey, JSON.stringify([...saved]));
  } catch {
    showNotice(
      "Your watchlist could not be saved to this browser. Changes last for this visit only.",
    );
  }
  render();
}
function readRoute() {
  const p = new URL(location.href).searchParams;
  const view = p.get("view");
  route = {
    view: ["overview", "directory", "saved", "map"].includes(view)
      ? view
      : "overview",
    q: (p.get("q") || "").slice(0, 200),
    province: p.get("province") || "",
    category: p.get("category") || "",
    party: p.get("party") || "",
    status: p.get("status") || (view === "saved" ? "all" : "active"),
    page: Math.max(1, Math.min(1000, Number(p.get("page")) || 1)),
    person: p.get("person") || "",
  };
  if (!["active", "all", "removed"].includes(route.status))
    route.status = "active";
}
function go(values, replace = false) {
  const url = new URL(location.href);
  url.search = "";
  for (const [key, value] of Object.entries(values))
    if (value !== "" && value !== undefined && value !== null)
      url.searchParams.set(key, String(value));
  history[replace ? "replaceState" : "pushState"](null, "", url);
  readRoute();
  render();
}
function directoryFilters() {
  return {
    view: route.view,
    q: $("query").value,
    province: $("province").value,
    category: $("category").value,
    party: $("party").value,
    status: $("status").value,
    page: 1,
  };
}
function renderOverview() {
  const active = people.filter((p) => p.status === "active"),
    counts = {};
  for (const person of active)
    counts[person.category] = (counts[person.category] || 0) + 1;
  $("stats").replaceChildren(
    ...[
      ["Representatives", active.length],
      ["Federal", counts.federal || 0],
      ["Provincial & territorial", counts.provincial_territorial || 0],
      ["Your watchlist", saved.size],
    ].map(([label, value]) => {
      const n = el("div", undefined, "stat");
      n.append(
        el("span", label),
        el("strong", value.toLocaleString()),
        el(
          "small",
          label === "Your watchlist"
            ? "Saved in this browser"
            : "In the published dataset",
        ),
      );
      return n;
    }),
  );
  $("regions").replaceChildren(
    ...Object.entries(regions).map(([code, name]) => {
      const button = el("button");
      button.append(
        el("span", code, "region-code"),
        el("span", name),
        el(
          "small",
          active.filter((p) => p.province_territory === code).length + " →",
        ),
      );
      button.onclick = () => go({ view: "directory", province: code });
      return button;
    }),
  );
  const coverage = $("coverage");
  coverage.replaceChildren(
    el("p", "Published " + new Date(dataset.generated_at).toLocaleString()),
    el(
      "p",
      (dataset.coverage.note || "") +
        " Profiles show when the collector last saw each record.",
      "muted",
    ),
  );
  for (const [category, label] of Object.entries(levels))
    coverage.append(
      el("p", label + ": " + (counts[category] || 0) + " active records"),
    );
  $("freshness").textContent =
    "Published " +
    new Date(dataset.generated_at).toLocaleDateString() +
    " · Source freshness varies.";
}
function filteredPeople() {
  const q = fold(route.q.trim());
  return people.filter(
    (p) =>
      (route.view !== "saved" || saved.has(p.id)) &&
      (route.status === "all" || p.status === route.status) &&
      (!route.province || p.province_territory === route.province) &&
      (!route.category || p.category === route.category) &&
      (!route.party || p.party_name === route.party) &&
      (!q || p.search.includes(q)),
  );
}
function renderDirectory() {
  $("directory-title").textContent =
    route.view === "saved" ? "Your watchlist" : "Meet your representatives";
  $("directory-description").textContent =
    route.view === "saved"
      ? "Saved only in this browser. No account, tracking service, or public list."
      : "Search the public dataset across its included levels of government.";
  for (const key of ["province", "category", "party", "status"])
    $(key).value = route[key];
  $("query").value = route.q;
  const matches = filteredPeople(),
    pages = Math.max(1, Math.ceil(matches.length / pageSize));
  route.page = Math.min(route.page, pages);
  const missing =
    route.view === "saved"
      ? [...saved].filter((id) => !byId.has(id)).length
      : 0;
  $("result-count").textContent =
    matches.length +
    " representatives" +
    (missing
      ? " · " +
        missing +
        " saved profiles are absent from this published dataset."
      : "");
  $("cards").replaceChildren(
    ...matches
      .slice((route.page - 1) * pageSize, route.page * pageSize)
      .map((person) => {
        const card = el("article", undefined, "person-card"),
          top = el("div", undefined, "card-top"),
          save = el("button", saved.has(person.id) ? "★" : "☆", "save");
        save.setAttribute(
          "aria-label",
          (saved.has(person.id) ? "Unsave " : "Save ") + person.name,
        );
        save.setAttribute("aria-pressed", String(saved.has(person.id)));
        save.onclick = () => savePerson(person.id);
        top.append(
          el(
            "span",
            person.name
              .trim()
              .split(/\s+/)
              .map((x) => x[0])
              .slice(0, 2)
              .join(""),
            "avatar",
          ),
          save,
        );
        const name = el("button", person.name, "name-button");
        name.onclick = () => go({ ...route, person: person.id });
        card.append(
          top,
          el(
            "small",
            (levels[person.category] || person.category) +
              " · " +
              (person.province_territory || "CANADA"),
            "eyebrow",
          ),
          name,
        );
        if (person.status === "removed")
          card.append(el("span", "No longer listed in source", "former"));
        card.append(
          el(
            "p",
            person.elected_office ||
              person.representative_set ||
              "Representative",
          ),
          el("div", person.district_name || "District not supplied", "riding"),
          el("div", person.party_name || "Party not supplied", "party"),
        );
        return card;
      }),
  );
  $("empty").hidden = matches.length !== 0;
  $("pagination").hidden = pages <= 1;
  $("page-label").textContent = "Page " + route.page + " of " + pages;
  $("previous").disabled = route.page <= 1;
  $("next").disabled = route.page >= pages;
}
function renderProfile() {
  const person = byId.get(route.person),
    dialog = $("profile");
  if (!person) {
    if (dialog.open) dialog.close();
    selected = undefined;
    if (route.person)
      showError("That profile is not in this published dataset.");
    return;
  }
  if (!dialog.open) lastFocus = document.activeElement;
  selected = person;
  $("profile-name").textContent = person.name;
  $("profile-office").textContent = [
    person.elected_office,
    person.district_name || person.representative_set,
  ]
    .filter(Boolean)
    .join(" · ");
  $("profile-save").textContent = saved.has(person.id)
    ? "Saved · Remove from watchlist"
    : "Save to my watchlist";
  $("profile-status").hidden = person.status !== "removed";
  $("profile-status").textContent =
    "This record is no longer listed in the source. It is retained for reference.";
  const fields = [
    ["Government", levels[person.category]],
    ["Region", regions[person.province_territory]],
    ["Party", person.party_name],
    ["Office email", person.email],
    ["Office phone", person.phone],
    ["Last observed", person.last_seen],
    ["Record status", person.status],
  ];
  $("profile-details").replaceChildren(
    ...fields.map(([label, value]) => {
      const row = el("div");
      row.append(el("dt", label), el("dd", value || "Not supplied"));
      return row;
    }),
  );
  $("profile-source").replaceChildren();
  const url = safeUrl(person.url) || safeUrl(person.source_url);
  if (url) {
    const link = el("a", "Open original source →", "source-link");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    $("profile-source").append(link);
  }
  const share = new URL(location.href);
  share.search = "";
  share.searchParams.set("person", person.id);
  $("profile-link").value = share.href;
  $("copy-status").textContent =
    "Anyone with this link can open the public profile. Your watchlist is not shared.";
  if (!dialog.open) dialog.showModal();
}
function closeProfile() {
  const next = { ...route };
  delete next.person;
  go(next, true);
  lastFocus?.focus();
}
function render() {
  if (!dataset) return;
  showError("");
  $("saved-count").textContent = saved.size;
  for (const view of ["overview", "directory", "map"])
    $(view).hidden = !(
      route.view === view ||
      (view === "directory" && route.view === "saved")
    );
  document.querySelectorAll("nav [data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === route.view);
    if (button.dataset.view === route.view)
      button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  renderOverview();
  if (["directory", "saved"].includes(route.view)) renderDirectory();
  if (route.view === "map") loadMap();
  renderProfile();
}
function setViewBox() {
  $("district-map").setAttribute("viewBox", viewBox.join(" "));
}
function zoom(factor) {
  const w = Math.max(30, Math.min(1800, viewBox[2] * factor)),
    h = (w * 480) / 900;
  viewBox = [
    viewBox[0] + (viewBox[2] - w) / 2,
    viewBox[1] + (viewBox[3] - h) / 2,
    w,
    h,
  ];
  setViewBox();
}
async function loadMap() {
  if (mapLoaded) return;
  mapLoaded = true;
  $("map-status").textContent = "Loading published boundaries…";
  try {
    const response = await fetch("./data/map.json");
    if (!response.ok)
      throw new Error("No district boundary file has been published yet.");
    const geo = await response.json();
    if (!Array.isArray(geo.features) || !geo.features.length)
      throw new Error("No district boundaries available.");
    const points = geo.features.flatMap((f) =>
      (f.geometry.type === "Polygon"
        ? [f.geometry.coordinates]
        : f.geometry.coordinates
      ).flat(2),
    );
    const bounds = points.reduce(
      (b, p) => [
        Math.min(b[0], p[0]),
        Math.min(b[1], p[1]),
        Math.max(b[2], p[0]),
        Math.max(b[3], p[1]),
      ],
      [180, 90, -180, -90],
    );
    const scale = Math.min(
      850 / Math.max(0.01, bounds[2] - bounds[0]),
      430 / Math.max(0.01, bounds[3] - bounds[1]),
    );
    const xOffset = (900 - (bounds[2] - bounds[0]) * scale) / 2,
      yOffset = (480 - (bounds[3] - bounds[1]) * scale) / 2;
    const svg = $("district-map");
    svg.replaceChildren();
    for (const feature of geo.features) {
      const polygons =
        feature.geometry.type === "Polygon"
          ? [feature.geometry.coordinates]
          : feature.geometry.coordinates;
      const path = document.createElementNS(
        "http://www.w3.org/2000/svg",
        "path",
      );
      const d = polygons
        .flatMap((p) =>
          p.map(
            (ring) =>
              ring
                .map(
                  (p, i) =>
                    (i ? "L" : "M") +
                    (xOffset + (p[0] - bounds[0]) * scale).toFixed(2) +
                    "," +
                    (yOffset + (bounds[3] - p[1]) * scale).toFixed(2),
                )
                .join(" ") + "Z",
          ),
        )
        .join(" ");
      path.setAttribute("d", d);
      path.setAttribute("fill-rule", "evenodd");
      path.setAttribute("tabindex", "0");
      path.setAttribute("role", "button");
      const district = feature.properties.district || "District";
      path.setAttribute("aria-label", "Explore " + district);
      const title = document.createElementNS(
        "http://www.w3.org/2000/svg",
        "title",
      );
      title.textContent = district;
      path.append(title);
      const explore = () =>
        go({
          view: "directory",
          q: district,
          province: feature.properties.province_territory || "",
        });
      path.addEventListener("click", explore);
      path.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          explore();
        }
      });
      svg.append(path);
    }
    $("map-status").textContent =
      geo.features.length +
      " published districts. Boundaries may not cover every representative.";
  } catch (error) {
    $("map-status").textContent = error.message;
  }
}
document
  .querySelectorAll("[data-view]")
  .forEach((button) =>
    button.addEventListener("click", () => go({ view: button.dataset.view })),
  );
$("filters").addEventListener("submit", (event) => {
  event.preventDefault();
  go(directoryFilters());
});
$("reset").onclick = () => go({ view: route.view });
$("previous").onclick = () => go({ ...route, page: route.page - 1 });
$("next").onclick = () => go({ ...route, page: route.page + 1 });
$("close-profile").onclick = closeProfile;
$("profile").addEventListener("cancel", (event) => {
  event.preventDefault();
  closeProfile();
});
$("profile").addEventListener("click", (event) => {
  if (event.target === $("profile")) {
    const r = $("profile").getBoundingClientRect();
    if (
      event.clientX < r.left ||
      event.clientX > r.right ||
      event.clientY < r.top ||
      event.clientY > r.bottom
    )
      closeProfile();
  }
});
$("profile-save").onclick = () => {
  if (selected) savePerson(selected.id);
};
$("profile-link").onfocus = (event) => event.target.select();
$("copy-link").onclick = async () => {
  try {
    await navigator.clipboard.writeText($("profile-link").value);
    $("copy-status").textContent =
      "Link copied. Your watchlist is not included.";
  } catch {
    $("profile-link").focus();
    $("profile-link").select();
    $("copy-status").textContent = "Select and copy the link above.";
  }
};
$("zoom-in").onclick = () => zoom(0.7);
$("zoom-out").onclick = () => zoom(1 / 0.7);
$("reset-map").onclick = () => {
  viewBox = [0, 0, 900, 480];
  setViewBox();
};
$("district-map").addEventListener("keydown", (event) => {
  if (event.target !== $("district-map")) return;
  const delta = viewBox[2] * 0.1;
  if (
    ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "+", "-", "="].includes(
      event.key,
    )
  )
    event.preventDefault();
  if (event.key === "ArrowLeft") viewBox[0] -= delta;
  if (event.key === "ArrowRight") viewBox[0] += delta;
  if (event.key === "ArrowUp") viewBox[1] -= delta;
  if (event.key === "ArrowDown") viewBox[1] += delta;
  if (["+", "="].includes(event.key)) zoom(0.7);
  else if (event.key === "-") zoom(1 / 0.7);
  else setViewBox();
});
window.addEventListener("popstate", () => {
  readRoute();
  render();
});
window.addEventListener("storage", (event) => {
  if (event.key === storageKey) {
    readSaved();
    render();
  }
});
async function start() {
  readSaved();
  readRoute();
  try {
    const response = await fetch("./data/people.json");
    if (!response.ok)
      throw new Error(
        "The public dataset has not been published yet. Please try again after the next deployment.",
      );
    dataset = await response.json();
    if (
      dataset.schema_version !== 1 ||
      !Array.isArray(dataset.people) ||
      !dataset.people.length
    )
      throw new Error("The published dataset is invalid.");
    people = dataset.people
      .map((p) => ({
        ...p,
        search: fold(
          [
            p.name,
            p.district_name,
            p.party_name,
            p.representative_set,
            p.province_territory,
          ].join(" "),
        ),
      }))
      .sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
    byId = new Map(people.map((p) => [p.id, p]));
    for (const [code, name] of Object.entries(regions)) {
      const option = el("option", name);
      option.value = code;
      $("province").append(option);
    }
    for (const party of [
      ...new Set(people.map((p) => p.party_name).filter(Boolean)),
    ].sort()) {
      const option = el("option", party);
      option.value = party;
      $("party").append(option);
    }
    render();
  } catch (error) {
    showError(error.message);
  } finally {
    $("loading").hidden = true;
  }
}
start();
