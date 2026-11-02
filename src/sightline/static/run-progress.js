/* The run page, re-read when the run changes (roadmap item 36).
 *
 * Optional. The page is complete without it: everything is rendered on the server from the run
 * store, and reloading shows the latest. While a run is going this asks GET /api/runs/{id} every
 * few seconds and reloads the page when the answer differs from the one the page was rendered
 * from. It renders nothing itself, so the page and the script cannot disagree about a run.
 *
 * The comparison is on a key-sorted form of both: the copy embedded in the page is written with
 * sorted keys and the endpoint's answer is not, and comparing them as sent would reload forever.
 */
(function () {
  "use strict";

  var banner = document.querySelector("[data-run-poll]");
  var embedded = document.getElementById("run-progress-data");
  if (!banner || !embedded || !window.fetch || !window.JSON) {
    return;
  }

  function canonical(value) {
    if (Array.isArray(value)) {
      return "[" + value.map(canonical).join(",") + "]";
    }
    if (value && typeof value === "object") {
      return "{" + Object.keys(value).sort().map(function (key) {
        return JSON.stringify(key) + ":" + canonical(value[key]);
      }).join(",") + "}";
    }
    return JSON.stringify(value);
  }

  var url = banner.getAttribute("data-run-poll");
  var every = parseInt(banner.getAttribute("data-poll-ms"), 10) || 5000;
  var seen;
  try {
    seen = canonical(JSON.parse(embedded.textContent));
  } catch (error) {
    return;
  }
  var note = banner.querySelector(".poll-note");
  if (note) {
    note.hidden = false;
  }

  function tick() {
    fetch(url, { cache: "no-store", headers: { Accept: "application/json" } })
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (payload) {
        if (payload && canonical(payload) !== seen) {
          window.location.reload();
          return;
        }
        window.setTimeout(tick, every);
      })
      .catch(function () {
        window.setTimeout(tick, every);
      });
  }

  window.setTimeout(tick, every);
})();
