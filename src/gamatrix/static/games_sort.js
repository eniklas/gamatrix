// Client-side column sorting for the games results table.
//
// The full result set is already in the DOM, so sorting a column is just a
// reorder of existing <tr> rows — no server round-trip. Each row carries the
// sort keys as data-sort-<col> attributes; each sortable header carries
// data-sort-col and data-sort-type ("text" or "num"). The server still renders
// the initial order, so this is pure progressive enhancement.
//
// A single delegated listener on document survives HTMX swaps that replace the
// table when filters change, so there is nothing to re-bind after a swap.
(function () {
    "use strict";

    function cellValue(row, col, type) {
        const raw = row.getAttribute("data-sort-" + col) || "";
        if (type === "num") {
            const n = parseFloat(raw);
            return isNaN(n) ? 0 : n;
        }
        return raw.toLowerCase();
    }

    function nextDirection(th) {
        // First click on a column sorts ascending; clicking the active
        // ascending column flips to descending. Mirrors the old server logic.
        return th.getAttribute("aria-sort") === "ascending"
            ? "descending"
            : "ascending";
    }

    function updateIndicators(thead, activeTh, direction) {
        for (const th of thead.querySelectorAll("th.sortable")) {
            const arrow = th.querySelector(".sort-arrow");
            if (th === activeTh) {
                th.setAttribute("aria-sort", direction);
                if (arrow) {
                    arrow.textContent = direction === "ascending" ? "▲" : "▼";
                }
            } else {
                th.removeAttribute("aria-sort");
                if (arrow) {
                    arrow.textContent = "";
                }
            }
        }
    }

    function sortTable(table, th) {
        const col = th.getAttribute("data-sort-col");
        const type = th.getAttribute("data-sort-type") || "text";
        const direction = nextDirection(th);
        const factor = direction === "ascending" ? 1 : -1;

        const tbody = table.tBodies[0];
        if (!tbody) {
            return;
        }

        const rows = Array.prototype.slice.call(tbody.rows);
        rows.sort(function (a, b) {
            const av = cellValue(a, col, type);
            const bv = cellValue(b, col, type);
            if (av < bv) {
                return -1 * factor;
            }
            if (av > bv) {
                return 1 * factor;
            }
            return 0;
        });

        // Re-appending an existing node moves it, so this reorders in place.
        const frag = document.createDocumentFragment();
        for (const row of rows) {
            frag.appendChild(row);
        }
        tbody.appendChild(frag);

        updateIndicators(th.closest("thead"), th, direction);
    }

    document.addEventListener("click", function (event) {
        const th = event.target.closest("th.sortable");
        if (!th) {
            return;
        }
        const table = th.closest("table.results");
        if (!table) {
            return;
        }
        sortTable(table, th);
    });
})();
