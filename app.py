from flask import Flask, render_template, request, redirect, session, flash, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, timezone
import sqlite3
import math
import io
import base64
import qrcode
import init_db   # creates database.db and all tables automatically if they are missing
import invoice
import pricing
import reminders
import secrets
from PIL import Image, ImageOps
from itsdangerous import URLSafeSerializer, BadSignature
import os
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
app.secret_key = "change-this-secret-key"

# signed link so an invoice can be opened from WhatsApp without login (link cannot be guessed)
share_serializer = URLSafeSerializer(app.secret_key, salt="invoice-share")
app.jinja_env.globals["share_token"] = share_serializer.dumps

app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024   # max total upload size 25 MB

# vehicle sections shown on the home page: (value stored in database, label)
# e-bike rides: parking zones inside GIFT City
GIFT_ZONES = ["GIFT City - Gate 1", "GIFT City - Metro Station", "GIFT City - Tower 1 (DTA)", "GIFT City - Riverfront", "GIFT City - Mall"]
OUT_OF_ZONE = "Outside GIFT City zone"
OUT_OF_ZONE_FEE = 200          # Rs. fee if the rider ends the ride outside the zones (bike has to be collected)
ALL_ZONES = GIFT_ZONES + [OUT_OF_ZONE]
app.jinja_env.globals["OUT_OF_ZONE"] = OUT_OF_ZONE
FUEL_LEVELS = ["Empty", "1/4", "1/2", "3/4", "Full"]            # stored as 0 to 4
FUEL_QUARTER_CHARGE = {"Car": 300, "Scooter": 40, "Bike": 60, "E-bike": 0}   # Rs. for every 1/4 tank missing at return
CATEGORIES = [("Car", "🚗 Cars"), ("Scooter", "🛵 Scooters"), ("Bike", "🏍️ Bikes"), ("E-bike", "⚡ E-bikes")]

# ---------- SETTINGS (change these) ----------
UPI_ID = "yourname@upi"        # your real UPI id, money will come here
PAYEE_NAME = "DriveEasy"
PAY_WINDOW_MIN = 15            # unpaid booking auto-cancels after this many minutes
GMAIL_USER = ""                # your gmail e.g. "myrental@gmail.com" (leave empty = email off)
GMAIL_APP_PASSWORD = ""        # 16 letter Google App Password (NOT your normal password)
CHECK_MINUTES = 5              # how often the background checker runs
CAR_IMG_DIR = "static/images/cars"   # uploaded car photos are saved here
PRIVATE_DIR = "uploads/inspections"   # pickup/return photos + licence (NOT public, opened only through /media)
BASE_URL = ""                  # for phone QR scan, put your laptop IP e.g. "http://192.168.1.5:5000"


@app.route("/")
def home():
    # optional date search: /?start=2026-10-05T10:00&end=2026-10-07T10:00
    search_start = request.args.get("start", "")
    search_end = request.args.get("end", "")
    cat = request.args.get("cat", "")
    if cat not in [c[0] for c in CATEGORIES]:
        cat = ""
    start_db, end_db = "9999-12-31 00:00", "0000-01-01 00:00"   # these two values match nothing (= no search)
    searching = False
    if search_start and search_end:
        try:
            s = datetime.strptime(search_start, "%Y-%m-%dT%H:%M")
            e = datetime.strptime(search_end, "%Y-%m-%dT%H:%M")
            if e > s:
                start_db, end_db = s.strftime("%Y-%m-%d %H:%M"), e.strftime("%Y-%m-%d %H:%M")
                searching = True
            else:
                flash("The return time should be after the pickup.!", "error")
        except ValueError:
            flash("Please select a valid date and time.", "error")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    cars = con.execute("""
        SELECT cars.*,
        (SELECT ROUND(AVG(rating), 1) FROM reviews WHERE reviews.car_id = cars.id) AS avg_rating,
        (SELECT COUNT(*) FROM reviews WHERE reviews.car_id = cars.id) AS review_count,
        -- booked_till: car is booked RIGHT NOW (or not returned yet)
        (SELECT MAX(end_datetime) FROM bookings
         WHERE bookings.car_id = cars.id
         AND (status = 'Active' OR (status IN ('Pending', 'Confirmed')
              AND start_datetime <= datetime('now', 'localtime')
              AND end_datetime >= datetime('now', 'localtime')))) AS booked_till,
        -- next booking in the future
        (SELECT start_datetime FROM bookings WHERE bookings.car_id = cars.id
         AND status IN ('Pending', 'Confirmed') AND start_datetime > datetime('now', 'localtime')
         ORDER BY start_datetime LIMIT 1) AS next_start,
        (SELECT end_datetime FROM bookings WHERE bookings.car_id = cars.id
         AND status IN ('Pending', 'Confirmed') AND start_datetime > datetime('now', 'localtime')
         ORDER BY start_datetime LIMIT 1) AS next_end,
        (SELECT COUNT(*) FROM rides WHERE rides.car_id = cars.id AND rides.status = 'Riding') AS riding,
        -- clash: booked during the dates the visitor searched for
        (SELECT COUNT(*) FROM bookings WHERE bookings.car_id = cars.id
         AND status IN ('Pending', 'Confirmed', 'Active')
         AND start_datetime < ? AND end_datetime > ?) AS clash
        FROM cars
        WHERE (? = '' OR cars.category = ?)
    """, (end_db, start_db, cat, cat)).fetchall()
    con.close()
    return render_template("home.html", cars=cars, now=datetime.now().strftime("%Y-%m-%d %H:%M"),
                           now_input=datetime.now().strftime("%Y-%m-%dT%H:%M"),
                           searching=searching, search_start=search_start, search_end=search_end,
                           cat=cat, categories=CATEGORIES)
# ---------- ALL VEHICLES ----------
@app.route("/vehicles")
def vehicles():
    search_start = request.args.get("start", "")
    search_end = request.args.get("end", "")
    cat = request.args.get("cat", "")

    if cat not in [c[0] for c in CATEGORIES]:
        cat = ""

    start_db = "9999-12-31 00:00"
    end_db = "0000-01-01 00:00"

    if search_start and search_end:
        try:
            s = datetime.strptime(search_start, "%Y-%m-%dT%H:%M")
            e = datetime.strptime(search_end, "%Y-%m-%dT%H:%M")

            if e > s:
                start_db = s.strftime("%Y-%m-%d %H:%M")
                end_db = e.strftime("%Y-%m-%d %H:%M")
            else:
                flash("The return time should be after the pickup.", "error")

        except ValueError:
            flash("Please select a valid date and time.", "error")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row

    cars = con.execute("""
        SELECT cars.*,

        (SELECT ROUND(AVG(rating), 1)
         FROM reviews
         WHERE reviews.car_id = cars.id) AS avg_rating,

        (SELECT COUNT(*)
         FROM reviews
         WHERE reviews.car_id = cars.id) AS review_count,

        (SELECT MAX(end_datetime)
         FROM bookings
         WHERE bookings.car_id = cars.id
         AND (
             status = 'Active'
             OR (
                 status IN ('Pending', 'Confirmed')
                 AND start_datetime <= datetime('now', 'localtime')
                 AND end_datetime >= datetime('now', 'localtime')
             )
         )) AS booked_till,

        (SELECT start_datetime
         FROM bookings
         WHERE bookings.car_id = cars.id
         AND status IN ('Pending', 'Confirmed')
         AND start_datetime > datetime('now', 'localtime')
         ORDER BY start_datetime
         LIMIT 1) AS next_start,

        (SELECT end_datetime
         FROM bookings
         WHERE bookings.car_id = cars.id
         AND status IN ('Pending', 'Confirmed')
         AND start_datetime > datetime('now', 'localtime')
         ORDER BY start_datetime
         LIMIT 1) AS next_end,

        (SELECT COUNT(*)
         FROM rides
         WHERE rides.car_id = cars.id
         AND rides.status = 'Riding') AS riding,

        (SELECT COUNT(*)
         FROM bookings
         WHERE bookings.car_id = cars.id
         AND status IN ('Pending', 'Confirmed', 'Active')
         AND start_datetime < ?
         AND end_datetime > ?) AS clash

        FROM cars
        WHERE (? = '' OR cars.category = ?)
        ORDER BY cars.id

    """, (end_db, start_db, cat, cat)).fetchall()

    con.close()

    return render_template(
        "vehicles.html",
        cars=cars,
        now=datetime.now().strftime("%Y-%m-%d %H:%M"),
        now_input=datetime.now().strftime("%Y-%m-%dT%H:%M"),
        search_start=search_start,
        search_end=search_end,
        cat=cat,
        categories=CATEGORIES
    )

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        phone = request.form["phone"].strip()
        password = request.form["password"]
        confirm = request.form["confirm"]

        if len(name) < 3:
            flash("Name at least 3 letters !", "error")
            return render_template("register.html")
        if not phone.isdigit() or len(phone) != 10:
            flash("Phone number 10 digit !", "error")
            return render_template("register.html")
        if len(password) < 6:
            flash("Password at least 6 characters !.", "error")
            return render_template("register.html")
        if password != confirm:
            flash("Both passwords Are Different!", "error")
            return render_template("register.html")

        con = sqlite3.connect("database.db")
        exists = con.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if exists:
            con.close()
            flash("This Email is Already Registered!. Please Login Now!", "error")
            return render_template("register.html")

        con.execute(
            "INSERT INTO users (name, email, phone, password) VALUES (?, ?, ?, ?)",
            (name, email, phone, generate_password_hash(password)),
        )
        con.commit()
        con.close()
        flash("Account Is Registered ! Now Login!", "success")
        return redirect("/login")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        con = sqlite3.connect("database.db")
        con.row_factory = sqlite3.Row
        user = con.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        con.close()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["name"] = user["name"]
            session["role"] = user["role"]
            flash("Welcome, " + user["name"] + "!", "success")
            if user["role"] == "admin":
                return redirect("/admin")
            return redirect("/dashboard")

        flash("Email Or Password are Wrong!.", "error")
        return render_template("login.html")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out successfully.", "success")
    return redirect("/")


@app.route("/car/<int:car_id>")
def car_detail(car_id):
    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    car = con.execute("SELECT * FROM cars WHERE id = ?", (car_id,)).fetchone()
    if car is None:
        con.close()
        flash("Car Is Not Available.", "error")
        return redirect("/")

    # all current + future booked slots of this car (no user info shown)
    slots = con.execute("""
        SELECT start_datetime, end_datetime, status FROM bookings
        WHERE car_id = ? AND status IN ('Pending', 'Confirmed', 'Active')
        AND (end_datetime >= datetime('now', 'localtime') OR status = 'Active')
        ORDER BY start_datetime
    """, (car_id,)).fetchall()
    reviews = con.execute("""
        SELECT reviews.*, users.name AS user_name FROM reviews
        JOIN users ON users.id = reviews.user_id
        WHERE reviews.car_id = ? ORDER BY reviews.id DESC LIMIT 10""", (car_id,)).fetchall()
    rating = con.execute("SELECT ROUND(AVG(rating), 1) AS avg, COUNT(*) AS n FROM reviews WHERE car_id = ?", (car_id,)).fetchone()
    riding = con.execute("SELECT COUNT(*) FROM rides WHERE car_id = ? AND status = 'Riding'", (car_id,)).fetchone()[0]
    con.close()

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    booked_till = None
    for s in slots:
        if s["start_datetime"] <= now and (now <= s["end_datetime"] or s["status"] == "Active"):
            booked_till = s["end_datetime"]

    return render_template("car.html", car=car, slots=slots, booked_till=booked_till, now=now,
                           reviews=reviews, rating=rating, riding=riding)


@app.route("/book/<int:car_id>", methods=["GET", "POST"])
def book(car_id):
    if "user_id" not in session:
        flash("Log in to continue with your booking.", "error")
        return redirect("/login")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    car = con.execute("SELECT * FROM cars WHERE id = ?", (car_id,)).fetchone()
    if car is None:
        con.close()
        flash("Car Is Not Availabel", "error")
        return redirect("/")

    if car["category"] == "E-bike":
        con.close()
        flash("E-Bike is not for booking. Just Click The Start Ride", "error")
        return redirect("/car/" + str(car_id))

    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M")

    if request.method == "POST":
        start_raw = request.form["start"]
        end_raw = request.form["end"]

        try:
            start = datetime.strptime(start_raw, "%Y-%m-%dT%H:%M")
            end = datetime.strptime(end_raw, "%Y-%m-%dT%H:%M")
        except ValueError:
            con.close()
            flash("Please select the date and time correctly.", "error")
            return render_template("book.html", car=car, now=now_str)

        error = None
        if start < datetime.now() - timedelta(minutes=5):
            error = "The pickup time cannot be in the past."
        elif end <= start:
            error = "The return time must be after the pickup time."
        elif (end - start) > timedelta(days=30):
            error = "Bookings are available for a maximum of 30 days."

        if error:
            con.close()
            flash(error, "error")
            return render_template("book.html", car=car, now=now_str, start=start_raw, end=end_raw)

        start_db = start.strftime("%Y-%m-%d %H:%M")
        end_db = end.strftime("%Y-%m-%d %H:%M")

        # DOUBLE BOOKING CHECK: any booking that overlaps with the chosen time
        clash = con.execute("""
            SELECT start_datetime, end_datetime FROM bookings
            WHERE car_id = ? AND status IN ('Pending', 'Confirmed', 'Active')
            AND start_datetime < ? AND end_datetime > ?
            ORDER BY end_datetime DESC LIMIT 1
        """, (car_id, end_db, start_db)).fetchone()

        if clash:
            con.close()
            flash("This Car " + clash["start_datetime"] + " to " + clash["end_datetime"] +
                  " is Booked. Select another Time.", "error")
            return render_template("book.html", car=car, now=now_str, start=start_raw, end=end_raw)

        # TRIP TYPE: Local (hourly/daily rules) or Outstation (outside the city, admin must approve)
        trip_type = request.form.get("trip_type", "Local")
        destination = request.form.get("destination", "").strip()[:60]
        km_limit = 0
        if trip_type == "Outstation":
            error = None
            if not car["outstation_rate"]:
                error = "This Vehical is Not Availabel For Outstation!"
            elif len(destination) < 3:
                error = "Write the Destination (where you will go)!"
            if error:
                con.close()
                flash(error, "error")
                return render_template("book.html", car=car, now=now_str, start=start_raw, end=end_raw,
                                       trip_type=trip_type, destination=destination)
            total, rate_note, out_days = pricing.calc_outstation(start, end, car["outstation_rate"])
            km_limit = (car["km_per_day"] or 0) * out_days
        else:
            trip_type, destination = "Local", None
            total, rate_note = pricing.calc_price(start, end, car["price_per_day"],
                                                  car["price_per_hour"] or 0, car["min_hours"] or 1)
        gross = total

        # COUPON (optional)
        coupon_code = request.form.get("coupon", "").strip().upper()
        discount = 0
        if coupon_code:
            cp = con.execute("""SELECT * FROM coupons WHERE code = ? AND active = 1
                                AND (expiry IS NULL OR expiry = '' OR expiry >= ?)""",
                             (coupon_code, datetime.now().strftime("%Y-%m-%d"))).fetchone()
            if cp is None:
                con.close()
                flash("Coupon code is invalid or has expired.", "error")
                return render_template("book.html", car=car, now=now_str, start=start_raw, end=end_raw, coupon=coupon_code)
            discount = total * cp["percent"] // 100
        total = total - discount

        if request.form.get("action") == "preview":   # "Check price" button: show the price, do not book yet
            con.close()
            return render_template("book.html", car=car, now=now_str, start=start_raw, end=end_raw, coupon=coupon_code,
                                   trip_type=trip_type, destination=destination,
                                   preview=dict(note=rate_note, gross=gross, discount=discount, total=total,
                                                deposit=car["deposit"] or 0, km_limit=km_limit))

        cur = con.execute("""
            INSERT INTO bookings (user_id, car_id, start_datetime, end_datetime, total_price, status, payment_status,
                                  coupon_code, discount, rate_note, trip_type, destination, km_limit,
                                  extra_km_rate, deposit, approval_status)
            VALUES (?, ?, ?, ?, ?, 'Pending', 'Unpaid', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (session["user_id"], car_id, start_db, end_db, total, coupon_code or None, discount, rate_note,
              trip_type, destination, km_limit, car["extra_km_rate"] or 0, car["deposit"] or 0,
              "Waiting" if trip_type == "Outstation" else "None"))
        con.commit()
        new_id = cur.lastrowid
        con.close()

        text = rate_note + " = Rs." + str(gross) + (", discount Rs." + str(discount) if discount else "")
        if trip_type == "Outstation":
            flash("Outstation request has been sent!  " + text + " You will be able to make the payment after the admin approves it (check the status in the dashboard). ", "success")
            return redirect("/dashboard")
        flash("Booking created successfully!" + text + ". Pay: Rs." + str(total) +
              (" + refundable deposit Rs." + str(car["deposit"]) if car["deposit"] else ""), "success")
        return redirect("/pay/" + str(new_id))

    con.close()
    return render_template("book.html", car=car, now=now_str,
                           start=request.args.get("start"), end=request.args.get("end"))


@app.route("/cancel/<int:booking_id>", methods=["GET", "POST"])
def cancel(booking_id):
    if "user_id" not in session:
        return redirect("/login")
    if request.method == "GET":
        return render_template("confirm.html", title="Cancel booking", message="Are you sure you want to cancel this booking?", button="Yes, cancel booking", action_url=request.path, back="/dashboard")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    booking = con.execute(
        "SELECT * FROM bookings WHERE id = ? AND user_id = ?", (booking_id, session["user_id"])
    ).fetchone()

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if booking is None:
        flash("Booking not found.", "error")
    elif booking["status"] not in ("Pending", "Confirmed"):
        flash("This booking cannot be cancelled.", "error")
    elif booking["start_datetime"] <= now:
        flash("The rental has already started, so it cannot be cancelled.", "error")
    else:
        con.execute("UPDATE bookings SET status = 'Cancelled' WHERE id = ?", (booking_id,))
        con.commit()
        flash("Booking #" + str(booking_id) + " has been cancelled.", "success")

    con.close()
    return redirect("/dashboard")


@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect("/login")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    bookings = con.execute("""
        SELECT bookings.*, cars.name AS car_name, cars.brand AS car_brand, cars.number_plate, cars.qr_token,
               (SELECT COUNT(*) FROM reviews WHERE reviews.booking_id = bookings.id) AS reviewed
        FROM bookings JOIN cars ON cars.id = bookings.car_id
        WHERE bookings.user_id = ?
        ORDER BY bookings.id DESC
    """, (session["user_id"],)).fetchall()
    alerts = con.execute(
        "SELECT * FROM notifications WHERE user_id = ? AND is_read = 0 ORDER BY id DESC", (session["user_id"],)
    ).fetchall()
    ride_rows = con.execute("""
        SELECT rides.*, cars.name AS car_name, cars.brand AS car_brand, cars.price_per_hour, cars.price_per_day
        FROM rides JOIN cars ON cars.id = rides.car_id
        WHERE rides.user_id = ? ORDER BY rides.id DESC LIMIT 15""", (session["user_id"],)).fetchall()
    con.close()

    total = len(bookings)
    active = len([b for b in bookings if b["status"] in ("Pending", "Confirmed", "Active")])
    spent = sum(b["total_price"] for b in bookings if b["payment_status"] == "Paid")

    now = datetime.now()
    rows = []
    for b in bookings:
        row = dict(b)
        row["countdown"], row["countdown_class"] = "", ""
        if b["status"] in ("Active", "Confirmed"):
            row["countdown"], row["countdown_class"] = reminders.countdown(b["start_datetime"], b["end_datetime"], now)
        rows.append(row)

    ride_list = []
    for r in ride_rows:
        row = dict(r)
        if r["status"] == "Riding":   # ride still running: show time and amount so far
            started = datetime.strptime(r["start_time"], "%Y-%m-%d %H:%M:%S")
            row["minutes"] = max(1, math.ceil((now - started).total_seconds() / 60))
            row["ride_amount"], row["note"] = pricing.calc_ride(row["minutes"], r["price_per_hour"], r["price_per_day"])
        ride_list.append(row)

    return render_template("dashboard.html", bookings=rows, rides=ride_list,
                           auto_refresh=any(r["countdown"] for r in rows) or any(r["status"] == "Riding" for r in ride_list),
                           total=total, active=active, spent=spent, alerts=alerts,
                           base=BASE_URL or request.host_url.rstrip("/"))



@app.errorhandler(413)
def file_too_large(e):
    flash("Files are too large. The total upload limit is 25 MB.", "error")
    return redirect(request.referrer or "/admin/cars")


# ---------- AUTO CANCEL UNPAID BOOKINGS ----------
@app.before_request
def expire_unpaid_bookings():
    if request.endpoint == "static":
        return
    con = sqlite3.connect("database.db")
    con.execute("""
        UPDATE bookings SET status = 'Cancelled'
        WHERE status = 'Pending' AND payment_status = 'Unpaid' AND approval_status != 'Waiting'
        AND created_at <= datetime('now', ?)
    """, ("-" + str(PAY_WINDOW_MIN) + " minutes",))
    # outstation requests that admin did not answer within 24 hours are cancelled
    con.execute("""UPDATE bookings SET status = 'Cancelled', approval_status = 'Rejected'
                   WHERE status = 'Pending' AND approval_status = 'Waiting' AND created_at <= datetime('now', '-24 hours')""")
    con.commit()
    con.close()


# ---------- PAYMENT ----------
@app.route("/pay/<int:booking_id>", methods=["GET", "POST"])
def pay(booking_id):
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect("/login")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    booking = con.execute("""
        SELECT bookings.*, cars.name AS car_name, cars.brand AS car_brand
        FROM bookings JOIN cars ON cars.id = bookings.car_id
        WHERE bookings.id = ? AND bookings.user_id = ?
    """, (booking_id, session["user_id"])).fetchone()

    if booking is None:
        con.close()
        flash("Booking not found.", "error")
        return redirect("/dashboard")
    if booking["status"] != "Pending":
        con.close()
        flash("This booking does not require payment (status: " + booking["status"] + ").", "error")
        return redirect("/dashboard")
    if booking["approval_status"] == "Waiting":
        con.close()
        flash("Your outstation booking is awaiting admin approval. You can pay after it is approved.", "error")
        return redirect("/dashboard")
    amount = booking["total_price"] + (booking["deposit"] or 0)   # rental + refundable security deposit

    if request.method == "POST":
        utr = request.form["utr"].strip()
        if booking["payment_status"] != "Unpaid":
            flash("Payment is already under verification.", "error")
        elif not utr.isdigit() or len(utr) != 12:
            flash("UTR / Transaction ID must be exactly 12 digits.", "error")
        elif con.execute("SELECT id FROM bookings WHERE utr_number = ? AND id != ?", (utr, booking_id)).fetchone() or con.execute("SELECT id FROM rides WHERE utr_number = ?", (utr,)).fetchone():
            flash("This UTR number has already been used for another booking.", "error")
        else:
            con.execute("UPDATE bookings SET utr_number = ?, payment_status = 'Verifying' WHERE id = ?", (utr, booking_id))
            con.commit()
            con.close()
            flash("Payment submitted successfully! Your booking will be confirmed after admin verification.", "success")
            return redirect("/dashboard")
        con.close()
        return redirect("/pay/" + str(booking_id))

    # build UPI link + QR image (kept in memory, no file saved)
    upi_link = ("upi://pay?pa=" + UPI_ID + "&pn=" + PAYEE_NAME + "&am=" + str(amount) +
                ".00&cu=INR&tn=Booking" + str(booking_id))
    buf = io.BytesIO()
    qrcode.make(upi_link).save(buf, format="PNG")
    qr_data = base64.b64encode(buf.getvalue()).decode()

    created = datetime.strptime(booking["created_at"], "%Y-%m-%d %H:%M:%S")
    used = int((datetime.now(timezone.utc).replace(tzinfo=None) - created).total_seconds() // 60)
    minutes_left = max(0, PAY_WINDOW_MIN - used)
    con.close()
    return render_template("pay.html", booking=booking, upi_link=upi_link, qr_data=qr_data,
                           minutes_left=minutes_left, window=PAY_WINDOW_MIN, amount=amount)


# ---------- ADMIN: VERIFY PAYMENTS ----------
@app.route("/admin/payments")
def admin_payments():
    if session.get("role") != "admin":
        flash("This page is available to administrators only.", "error")
        return redirect("/login")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT bookings.*, cars.name AS car_name, cars.brand AS car_brand,
               users.name AS user_name, users.phone AS user_phone
        FROM bookings
        JOIN cars ON cars.id = bookings.car_id
        JOIN users ON users.id = bookings.user_id
        WHERE bookings.payment_status = 'Verifying'
        ORDER BY bookings.id
    """).fetchall()
    confirmed = con.execute("""
        SELECT bookings.*, cars.name AS car_name, cars.brand AS car_brand, cars.qr_token,
               users.name AS user_name, users.phone AS user_phone
        FROM bookings
        JOIN cars ON cars.id = bookings.car_id
        JOIN users ON users.id = bookings.user_id
        WHERE bookings.payment_status = 'Paid'
        ORDER BY bookings.id DESC LIMIT 10
    """).fetchall()
    ride_rows = con.execute("""
        SELECT rides.*, cars.name AS car_name, users.name AS user_name, users.phone AS user_phone
        FROM rides JOIN cars ON cars.id = rides.car_id JOIN users ON users.id = rides.user_id
        WHERE rides.payment_status = 'Verifying' ORDER BY rides.id""").fetchall()
    con.close()
    return render_template("admin_payments.html", rows=rows, confirmed=confirmed, ride_rows=ride_rows,
                           base=BASE_URL or request.host_url.rstrip("/"))


@app.route("/admin/payment/<int:booking_id>/<action>", methods=["GET", "POST"])
def admin_payment_action(booking_id, action):
    if session.get("role") != "admin":
        return redirect("/login")
    if request.method == "GET" and action != "reject":
        return redirect("/admin/payments")
    if request.method == "GET":
        return render_template("confirm.html", title="Reject payment", message="Reject this payment? The customer will get a fresh payment window.", button="Yes, reject", action_url=request.path, back="/admin/payments")

    con = sqlite3.connect("database.db")
    if action == "approve":
        con.execute("""UPDATE bookings SET payment_status = 'Paid', status = 'Confirmed'
                       WHERE id = ? AND payment_status = 'Verifying'""", (booking_id,))
        con.commit()
        info = ""
        row = invoice.load_booking(booking_id)
        if GMAIL_USER and row and row["payment_status"] == "Paid":
            pdf = invoice.make_invoice_pdf(row, BASE_URL or request.host_url)
            ok, info = invoice.send_invoice_email(row, pdf, GMAIL_USER, GMAIL_APP_PASSWORD)
            info = " " + info
        elif not GMAIL_USER:
            info = " (Email not sent: Gmail settings empty)"
        flash("Booking #" + str(booking_id) + " Confirmed." + info, "success")
    elif action == "reject":
        # user gets a fresh payment window to try again
        con.execute("""UPDATE bookings SET payment_status = 'Unpaid', utr_number = NULL,
                       created_at = CURRENT_TIMESTAMP
                       WHERE id = ? AND payment_status = 'Verifying'""", (booking_id,))
        flash("Booking #" + str(booking_id) + " payment was rejected.", "success")
    con.commit()
    con.close()
    return redirect("/admin/payments")



# ---------- INVOICE: DOWNLOAD PDF + EMAIL ----------
@app.route("/invoice/<int:booking_id>.pdf")
def invoice_pdf(booking_id):
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect("/login")

    row = invoice.load_booking(booking_id)
    if row is None or (row["user_id"] != session["user_id"] and session.get("role") != "admin"):
        flash("Booking not found.", "error")
        return redirect("/dashboard")
    if row["payment_status"] != "Paid":
        flash("Your invoice will be available after the payment is verified.", "error")
        return redirect("/dashboard")

    pdf = invoice.make_invoice_pdf(row, BASE_URL or request.host_url)
    return send_file(io.BytesIO(pdf), mimetype="application/pdf",
                     as_attachment=bool(request.args.get("download")),
                     download_name="invoice-" + str(booking_id).zfill(5) + ".pdf")


@app.route("/invoice/<int:booking_id>/email", methods=["POST"])
def invoice_email(booking_id):
    if "user_id" not in session:
        return redirect("/login")

    row = invoice.load_booking(booking_id)
    if row is None or (row["user_id"] != session["user_id"] and session.get("role") != "admin"):
        flash("Booking not found.", "error")
    elif row["payment_status"] != "Paid":
        flash("Your invoice will be available after the payment is verified.", "error")
    elif not GMAIL_USER:
        flash("Email settings are not configured (set GMAIL_USER in app.py).", "error")
    else:
        pdf = invoice.make_invoice_pdf(row, BASE_URL or request.host_url)
        ok, info = invoice.send_invoice_email(row, pdf, GMAIL_USER, GMAIL_APP_PASSWORD)
        flash(info, "success" if ok else "error")

    return redirect(request.referrer or "/dashboard")



# ---------- RETURN ALERTS ----------
@app.route("/notifications/read", methods=["POST"])
def notifications_read():
    if "user_id" in session:
        con = sqlite3.connect("database.db")
        con.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (session["user_id"],))
        con.commit()
        con.close()
    return redirect("/dashboard")


@app.route("/admin/bookings")
def admin_bookings():
    if session.get("role") != "admin":
        flash("This page is available to administrators only.", "error")
        return redirect("/login")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT bookings.*, cars.brand AS car_brand, cars.name AS car_name, cars.number_plate,
               cars.price_per_day, users.name AS user_name, users.phone AS user_phone,
               (SELECT COUNT(*) FROM inspections WHERE inspections.booking_id = bookings.id AND kind = 'Pickup') AS picked
        FROM bookings
        JOIN cars ON cars.id = bookings.car_id
        JOIN users ON users.id = bookings.user_id
        WHERE bookings.status IN ('Active', 'Confirmed')
        ORDER BY bookings.end_datetime
    """).fetchall()
    con.close()

    now = datetime.now()
    now_s = now.strftime("%Y-%m-%d %H:%M")
    late, active, upcoming = [], [], []
    for r in rows:
        b = dict(r)
        b["countdown"], b["countdown_class"] = reminders.countdown(b["start_datetime"], b["end_datetime"], now)
        if b["status"] == "Confirmed" and b["start_datetime"] > now_s:
            upcoming.append(b)
        elif b["end_datetime"] < now_s:
            b["fee_now"] = reminders.calc_late_fee(b["end_datetime"], now, b["price_per_day"])
            late.append(b)
        else:
            active.append(b)
    return render_template("admin_bookings.html", late=late, active=active, upcoming=upcoming)


# ---------- OUTSTATION APPROVALS ----------
@app.route("/admin/approvals")
def admin_approvals():
    if session.get("role") != "admin":
        flash("This page is available to administrators only.", "error")
        return redirect("/login")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT bookings.*, cars.brand AS car_brand, cars.name AS car_name, cars.number_plate,
               users.name AS user_name, users.phone AS user_phone
        FROM bookings
        JOIN cars ON cars.id = bookings.car_id
        JOIN users ON users.id = bookings.user_id
        WHERE bookings.status = 'Pending' AND bookings.approval_status = 'Waiting'
        ORDER BY bookings.id""").fetchall()
    con.close()
    return render_template("admin_approvals.html", rows=rows, window=PAY_WINDOW_MIN)


@app.route("/admin/approval/<int:booking_id>/<action>", methods=["GET", "POST"])
def admin_approval_action(booking_id, action):
    if session.get("role") != "admin":
        return redirect("/login")
    if request.method == "GET" and action != "reject":
        return redirect("/admin/approvals")
    if request.method == "GET":
        return render_template("confirm.html", title="Reject outstation request", message="Reject this outstation request? The customer will be told.", button="Yes, reject", action_url=request.path, back="/admin/approvals")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    b = con.execute("""
        SELECT bookings.*, users.name AS user_name, users.email AS user_email,
               cars.brand AS car_brand, cars.name AS car_name
        FROM bookings JOIN users ON users.id = bookings.user_id JOIN cars ON cars.id = bookings.car_id
        WHERE bookings.id = ? AND bookings.status = 'Pending' AND bookings.approval_status = 'Waiting'
    """, (booking_id,)).fetchone()

    if b is None:
        flash("Request not found or it has already been processed.", "error")
    elif action == "approve":
        # customer gets a fresh payment window starting now
        con.execute("UPDATE bookings SET approval_status = 'Approved', created_at = CURRENT_TIMESTAMP WHERE id = ?", (booking_id,))
        text = ("Your outstation trip to " + str(b["destination"]) + " (" + b["car_brand"] + " " + b["car_name"] +
                ") is APPROVED. Please pay within " + str(PAY_WINDOW_MIN) + " minutes from your dashboard (Pay now).")
        con.execute("INSERT INTO notifications (user_id, booking_id, message) VALUES (?, ?, ?)", (b["user_id"], booking_id, text))
        if GMAIL_USER:
            reminders.send_mail(b["user_email"], "Outstation trip approved - booking #" + str(booking_id),
                                "Hi " + b["user_name"] + ",\n\n" + text, GMAIL_USER, GMAIL_APP_PASSWORD)
        flash("Booking #" + str(booking_id) + " approved. Customer can pay now.", "success")
    elif action == "reject":
        con.execute("UPDATE bookings SET status = 'Cancelled', approval_status = 'Rejected' WHERE id = ?", (booking_id,))
        text = "Sorry, your outstation request to " + str(b["destination"]) + " (booking #" + str(booking_id) + ") was not approved."
        con.execute("INSERT INTO notifications (user_id, booking_id, message) VALUES (?, ?, ?)", (b["user_id"], booking_id, text))
        flash("Booking #" + str(booking_id) + " rejected.", "success")

    con.commit()
    con.close()
    return redirect("/admin/approvals")


# ---------- PICKUP CHECK / RETURN CHECK ----------
@app.route("/admin/booking/<int:booking_id>/pickup", methods=["GET", "POST"], defaults={"kind": "Pickup"})
@app.route("/admin/booking/<int:booking_id>/return", methods=["GET", "POST"], defaults={"kind": "Return"})
def admin_inspection(booking_id, kind):
    if session.get("role") != "admin":
        flash("This page is available to administrators only.", "error")
        return redirect("/login")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    b = con.execute("""
        SELECT bookings.*, cars.brand AS car_brand, cars.name AS car_name, cars.number_plate, cars.category,
               cars.price_per_day, users.name AS user_name, users.phone AS user_phone
        FROM bookings JOIN cars ON cars.id = bookings.car_id JOIN users ON users.id = bookings.user_id
        WHERE bookings.id = ?""", (booking_id,)).fetchone()

    if b is None or b["status"] not in ("Confirmed", "Active") or b["payment_status"] != "Paid":
        con.close()
        flash("This booking cannot be checked. It must be paid and have Confirmed or Active status.", "error")
        return redirect("/admin/bookings")

    pickup = con.execute("SELECT * FROM inspections WHERE booking_id = ? AND kind = 'Pickup'", (booking_id,)).fetchone()
    if kind == "Pickup" and pickup:
        con.close()
        flash("The pickup inspection has already been completed.", "error")
        return redirect("/admin/bookings")

    if request.method == "POST":
        f = request.form
        error = None
        try:
            fuel, odo = int(f["fuel"]), int(f["odometer"])
            helmets = int(f.get("helmets") or 0)
            damage = int(f.get("damage_charge") or 0) if kind == "Return" else 0
        except ValueError:
            fuel = odo = helmets = damage = 0
            error = "Fuel, odometer, helmet count, and damage charge must be valid numbers."

        if error is None and (fuel < 0 or fuel > 4 or odo < 0 or helmets < 0 or helmets > 4 or damage < 0):
            error = "Fuel must be 0-4, helmets must be 0-4, and other values cannot be negative."
        elif error is None and kind == "Return" and pickup and odo < pickup["odometer"]:
            error = "Return odometer cannot be lower than the pickup odometer (" + str(pickup["odometer"]) + ")."
        notes = f["notes"].strip()[:500]

        # collect photos (+ driving licence at pickup); check they are real images BEFORE saving anything
        files = [(p, "Photo") for p in request.files.getlist("photos") if p and p.filename]
        licence = request.files.get("licence")
        if kind == "Pickup":
            if error is None and not (licence and licence.filename):
                error = "A photo of the customer's driving licence is required."
            if licence and licence.filename:
                files.append((licence, "Licence"))
        if error is None and len(files) > 7:
            error = "You can upload up to 6 photos, plus the driving licence."
        images = []
        if error is None:
            for file, label in files:
                try:
                    img = Image.open(file.stream)
                    if img.format not in ("JPEG", "PNG", "WEBP"):
                        raise ValueError("bad format")
                    img = ImageOps.exif_transpose(img).convert("RGB")
                    img.thumbnail((1600, 1600))
                    images.append((img, label))
                except Exception:
                    error = "All uploaded files must be valid JPG, PNG, or WEBP images."
                    break

        if error:
            con.close()
            flash(error, "error")
            return render_template("inspection_form.html", b=b, kind=kind, pickup=pickup, fuel_levels=FUEL_LEVELS)

        os.makedirs(PRIVATE_DIR, exist_ok=True)
        cur = con.execute("INSERT INTO inspections (booking_id, kind, fuel_level, odometer, helmets, notes) VALUES (?, ?, ?, ?, ?, ?)",
                          (booking_id, kind, fuel, odo, helmets, notes))
        for img, label in images:
            name = "ins_" + secrets.token_hex(10) + ".jpg"
            img.save(os.path.join(PRIVATE_DIR, name), "JPEG", quality=85)
            con.execute("INSERT INTO inspection_photos (inspection_id, filename, label) VALUES (?, ?, ?)", (cur.lastrowid, name, label))

        if kind == "Pickup":
            con.execute("UPDATE bookings SET status = 'Active' WHERE id = ?", (booking_id,))
            con.commit()
            con.close()
            flash("Pickup inspection saved successfully. The vehicle has been handed over to the customer.", "success")
            return redirect("/admin/bookings")

        # ---- RETURN: work out every charge ----
        now = datetime.now()
        late_fee = reminders.calc_late_fee(b["end_datetime"], now, b["price_per_day"])
        km_charge = fuel_charge = 0
        if pickup:
            if (b["km_limit"] or 0) > 0:
                km_charge = max(0, (odo - pickup["odometer"]) - b["km_limit"]) * (b["extra_km_rate"] or 0)
            fuel_charge = max(0, pickup["fuel_level"] - fuel) * FUEL_QUARTER_CHARGE.get(b["category"], 0)
        deductions = late_fee + damage + fuel_charge + km_charge
        deposit = b["deposit"] or 0
        refund = max(0, deposit - deductions)
        balance = max(0, deductions - deposit)

        con.execute("""UPDATE bookings SET status = 'Completed', returned_at = ?, late_fee = ?, damage_charge = ?,
                       fuel_charge = ?, km_charge = ?, deposit_refund = ?, balance_due = ? WHERE id = ?""",
                    (now.strftime("%Y-%m-%d %H:%M"), late_fee, damage, fuel_charge, km_charge, refund, balance, booking_id))

        text = "Thank you! " + b["car_name"] + " returned (booking #" + str(booking_id) + ")."
        parts = []
        if late_fee: parts.append("late fee Rs." + str(late_fee))
        if damage: parts.append("damage Rs." + str(damage))
        if fuel_charge: parts.append("fuel Rs." + str(fuel_charge))
        if km_charge: parts.append("extra km Rs." + str(km_charge))
        if parts:
            text += " Charges: " + ", ".join(parts) + "."
        if deposit:
            text += " Deposit refund: Rs." + str(refund) + "."
        if balance:
            text += " Balance to pay: Rs." + str(balance) + "."
        con.execute("INSERT INTO notifications (user_id, booking_id, message) VALUES (?, ?, ?)", (b["user_id"], booking_id, text))
        con.commit()
        con.close()
        flash("Return inspection saved successfully. " + text, "success")
        return redirect("/admin/bookings")

    con.close()
    return render_template("inspection_form.html", b=b, kind=kind, pickup=pickup, fuel_levels=FUEL_LEVELS)


@app.route("/booking/<int:booking_id>/inspection")
def inspection_view(booking_id):
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect("/login")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    b = con.execute("""
        SELECT bookings.*, cars.brand AS car_brand, cars.name AS car_name, cars.number_plate, cars.category,
               users.name AS user_name
        FROM bookings JOIN cars ON cars.id = bookings.car_id JOIN users ON users.id = bookings.user_id
        WHERE bookings.id = ?""", (booking_id,)).fetchone()
    if b is None or (b["user_id"] != session["user_id"] and session.get("role") != "admin"):
        con.close()
        flash("Booking not found.", "error")
        return redirect("/dashboard")

    pickup = con.execute("SELECT * FROM inspections WHERE booking_id = ? AND kind = 'Pickup'", (booking_id,)).fetchone()
    ret = con.execute("SELECT * FROM inspections WHERE booking_id = ? AND kind = 'Return'", (booking_id,)).fetchone()
    photo_rows = con.execute("""
        SELECT inspection_photos.* FROM inspection_photos
        JOIN inspections ON inspections.id = inspection_photos.inspection_id
        WHERE inspections.booking_id = ? ORDER BY inspection_photos.id""", (booking_id,)).fetchall()
    con.close()

    photos = {}
    for p in photo_rows:
        photos.setdefault(p["inspection_id"], []).append(p)
    return render_template("inspection_view.html", b=b, pickup=pickup, ret=ret, photos=photos, fuel_levels=FUEL_LEVELS)


@app.route("/media/<filename>")
def media(filename):
    # inspection photos are private: only the booking owner and admin can open them
    if "user_id" not in session:
        return redirect("/login")
    con = sqlite3.connect("database.db")
    row = con.execute("""
        SELECT bookings.user_id FROM inspection_photos
        JOIN inspections ON inspections.id = inspection_photos.inspection_id
        JOIN bookings ON bookings.id = inspections.booking_id
        WHERE inspection_photos.filename = ?""", (filename,)).fetchone()
    con.close()
    if row is None or (row[0] != session["user_id"] and session.get("role") != "admin"):
        return "Not found", 404
    return send_file(os.path.join(PRIVATE_DIR, filename), mimetype="image/jpeg")


@app.route("/admin/run-checks", methods=["POST"])
def admin_run_checks():
    if session.get("role") != "admin":
        return redirect("/login")
    done = reminders.run_checks(GMAIL_USER, GMAIL_APP_PASSWORD)
    flash("Checks done: " + str(done["activated"]) + " started, " + str(done["reminders"]) + " reminders, " +
          str(done["due"]) + " return-time alerts, " + str(done["late"]) + " late alerts, " + str(done["rides"]) + " ride alerts.", "success")
    return redirect("/admin/bookings")


@app.route("/invoice/share/<token>.pdf")
def invoice_share(token):
    try:
        booking_id = share_serializer.loads(token)
    except BadSignature:
        return "Invalid or broken invoice link.", 404

    row = invoice.load_booking(booking_id)
    if row is None or row["payment_status"] != "Paid":
        return "Invoice not available.", 404
    pdf = invoice.make_invoice_pdf(row, BASE_URL or request.host_url)
    return send_file(io.BytesIO(pdf), mimetype="application/pdf",
                     download_name="invoice-" + str(booking_id).zfill(5) + ".pdf")


# ---------- SMART CAR PASSPORT ----------
@app.route("/passport/<token>")
def passport(token):
    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    car = con.execute("SELECT * FROM cars WHERE qr_token = ?", (token,)).fetchone()
    if car is None:
        con.close()
        flash("This QR code is invalid.", "error")
        return redirect("/")

    booked = con.execute("""
        SELECT MAX(end_datetime) AS till FROM bookings
        WHERE car_id = ? AND (status = 'Active' OR (status IN ('Pending', 'Confirmed')
        AND start_datetime <= datetime('now', 'localtime')
        AND end_datetime >= datetime('now', 'localtime')))
    """, (car["id"],)).fetchone()
    riding = con.execute("SELECT COUNT(*) FROM rides WHERE car_id = ? AND status = 'Riding'", (car["id"],)).fetchone()[0]
    con.close()

    today = datetime.now().strftime("%Y-%m-%d")
    logged_in = "user_id" in session   # sensitive documents only for logged-in users
    return render_template("passport.html", car=car, booked_till=booked["till"], today=today, logged_in=logged_in,
                           now=datetime.now().strftime("%Y-%m-%d %H:%M"), riding=riding)


@app.route("/qr/car/<token>.png")
def car_qr(token):
    con = sqlite3.connect("database.db")
    car = con.execute("SELECT id FROM cars WHERE qr_token = ?", (token,)).fetchone()
    con.close()
    if car is None:
        return "Not found", 404

    base = BASE_URL or request.host_url.rstrip("/")
    buf = io.BytesIO()
    qrcode.make(base + "/passport/" + token).save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", as_attachment=bool(request.args.get("download")),
                     download_name="car-passport-" + token + ".png")




# ---------- ADMIN DASHBOARD ----------
@app.route("/admin")
def admin_home():
    if session.get("role") != "admin":
        flash("This page is available to administrators only.", "error")
        return redirect("/login")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    stats = {}
    stats["users"] = con.execute("SELECT COUNT(*) FROM users WHERE role = 'user'").fetchone()[0]
    stats["cars"] = con.execute("SELECT COUNT(*) FROM cars").fetchone()[0]
    stats["bookings"] = con.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
    stats["out"] = con.execute("SELECT COUNT(*) FROM bookings WHERE status = 'Active'").fetchone()[0]
    stats["verify"] = con.execute("SELECT COUNT(*) FROM bookings WHERE payment_status = 'Verifying'").fetchone()[0]
    stats["approvals"] = con.execute("SELECT COUNT(*) FROM bookings WHERE status = 'Pending' AND approval_status = 'Waiting'").fetchone()[0]
    stats["late"] = con.execute("""SELECT COUNT(*) FROM bookings WHERE status = 'Active'
                                   AND end_datetime < datetime('now', 'localtime')""").fetchone()[0]
    stats["earning"] = con.execute("""
        SELECT COALESCE(SUM(CASE WHEN payment_status = 'Paid' THEN total_price END), 0)
             + COALESCE(SUM(CASE WHEN status = 'Completed' THEN late_fee + damage_charge + fuel_charge + km_charge END), 0)
        FROM bookings""").fetchone()[0]
    stats["rides"] = con.execute("SELECT COUNT(*) FROM rides").fetchone()[0]
    stats["earning"] += con.execute("SELECT COALESCE(SUM(ride_amount + zone_fee), 0) FROM rides WHERE payment_status = 'Paid'").fetchone()[0]

    monthly = con.execute("""
        SELECT strftime('%Y-%m', created_at) AS m, SUM(total_price) AS s FROM bookings
        WHERE payment_status = 'Paid' GROUP BY m ORDER BY m DESC LIMIT 6""").fetchall()
    monthly = list(reversed(monthly))
    by_status = con.execute("SELECT status, COUNT(*) AS c FROM bookings GROUP BY status").fetchall()
    top_cars = con.execute("""
        SELECT cars.brand || ' ' || cars.name AS car, COUNT(bookings.id) AS c
        FROM cars LEFT JOIN bookings ON bookings.car_id = cars.id AND bookings.status != 'Cancelled'
        GROUP BY cars.id ORDER BY c DESC LIMIT 5""").fetchall()
    con.close()

    max_rev = max([m["s"] for m in monthly] or [1]) or 1
    revenue_bars = [dict(label=m["m"], value=m["s"], pct=round(m["s"] * 100 / max_rev)) for m in monthly]
    max_cnt = max([s["c"] for s in by_status] or [1]) or 1
    status_bars = [dict(label=s["status"], value=s["c"], pct=round(s["c"] * 100 / max_cnt)) for s in by_status]

    return render_template("admin_home.html", stats=stats, top_cars=top_cars,
                           revenue_bars=revenue_bars, status_bars=status_bars)


# ---------- ADMIN: CARS (list, add, edit, delete) ----------
@app.route("/admin/cars")
def admin_cars():
    if session.get("role") != "admin":
        flash("This page is available to administrators only.", "error")
        return redirect("/login")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    cars = con.execute("""
        SELECT cars.*, (SELECT COUNT(*) FROM bookings WHERE bookings.car_id = cars.id) AS booking_count
        FROM cars ORDER BY cars.id""").fetchall()
    con.close()
    return render_template("admin_cars.html", cars=cars)


CAR_TYPES = ["Hatchback", "Sedan", "SUV", "MUV", "Luxury", "Van", "Scooter", "Motorcycle", "E-bike"]
FUELS = ["Petrol", "Diesel", "CNG", "Electric", "Hybrid"]


@app.route("/admin/cars/add", methods=["GET", "POST"], defaults={"car_id": None})
@app.route("/admin/cars/<int:car_id>/edit", methods=["GET", "POST"])
def admin_car_form(car_id):
    if session.get("role") != "admin":
        flash("This page is available to administrators only.", "error")
        return redirect("/login")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    car = None
    if car_id is not None:
        car = con.execute("SELECT * FROM cars WHERE id = ?", (car_id,)).fetchone()
        if car is None:
            con.close()
            flash("Car not found.", "error")
            return redirect("/admin/cars")

    if request.method == "POST":
        f = request.form
        name, brand = f["name"].strip(), f["brand"].strip()
        ctype, fuel, category = f["type"], f["fuel"], f["category"]
        station = f.get("station", "") if category == "E-bike" else ""
        plate = f["number_plate"].strip().upper().replace(" ", "")
        ins, puc, service = f["insurance_expiry"], f["puc_expiry"], f["last_service"]

        error = None
        try:
            seats, price, km = int(f["seats"]), int(f["price_per_day"]), int(f["km_driven"] or 0)
            hour_price, min_hours = int(f.get("price_per_hour") or 0), int(f.get("min_hours") or 1)
            out_rate, km_day = int(f.get("outstation_rate") or 0), int(f.get("km_per_day") or 0)
            extra_km, deposit = int(f.get("extra_km_rate") or 0), int(f.get("deposit") or 0)
        except ValueError:
            seats = price = km = hour_price = min_hours = out_rate = km_day = extra_km = deposit = 0
            error = "Seats, price, and kilometres must be valid numbers."

        if error is None and (not name or not brand or not plate):
            error = "Name, brand, and number plate are required."
        elif error is None and (seats < 1 or seats > 12 or price <= 0 or km < 0):
            error = "Seats must be between 1 and 12, and price must be greater than 0."
        elif error is None and (ctype not in CAR_TYPES or fuel not in FUELS or category not in [c[0] for c in CATEGORIES]):
            error = "Please select a valid category, vehicle type, and fuel type."
        elif error is None and (hour_price < 0 or min_hours < 1 or min_hours > 24 or hour_price > price):
            error = "Hourly rate cannot exceed the daily rate (0 means hourly rental is disabled), and minimum hours must be between 1 and 24."
        elif error is None and category == "E-bike" and station not in ALL_ZONES:
            error = "Please select a parking zone for the e-bike."
        elif error is None and (out_rate < 0 or km_day < 0 or extra_km < 0 or deposit < 0 or (out_rate > 0 and km_day == 0)):
            error = "Outstation rate, extra-km rate, and deposit cannot be negative. Outstation rentals also require kilometres per day."
        elif error is None and con.execute("SELECT id FROM cars WHERE number_plate = ? AND id != ?", (plate, car_id or 0)).fetchone():
            error = "A car with this number plate already exists."

        # PHOTO (optional): must be a real JPG / PNG / WEBP image
        new_image = None
        photo = request.files.get("photo")
        if error is None and photo and photo.filename:
            try:
                img = Image.open(photo.stream)
                if img.format not in ("JPEG", "PNG", "WEBP"):
                    raise ValueError("bad format")
                img = ImageOps.exif_transpose(img).convert("RGB")   # fix phone rotation
                img.thumbnail((1200, 1200))                          # shrink big photos
                os.makedirs(CAR_IMG_DIR, exist_ok=True)
                new_image = "car_" + secrets.token_hex(8) + ".jpg"
                img.save(os.path.join(CAR_IMG_DIR, new_image), "JPEG", quality=85)
            except Exception:
                error = "Photo must be a valid JPG, PNG, or WEBP image."

        if error:
            con.close()
            flash(error, "error")
            return render_template("car_form.html", car=dict(f), editing=car_id is not None, types=CAR_TYPES, fuels=FUELS, categories=CATEGORIES, zones=ALL_ZONES)

        if car_id is None:
            con.execute("""INSERT INTO cars (name, brand, category, type, fuel, seats, price_per_day, price_per_hour,
                           min_hours, outstation_rate, km_per_day, extra_km_rate, deposit, number_plate, qr_token,
                           insurance_expiry, puc_expiry, last_service, km_driven, image)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (name, brand, category, ctype, fuel, seats, price, hour_price, min_hours, out_rate, km_day,
                         extra_km, deposit, plate, secrets.token_urlsafe(8), ins, puc, service, km, new_image or "default.jpg"))
            con.execute("UPDATE cars SET station = ? WHERE number_plate = ?", (station, plate))
            flash("Car added successfully. Download its QR code from the Cars list.", "success")
        else:
            con.execute("""UPDATE cars SET name=?, brand=?, category=?, type=?, fuel=?, seats=?, price_per_day=?,
                           price_per_hour=?, min_hours=?, outstation_rate=?, km_per_day=?, extra_km_rate=?, deposit=?,
                           number_plate=?, insurance_expiry=?, puc_expiry=?, last_service=?, km_driven=? WHERE id=?""",
                        (name, brand, category, ctype, fuel, seats, price, hour_price, min_hours, out_rate, km_day,
                         extra_km, deposit, plate, ins, puc, service, km, car_id))
            con.execute("UPDATE cars SET station = ? WHERE id = ?", (station, car_id))
            if new_image:
                con.execute("UPDATE cars SET image = ? WHERE id = ?", (new_image, car_id))
                if car["image"] != "default.jpg" and os.path.exists(os.path.join(CAR_IMG_DIR, car["image"])):
                    os.remove(os.path.join(CAR_IMG_DIR, car["image"]))   # remove the old photo
            flash("Car updated successfully.", "success")
        con.commit()
        con.close()
        return redirect("/admin/cars")

    con.close()
    return render_template("car_form.html", car=car, editing=car_id is not None, types=CAR_TYPES, fuels=FUELS, categories=CATEGORIES, zones=ALL_ZONES)


@app.route("/admin/cars/<int:car_id>/delete", methods=["GET", "POST"])
def admin_car_delete(car_id):
    if session.get("role") != "admin":
        return redirect("/login")
    if request.method == "GET":
        return render_template("confirm.html", title="Delete vehicle", message="Are you sure you want to delete this vehicle? This cannot be undone.", button="Yes, delete", action_url=request.path, back="/admin/cars")

    con = sqlite3.connect("database.db")
    used = con.execute("SELECT COUNT(*) FROM bookings WHERE car_id = ?", (car_id,)).fetchone()[0]
    if used > 0:
        flash("This car has booking history, so it cannot be deleted.", "error")
    else:
        row = con.execute("SELECT image FROM cars WHERE id = ?", (car_id,)).fetchone()
        con.execute("DELETE FROM cars WHERE id = ?", (car_id,))
        con.commit()
        if row and row[0] != "default.jpg" and os.path.exists(os.path.join(CAR_IMG_DIR, row[0])):
            os.remove(os.path.join(CAR_IMG_DIR, row[0]))
        flash("Car deleted successfully.", "success")
    con.close()
    return redirect("/admin/cars")


# ---------- ADMIN: USERS ----------
@app.route("/admin/users")
def admin_users():
    if session.get("role") != "admin":
        flash("This page is available to administrators only.", "error")
        return redirect("/login")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    users = con.execute("""
        SELECT users.*, COUNT(bookings.id) AS booking_count,
               COALESCE(SUM(CASE WHEN bookings.payment_status = 'Paid' THEN bookings.total_price END), 0) AS spent
        FROM users LEFT JOIN bookings ON bookings.user_id = users.id
        GROUP BY users.id ORDER BY users.id""").fetchall()
    con.close()
    return render_template("admin_users.html", users=users)


# ---------- ADMIN: COUPONS ----------
@app.route("/admin/coupons", methods=["GET", "POST"])
def admin_coupons():
    if session.get("role") != "admin":
        flash("This page is available to administrators only.", "error")
        return redirect("/login")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    if request.method == "POST":
        code = request.form["code"].strip().upper()
        expiry = request.form["expiry"]
        try:
            percent = int(request.form["percent"])
        except ValueError:
            percent = 0

        if not code.isalnum() or len(code) < 3 or len(code) > 15:
            flash("Code must contain 3 to 15 letters or numbers.", "error")
        elif percent < 1 or percent > 90:
            flash("Discount must be between 1% and 90%.", "error")
        elif con.execute("SELECT id FROM coupons WHERE code = ?", (code,)).fetchone():
            flash("This coupon code already exists.", "error")
        else:
            con.execute("INSERT INTO coupons (code, percent, expiry) VALUES (?, ?, ?)", (code, percent, expiry))
            con.commit()
            flash("Coupon " + code + " added successfully.", "success")

    coupons = con.execute("SELECT * FROM coupons ORDER BY id DESC").fetchall()
    con.close()
    return render_template("admin_coupons.html", coupons=coupons, today=datetime.now().strftime("%Y-%m-%d"))


@app.route("/admin/coupons/<int:coupon_id>/toggle", methods=["POST"])
def admin_coupon_toggle(coupon_id):
    if session.get("role") != "admin":
        return redirect("/login")
    con = sqlite3.connect("database.db")
    con.execute("UPDATE coupons SET active = 1 - active WHERE id = ?", (coupon_id,))
    con.commit()
    con.close()
    return redirect("/admin/coupons")


# ---------- REVIEWS ----------
@app.route("/review/<int:booking_id>", methods=["GET", "POST"])
def review(booking_id):
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect("/login")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    b = con.execute("""
        SELECT bookings.*, cars.brand AS car_brand, cars.name AS car_name FROM bookings
        JOIN cars ON cars.id = bookings.car_id
        WHERE bookings.id = ? AND bookings.user_id = ?""", (booking_id, session["user_id"])).fetchone()

    if b is None or b["status"] != "Completed":
        con.close()
        flash("A review can only be submitted for your completed booking.", "error")
        return redirect("/dashboard")
    if con.execute("SELECT id FROM reviews WHERE booking_id = ?", (booking_id,)).fetchone():
        con.close()
        flash("You have already reviewed this booking.", "error")
        return redirect("/dashboard")

    if request.method == "POST":
        try:
            rating = int(request.form["rating"])
        except ValueError:
            rating = 0
        comment = request.form["comment"].strip()[:300]
        if rating < 1 or rating > 5:
            flash("Please select a rating from 1 to 5.", "error")
        else:
            con.execute("INSERT INTO reviews (booking_id, user_id, car_id, rating, comment) VALUES (?, ?, ?, ?, ?)",
                        (booking_id, session["user_id"], b["car_id"], rating, comment))
            con.commit()
            con.close()
            flash("Review badal aabhar!", "success")
            return redirect("/car/" + str(b["car_id"]))

    con.close()
    return render_template("review.html", b=b)


# ---------- E-BIKE RIDES (Start ride / End ride, GIFT City) ----------
@app.route("/ride/start/<int:car_id>", methods=["GET", "POST"])
def ride_start(car_id):
    if "user_id" not in session:
        flash("Please log in first to start a ride.", "error")
        return redirect("/login")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    bike = con.execute("SELECT * FROM cars WHERE id = ? AND category = 'E-bike'", (car_id,)).fetchone()
    if bike is None:
        con.close()
        flash("E-bike not found.", "error")
        return redirect("/?cat=E-bike")

    error = None
    if con.execute("SELECT id FROM rides WHERE car_id = ? AND status = 'Riding'", (car_id,)).fetchone():
        error = "This e-bike is currently in use by another rider."
    elif con.execute("SELECT id FROM rides WHERE user_id = ? AND status = 'Riding'", (session["user_id"],)).fetchone():
        error = "You already have an active ride. Please end it first."
    elif con.execute("SELECT id FROM rides WHERE user_id = ? AND status = 'Completed' AND payment_status != 'Paid'",
                     (session["user_id"],)).fetchone():
        error = "Payment for your previous ride is still pending. Please use Pay Now from your dashboard."
    elif not bike["station"] or bike["station"] == OUT_OF_ZONE:
        error = "This e-bike is currently unavailable."
    elif not bike["price_per_hour"]:
        error = "The hourly rate for this e-bike has not been configured."
    if error:
        con.close()
        flash(error, "error")
        return redirect("/car/" + str(car_id))

    if request.method == "POST":
        if not request.form.get("agree"):
            con.close()
            flash("Please accept the ride rules to continue.", "error")
            return redirect("/ride/start/" + str(car_id))
        con.execute("INSERT INTO rides (user_id, car_id, start_time, start_station) VALUES (?, ?, ?, ?)",
                    (session["user_id"], car_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), bike["station"]))
        con.commit()
        con.close()
        flash("Ride started successfully! When you return, use End Ride from your dashboard.", "success")
        return redirect("/dashboard")

    con.close()
    return render_template("ride_start.html", bike=bike, min_minutes=pricing.RIDE_MIN_MINUTES, out_fee=OUT_OF_ZONE_FEE)


@app.route("/ride/end/<int:ride_id>", methods=["GET", "POST"])
def ride_end(ride_id):
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect("/login")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    ride = con.execute("""
        SELECT rides.*, cars.name AS car_name, cars.brand AS car_brand, cars.price_per_hour, cars.price_per_day
        FROM rides JOIN cars ON cars.id = rides.car_id WHERE rides.id = ?""", (ride_id,)).fetchone()
    is_admin = session.get("role") == "admin"
    if ride is None or ride["status"] != "Riding" or (ride["user_id"] != session["user_id"] and not is_admin):
        con.close()
        flash("Ride not found or it has already ended.", "error")
        return redirect("/dashboard")

    now = datetime.now()
    started = datetime.strptime(ride["start_time"], "%Y-%m-%d %H:%M:%S")
    minutes = max(1, math.ceil((now - started).total_seconds() / 60))
    amount, note = pricing.calc_ride(minutes, ride["price_per_hour"], ride["price_per_day"])

    if request.method == "POST":
        zone = request.form.get("zone", "")
        if zone not in ALL_ZONES:
            con.close()
            flash("Please select a parking zone.", "error")
            return redirect("/ride/end/" + str(ride_id))
        fee = OUT_OF_ZONE_FEE if zone == OUT_OF_ZONE else 0
        con.execute("""UPDATE rides SET end_time = ?, minutes = ?, ride_amount = ?, zone_fee = ?, note = ?,
                       end_station = ?, status = 'Completed' WHERE id = ?""",
                    (now.strftime("%Y-%m-%d %H:%M:%S"), minutes, amount, fee, note, zone, ride_id))
        con.execute("UPDATE cars SET station = ? WHERE id = ?", (zone, ride["car_id"]))
        if ride["user_id"] != session["user_id"]:   # admin ended it for the rider
            con.execute("INSERT INTO notifications (user_id, booking_id, message) VALUES (?, NULL, ?)",
                        (ride["user_id"], "Admin ended your e-bike ride #" + str(ride_id) + ": " + str(minutes) +
                         " min, Rs." + str(amount + fee) + ". Please pay from your dashboard."))
        con.commit()
        con.close()
        flash("Ride ended: " + str(minutes) + " min = Rs." + str(amount + fee), "success")
        if ride["user_id"] != session["user_id"]:
            return redirect("/admin/rides")
        return redirect("/ride/pay/" + str(ride_id))

    con.close()
    return render_template("ride_end.html", ride=ride, minutes=minutes, amount=amount, note=note,
                           zones=GIFT_ZONES, out_zone=OUT_OF_ZONE, out_fee=OUT_OF_ZONE_FEE)


@app.route("/ride/pay/<int:ride_id>", methods=["GET", "POST"])
def ride_pay(ride_id):
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect("/login")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    ride = con.execute("""
        SELECT rides.*, cars.name AS car_name, cars.brand AS car_brand
        FROM rides JOIN cars ON cars.id = rides.car_id
        WHERE rides.id = ? AND rides.user_id = ?""", (ride_id, session["user_id"])).fetchone()
    if ride is None or ride["status"] != "Completed":
        con.close()
        flash("Ride not found or it is still active.", "error")
        return redirect("/dashboard")

    total = ride["ride_amount"] + ride["zone_fee"]

    if request.method == "POST":
        utr = request.form["utr"].strip()
        if ride["payment_status"] != "Unpaid":
            flash("Payment is already under verification or has already been paid.", "error")
        elif not utr.isdigit() or len(utr) != 12:
            flash("UTR / Transaction ID must be exactly 12 digits.", "error")
        elif con.execute("SELECT id FROM rides WHERE utr_number = ?", (utr,)).fetchone() or \
                con.execute("SELECT id FROM bookings WHERE utr_number = ?", (utr,)).fetchone():
            flash("This UTR number has already been used for another payment.", "error")
        else:
            con.execute("UPDATE rides SET utr_number = ?, payment_status = 'Verifying' WHERE id = ?", (utr, ride_id))
            con.commit()
            con.close()
            flash("Payment submitted successfully! The admin will verify it shortly.", "success")
            return redirect("/dashboard")
        con.close()
        return redirect("/ride/pay/" + str(ride_id))

    qr_data = None
    upi_link = ""
    if ride["payment_status"] == "Unpaid":
        upi_link = ("upi://pay?pa=" + UPI_ID + "&pn=" + PAYEE_NAME + "&am=" + str(total) + ".00&cu=INR&tn=Ride" + str(ride_id))
        buf = io.BytesIO()
        qrcode.make(upi_link).save(buf, format="PNG")
        qr_data = base64.b64encode(buf.getvalue()).decode()
    con.close()
    return render_template("ride_pay.html", ride=ride, total=total, qr_data=qr_data, upi_link=upi_link)


@app.route("/admin/ride-payment/<int:ride_id>/<action>", methods=["GET", "POST"])
def admin_ride_payment_action(ride_id, action):
    if session.get("role") != "admin":
        return redirect("/login")
    if request.method == "GET":
        if action != "reject":
            return redirect("/admin/payments")
        return render_template("confirm.html", title="Reject ride payment", message="Reject this ride payment? The rider can pay again.",
                               button="Yes, reject", action_url=request.path, back="/admin/payments")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    ride = con.execute("SELECT * FROM rides WHERE id = ? AND payment_status = 'Verifying'", (ride_id,)).fetchone()
    if ride is None:
        flash("Payment not found or it has already been processed.", "error")
    elif action == "approve":
        con.execute("UPDATE rides SET payment_status = 'Paid' WHERE id = ?", (ride_id,))
        con.execute("INSERT INTO notifications (user_id, booking_id, message) VALUES (?, NULL, ?)",
                    (ride["user_id"], "Payment received for e-bike ride #" + str(ride_id) + ". Thank you!"))
        flash("Ride #" + str(ride_id) + " payment approved.", "success")
    elif action == "reject":
        con.execute("UPDATE rides SET payment_status = 'Unpaid', utr_number = NULL WHERE id = ?", (ride_id,))
        con.execute("INSERT INTO notifications (user_id, booking_id, message) VALUES (?, NULL, ?)",
                    (ride["user_id"], "Payment for e-bike ride #" + str(ride_id) + " could not be verified. Please pay again."))
        flash("Ride #" + str(ride_id) + " payment rejected.", "success")
    con.commit()
    con.close()
    return redirect("/admin/payments")


@app.route("/admin/rides")
def admin_rides():
    if session.get("role") != "admin":
        flash("This page is available to administrators only.", "error")
        return redirect("/login")

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    base_sql = """
        SELECT rides.*, cars.brand AS car_brand, cars.name AS car_name, cars.number_plate,
               cars.price_per_hour, cars.price_per_day, users.name AS user_name, users.phone AS user_phone
        FROM rides JOIN cars ON cars.id = rides.car_id JOIN users ON users.id = rides.user_id """
    active_rows = con.execute(base_sql + "WHERE rides.status = 'Riding' ORDER BY rides.id").fetchall()
    recent = con.execute(base_sql + "WHERE rides.status = 'Completed' ORDER BY rides.id DESC LIMIT 15").fetchall()
    outside = con.execute("SELECT * FROM cars WHERE station = ?", (OUT_OF_ZONE,)).fetchall()
    con.close()

    now = datetime.now()
    active = []
    for r in active_rows:
        row = dict(r)
        row["minutes"] = max(1, math.ceil((now - datetime.strptime(r["start_time"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60))
        row["amount"], row["note"] = pricing.calc_ride(row["minutes"], r["price_per_hour"], r["price_per_day"])
        active.append(row)
    return render_template("admin_rides.html", active=active, recent=recent, outside=outside)


if __name__ == "__main__":
    # Flask debug mode starts two processes, so start the scheduler only in the real one
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        scheduler = BackgroundScheduler()
        scheduler.add_job(reminders.run_checks, "interval", minutes=CHECK_MINUTES,
                          args=[GMAIL_USER, GMAIL_APP_PASSWORD], next_run_time=datetime.now())
        scheduler.start()
        print("Return-alert scheduler started (every", CHECK_MINUTES, "minutes)")

    # For phone QR scan test: app.run(host="0.0.0.0", debug=True)
    app.run(debug=True)
