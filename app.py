from flask import Flask, render_template, request, redirect, url_for, session
from dotenv import load_dotenv
import os
import sqlite3
import smtplib
import secrets
import time
from datetime import date
from email.message import EmailMessage

load_dotenv()

app = Flask(__name__,
templates_folder="."
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

MAIL_USERNAME = os.getenv("MAIL_USERNAME")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")

DATABASE = os.path.join(app.root_path, "tracktrain.db")
otp_storage = {}

CLASS_PRICES = {
    "General Class": 200,
    "Sleeper Class": 350,
    "AC 3-Tier": 600,
    "First AC": 1000
}

ALLOWED_TRAINS = {
    "12727", "12728", "12759", "12760",
    "12703", "12704", "17015", "17016"
}

ALLOWED_SEATS = (
    {f"F{i}" for i in range(1, 16)}
    | {f"S{i}" for i in range(1, 11)}
    | {f"G{i}" for i in range(1, 31)}
)


# ---------------- DATABASE ----------------

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            train_name TEXT NOT NULL DEFAULT '12727',
            from_station TEXT NOT NULL,
            to_station TEXT NOT NULL,
            journey_date TEXT NOT NULL,
            travel_class TEXT NOT NULL,
            seats TEXT NOT NULL,
            passenger_count INTEGER NOT NULL,
            total_price INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    columns = conn.execute("PRAGMA table_info(bookings)").fetchall()
    column_names = [column["name"] for column in columns]

    if "train_name" not in column_names:
        conn.execute("""
            ALTER TABLE bookings
            ADD COLUMN train_name TEXT NOT NULL DEFAULT '12727'
        """)

    conn.commit()
    conn.close()


init_db()


# ---------------- EMAIL OTP ----------------

def send_email_otp(receiver_email, otp_code):
    if not MAIL_USERNAME or not MAIL_PASSWORD:
        raise RuntimeError("Gmail settings are missing in the .env file.")

    msg = EmailMessage()
    msg["Subject"] = "Your TrainTrack Login OTP"
    msg["From"] = MAIL_USERNAME
    msg["To"] = receiver_email

    msg.set_content(
        f"Your TrainTrack verification OTP is: {otp_code}\n"
        "This OTP is valid for 5 minutes. Do not share it with anyone."
    )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(MAIL_USERNAME.strip(), MAIL_PASSWORD.strip())
        server.send_message(msg)


# ---------------- HOME ----------------

@app.route("/")
def home():
    return render_template("index.html")


# ---------------- LOGIN ----------------

@app.route("/login")
def login():
    return render_template("login.html")


@app.route("/otp")
def otp():
    if "otp_email" not in session:
        return redirect(url_for("login"))

    return render_template("otp.html")


@app.route("/send-otp", methods=["POST"])
def send_otp():
    email = request.form.get("email", "").strip().lower()

    if not MAIL_USERNAME or not MAIL_PASSWORD:
        return "Gmail settings are missing. Check your .env file.", 500

    if not email or "@" not in email:
        return "Please enter a valid email address.", 400

    code = str(secrets.randbelow(900000) + 100000)

    try:
        send_email_otp(email, code)

        otp_storage[email] = {
            "code": code,
            "expires": time.time() + 300
        }

        session["otp_email"] = email
        return redirect(url_for("otp"))

    except Exception:
        app.logger.exception("Failed to send OTP email")
        return "Unable to send OTP. Check the VS Code Terminal.", 500


@app.route("/verify-otp", methods=["POST"])
def verify_otp():
    email = session.get("otp_email")
    entered_otp = request.form.get("otp", "").strip()
    saved_otp = otp_storage.get(email) if email else None

    if not saved_otp:
        return "Please request a new OTP.", 400

    if time.time() > saved_otp["expires"]:
        otp_storage.pop(email, None)
        session.pop("otp_email", None)
        return "OTP expired. Please request a new OTP.", 400

    if not secrets.compare_digest(entered_otp, saved_otp["code"]):
        return "Incorrect OTP. Please try again.", 400

    otp_storage.pop(email, None)
    session.pop("otp_email", None)
    session["logged_in_email"] = email

    return redirect(url_for("dashboard"))


# ---------------- REGISTER ----------------

@app.route("/register")
def register():
    return render_template("register.html")


# ---------------- DASHBOARD ----------------

@app.route("/dashboard")
def dashboard():
    if "logged_in_email" not in session:
        return redirect(url_for("login"))

    return render_template("dashboard.html")


# ---------------- BOOKING ----------------

@app.route("/booking", methods=["GET", "POST"])
def booking():
    if "logged_in_email" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        email = session["logged_in_email"]
        train_name = request.form.get("train_name", "").strip()
        from_station = request.form.get("from_station", "").strip()
        to_station = request.form.get("to_station", "").strip()
        journey_date = request.form.get("journey_date", "").strip()
        travel_class = request.form.get("travel_class", "").strip()

        selected_seats = [
            seat.strip()
            for seat in request.form.get("seats", "").split(",")
            if seat.strip()
        ]

        if not all([
            train_name, from_station, to_station,
            journey_date, travel_class
        ]):
            return "Please complete all booking details.", 400

        if train_name not in ALLOWED_TRAINS:
            return "Please select a valid train.", 400

        if from_station.casefold() == to_station.casefold():
            return "Starting station and destination must be different.", 400

        if travel_class not in CLASS_PRICES:
            return "Invalid travel class.", 400

        if not selected_seats:
            return "Please select at least one seat.", 400

        if len(selected_seats) != len(set(selected_seats)):
            return "Duplicate seat selection is not allowed.", 400

        if not set(selected_seats).issubset(ALLOWED_SEATS):
            return "Invalid seat selection.", 400

        try:
            selected_date = date.fromisoformat(journey_date)
            if selected_date < date.today():
                return "Journey date cannot be in the past.", 400
        except ValueError:
            return "Please enter a valid journey date.", 400

        passenger_count = len(selected_seats)
        total_price = passenger_count * CLASS_PRICES[travel_class]

        conn = get_db()
        try:
            existing = conn.execute("""
                SELECT seats FROM bookings
                WHERE train_name = ?
                  AND LOWER(from_station) = LOWER(?)
                  AND LOWER(to_station) = LOWER(?)
                  AND journey_date = ?
                  AND travel_class = ?
            """, (
                train_name, from_station, to_station,
                journey_date, travel_class
            )).fetchall()

            booked_seats = set()
            for row in existing:
                booked_seats.update(
                    seat.strip()
                    for seat in row["seats"].split(",")
                    if seat.strip()
                )

            conflicts = set(selected_seats) & booked_seats
            if conflicts:
                return (
                    "These seats are already booked: "
                    + ", ".join(sorted(conflicts))
                    + ". Please choose other seats."
                ), 409

            conn.execute("""
                INSERT INTO bookings (
                    email, train_name, from_station, to_station,
                    journey_date, travel_class, seats,
                    passenger_count, total_price
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                email, train_name, from_station, to_station,
                journey_date, travel_class, ",".join(selected_seats),
                passenger_count, total_price
            ))

            conn.commit()
        finally:
            conn.close() 
    
        return redirect(url_for(
    "booking",
    success=1,
    train_name=train_name,
    from_station=from_station,
    to_station=to_station,
    journey_date=journey_date,
    travel_class=travel_class
))
 
    train_name = request.args.get("train_name", "").strip()
    from_station = request.args.get("from_station", "").strip()
    to_station = request.args.get("to_station", "").strip()
    journey_date = request.args.get("journey_date", "").strip()
    travel_class = request.args.get("travel_class", "").strip()

    booked_seats = []

    if all([
        train_name, from_station, to_station,
        journey_date, travel_class
    ]):
        conn = get_db()
        try:
            rows = conn.execute("""
                SELECT seats FROM bookings
                WHERE train_name = ?
                  AND LOWER(from_station) = LOWER(?)
                  AND LOWER(to_station) = LOWER(?)
                  AND journey_date = ?
                  AND travel_class = ?
            """, (
                train_name, from_station, to_station,
                journey_date, travel_class
            )).fetchall()

            for row in rows:
                booked_seats.extend(
                    seat.strip()
                    for seat in row["seats"].split(",")
                    if seat.strip()
                )
        finally:
            conn.close()

    return render_template(
        "booking.html",
        class_prices=CLASS_PRICES,
        booked_seats=booked_seats,
        train_name=train_name,
        from_station=from_station,
        to_station=to_station,
        journey_date=journey_date,
        travel_class=travel_class
    )

# ---------------- MY PROFILE ----------------

@app.route("/profile")
def profile():
    if "logged_in_email" not in session:
        return redirect(url_for("login"))

    return render_template(
        "profile.html",
        email=session["logged_in_email"]
    )
# ---------------- MY BOOKINGS ----------------

@app.route("/my_bookings")
def my_bookings():
    if "logged_in_email" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    try:
        bookings = conn.execute("""
            SELECT * FROM bookings
            WHERE email = ?
            ORDER BY id DESC
        """, (session["logged_in_email"],)).fetchall()
    finally:
        conn.close()

    success = request.args.get("success") == "1"

    return render_template(
        "my_bookings.html",
        bookings=bookings,
        success=success
    )


# ---------------- CANCEL TICKET ----------------

@app.route("/cancel_ticket/<int:booking_id>", methods=["POST"])
def cancel_ticket(booking_id):
    if "logged_in_email" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    try:
        conn.execute("""
            DELETE FROM bookings
            WHERE id = ? AND email = ?
        """, (booking_id, session["logged_in_email"]))
        conn.commit()
    finally:
        conn.close()

    return redirect(url_for("my_bookings", cancelled=1))


# ---------------- TRAIN TRACKING ----------------

@app.route("/train_tracking")
def train_tracking():
    if "logged_in_email" not in session:
        return redirect(url_for("login"))

    return render_template("train_tracking.html")


# ---------------- LOGOUT ----------------

@app.route("/logout")
def logout():
    email = session.pop("logged_in_email", None)
    session.pop("otp_email", None)

    if email:
        otp_storage.pop(email, None)

    return redirect(url_for("login"))


# ---------------- RUN APP ----------------

if __name__ == "__main__":
    app.run(debug=True)
