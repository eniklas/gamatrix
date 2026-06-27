// "Select all / unselect all" master checkbox for grouped filter checkboxes.
//
// Any <fieldset> whose legend holds an <input class="toggle-all"> gets a
// tri-state master: checked when every member is on, empty when none are, and
// indeterminate (a dash) when only some are. Toggling the master flips every
// member box in that fieldset.
//
// The filter form auto-refreshes on `change` (HTMX hx-trigger="change"), but
// setting `.checked` in script does NOT fire `change`, so after a bulk toggle
// we dispatch one bubbling `change` from the fieldset — a single server
// round-trip rather than one per box.
//
// We listen in the CAPTURE phase (top-down) so we run before HTMX's listener on
// the form, which is an ancestor of the master. The master's own native change
// still carries the children's pre-toggle state, so we stop it there and let
// only the fresh post-toggle event reach HTMX; otherwise the form would fire one
// stale request and one correct one, and whichever lands last would win. A
// single delegated listener on document survives HTMX swaps, so there is
// nothing to re-bind.
(function () {
    "use strict";

    function members(fieldset) {
        return fieldset.querySelectorAll(
            'input[type="checkbox"]:not(.toggle-all)'
        );
    }

    function syncMaster(master) {
        const fieldset = master.closest("fieldset");
        if (!fieldset) {
            return;
        }
        const boxes = members(fieldset);
        let checked = 0;
        for (const box of boxes) {
            if (box.checked) {
                checked++;
            }
        }
        master.checked = boxes.length > 0 && checked === boxes.length;
        master.indeterminate = checked > 0 && checked < boxes.length;
    }

    document.addEventListener(
        "change",
        function (event) {
            const target = event.target;
            if (!(target instanceof HTMLInputElement)) {
                return;
            }
            if (target.classList.contains("toggle-all")) {
                const fieldset = target.closest("fieldset");
                if (!fieldset) {
                    return;
                }
                // Suppress the master's native change (stale: the boxes below
                // aren't flipped yet) before HTMX, on the form, can act on it.
                event.stopPropagation();
                for (const box of members(fieldset)) {
                    box.checked = target.checked;
                }
                target.indeterminate = false;
                // One fresh bubbling change so HTMX refetches the table once.
                fieldset.dispatchEvent(new Event("change", { bubbles: true }));
                return;
            }
            if (target.type === "checkbox") {
                const fieldset = target.closest("fieldset");
                const master = fieldset
                    ? fieldset.querySelector("input.toggle-all")
                    : null;
                if (master) {
                    syncMaster(master);
                }
            }
        },
        true
    );

    function initMasters() {
        // `indeterminate` can't be set in HTML, so seed each master on load.
        for (const master of document.querySelectorAll("input.toggle-all")) {
            syncMaster(master);
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initMasters, {
            once: true,
        });
    } else {
        initMasters();
    }
})();
