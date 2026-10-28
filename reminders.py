# Background checks: activates rentals, sends return reminders, adds late fees.
# app.py runs run_checks() every few minutes using APScheduler.
import math
import smtplib
import sqlite3
from datetime import datetime, timedelta
from email.message import EmailMessage

RIDE_REMIND_HOURS = 3       # e-bike ride still running after this many hours -> reminder to the rider
RIDE_ALERT_HOURS = 12       # ... and an alert to the admin
REMIND_HOURS = 2            # reminder this many hours before return time
GRACE_MINUTES = 30          # no late fee for the first 30 minutes
LATE_PERCENT_PER_HOUR = 10  # late fee = 10% of daily rate for every late hour
FMT = "%Y-%m-%d %H:%M"


def calc_late_fee(end_str, now, price_per_day):
    """Late fee in rupees for a booking that should have ended at end_str."""
    end = datetime.strptime(end_str, FMT)
    late_minutes = (now - end).total_seconds() / 60 - GRACE_MINUTES
    if late_minutes <= 0:
        return 0
    return math.ceil(late_minutes / 60) * int(price_per_day * LATE_PERCENT_PER_HOUR / 100)


def send_mail(to, subject, body, gmail_user, gmail_pw):
    if not gmail_user:
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "DriveEasy <" + gmail_user + ">"
    msg["To"] = to
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(gmail_user, gmail_pw)
            server.send_message(msg)
        return True
    except Exception as e:
        print("Email failed:", e)
        return False


def run_checks(gmail_user="", gmail_pw=""):
    now = datetime.now()
    now_s = now.strftime(FMT)
    soon_s = (now + timedelta(hours=REMIND_HOURS)).strftime(FMT)
    done = {"activated": 0, "reminders": 0, "due": 0, "late": 0, "rides": 0}

    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row

    # 1) Confirmed -> Active when rental time starts
    cur = con.execute("UPDATE bookings SET status = 'Active' WHERE status = 'Confirmed' AND start_datetime <= ?", (now_s,))
    done["activated"] = cur.rowcount

    base_sql = """
        SELECT bookings.*, cars.brand AS car_brand, cars.name AS car_name, cars.number_plate,
               cars.price_per_day, users.name AS user_name, users.email AS user_email
        FROM bookings
        JOIN cars ON cars.id = bookings.car_id
        JOIN users ON users.id = bookings.user_id
    """
    admins = [a["id"] for a in con.execute("SELECT id FROM users WHERE role = 'admin'").fetchall()]

    # 2) reminder: return time is within the next 2 hours
    rows = con.execute(base_sql + """ WHERE bookings.status = 'Active' AND bookings.reminder_sent = 0
                                      AND bookings.end_datetime > ? AND bookings.end_datetime <= ?""",
                       (now_s, soon_s)).fetchall()
    for b in rows:
        text = ("Reminder: please return " + b["car_brand"] + " " + b["car_name"] + " (" + b["number_plate"] +
                ") by " + b["end_datetime"] + ". After a " + str(GRACE_MINUTES) + " min grace time, late fee is " +
                str(LATE_PERCENT_PER_HOUR) + "% of the daily rate per hour.")
        con.execute("INSERT INTO notifications (user_id, booking_id, message) VALUES (?, ?, ?)", (b["user_id"], b["id"], text))
        send_mail(b["user_email"], "Return reminder - booking #" + str(b["id"]), "Hi " + b["user_name"] + ",\n\n" + text, gmail_user, gmail_pw)
        con.execute("UPDATE bookings SET reminder_sent = 1 WHERE id = ?", (b["id"],))
        done["reminders"] += 1

    # 3) return time has come
    rows = con.execute(base_sql + """ WHERE bookings.status = 'Active' AND bookings.due_sent = 0
                                      AND bookings.end_datetime <= ?""", (now_s,)).fetchall()
    for b in rows:
        text = ("Return time is over for " + b["car_brand"] + " " + b["car_name"] + " (" + b["number_plate"] +
                "). It was due at " + b["end_datetime"] + ". Please return the car now to avoid late fee.")
        con.execute("INSERT INTO notifications (user_id, booking_id, message) VALUES (?, ?, ?)", (b["user_id"], b["id"], text))
        send_mail(b["user_email"], "Return time is over - booking #" + str(b["id"]), "Hi " + b["user_name"] + ",\n\n" + text, gmail_user, gmail_pw)
        con.execute("UPDATE bookings SET due_sent = 1, reminder_sent = 1 WHERE id = ?", (b["id"],))
        done["due"] += 1

    # 4) late fee for cars not returned (updated on every run)
    rows = con.execute(base_sql + " WHERE bookings.status = 'Active' AND bookings.end_datetime <= ?", (now_s,)).fetchall()
    for b in rows:
        fee = calc_late_fee(b["end_datetime"], now, b["price_per_day"])
        if fee > 0:
            con.execute("UPDATE bookings SET late_fee = ? WHERE id = ?", (fee, b["id"]))
            if b["late_alert_sent"] == 0:
                text = ("LATE RETURN: booking #" + str(b["id"]) + " - " + b["user_name"] + " has not returned " +
                        b["car_brand"] + " " + b["car_name"] + " (" + b["number_plate"] + "). Due: " + b["end_datetime"])
                for admin_id in admins:
                    con.execute("INSERT INTO notifications (user_id, booking_id, message) VALUES (?, ?, ?)", (admin_id, b["id"], text))
                con.execute("UPDATE bookings SET late_alert_sent = 1 WHERE id = ?", (b["id"],))
                done["late"] += 1

    # 5) e-bike rides that are still running
    rides = con.execute("""
        SELECT rides.*, cars.name AS car_name, users.name AS user_name, users.email AS user_email
        FROM rides JOIN cars ON cars.id = rides.car_id JOIN users ON users.id = rides.user_id
        WHERE rides.status = 'Riding'""").fetchall()
    for ride in rides:
        hours = (now - datetime.strptime(ride["start_time"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
        if hours >= RIDE_REMIND_HOURS and ride["remind_sent"] == 0:
            text = ("Your e-bike ride on " + ride["car_name"] + " is running for " + str(int(hours)) +
                    " hours. Please end the ride from your dashboard to stop the charge.")
            con.execute("INSERT INTO notifications (user_id, booking_id, message) VALUES (?, NULL, ?)", (ride["user_id"], text))
            send_mail(ride["user_email"], "Your e-bike ride is still running", "Hi " + ride["user_name"] + ",\n\n" + text, gmail_user, gmail_pw)
            con.execute("UPDATE rides SET remind_sent = 1 WHERE id = ?", (ride["id"],))
            done["rides"] += 1
        if hours >= RIDE_ALERT_HOURS and ride["admin_alert_sent"] == 0:
            text = "LONG RIDE: " + ride["user_name"] + " has been riding " + ride["car_name"] + " for " + str(int(hours)) + " hours (ride #" + str(ride["id"]) + ")."
            for admin_id in admins:
                con.execute("INSERT INTO notifications (user_id, booking_id, message) VALUES (?, NULL, ?)", (admin_id, text))
            con.execute("UPDATE rides SET admin_alert_sent = 1 WHERE id = ?", (ride["id"],))
            done["rides"] += 1

    con.commit()
    con.close()
    return done


def _dur(td):
    minutes = int(td.total_seconds() // 60)
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    return (str(days) + "d " if days else "") + str(hours) + "h " + str(mins) + "m"


def countdown(start_str, end_str, now):
    """Text for the dashboard, worked out in Python (no JavaScript): returns (text, css_class)."""
    start = datetime.strptime(start_str, FMT)
    end = datetime.strptime(end_str, FMT)
    if now < start:
        return "Starts in " + _dur(start - now), ""
    if now <= end:
        left = end - now
        return "Return in " + _dur(left), ("soon" if left < timedelta(hours=REMIND_HOURS) else "ok")
    return "OVERDUE by " + _dur(now - end), "late"
