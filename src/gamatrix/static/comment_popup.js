(function () {
    document.body.addEventListener("htmx:beforeSwap", function (event) {
        const detail = event.detail;
        if (!detail.target.matches(".game-comment-cell")) { return; }
        if (detail.xhr.status !== 409 && detail.xhr.status !== 422) { return; }
        if (!(detail.xhr.getResponseHeader("Content-Type") || "").startsWith("text/html")) {
            return;
        }
        if (!detail.serverResponse.includes('class="game-comment-form"')) { return; }
        detail.shouldSwap = true;
    });

    function updateCommentOverflow(root) {
        const scope = root && root.querySelectorAll ? root : document;
        for (const details of scope.querySelectorAll(".game-comment-details")) {
            if (details.dataset.commentUpdated) {
                const when = new Date(details.dataset.commentUpdated);
                if (!isNaN(when)) {
                    const pad = value => String(value).padStart(2, "0");
                    const local = `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())} ` +
                        `${pad(when.getHours())}:${pad(when.getMinutes())}`;
                    const zone = new Intl.DateTimeFormat("en", { timeZoneName: "short" })
                        .formatToParts(when).find(part => part.type === "timeZoneName").value;
                    details.title = `${details.dataset.commentAuthor} edited ${local} ${zone}`;
                } else {
                    details.title = `${details.dataset.commentAuthor}: edit time unavailable`;
                }
            }
            const preview = details.querySelector(".game-comment-preview");
            if (!preview) { continue; }
            const overflowing = preview.scrollWidth > preview.clientWidth + 1;
            details.classList.toggle("is-overflowing", overflowing);
            if (!overflowing) { details.open = false; }
        }
    }

    document.body.addEventListener("commentChanged", function (event) {
        const cells = Array.from(document.querySelectorAll(".game-comment-cell"));
        const source = cells.find(cell =>
            cell.dataset.slug === event.detail.slug &&
            cell.dataset.releaseKey === event.detail.releaseKey
        );
        if (!source || !source.querySelector(".game-comment-display")) { return; }
        for (const cell of cells) {
            if (cell === source || cell.dataset.slug !== event.detail.slug) { continue; }
            if (cell.querySelector(".game-comment-form")) { continue; }
            cell.innerHTML = source.innerHTML;
            const edit = cell.querySelector(".comment-edit");
            if (edit) {
                edit.setAttribute("hx-get", "/games/" +
                    encodeURIComponent(cell.dataset.releaseKey) + "/comment/edit");
            }
            htmx.process(cell);
            updateCommentOverflow(cell);
        }
    });

    document.addEventListener("click", function (event) {
        if (!(event.target instanceof Element)) { return; }
        const summary = event.target.closest(".game-comment-details > summary");
        if (summary && !summary.parentElement.classList.contains("is-overflowing")) {
            event.preventDefault();
        }
        if (!event.target.closest(".game-comment-details")) {
            for (const details of document.querySelectorAll(".game-comment-details[open]")) {
                details.open = false;
            }
        }
    });

    document.addEventListener("DOMContentLoaded", function () {
        updateCommentOverflow(document);
    });
    document.body.addEventListener("htmx:afterSwap", function (event) {
        updateCommentOverflow(event.target);
    });
    window.addEventListener("resize", function () {
        updateCommentOverflow(document);
    });
})();
