# Invoice helpers: builds the PDF and sends it by Gmail.
# Kept in a separate file so app.py routes stay short and simple.
import io
import math
from datetime import datetime
import smtplib
import sqlite3
from email.message import EmailMessage

import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image

BRAND = colors.HexColor("#0c3b47")
ACCENT = colors.HexColor("#80510b")
LIGHT = colors.HexColor("#283e46")


def load_booking(booking_id):
    """Booking + car + user details in one row (or None)."""
    con = sqlite3.connect("database.db")
    con.row_factory = sqlite3.Row
    row = con.execute("""
        SELECT bookings.*,
               cars.name AS car_name, cars.brand AS car_brand, cars.type AS car_type,
               cars.fuel AS car_fuel, cars.seats AS car_seats, cars.number_plate,
               cars.qr_token, cars.price_per_day,
               users.name AS user_name, users.email AS user_email, users.phone AS user_phone
        FROM bookings
        JOIN cars ON cars.id = bookings.car_id
        JOIN users ON users.id = bookings.user_id
        WHERE bookings.id = ?
    """, (booking_id,)).fetchone()
    con.close()
    return row


def make_invoice_pdf(row, base_url):
    """Returns the invoice as PDF bytes."""
    styles = getSampleStyleSheet()
    normal = ParagraphStyle("n", parent=styles["Normal"], fontSize=10, leading=14)
    small = ParagraphStyle("s", parent=normal, fontSize=8.5, textColor=colors.grey)
    label = ParagraphStyle("l", parent=normal, textColor=colors.grey, fontSize=9)
    title = ParagraphStyle("t", parent=styles["Title"], alignment=0, textColor=BRAND, fontSize=22, leading=26)
    right = ParagraphStyle("r", parent=normal, alignment=2)
    h = ParagraphStyle("h", parent=normal, fontName="Helvetica-Bold", textColor=BRAND, fontSize=11, spaceBefore=10, spaceAfter=4)

    hours = (datetime.strptime(row["end_datetime"], "%Y-%m-%d %H:%M") - datetime.strptime(row["start_datetime"], "%Y-%m-%d %H:%M")).total_seconds() / 3600
    days = max(1, math.ceil(hours / 24))
    inv_no = "INV-" + str(row["id"]).zfill(5)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title="Invoice " + inv_no, author="DriveEasy")
    story = []

    # header
    header = Table([[
        [Paragraph("DriveEasy", title), Paragraph("Car Rental", label)],
        [Paragraph("<b>BOOKING INVOICE</b>", right), Paragraph(inv_no, right),
         Paragraph("Date: " + str(row["created_at"])[:10], right)],
    ]], colWidths=[95 * mm, 79 * mm])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("LINEBELOW", (0, 0), (-1, 0), 1.5, ACCENT),
                                ("BOTTOMPADDING", (0, 0), (-1, 0), 8)]))
    story += [header, Spacer(1, 8)]

    # customer + car
    def block(heading, lines):
        cells = [Paragraph("<b>" + heading + "</b>", h)] + [Paragraph(x, normal) for x in lines]
        return cells

    two = Table([[
        block("Customer", [row["user_name"], row["user_email"], "Phone: " + row["user_phone"]]),
        block("Car", [row["car_brand"] + " " + row["car_name"],
                      "Plate: <b>" + row["number_plate"] + "</b>",
                      row["car_type"] + " | " + row["car_fuel"] + " | " + str(row["car_seats"]) + " seats"]),
    ]], colWidths=[87 * mm, 87 * mm])
    two.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
                             ("LEFTPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story += [two, Spacer(1, 10)]

    # rental + price
    data = [
        ["Description", "Details"],
        ["Pickup", row["start_datetime"]],
        ["Return", row["end_datetime"]],
        ["Charged for", Paragraph(row["rate_note"] or (str(days) + " day(s) x Rs." + str(row["price_per_day"])), right)],
        ["Rental amount", "Rs. " + str(row["total_price"] + (row["discount"] or 0))],
    ]
    if row["trip_type"] == "Outstation":
        data.insert(3, ["Trip", Paragraph("Outstation to " + str(row["destination"]) + " (" + str(row["km_limit"]) + " km included)", right)])
    if row["discount"]:
        data.append(["Discount (" + str(row["coupon_code"]) + ")", "- Rs. " + str(row["discount"])])
    total = row["total_price"]
    for label, key in [("Late fee", "late_fee"), ("Damage charge", "damage_charge"),
                       ("Fuel charge", "fuel_charge"), ("Extra km charge", "km_charge")]:
        if row[key]:
            data.append([label, "Rs. " + str(row[key])])
            total += row[key]
    data.append(["TOTAL", "Rs. " + str(total)])

    t = Table(data, colWidths=[87 * mm, 87 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("BACKGROUND", (0, -1), (-1, -1), LIGHT),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, colors.lightgrey),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story += [t, Spacer(1, 6)]

    pay_text = "Payment: <b>" + row["payment_status"] + "</b>"
    if row["utr_number"]:
        pay_text += " | UTR: " + row["utr_number"]
    if row["deposit"]:
        pay_text += " | Security deposit paid: Rs. " + str(row["deposit"]) + " (refundable)"
        if row["status"] == "Completed":
            pay_text += " | Deposit refunded: Rs. " + str(row["deposit_refund"])
            if row["balance_due"]:
                pay_text += " | Balance due from customer: Rs. " + str(row["balance_due"])
    story += [Paragraph(pay_text, normal), Paragraph("Booking status: <b>" + row["status"] + "</b>", normal), Spacer(1, 10)]

    # passport QR
    qr_buf = io.BytesIO()
    qrcode.make(base_url.rstrip("/") + "/passport/" + row["qr_token"]).save(qr_buf, format="PNG")
    qr_buf.seek(0)
    qr_row = Table([[Image(qr_buf, 32 * mm, 32 * mm),
                     [Paragraph("<b>Smart Car Passport</b>", h),
                      Paragraph("Scan this QR to see the car details, documents and live availability.", normal)]]],
                   colWidths=[38 * mm, 136 * mm])
    qr_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story += [qr_row, Spacer(1, 10)]

    # terms
    story.append(Paragraph("<b>Terms</b>", h))
    for line in ["Carry a valid driving licence and photo ID at pickup.",
                 "Return the car on time and with the same fuel level. Late return may attract a late fee.",
                 "Customer is responsible for traffic fines and damage during the rental period."]:
        story.append(Paragraph("&bull; " + line, small))

    doc.build(story)
    return buf.getvalue()


def send_invoice_email(row, pdf_bytes, gmail_user, app_password):
    """Sends the invoice PDF to the customer. Returns (True/False, message)."""
    msg = EmailMessage()
    msg["Subject"] = "Your DriveEasy booking #" + str(row["id"]) + " is confirmed"
    msg["From"] = "DriveEasy <" + gmail_user + ">"
    msg["To"] = row["user_email"]
    msg.set_content(
        "Hi " + row["user_name"] + ",\n\n"
        "Your booking is confirmed.\n\n"
        "Car: " + row["car_brand"] + " " + row["car_name"] + " (" + row["number_plate"] + ")\n"
        "Pickup: " + row["start_datetime"] + "\n"
        "Return: " + row["end_datetime"] + "\n"
        "Amount paid: Rs. " + str(row["total_price"]) + "\n\n"
        "Your invoice is attached. Scan the QR in the PDF to open the Smart Car Passport.\n\n"
        "Thank you,\nDriveEasy"
    )
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf",
                       filename="invoice-" + str(row["id"]).zfill(5) + ".pdf")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(gmail_user, app_password)
            server.send_message(msg)
        return True, "Invoice email sent to " + row["user_email"]
    except Exception as e:
        return False, "Email failed: " + str(e)
