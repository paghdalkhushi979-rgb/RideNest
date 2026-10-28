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

