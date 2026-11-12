from datetime import date, datetime, timedelta
from functools import wraps
import os

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask.sessions import SecureCookieSessionInterface
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
import services
from services import ValidationError


class AdaptiveSessionInterface(SecureCookieSessionInterface):
    """Use SameSite=None; Secure behind HTTPS proxies (including iframes),
    while keeping SameSite=Lax on plain local HTTP and test clients.
    """

    def get_cookie_secure(self, app):
        try:
            return bool(request.is_secure)
        except RuntimeError:
            return False

    def get_cookie_samesite(self, app):
        try:
            return "None" if request.is_secure else "Lax"
        except RuntimeError:
            return "Lax"


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    app.session_interface = AdaptiveSessionInterface()

    @app.template_filter("money")
    def format_money(value):
        try:
            amount = float(value or 0)
        except (TypeError, ValueError):
            amount = 0.0
        return f"{Config.CURRENCY_SYMBOL}{amount:,.2f}"

    @app.context_processor
    def inject_globals():
        return {
            "hotel_name": Config.HOTEL_NAME,
            "currency_symbol": Config.CURRENCY_SYMBOL,
            "today_iso": date.today().isoformat(),
            "current_admin": session.get("admin"),
        }

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("admin"):
                return redirect(url_for("login"))
            return view(*args, **kwargs)

        return wrapped

    def _safe_next_url(default_endpoint: str) -> str:
        next_target = request.form.get("next") or request.args.get("next") or ""
        if next_target.startswith("/") and not next_target.startswith("//"):
            return next_target
        return url_for(default_endpoint)

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    @app.route("/")
    def index():
        if session.get("admin"):
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("admin"):
            return redirect(url_for("dashboard"))

        error = None
        username = ""
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            admin = services.authenticate_admin(username, password)
            if admin:
                session.clear()
                session["admin"] = admin
                return redirect(url_for("dashboard"))
            error = "Invalid username or password."

        return render_template("login.html", error=error, username=username)

    @app.route("/logout", methods=["GET", "POST"])
    def logout():
        session.clear()
        flash("You have been logged out.", "info")
        return redirect(url_for("login"))

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    @app.route("/dashboard")
    @login_required
    def dashboard():
        data = services.get_dashboard_data()
        return render_template(
            "dashboard.html",
            stats=data["stats"],
            today_checkins=data["today_checkins"],
            today_checkouts=data["today_checkouts"],
        )

    # ------------------------------------------------------------------
    # Room Management
    # ------------------------------------------------------------------

    @app.route("/rooms")
    @login_required
    def rooms_list():
        search = request.args.get("q", "")
        room_type = request.args.get("room_type", "")
        availability = request.args.get("availability", "")
        rooms = services.list_rooms(search=search, room_type=room_type, availability=availability)
        return render_template(
            "rooms/list.html",
            rooms=rooms,
            search=search,
            room_type=room_type,
            availability=availability,
            room_types=services.ROOM_TYPES,
            room_statuses=services.ROOM_STATUSES,
        )

    @app.route("/rooms/new", methods=["GET", "POST"])
    @login_required
    def room_create():
        form_data = {
            "room_number": "",
            "room_type": "Single",
            "price_per_night": "",
            "availability_status": "Available",
            "description": "",
        }
        if request.method == "POST":
            form_data = {
                "room_number": request.form.get("room_number", ""),
                "room_type": request.form.get("room_type", ""),
                "price_per_night": request.form.get("price_per_night", ""),
                "availability_status": request.form.get("availability_status", "Available"),
                "description": request.form.get("description", ""),
            }
            try:
                services.create_room(**form_data)
                flash(f"Room {form_data['room_number'].strip()} added successfully.", "success")
                return redirect(url_for("rooms_list"))
            except ValidationError as exc:
                flash(str(exc), "error")

        return render_template(
            "rooms/form.html",
            mode="create",
            room=form_data,
            room_types=services.ROOM_TYPES,
            room_statuses=services.ROOM_STATUSES,
        )

    @app.route("/rooms/<int:room_id>/edit", methods=["GET", "POST"])
    @login_required
    def room_edit(room_id: int):
        existing = services.get_room(room_id)
        if not existing:
            flash("Room not found.", "error")
            return redirect(url_for("rooms_list"))

        form_data = dict(existing)
        if request.method == "POST":
            form_data = {
                "room_id": room_id,
                "room_number": request.form.get("room_number", ""),
                "room_type": request.form.get("room_type", ""),
                "price_per_night": request.form.get("price_per_night", ""),
                "availability_status": request.form.get("availability_status", ""),
                "description": request.form.get("description", ""),
            }
            try:
                services.update_room(
                    room_id=room_id,
                    room_number=form_data["room_number"],
                    room_type=form_data["room_type"],
                    price_per_night=form_data["price_per_night"],
                    availability_status=form_data["availability_status"],
                    description=form_data["description"],
                )
                flash(f"Room {form_data['room_number'].strip()} updated.", "success")
                return redirect(url_for("rooms_list"))
            except ValidationError as exc:
                flash(str(exc), "error")

        return render_template(
            "rooms/form.html",
            mode="edit",
            room=form_data,
            room_types=services.ROOM_TYPES,
            room_statuses=services.ROOM_STATUSES,
        )

    @app.route("/rooms/<int:room_id>/delete", methods=["POST"])
    @login_required
    def room_delete(room_id: int):
        try:
            services.delete_room(room_id)
            flash("Room deleted successfully.", "success")
        except ValidationError as exc:
            flash(str(exc), "error")
        return redirect(url_for("rooms_list"))

    # ------------------------------------------------------------------
    # Customer Management
    # ------------------------------------------------------------------

    @app.route("/customers")
    @login_required
    def customers_list():
        search = request.args.get("q", "")
        customers = services.list_customers(search=search)
        return render_template("customers/list.html", customers=customers, search=search)

    @app.route("/customers/new", methods=["GET", "POST"])
    @login_required
    def customer_create():
        form_data = {"full_name": "", "phone_number": "", "email": "", "address": ""}
        if request.method == "POST":
            form_data = {
                "full_name": request.form.get("full_name", ""),
                "phone_number": request.form.get("phone_number", ""),
                "email": request.form.get("email", ""),
                "address": request.form.get("address", ""),
            }
            try:
                customer_id = services.create_customer(**form_data)
                flash("Customer registered successfully.", "success")
                return redirect(url_for("customer_detail", customer_id=customer_id))
            except ValidationError as exc:
                flash(str(exc), "error")

        return render_template("customers/form.html", mode="create", customer=form_data)

    @app.route("/customers/<int:customer_id>")
    @login_required
    def customer_detail(customer_id: int):
        customer = services.get_customer(customer_id)
        if not customer:
            flash("Customer not found.", "error")
            return redirect(url_for("customers_list"))
        bookings = services.get_customer_bookings(customer_id)
        return render_template("customers/detail.html", customer=customer, bookings=bookings)

    @app.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
    @login_required
    def customer_edit(customer_id: int):
        existing = services.get_customer(customer_id)
        if not existing:
            flash("Customer not found.", "error")
            return redirect(url_for("customers_list"))

        form_data = dict(existing)
        if request.method == "POST":
            form_data = {
                "customer_id": customer_id,
                "full_name": request.form.get("full_name", ""),
                "phone_number": request.form.get("phone_number", ""),
                "email": request.form.get("email", ""),
                "address": request.form.get("address", ""),
            }
            try:
                services.update_customer(
                    customer_id=customer_id,
                    full_name=form_data["full_name"],
                    phone_number=form_data["phone_number"],
                    email=form_data["email"],
                    address=form_data["address"],
                )
                flash("Customer information updated.", "success")
                return redirect(url_for("customer_detail", customer_id=customer_id))
            except ValidationError as exc:
                flash(str(exc), "error")

        return render_template("customers/form.html", mode="edit", customer=form_data)

    # ------------------------------------------------------------------
    # Booking Management
    # ------------------------------------------------------------------

    @app.route("/bookings")
    @login_required
    def bookings_list():
        search = request.args.get("q", "")
        status = request.args.get("status", "")
        bookings = services.list_bookings(search=search, status=status)
        return render_template(
            "bookings/list.html",
            bookings=bookings,
            search=search,
            status=status,
            booking_statuses=services.BOOKING_STATUSES,
        )

    @app.route("/api/rooms/available")
    @login_required
    def api_available_rooms():
        check_in_str = request.args.get("check_in", "").strip()
        check_out_str = request.args.get("check_out", "").strip()
        exclude_raw = request.args.get("exclude_booking_id", "").strip()
        exclude_id = int(exclude_raw) if exclude_raw.isdigit() else None

        check_in_val = None
        check_out_val = None
        if check_in_str and check_out_str:
            try:
                check_in_val = datetime.strptime(check_in_str, "%Y-%m-%d").date()
                check_out_val = datetime.strptime(check_out_str, "%Y-%m-%d").date()
            except ValueError:
                return jsonify({"error": "Invalid date format."}), 400
            if check_out_val <= check_in_val:
                return jsonify({"rooms": [], "error": "Check-out date must be after check-in date."})

        rooms = services.get_available_rooms(
            check_in=check_in_val,
            check_out=check_out_val,
            exclude_booking_id=exclude_id,
        )
        return jsonify(
            {
                "rooms": [
                    {
                        "room_id": r["room_id"],
                        "room_number": r["room_number"],
                        "room_type": r["room_type"],
                        "price_per_night": float(r["price_per_night"]),
                    }
                    for r in rooms
                ]
            }
        )

    @app.route("/bookings/new", methods=["GET", "POST"])
    @login_required
    def booking_create():
        customers = services.list_customers()
        today = date.today()
        tomorrow = today + timedelta(days=1)

        form_data = {
            "customer_mode": "existing",
            "customer_id": request.args.get("customer_id", ""),
            "new_full_name": "",
            "new_phone_number": "",
            "new_email": "",
            "new_address": "",
            "room_id": "",
            "check_in_date": today.isoformat(),
            "check_out_date": tomorrow.isoformat(),
            "notes": "",
        }

        if request.method == "POST":
            form_data = {
                "customer_mode": request.form.get("customer_mode", "existing"),
                "customer_id": request.form.get("customer_id", ""),
                "new_full_name": request.form.get("new_full_name", ""),
                "new_phone_number": request.form.get("new_phone_number", ""),
                "new_email": request.form.get("new_email", ""),
                "new_address": request.form.get("new_address", ""),
                "room_id": request.form.get("room_id", ""),
                "check_in_date": request.form.get("check_in_date", ""),
                "check_out_date": request.form.get("check_out_date", ""),
                "notes": request.form.get("notes", ""),
            }
            try:
                booking_id = services.create_booking(
                    customer_mode=form_data["customer_mode"],
                    customer_id_raw=form_data["customer_id"],
                    new_customer_data={
                        "full_name": form_data["new_full_name"],
                        "phone_number": form_data["new_phone_number"],
                        "email": form_data["new_email"],
                        "address": form_data["new_address"],
                    },
                    room_id_raw=form_data["room_id"],
                    check_in_str=form_data["check_in_date"],
                    check_out_str=form_data["check_out_date"],
                    notes=form_data["notes"],
                )
                flash(f"Booking #{booking_id} created successfully.", "success")
                return redirect(url_for("booking_detail", booking_id=booking_id))
            except ValidationError as exc:
                flash(str(exc), "error")

        # Show available rooms (filtered by chosen dates if currently valid)
        ci_date = None
        co_date = None
        try:
            ci_date = datetime.strptime(form_data["check_in_date"], "%Y-%m-%d").date()
            co_date = datetime.strptime(form_data["check_out_date"], "%Y-%m-%d").date()
        except ValueError:
            pass
        available_rooms = services.get_available_rooms(check_in=ci_date, check_out=co_date)
        all_available_status_rooms = services.get_available_rooms()

        return render_template(
            "bookings/form.html",
            mode="create",
            form_data=form_data,
            customers=customers,
            rooms=all_available_status_rooms,
            date_filtered_room_ids={r["room_id"] for r in available_rooms},
        )

    @app.route("/bookings/<int:booking_id>")
    @login_required
    def booking_detail(booking_id: int):
        detail = services.get_booking(booking_id)
        if not detail:
            flash("Booking not found.", "error")
            return redirect(url_for("bookings_list"))
        return render_template(
            "bookings/detail.html",
            booking=detail["booking"],
            payments=detail["payments"],
        )

    @app.route("/bookings/<int:booking_id>/edit", methods=["GET", "POST"])
    @login_required
    def booking_edit(booking_id: int):
        detail = services.get_booking(booking_id)
        if not detail:
            flash("Booking not found.", "error")
            return redirect(url_for("bookings_list"))

        booking = detail["booking"]
        form_data = {
            "room_id": str(booking["room_id"]),
            "check_in_date": booking["check_in_date"].isoformat(),
            "check_out_date": booking["check_out_date"].isoformat(),
            "booking_status": booking["booking_status"],
            "notes": booking["notes"] or "",
        }

        if request.method == "POST":
            form_data = {
                "room_id": request.form.get("room_id", ""),
                "check_in_date": request.form.get("check_in_date", ""),
                "check_out_date": request.form.get("check_out_date", ""),
                "booking_status": request.form.get("booking_status", ""),
                "notes": request.form.get("notes", ""),
            }
            try:
                services.update_booking(
                    booking_id=booking_id,
                    room_id_raw=form_data["room_id"],
                    check_in_str=form_data["check_in_date"],
                    check_out_str=form_data["check_out_date"],
                    new_status=form_data["booking_status"],
                    notes=form_data["notes"],
                )
                flash(f"Booking #{booking_id} updated.", "success")
                return redirect(url_for("booking_detail", booking_id=booking_id))
            except ValidationError as exc:
                flash(str(exc), "error")

        # Include currently assigned room + any Available rooms
        available_rooms = services.get_available_rooms()
        room_map = {r["room_id"]: r for r in available_rooms}
        if booking["room_id"] not in room_map:
            current_room = services.get_room(booking["room_id"])
            if current_room:
                room_map[current_room["room_id"]] = current_room
        selectable_rooms = sorted(room_map.values(), key=lambda x: x["room_number"])

        return render_template(
            "bookings/form.html",
            mode="edit",
            booking=booking,
            form_data=form_data,
            rooms=selectable_rooms,
            booking_statuses=services.BOOKING_STATUSES,
        )

    @app.route("/bookings/<int:booking_id>/cancel", methods=["POST"])
    @login_required
    def booking_cancel(booking_id: int):
        try:
            services.cancel_booking(booking_id)
            flash(f"Booking #{booking_id} cancelled.", "success")
        except ValidationError as exc:
            flash(str(exc), "error")
        return redirect(_safe_next_url("bookings_list"))

    # ------------------------------------------------------------------
    # Check-in / Check-out
    # ------------------------------------------------------------------

    @app.route("/checkin-checkout")
    @login_required
    def checkin_checkout():
        search = request.args.get("q", "")
        board = services.get_checkin_checkout_board(search=search)
        return render_template(
            "checkin_checkout.html",
            pending_checkins=board["pending_checkins"],
            active_checkouts=board["active_checkouts"],
            recent_checkouts=board["recent_checkouts"],
            search=search,
            today=date.today(),
        )

    @app.route("/bookings/<int:booking_id>/checkin", methods=["POST"])
    @login_required
    def booking_checkin(booking_id: int):
        try:
            room_number = services.check_in_booking(booking_id)
            flash(f"Booking #{booking_id} checked in. Room {room_number} is now Occupied.", "success")
        except ValidationError as exc:
            flash(str(exc), "error")
        return redirect(_safe_next_url("checkin_checkout"))

    @app.route("/bookings/<int:booking_id>/checkout", methods=["POST"])
    @login_required
    def booking_checkout(booking_id: int):
        try:
            room_number = services.check_out_booking(booking_id)
            flash(
                f"Booking #{booking_id} checked out. Room {room_number} is now Available for future bookings.",
                "success",
            )
        except ValidationError as exc:
            flash(str(exc), "error")
        return redirect(_safe_next_url("checkin_checkout"))

    # ------------------------------------------------------------------
    # Payments
    # ------------------------------------------------------------------

    @app.route("/payments")
    @login_required
    def payments_list():
        search = request.args.get("q", "")
        method = request.args.get("method", "")
        status = request.args.get("status", "")
        data = services.list_payments(search=search, method=method, status=status)
        return render_template(
            "payments/list.html",
            payments=data["payments"],
            summary=data["summary"],
            search=search,
            method=method,
            status=status,
            payment_methods=services.PAYMENT_METHODS,
            payment_statuses=services.PAYMENT_STATUSES,
        )

    @app.route("/payments/new", methods=["GET", "POST"])
    @login_required
    def payment_create():
        bookings = services.get_bookings_for_payment()
        preselected_booking = request.args.get("booking_id", "")
        form_data = {
            "booking_id": preselected_booking,
            "amount": "",
            "payment_date": date.today().isoformat(),
            "payment_method": "Card",
            "payment_status": "Paid",
            "notes": "",
        }

        if preselected_booking.isdigit() and request.method == "GET":
            for b in bookings:
                if str(b["booking_id"]) == preselected_booking:
                    form_data["amount"] = f"{b['outstanding_balance']:.2f}"
                    break

        if request.method == "POST":
            form_data = {
                "booking_id": request.form.get("booking_id", ""),
                "amount": request.form.get("amount", ""),
                "payment_date": request.form.get("payment_date", ""),
                "payment_method": request.form.get("payment_method", ""),
                "payment_status": request.form.get("payment_status", ""),
                "notes": request.form.get("notes", ""),
            }
            try:
                payment_id, final_status = services.record_payment(
                    booking_id_raw=form_data["booking_id"],
                    amount_raw=form_data["amount"],
                    payment_date_str=form_data["payment_date"],
                    payment_method=form_data["payment_method"],
                    payment_status=form_data["payment_status"],
                    notes=form_data["notes"],
                )
                flash(f"Payment #{payment_id} recorded ({final_status}).", "success")
                return redirect(url_for("payment_detail", payment_id=payment_id))
            except ValidationError as exc:
                flash(str(exc), "error")

        return render_template(
            "payments/form.html",
            form_data=form_data,
            bookings=bookings,
            payment_methods=services.PAYMENT_METHODS,
            payment_statuses=services.PAYMENT_STATUSES,
        )

    @app.route("/payments/<int:payment_id>")
    @login_required
    def payment_detail(payment_id: int):
        payment = services.get_payment(payment_id)
        if not payment:
            flash("Payment not found.", "error")
            return redirect(url_for("payments_list"))
        return render_template("payments/detail.html", payment=payment)

    @app.route("/payments/<int:payment_id>/mark-paid", methods=["POST"])
    @login_required
    def payment_mark_paid(payment_id: int):
        try:
            new_status = services.mark_payment_paid(payment_id)
            flash(f"Payment #{payment_id} updated to {new_status}.", "success")
        except ValidationError as exc:
            flash(str(exc), "error")
        return redirect(_safe_next_url("payments_list"))

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    @app.route("/reports")
    @login_required
    def reports():
        data = services.get_reports_data()
        return render_template(
            "reports.html",
            summary=data["summary"],
            revenue_by_month=data["revenue_by_month"],
            room_type_stats=data["room_type_stats"],
            customer_history=data["customer_history"],
            room_occupancy=data["room_occupancy"],
        )

    # ------------------------------------------------------------------
    # Error Handlers
    # ------------------------------------------------------------------

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", code=404, message="The requested page could not be found."), 404

    @app.errorhandler(500)
    def server_error(error):
        return render_template("error.html", code=500, message="An unexpected server error occurred."), 500

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
