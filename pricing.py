# All rent-price rules live here (used by booking, and mirrored in templates/book.html for the live preview).
import math


def days_text(n):
    return str(n) + (" day" if n == 1 else " days")


def calc_price(start, end, day_rate, hour_rate, min_hours):
    """Returns (amount, note).

    Vehicles WITHOUT an hourly rate: every 24 hours (or part of it) = 1 day, minimum 1 day.
    Vehicles WITH an hourly rate:
      - full 24-hour blocks are charged at the day rate
      - the remaining time is charged per hour (rounded up)
      - a trip shorter than 1 day is charged at least `min_hours`
      - the hourly part never costs more than one day rate (it becomes 1 more day)
    """
    minutes = int((end - start).total_seconds() // 60)
    full_days = minutes // 1440
    rem_min = minutes % 1440

    if not hour_rate or hour_rate <= 0:
        days = full_days + (1 if (rem_min > 0 or full_days == 0) else 0)
        return days * day_rate, days_text(days) + " x Rs." + str(day_rate)

    amount = full_days * day_rate
    parts = []
    if full_days > 0:
        parts.append(days_text(full_days) + " x Rs." + str(day_rate))

    if rem_min > 0 or full_days == 0:
        hours = math.ceil(rem_min / 60)
        if full_days == 0:
            hours = max(hours, min_hours)
        if hours * hour_rate >= day_rate:
            days = full_days + 1
            return days * day_rate, days_text(days) + " x Rs." + str(day_rate) + " (hourly capped at day rate)"
        amount += hours * hour_rate
        parts.append(str(hours) + " hr x Rs." + str(hour_rate))

    return amount, " + ".join(parts)


def calc_outstation(start, end, out_rate):
    """Outstation trips: every 24 hours (or part of it) = 1 day at the outstation rate, minimum 1 day.
    Returns (amount, note, days)."""
    minutes = int((end - start).total_seconds() // 60)
    days = max(1, math.ceil(minutes / 1440))
    return days * out_rate, days_text(days) + " x Rs." + str(out_rate) + " (outstation)", days


RIDE_MIN_MINUTES = 15


def calc_ride(minutes, hour_rate, day_rate):
    """E-bike ride (Start ride / End ride): charged per minute at the hourly rate, minimum 15 minutes,
    and every 24 hours is capped at the day rate. Returns (amount, note)."""
    full_days, rem = divmod(minutes, 1440)
    amount = full_days * day_rate
    notes = []
    if full_days:
        notes.append(days_text(full_days) + " x Rs." + str(day_rate))
    if rem > 0 or full_days == 0:
        billed = max(rem, RIDE_MIN_MINUTES) if full_days == 0 else rem
        part = math.ceil(billed * hour_rate / 60)
        if part >= day_rate:
            amount += day_rate
            notes.append("capped at day rate Rs." + str(day_rate))
        else:
            amount += part
            notes.append(str(billed) + " min" + (" (minimum)" if billed != rem else "") + " at Rs." + str(hour_rate) + "/hr")
    return amount, " + ".join(notes)
