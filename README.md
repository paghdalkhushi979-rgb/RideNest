# DriveEasy - Vehicle Rental (Flask + SQLite)

Built with **Python (Flask), HTML, CSS and SQL only. No JavaScript.**

## Run
```bash
pip install -r requirements.txt
python app.py          # open http://127.0.0.1:5000  (database + sample data are created automatically)
```
Admin login: `admin@carrental.com` / `admin123` (change it!)

## Settings (top of app.py)
| Setting | Meaning |
|---|---|
| `UPI_ID` | your UPI id, the payment QR is made for this |
| `GMAIL_USER`, `GMAIL_APP_PASSWORD` | Gmail + Google App Password for invoice/alert emails |
| `BASE_URL` | laptop IP (e.g. `http://192.168.1.5:5000`) so a phone can open QR / invoice links |
| `PAY_WINDOW_MIN` | unpaid booking auto-cancels after this many minutes |
| `FUEL_QUARTER_CHARGE` | fuel charge for every 1/4 tank missing at return (per category) |

Late fee rules are in `reminders.py`, price rules in `pricing.py`.

## Features
- Sections: Cars, Scooters (Activa), Bikes (Bullet), E-bikes
- Pricing: hourly + daily (hourly never costs more than a day), outstation per day with km limit
- Booking: availability search by dates, double-booking check, coupon codes, "Check price" button
- Outstation trips: admin approval first, then payment; extra km charged at return
- Security deposit: paid with the rent, refunded at return minus late fee / damage / fuel / extra km
- UPI QR payment, UTR entry, admin verification, unpaid bookings auto-cancel
- Pickup check and return check (fuel, odometer, helmets, notes, photos, driving licence)
  with a side-by-side checklist page; photos are private (owner and admin only)
- Smart Car Passport: unique QR per vehicle
- PDF invoice, Gmail, WhatsApp share link (with invoice link)
- Return alerts + late fee (background scheduler), countdown on dashboard
- Admin: dashboard (bar charts made with HTML/CSS), vehicles + photo upload, users, coupons, rentals, payments, approvals
- E-bike rides in GIFT City (Yulu style): scan QR / press Start ride, End ride in a GIFT City zone, per-minute bill, UPI pay,
  out-of-zone fee, reminder after 3 hours, admin alert after 12 hours
- Reviews and ratings

## Files
`app.py` routes | `init_db.py` tables + sample data (auto-upgrades old databases) | `pricing.py` | `invoice.py` | `reminders.py` | `templates/` | `static/css/style.css`

## Viva: likely questions
1. **Double booking?** SQL overlap check: `start < new_end AND end > new_start`.
2. **Why random QR token?** IDs can be guessed; `secrets.token_urlsafe` cannot.
3. **Passwords?** Hashed with `werkzeug.security`.
4. **Hourly vs daily price?** Hourly part is capped at one day rate (see `pricing.py`).
5. **Why admin approval for outstation?** Higher risk trips, so admin checks first.
6. **How does the deposit work?** Paid upfront; at return deductions are subtracted and the rest is refunded.
7. **Why are licence photos not in /static?** Static files are public. They are served by a route that checks the user.
8. **Why manual payment verification?** UPI QR has no callback; a gateway (Razorpay) would use a webhook (future scope).

## Future scope
Real GPS / lock hardware for e-bikes, Razorpay webhook, WhatsApp Cloud API, GPS tracking, OTP email, CSRF protection.

## Before real use
Change `app.secret_key` and the admin password, run with `debug=False` behind a proper server, add CSRF protection.
