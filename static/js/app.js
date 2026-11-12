document.addEventListener("DOMContentLoaded", function () {
    // Confirmation prompt for destructive or critical actions
    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
        form.addEventListener("submit", function (event) {
            var message = form.getAttribute("data-confirm");
            if (message && !window.confirm(message)) {
                event.preventDefault();
            }
        });
    });

    // Booking form logic (customer mode toggle, date calculation, room availability refresh)
    var bookingForm = document.getElementById("booking-form");
    if (bookingForm) {
        var modeInputs = bookingForm.querySelectorAll('input[name="customer_mode"]');
        var existingSection = document.getElementById("existing-customer-section");
        var newSection = document.getElementById("new-customer-section");
        var existingSelect = document.getElementById("customer_id");
        var newInputs = newSection ? newSection.querySelectorAll("input, textarea") : [];

        function syncCustomerMode() {
            var selected = bookingForm.querySelector('input[name="customer_mode"]:checked');
            var mode = selected ? selected.value : "existing";
            if (existingSection && newSection) {
                var isNew = mode === "new";
                existingSection.style.display = isNew ? "none" : "block";
                newSection.style.display = isNew ? "block" : "none";
                if (existingSelect) {
                    existingSelect.required = !isNew;
                }
                newInputs.forEach(function (el) {
                    el.required = isNew;
                });
            }
        }

        modeInputs.forEach(function (radio) {
            radio.addEventListener("change", syncCustomerMode);
        });
        syncCustomerMode();

        var roomSelect = document.getElementById("room_id");
        var checkInInput = document.getElementById("check_in_date");
        var checkOutInput = document.getElementById("check_out_date");
        var nightsOut = document.getElementById("calc-nights");
        var rateOut = document.getElementById("calc-rate");
        var totalOut = document.getElementById("calc-total");
        var currency = bookingForm.getAttribute("data-currency") || "$";
        var formMode = bookingForm.getAttribute("data-mode") || "create";

        function updateBookingTotal() {
            if (!roomSelect || !checkInInput || !checkOutInput) return;
            var opt = roomSelect.options[roomSelect.selectedIndex];
            var price = opt ? parseFloat(opt.getAttribute("data-price") || "0") : 0;
            var ci = checkInInput.value ? new Date(checkInInput.value + "T00:00:00") : null;
            var co = checkOutInput.value ? new Date(checkOutInput.value + "T00:00:00") : null;

            var nights = 0;
            if (ci && co && co > ci) {
                nights = Math.round((co - ci) / (1000 * 60 * 60 * 24));
            }
            var total = nights > 0 && price > 0 ? nights * price : 0;

            if (nightsOut) nightsOut.textContent = nights > 0 ? String(nights) : "-";
            if (rateOut) rateOut.textContent = price > 0 ? currency + price.toFixed(2) : "-";
            if (totalOut) totalOut.textContent = total > 0 ? currency + total.toFixed(2) : "-";
        }

        function refreshAvailableRooms() {
            if (formMode !== "create" || !roomSelect || !checkInInput || !checkOutInput) return;
            var ci = checkInInput.value;
            var co = checkOutInput.value;
            if (!ci || !co || co <= ci) return;

            var currentVal = roomSelect.value;
            var url = "/api/rooms/available?check_in=" + encodeURIComponent(ci) + "&check_out=" + encodeURIComponent(co);
            fetch(url, { headers: { Accept: "application/json" } })
                .then(function (resp) {
                    return resp.json();
                })
                .then(function (data) {
                    if (!data || !Array.isArray(data.rooms)) return;
                    roomSelect.innerHTML = "";
                    var placeholder = document.createElement("option");
                    placeholder.value = "";
                    placeholder.textContent = data.rooms.length
                        ? "-- Select an available room --"
                        : "-- No rooms available for selected dates --";
                    roomSelect.appendChild(placeholder);

                    data.rooms.forEach(function (room) {
                        var opt = document.createElement("option");
                        opt.value = String(room.room_id);
                        opt.setAttribute("data-price", room.price_per_night.toFixed(2));
                        opt.textContent =
                            "Room " +
                            room.room_number +
                            " (" +
                            room.room_type +
                            ") - " +
                            currency +
                            room.price_per_night.toFixed(2) +
                            "/night";
                        if (String(room.room_id) === currentVal) {
                            opt.selected = true;
                        }
                        roomSelect.appendChild(opt);
                    });
                    updateBookingTotal();
                })
                .catch(function () {
                    updateBookingTotal();
                });
        }

        if (roomSelect) roomSelect.addEventListener("change", updateBookingTotal);
        if (checkInInput) {
            checkInInput.addEventListener("change", function () {
                updateBookingTotal();
                refreshAvailableRooms();
            });
        }
        if (checkOutInput) {
            checkOutInput.addEventListener("change", function () {
                updateBookingTotal();
                refreshAvailableRooms();
            });
        }
        updateBookingTotal();
    }

    // Payment form logic (booking summary + automatic status suggestion)
    var paymentForm = document.getElementById("payment-form");
    if (paymentForm) {
        var bookingSelect = document.getElementById("booking_id");
        var amountInput = document.getElementById("amount");
        var statusSelect = document.getElementById("payment_status");
        var totalEl = document.getElementById("pay-booking-total");
        var paidEl = document.getElementById("pay-booking-paid");
        var balanceEl = document.getElementById("pay-booking-balance");
        var currency = paymentForm.getAttribute("data-currency") || "$";

        function getSelectedBalance() {
            if (!bookingSelect) return 0;
            var opt = bookingSelect.options[bookingSelect.selectedIndex];
            if (!opt || !opt.value) return 0;
            return parseFloat(opt.getAttribute("data-balance") || "0");
        }

        function updatePaymentSummary(autofillAmount) {
            if (!bookingSelect) return;
            var opt = bookingSelect.options[bookingSelect.selectedIndex];
            if (!opt || !opt.value) {
                if (totalEl) totalEl.textContent = "-";
                if (paidEl) paidEl.textContent = "-";
                if (balanceEl) balanceEl.textContent = "-";
                return;
            }
            var total = parseFloat(opt.getAttribute("data-total") || "0");
            var paid = parseFloat(opt.getAttribute("data-paid") || "0");
            var balance = parseFloat(opt.getAttribute("data-balance") || "0");

            if (totalEl) totalEl.textContent = currency + total.toFixed(2);
            if (paidEl) paidEl.textContent = currency + paid.toFixed(2);
            if (balanceEl) balanceEl.textContent = currency + balance.toFixed(2);

            if (autofillAmount && amountInput && balance > 0) {
                amountInput.value = balance.toFixed(2);
            }
            syncPaymentStatus();
        }

        function syncPaymentStatus() {
            if (!amountInput || !statusSelect) return;
            if (statusSelect.value === "Pending") return;
            var balance = getSelectedBalance();
            var amount = parseFloat(amountInput.value || "0");
            if (balance > 0 && amount > 0) {
                statusSelect.value = amount < balance ? "Partially Paid" : "Paid";
            }
        }

        if (bookingSelect) {
            bookingSelect.addEventListener("change", function () {
                updatePaymentSummary(true);
            });
            updatePaymentSummary(false);
        }
        if (amountInput) {
            amountInput.addEventListener("input", syncPaymentStatus);
        }
    }
});
