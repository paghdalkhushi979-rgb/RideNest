# Run ONE time:  python init_db.py
import sqlite3
import secrets
from werkzeug.security import generate_password_hash

con = sqlite3.connect("database.db")

con.executescript("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    phone TEXT NOT NULL,
    password TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    brand TEXT NOT NULL,
    type TEXT NOT NULL,
    fuel TEXT NOT NULL,
    seats INTEGER NOT NULL,
    price_per_day INTEGER NOT NULL,
    number_plate TEXT NOT NULL UNIQUE,
    qr_token TEXT NOT NULL UNIQUE,
    insurance_expiry TEXT,
    puc_expiry TEXT,
    last_service TEXT,
    km_driven INTEGER DEFAULT 0,
    image TEXT DEFAULT 'default.jpg',
    category TEXT NOT NULL DEFAULT 'Car',
    price_per_hour INTEGER DEFAULT 0,
    min_hours INTEGER DEFAULT 1,
    outstation_rate INTEGER DEFAULT 0,
    km_per_day INTEGER DEFAULT 0,
    extra_km_rate INTEGER DEFAULT 0,
    deposit INTEGER DEFAULT 0,
    station TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    car_id INTEGER NOT NULL,
    start_datetime TEXT NOT NULL,
    end_datetime TEXT NOT NULL,
    total_price INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending',
    payment_status TEXT NOT NULL DEFAULT 'Unpaid',
    utr_number TEXT,
    late_fee INTEGER DEFAULT 0,
    reminder_sent INTEGER DEFAULT 0,
    due_sent INTEGER DEFAULT 0,
    late_alert_sent INTEGER DEFAULT 0,
    returned_at TEXT,
    coupon_code TEXT,
    discount INTEGER DEFAULT 0,
    rate_note TEXT,
    trip_type TEXT DEFAULT 'Local',
    destination TEXT,
    km_limit INTEGER DEFAULT 0,
    extra_km_rate INTEGER DEFAULT 0,
    deposit INTEGER DEFAULT 0,
    approval_status TEXT DEFAULT 'None',
    damage_charge INTEGER DEFAULT 0,
    fuel_charge INTEGER DEFAULT 0,
    km_charge INTEGER DEFAULT 0,
    deposit_refund INTEGER DEFAULT 0,
    balance_due INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (car_id) REFERENCES cars(id)
);

CREATE TABLE IF NOT EXISTS coupons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    percent INTEGER NOT NULL,
    active INTEGER DEFAULT 1,
    expiry TEXT
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    car_id INTEGER NOT NULL,
    rating INTEGER NOT NULL,
    comment TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    car_id INTEGER NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT,
    minutes INTEGER DEFAULT 0,
    ride_amount INTEGER DEFAULT 0,
    zone_fee INTEGER DEFAULT 0,
    note TEXT,
    start_station TEXT,
    end_station TEXT,
    status TEXT NOT NULL DEFAULT 'Riding',
    payment_status TEXT NOT NULL DEFAULT 'Unpaid',
    utr_number TEXT,
    remind_sent INTEGER DEFAULT 0,
    admin_alert_sent INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS inspections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    fuel_level INTEGER NOT NULL,
    odometer INTEGER NOT NULL,
    helmets INTEGER DEFAULT 0,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (booking_id, kind)
);

CREATE TABLE IF NOT EXISTS inspection_photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inspection_id INTEGER NOT NULL,
    filename TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    booking_id INTEGER,
    message TEXT NOT NULL,
    is_read INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
""")

# Admin user
con.execute(
    "INSERT OR IGNORE INTO users (name, email, phone, password, role) VALUES (?, ?, ?, ?, 'admin')",
    ("Admin", "admin@carrental.com", "9999999999", generate_password_hash("admin123")),
)

# ---- upgrade an old database (adds new columns, keeps all your data) ----
migrated = False
car_cols = [r[1] for r in con.execute("PRAGMA table_info(cars)")]
if "category" not in car_cols:
    con.execute("ALTER TABLE cars ADD COLUMN category TEXT NOT NULL DEFAULT 'Car'")
    migrated = True
if "price_per_hour" not in car_cols:
    con.execute("ALTER TABLE cars ADD COLUMN price_per_hour INTEGER DEFAULT 0")
if "min_hours" not in car_cols:
    con.execute("ALTER TABLE cars ADD COLUMN min_hours INTEGER DEFAULT 1")
migrated2 = "outstation_rate" not in car_cols
for col in ["outstation_rate", "km_per_day", "extra_km_rate", "deposit"]:
    if col not in car_cols:
        con.execute("ALTER TABLE cars ADD COLUMN " + col + " INTEGER DEFAULT 0")

if "station" not in car_cols:
    con.execute("ALTER TABLE cars ADD COLUMN station TEXT DEFAULT ''")

booking_cols = [r[1] for r in con.execute("PRAGMA table_info(bookings)")]
new_booking_cols = [("rate_note", "TEXT"), ("trip_type", "TEXT DEFAULT 'Local'"), ("destination", "TEXT"),
                    ("km_limit", "INTEGER DEFAULT 0"), ("extra_km_rate", "INTEGER DEFAULT 0"), ("deposit", "INTEGER DEFAULT 0"),
                    ("approval_status", "TEXT DEFAULT 'None'"), ("damage_charge", "INTEGER DEFAULT 0"),
                    ("fuel_charge", "INTEGER DEFAULT 0"), ("km_charge", "INTEGER DEFAULT 0"),
                    ("deposit_refund", "INTEGER DEFAULT 0"), ("balance_due", "INTEGER DEFAULT 0")]
for col, ddl in new_booking_cols:
    if col not in booking_cols:
        con.execute("ALTER TABLE bookings ADD COLUMN " + col + " " + ddl)

# Sample vehicles: name, brand, category, type, fuel, seats, price/day, price/hour, min hours, number plate,
#                  outstation rate/day, km per day, extra km rate, security deposit
samples = [
    ("Swift", "Maruti", "Car", "Hatchback", "Petrol", 5, 1500, 150, 4, "GJ01AB1234", 1900, 250, 8, 3000),
    ("Creta", "Hyundai", "Car", "SUV", "Diesel", 5, 3000, 300, 4, "GJ01CD5678", 3800, 250, 12, 5000),
    ("Innova Crysta", "Toyota", "Car", "MUV", "Diesel", 7, 4500, 450, 4, "GJ01EF9012", 5500, 300, 15, 5000),
    ("City", "Honda", "Car", "Sedan", "Petrol", 5, 2500, 250, 4, "GJ01GH3456", 3200, 250, 10, 4000),
    ("Nexon EV", "Tata", "Car", "SUV", "Electric", 5, 3200, 320, 4, "GJ01IJ7890", 4000, 250, 10, 5000),
    ("Thar", "Mahindra", "Car", "SUV", "Diesel", 4, 3500, 350, 4, "GJ01KL2468", 4300, 200, 14, 5000),
    ("Activa 6G", "Honda", "Scooter", "Scooter", "Petrol", 2, 500, 60, 2, "GJ01SC1001", 0, 0, 0, 1000),
    ("Jupiter", "TVS", "Scooter", "Scooter", "Petrol", 2, 450, 55, 2, "GJ01SC1002", 0, 0, 0, 1000),
    ("Bullet 350", "Royal Enfield", "Bike", "Motorcycle", "Petrol", 2, 1000, 120, 3, "GJ01BK2001", 1400, 200, 6, 2000),
    ("Splendor", "Hero", "Bike", "Motorcycle", "Petrol", 2, 400, 50, 2, "GJ01BK2002", 600, 200, 3, 1000),
    
    # Additional cars
    ("Tata Tiago", "Tata", "Car", "Hatchback", "Petrol", 5, 1200, 120, 4, "GJ01CA4001", 1500, 250, 8, 2500),
    ("Hyundai i20", "Hyundai", "Car", "Hatchback", "Petrol", 5, 1600, 160, 4, "GJ01CA4002", 2000, 250, 9, 3000),
    ("Maruti Dzire", "Maruti", "Car", "Sedan", "Petrol", 5, 1400, 140, 4, "GJ01CA4003", 1800, 250, 8, 2500),
    ("Hyundai Verna", "Hyundai", "Car", "Sedan", "Petrol", 5, 2500, 250, 4, "GJ01CA4004", 3200, 250, 10, 4000),
    ("Skoda Slavia", "Skoda", "Car", "Sedan", "Petrol", 5, 2800, 280, 4, "GJ01CA4005", 3500, 250, 11, 4000),
    ("Tata Tigor EV", "Tata", "Car", "Sedan", "Electric", 5, 2200, 220, 4, "GJ01CA4006", 2800, 250, 8, 3500),
    ("Tata Punch", "Tata", "Car", "SUV", "Petrol", 5, 1700, 170, 4, "GJ01CA4007", 2200, 250, 9, 3000),
    ("Hyundai Venue", "Hyundai", "Car", "SUV", "Petrol", 5, 2000, 200, 4, "GJ01CA4008", 2500, 250, 10, 3500),
    ("Maruti Brezza", "Maruti", "Car", "SUV", "Petrol", 5, 2200, 220, 4, "GJ01CA4009", 2800, 250, 10, 3500),
    ("Mahindra XUV700", "Mahindra", "Car", "SUV", "Diesel", 7, 4000, 400, 4, "GJ01CA4010", 5000, 250, 14, 5000),
    ("Toyota Fortuner", "Toyota", "Car", "SUV", "Diesel", 7, 6500, 650, 4, "GJ01CA4011", 8000, 300, 18, 7000),
    ("Maruti Ertiga", "Maruti", "Car", "MUV", "Petrol", 7, 2400, 240, 4, "GJ01CA4012", 3000, 250, 10, 3500),
    ("Kia Carens", "Kia", "Car", "MUV", "Diesel", 7, 3200, 320, 4, "GJ01CA4013", 4000, 250, 12, 4500),
    ("BMW 3 Series", "BMW", "Car", "Luxury Sedan", "Petrol", 5, 7500, 750, 4, "GJ01CA4014", 9000, 200, 20, 10000),
    ("Mercedes-Benz C-Class", "Mercedes-Benz", "Car", "Luxury Sedan", "Petrol", 5, 8000, 800, 4, "GJ01CA4015", 10000, 200, 20, 10000),

    # Additional scooters
    ("TVS NTorq 125", "TVS", "Scooter", "Scooter", "Petrol", 2, 550, 65, 2, "GJ01SC4016", 0, 0, 0, 1000),
    ("Suzuki Access 125", "Suzuki", "Scooter", "Scooter", "Petrol", 2, 500, 60, 2, "GJ01SC4017", 0, 0, 0, 1000),
    ("Yamaha Fascino", "Yamaha", "Scooter", "Scooter", "Petrol", 2, 500, 60, 2, "GJ01SC4018", 0, 0, 0, 1000),
    ("Bajaj Chetak", "Bajaj", "Scooter", "Scooter", "Electric", 2, 700, 80, 2, "GJ01SC4019", 0, 0, 0, 1500),
    ("Ola S1 Pro", "Ola Electric", "Scooter", "Scooter", "Electric", 2, 750, 85, 2, "GJ01SC4020", 0, 0, 0, 1500),

    # Additional bikes
    ("Honda Shine", "Honda", "Bike", "Motorcycle", "Petrol", 2, 450, 55, 2, "GJ01BK4021", 600, 200, 3, 1000),
    ("Bajaj Pulsar 150", "Bajaj", "Bike", "Motorcycle", "Petrol", 2, 550, 65, 2, "GJ01BK4022", 750, 200, 4, 1200),
    ("Yamaha FZ-S", "Yamaha", "Bike", "Motorcycle", "Petrol", 2, 600, 70, 2, "GJ01BK4023", 800, 200, 4, 1200),
    ("TVS Apache RTR 160", "TVS", "Bike", "Motorcycle", "Petrol", 2, 600, 70, 2, "GJ01BK4024", 800, 200, 4, 1200),
    ("Hero Xtreme 160R", "Hero", "Bike", "Motorcycle", "Petrol", 2, 600, 70, 2, "GJ01BK4025", 800, 200, 4, 1200),
    ("KTM Duke 200", "KTM", "Bike", "Motorcycle", "Petrol", 2, 900, 100, 2, "GJ01BK4026", 1200, 200, 5, 1800),
    ("Royal Enfield Hunter 350", "Royal Enfield", "Bike", "Motorcycle", "Petrol", 2, 1000, 120, 3, "GJ01BK4027", 1400, 200, 6, 2000),
    ("Classic 350", "Royal Enfield", "Bike", "Motorcycle", "Petrol", 2, 1100, 130, 3, "GJ01BK4028", 1500, 200, 6, 2000),
    ("Himalayan", "Royal Enfield", "Bike", "Motorcycle", "Petrol", 2, 1500, 170, 3, "GJ01BK4029", 2000, 200, 8, 2500),

    ("GIFT Ride E-bike 1", "GIFT Ride", "E-bike", "E-bike", "Electric", 1, 250, 30, 1, "GJ18EB3001", 0, 0, 0, 500),
    ("GIFT Ride E-bike 2", "GIFT Ride", "E-bike", "E-bike", "Electric", 1, 250, 30, 1, "GJ18EB3002", 0, 0, 0, 500),
]

count = con.execute("SELECT COUNT(*) FROM cars").fetchone()[0]
if count == 0 or migrated:
    for v in samples:
        if migrated:
            con.execute(
            """
            UPDATE cars
            SET price_per_hour = ?, min_hours = ?
            WHERE number_plate = ? AND price_per_hour = 0
            """,
            (v[7], v[8], v[9])
        )

        con.execute(
        """
        INSERT OR IGNORE INTO cars (
            name, brand, category, type, fuel, seats,
            price_per_day, price_per_hour, min_hours,
            number_plate, outstation_rate, km_per_day,
            extra_km_rate, deposit, qr_token,
            insurance_expiry, puc_expiry, last_service, km_driven
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            '2027-03-31', '2026-12-31', '2026-08-15', 24000
        )
        """,
        v + (secrets.token_urlsafe(8),)
    )

    

# e-bikes must be parked in a GIFT City zone
con.execute("UPDATE cars SET station = 'GIFT City - Metro Station' WHERE number_plate = 'GJ18EB3002' AND (station IS NULL OR station = '')")
con.execute("UPDATE cars SET station = 'GIFT City - Gate 1' WHERE category = 'E-bike' AND (station IS NULL OR station = '')")

if migrated2:   # old database: give the sample vehicles their outstation rates and deposits
    for v in samples:
        con.execute("UPDATE cars SET outstation_rate = ?, km_per_day = ?, extra_km_rate = ?, deposit = ? WHERE number_plate = ?",
                    (v[10], v[11], v[12], v[13], v[9]))

con.execute("INSERT OR IGNORE INTO coupons (code, percent) VALUES ('WELCOME10', 10)")
con.commit()
con.close()
print("Database ready! Admin login -> admin@carrental.com / admin123")
