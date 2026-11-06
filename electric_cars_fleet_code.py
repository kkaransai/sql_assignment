# ============================================================
# ELECTRIC CAR FLEET, CHARGING AND MAINTENANCE
# Original synthetic records — no downloaded dataset
#
# Run this entire cell in Google Colab.
# Only Python's standard library is required.
# Rerunning replaces electric_car_fleet.db.
# ============================================================

import sqlite3
import random
import math
from pathlib import Path
from datetime import datetime, timedelta

rng = random.Random(3398)
db_path = Path("electric_car_fleet.db")

# Close a connection left open by an earlier notebook run.
if "db" in globals():
    try:
        db.close()
    except sqlite3.Error:
        pass

if db_path.exists():
    db_path.unlink()

db = sqlite3.connect(db_path)
db.execute("PRAGMA foreign_keys = ON")


# ============================================================
# 1. DATABASE STRUCTURE
# ============================================================

# Asset details are stored separately from repeated events.
# Journey and session numbers restart for each car.
# Therefore, both event tables have meaningful composite keys.

db.executescript("""
CREATE TABLE electric_cars (
    car_id INTEGER PRIMARY KEY,
    fleet_code TEXT NOT NULL UNIQUE
        CHECK(length(trim(fleet_code)) > 0),
    model_name TEXT NOT NULL
        CHECK(length(trim(model_name)) > 0),
    vehicle_type TEXT NOT NULL
        CHECK(vehicle_type IN ('Hatchback', 'Saloon', 'SUV')),
    manufacture_year INTEGER NOT NULL
        CHECK(typeof(manufacture_year) = 'integer'
              AND manufacture_year BETWEEN 2018 AND 2025),
    usable_battery_kwh REAL NOT NULL
        CHECK(usable_battery_kwh BETWEEN 20 AND 120),
    maximum_ac_power_kw REAL NOT NULL
        CHECK(maximum_ac_power_kw > 0),
    maximum_dc_power_kw REAL NOT NULL
        CHECK(maximum_dc_power_kw > 0),
    ac_connector TEXT NOT NULL CHECK(ac_connector = 'Type 2'),
    dc_connector TEXT NOT NULL CHECK(dc_connector = 'CCS'),
    initial_battery_percent REAL NOT NULL
        CHECK(initial_battery_percent BETWEEN 0 AND 100),
    initial_odometer_km REAL NOT NULL
        CHECK(initial_odometer_km >= 0),
    purchase_cost REAL NOT NULL CHECK(purchase_cost > 0)
);

CREATE TABLE charging_points (
    point_id INTEGER PRIMARY KEY,
    point_code TEXT NOT NULL UNIQUE
        CHECK(length(trim(point_code)) > 0),
    location_zone TEXT NOT NULL,
    supply_type TEXT NOT NULL CHECK(supply_type IN ('AC', 'DC')),
    connector_type TEXT NOT NULL
        CHECK(connector_type IN ('Type 2', 'CCS')),
    maximum_power_kw REAL NOT NULL CHECK(maximum_power_kw > 0),
    standard_price_per_kwh REAL NOT NULL
        CHECK(standard_price_per_kwh > 0),
    CHECK(
        (supply_type = 'AC' AND connector_type = 'Type 2')
        OR
        (supply_type = 'DC' AND connector_type = 'CCS')
    )
);

CREATE TABLE journeys (
    car_id INTEGER NOT NULL
        REFERENCES electric_cars(car_id) ON DELETE RESTRICT,
    journey_no INTEGER NOT NULL
        CHECK(typeof(journey_no) = 'integer' AND journey_no > 0),
    departure_time TEXT NOT NULL
        CHECK(datetime(departure_time) IS NOT NULL
              AND departure_time = datetime(departure_time)),
    arrival_time TEXT NOT NULL
        CHECK(datetime(arrival_time) IS NOT NULL
              AND arrival_time = datetime(arrival_time)),
    distance_km REAL NOT NULL
        CHECK(distance_km > 0 AND distance_km <= 1000),
    energy_used_kwh REAL NOT NULL CHECK(energy_used_kwh > 0),
    battery_before_percent REAL NOT NULL
        CHECK(battery_before_percent BETWEEN 0 AND 100),
    battery_after_percent REAL NOT NULL
        CHECK(battery_after_percent BETWEEN 0 AND 100),
    ambient_temperature_c REAL
        CHECK(ambient_temperature_c BETWEEN -20 AND 50),
    journey_type TEXT NOT NULL
        CHECK(journey_type IN ('Urban', 'Mixed', 'Motorway')),
    odometer_after_km REAL NOT NULL CHECK(odometer_after_km >= 0),

    PRIMARY KEY(car_id, journey_no),
    UNIQUE(car_id, departure_time),
    CHECK(arrival_time > departure_time),
    CHECK(battery_after_percent < battery_before_percent)
);

CREATE TABLE charging_sessions (
    car_id INTEGER NOT NULL
        REFERENCES electric_cars(car_id) ON DELETE RESTRICT,
    session_no INTEGER NOT NULL
        CHECK(typeof(session_no) = 'integer' AND session_no > 0),
    point_id INTEGER NOT NULL
        REFERENCES charging_points(point_id) ON DELETE RESTRICT,
    connected_at TEXT NOT NULL
        CHECK(datetime(connected_at) IS NOT NULL
              AND connected_at = datetime(connected_at)),
    disconnected_at TEXT NOT NULL
        CHECK(datetime(disconnected_at) IS NOT NULL
              AND disconnected_at = datetime(disconnected_at)),
    energy_delivered_kwh REAL NOT NULL
        CHECK(energy_delivered_kwh > 0),
    charging_efficiency REAL NOT NULL
        CHECK(charging_efficiency BETWEEN 0.80 AND 1.00),
    battery_before_percent REAL NOT NULL
        CHECK(battery_before_percent BETWEEN 0 AND 100),
    battery_after_percent REAL NOT NULL
        CHECK(battery_after_percent BETWEEN 0 AND 100),
    price_per_kwh REAL NOT NULL CHECK(price_per_kwh > 0),
    charging_cost REAL NOT NULL CHECK(charging_cost >= 0),

    PRIMARY KEY(car_id, session_no),
    UNIQUE(car_id, connected_at),
    CHECK(disconnected_at > connected_at),
    CHECK(battery_after_percent > battery_before_percent),
    CHECK(
        ABS(
            charging_cost -
            ROUND(energy_delivered_kwh * price_per_kwh, 2)
        ) < 0.005
    )
);

CREATE TABLE maintenance_jobs (
    maintenance_id INTEGER PRIMARY KEY,
    car_id INTEGER NOT NULL
        REFERENCES electric_cars(car_id) ON DELETE RESTRICT,
    reported_at TEXT NOT NULL
        CHECK(datetime(reported_at) IS NOT NULL
              AND reported_at = datetime(reported_at)),
    started_at TEXT NOT NULL
        CHECK(datetime(started_at) IS NOT NULL
              AND started_at = datetime(started_at)),
    completed_at TEXT NOT NULL
        CHECK(datetime(completed_at) IS NOT NULL
              AND completed_at = datetime(completed_at)),
    maintenance_priority TEXT NOT NULL
        CHECK(maintenance_priority IN (
            'Low', 'Medium', 'High', 'Critical'
        )),
    task_type TEXT NOT NULL
        CHECK(task_type IN (
            'Safety inspection', 'Tyre service',
            'Brake service', 'Electrical repair'
        )),
    maintenance_cost REAL NOT NULL CHECK(maintenance_cost >= 0),
    technician_notes TEXT,

    CHECK(started_at >= reported_at),
    CHECK(completed_at > started_at)
);

CREATE INDEX idx_session_point ON charging_sessions(point_id);
CREATE INDEX idx_maintenance_car ON maintenance_jobs(car_id);
""")


# ============================================================
# 2. GENERATE VEHICLES
# ============================================================

# These fictional models use illustrative specifications.
# Monetary amounts represent simulated GBP.
#
# Nominal: vehicle_type, connector_type, journey_type
# Ordinal: Low < Medium < High < Critical
# Interval: ambient_temperature_c
# Ratio: distance, energy, duration and costs

profiles = [
    ("City E", "Hatchback", 45.0, 0.145, 26000),
    ("Tour E", "Saloon", 60.0, 0.165, 35000),
    ("Trail E", "SUV", 75.0, 0.195, 43000)
]

cars = {}

for car_id in range(1, 81):
    model, category, capacity, consumption, base_price = rng.choices(
        profiles, weights=[40, 35, 25]
    )[0]

    initial_soc = round(rng.uniform(82, 90), 4)
    odometer = round(rng.uniform(2000, 45000), 2)
    year = rng.randint(2020, 2024)
    dc_limit = rng.choice([80.0, 100.0, 120.0])
    purchase_price = round(base_price * rng.uniform(0.90, 1.10), 2)

    db.execute(
        """
        INSERT INTO electric_cars
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            car_id, f"EV-{car_id:03d}", model, category, year,
            capacity, 11.0, dc_limit, "Type 2", "CCS",
            initial_soc, odometer, purchase_price
        )
    )

    cars[car_id] = {
        "capacity": capacity,
        "consumption": consumption,
        "soc": initial_soc,
        "odometer": odometer,
        "dc_limit": dc_limit
    }


# ============================================================
# 3. GENERATE CHARGING POINTS
# ============================================================

# Each record is one charging point that serves one car at a time.
points = {}

for point_id in range(1, 21):
    supply = "AC" if point_id <= 12 else "DC"
    connector = "Type 2" if supply == "AC" else "CCS"

    power = (
        rng.choice([11.0, 22.0])
        if supply == "AC"
        else rng.choice([50.0, 100.0])
    )

    price = round(
        rng.uniform(0.25, 0.38)
        if supply == "AC"
        else rng.uniform(0.45, 0.65),
        3
    )

    db.execute(
        "INSERT INTO charging_points VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            point_id, f"CP-{point_id:03d}",
            f"Depot zone {(point_id - 1) // 5 + 1}",
            supply, connector, power, price
        )
    )

    points[point_id] = {
        "supply": supply,
        "power": power,
        "price": price
    }


# ============================================================
# 4. GENERATE JOURNEYS AND CHARGING SESSIONS
# ============================================================

# The model covers 20 consecutive operating days.
# Each car has one journey and one charging session per day.
# This creates 1,600 journeys and 1,600 charging sessions.
#
# Battery state carries forward between events.
# Charging losses are included.
# Battery degradation and parked energy use are excluded.

journey_rows = []
charging_rows = []
start_day = datetime(2025, 1, 6)

for day_number in range(20):
    day = start_day + timedelta(days=day_number)

    for car_id in range(1, 81):
        car = cars[car_id]

        route = rng.choices(
            ["Urban", "Mixed", "Motorway"],
            weights=[40, 45, 15]
        )[0]

        distance = round(rng.triangular(8, 65, 25), 2)
        temperature = max(
            -10.0,
            min(25.0, round(rng.gauss(8, 4), 1))
        )

        route_factor = {
            "Urban": 0.95,
            "Mixed": 1.00,
            "Motorway": 1.15
        }[route]

        cold_factor = 1 + max(0, 15 - temperature) * 0.008

        energy = round(
            distance
            * car["consumption"]
            * route_factor
            * cold_factor
            * rng.uniform(0.95, 1.05),
            4
        )

        before = car["soc"]
        after = round(
            before - energy / car["capacity"] * 100,
            4
        )

        assert after >= 15, "Insufficient battery reserve."

        departure = day + timedelta(
            hours=8,
            minutes=rng.randint(0, 30)
        )

        speed = {"Urban": 25, "Mixed": 40, "Motorway": 65}[route]

        journey_minutes = max(
            15,
            math.ceil(distance / speed * 60 + rng.uniform(5, 15))
        )

        arrival = departure + timedelta(minutes=journey_minutes)

        car["odometer"] = round(car["odometer"] + distance, 2)

        journey_rows.append([
            car_id, day_number + 1,
            departure.isoformat(" "), arrival.isoformat(" "),
            distance, energy, before, after,
            temperature, route, car["odometer"]
        ])

        # Four cars share each charging point in separate slots.
        point_id = (car_id - 1) % 20 + 1
        slot_number = (car_id - 1) // 20
        point = points[point_id]

        connected = day + timedelta(
            hours=14 + slot_number * 4,
            minutes=rng.randint(0, 10)
        )

        assert connected > arrival

        # FIX: the target must exceed the remaining charge.
        # Short journeys can leave more than 82% in the battery.
        minimum_target = max(82.0, after + 0.1)

        assert minimum_target <= 90.0

        target_soc = round(
            rng.uniform(minimum_target, 90.0),
            4
        )

        assert after < target_soc <= 90.0

        efficiency = round(rng.uniform(0.90, 0.95), 4)

        added_to_battery = (
            (target_soc - after) / 100 * car["capacity"]
        )

        delivered_energy = round(
            added_to_battery / efficiency,
            4
        )

        car_limit = (
            11.0 if point["supply"] == "AC"
            else car["dc_limit"]
        )

        average_power = (
            min(point["power"], car_limit)
            * rng.uniform(0.80, 0.90)
        )

        charge_minutes = max(
            1,
            math.ceil(delivered_energy / average_power * 60)
        )

        assert charge_minutes <= 230, "Charging exceeds its slot."

        disconnected = connected + timedelta(minutes=charge_minutes)
        price = point["price"]
        cost = round(delivered_energy * price, 2)

        charging_rows.append([
            car_id, day_number + 1, point_id,
            connected.isoformat(" "), disconnected.isoformat(" "),
            delivered_energy, efficiency, after, target_soc,
            price, cost
        ])

        car["soc"] = target_soc


# ============================================================
# 5. DELIBERATE MISSING READINGS
# ============================================================

# Exactly 48 of 1,600 temperatures are missing: 3%.
# Removing readings after simulation preserves battery consistency.
# Python None becomes SQL NULL.

for position in rng.sample(range(len(journey_rows)), 48):
    journey_rows[position][8] = None

db.executemany(
    "INSERT INTO journeys VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    journey_rows
)

db.executemany(
    """
    INSERT INTO charging_sessions
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
    charging_rows
)


# ============================================================
# 6. GENERATE MAINTENANCE JOBS
# ============================================================

# Servicing takes place after the journey-monitoring period.
# Jobs for the same car are scheduled on different days.

task_rules = {
    "Safety inspection": ("Low", 30, 90),
    "Tyre service": ("Medium", 60, 240),
    "Brake service": ("High", 100, 400),
    "Electrical repair": ("Critical", 150, 700)
}

maintenance_rows = []
jobs_per_car = {car_id: 0 for car_id in cars}

for maintenance_id in range(1, 251):
    car_id = rng.randint(1, 80)
    sequence = jobs_per_car[car_id]
    jobs_per_car[car_id] += 1

    task = rng.choices(
        list(task_rules),
        weights=[45, 30, 20, 5]
    )[0]

    priority, low_cost, high_cost = task_rules[task]

    started = datetime(2025, 2, 1, 9) + timedelta(
        days=sequence * 2
    )

    reported = started - timedelta(hours=rng.randint(2, 20))
    completed = started + timedelta(minutes=rng.randint(45, 240))

    maintenance_rows.append([
        maintenance_id,
        car_id,
        reported.isoformat(" "),
        started.isoformat(" "),
        completed.isoformat(" "),
        priority,
        task,
        round(rng.triangular(low_cost, high_cost, low_cost), 2),
        None
    ])


# ============================================================
# 7. DELIBERATE DUPLICATE MAINTENANCE RECORDS
# ============================================================

# Five extra copies simulate accidental double entry.
# A duplicate matches all business fields except maintenance_id.
# Different IDs preserve primary-key uniqueness.
# The submitted database intentionally retains these copies.

for position in rng.sample(range(250), 5):
    duplicate = maintenance_rows[position].copy()
    duplicate[0] = len(maintenance_rows) + 1
    maintenance_rows.append(duplicate)

db.executemany(
    "INSERT INTO maintenance_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
    maintenance_rows
)

db.commit()


# ============================================================
# 8. CHECK BATTERY HISTORIES AND ODOMETERS
# ============================================================

# Small tolerances accommodate stored decimal rounding.

for car_id in cars:
    capacity, previous_soc, previous_odometer = db.execute(
        """
        SELECT usable_battery_kwh,
               initial_battery_percent,
               initial_odometer_km
        FROM electric_cars WHERE car_id = ?
        """,
        (car_id,)
    ).fetchone()

    records = db.execute(
        """
        SELECT journey_no, departure_time, arrival_time,
               distance_km, energy_used_kwh,
               battery_before_percent, battery_after_percent,
               odometer_after_km
        FROM journeys
        WHERE car_id = ?
        ORDER BY journey_no
        """,
        (car_id,)
    ).fetchall()

    previous_charge_end = None

    for record in records:
        (
            number, departure, arrival, distance,
            energy, before, after, odometer
        ) = record

        assert abs(before - previous_soc) < 0.0001

        assert abs(
            (before - after) / 100 * capacity - energy
        ) < 0.001

        assert abs(
            odometer - previous_odometer - distance
        ) < 0.011

        if previous_charge_end is not None:
            assert departure >= previous_charge_end

        charge = db.execute(
            """
            SELECT connected_at, disconnected_at,
                   energy_delivered_kwh, charging_efficiency,
                   battery_before_percent, battery_after_percent
            FROM charging_sessions
            WHERE car_id = ? AND session_no = ?
            """,
            (car_id, number)
        ).fetchone()

        assert charge is not None

        (
            connected, disconnected, delivered,
            efficiency, charge_before, charge_after
        ) = charge

        assert connected >= arrival
        assert abs(charge_before - after) < 0.0001

        assert abs(
            delivered * efficiency
            - (charge_after - charge_before) / 100 * capacity
        ) < 0.001

        previous_soc = charge_after
        previous_odometer = odometer
        previous_charge_end = disconnected


# ============================================================
# 9. CHECK CHARGING LIMITS, COMPATIBILITY AND COSTS
# ============================================================

records = db.execute("""
SELECT
    s.connected_at, s.disconnected_at,
    s.energy_delivered_kwh, s.price_per_kwh, s.charging_cost,
    p.supply_type, p.connector_type, p.maximum_power_kw,
    c.maximum_ac_power_kw, c.maximum_dc_power_kw,
    c.ac_connector, c.dc_connector
FROM charging_sessions s
JOIN charging_points p USING(point_id)
JOIN electric_cars c USING(car_id)
""").fetchall()

for record in records:
    (
        connected, disconnected, delivered, price, cost,
        supply, connector, point_limit, ac_limit, dc_limit,
        ac_connector, dc_connector
    ) = record

    hours = (
        datetime.fromisoformat(disconnected)
        - datetime.fromisoformat(connected)
    ).total_seconds() / 3600

    vehicle_limit = ac_limit if supply == "AC" else dc_limit
    vehicle_connector = ac_connector if supply == "AC" else dc_connector

    assert connector == vehicle_connector
    assert delivered / hours <= min(point_limit, vehicle_limit) + 0.001
    assert abs(cost - round(delivered * price, 2)) < 0.005


# ============================================================
# 10. CHECK VEHICLE AND CHARGING-POINT OVERLAPS
# ============================================================

# Duplicate maintenance rows describe one physical event.
# DISTINCT is used only for overlap checking.
# No records are removed from the submitted table.

db.executescript("""
CREATE TEMP VIEW vehicle_events AS
SELECT car_id, departure_time AS starts, arrival_time AS ends
FROM journeys
UNION ALL
SELECT car_id, connected_at, disconnected_at
FROM charging_sessions
UNION ALL
SELECT DISTINCT car_id, started_at, completed_at
FROM maintenance_jobs;
""")

vehicle_overlaps = db.execute("""
SELECT COUNT(*)
FROM (
    SELECT starts,
           MAX(ends) OVER (
               PARTITION BY car_id
               ORDER BY starts, ends
               ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
           ) AS previous_end
    FROM vehicle_events
)
WHERE starts < previous_end
""").fetchone()[0]

point_overlaps = db.execute("""
SELECT COUNT(*)
FROM (
    SELECT connected_at,
           MAX(disconnected_at) OVER (
               PARTITION BY point_id
               ORDER BY connected_at, disconnected_at
               ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
           ) AS previous_end
    FROM charging_sessions
)
WHERE connected_at < previous_end
""").fetchone()[0]

assert vehicle_overlaps == 0
assert point_overlaps == 0


# ============================================================
# 11. DATABASE INTEGRITY AND TABLE STRUCTURE
# ============================================================

assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
assert db.execute("PRAGMA foreign_key_check").fetchall() == []

expected_counts = {
    "electric_cars": 80,
    "charging_points": 20,
    "journeys": 1600,
    "charging_sessions": 1600,
    "maintenance_jobs": 255
}

print("\nTABLE COUNTS AND STRUCTURE")

for table, expected in expected_counts.items():
    actual = db.execute(
        f"SELECT COUNT(*) FROM {table}"
    ).fetchone()[0]

    columns = db.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()

    assert actual == expected

    print(f"\n{table}: {actual} rows, {len(columns)} columns")

    for column in columns:
        print(
            f"  {column[1]}: {column[2]}, "
            f"PK position={column[5]}, "
            f"NOT NULL={bool(column[3])}"
        )


# ============================================================
# 12. VERIFY MISSING AND DUPLICATE COUNTS
# ============================================================

missing = db.execute("""
SELECT COUNT(*) FROM journeys
WHERE ambient_temperature_c IS NULL
""").fetchone()[0]

duplicate_groups = db.execute("""
SELECT COUNT(*)
FROM maintenance_jobs
GROUP BY car_id, reported_at, started_at, completed_at,
         maintenance_priority, task_type,
         maintenance_cost, technician_notes
HAVING COUNT(*) > 1
""").fetchall()

extra_copies = sum(size - 1 for (size,) in duplicate_groups)
participating_rows = sum(size for (size,) in duplicate_groups)

assert missing == 48
assert extra_copies == 5
assert len(duplicate_groups) == 5

print("\nMISSING AND DUPLICATE DATA")
print(f"Missing temperatures: {missing}/1600 ({missing / 1600:.2%})")
print(f"Extra maintenance copies: {extra_copies}/255 ({extra_copies / 255:.2%})")
print(f"Duplicate groups: {len(duplicate_groups)}")
print(f"Rows in duplicate groups: {participating_rows}")

null_notes = db.execute("""
SELECT COUNT(*) FROM maintenance_jobs
WHERE technician_notes IS NULL
""").fetchone()[0]

# Optional notes are empty when no comment was recorded.
print(f"Optional notes NULL: {null_notes}/255 ({null_notes / 255:.2%})")


# ============================================================
# 13. TEST REJECTION OF INVALID RECORDS
# ============================================================

def must_reject(sql):
    # Roll back each test so the final records remain unchanged.
    db.execute("SAVEPOINT validation_test")

    try:
        db.execute(sql)
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("An invalid record was accepted.")
    finally:
        db.execute("ROLLBACK TO validation_test")
        db.execute("RELEASE validation_test")


invalid_writes = [
    "UPDATE electric_cars SET usable_battery_kwh=-1 WHERE car_id=1",

    "UPDATE electric_cars SET fleet_code='' WHERE car_id=1",

    """
    UPDATE electric_cars
    SET fleet_code=(
        SELECT fleet_code FROM electric_cars WHERE car_id=2
    )
    WHERE car_id=1
    """,

    "UPDATE electric_cars SET model_name=NULL WHERE car_id=1",

    """
    UPDATE maintenance_jobs SET maintenance_priority='Urgent'
    WHERE maintenance_id=1
    """,

    """
    UPDATE maintenance_jobs SET maintenance_cost=-10
    WHERE maintenance_id=1
    """,

    """
    UPDATE maintenance_jobs SET car_id=9999
    WHERE maintenance_id=1
    """,

    """
    UPDATE journeys SET ambient_temperature_c=100
    WHERE car_id=1 AND journey_no=1
    """,

    """
    UPDATE charging_sessions SET battery_after_percent=110
    WHERE car_id=1 AND session_no=1
    """,

    """
    UPDATE charging_sessions SET charging_cost=charging_cost+10
    WHERE car_id=1 AND session_no=1
    """,

    """
    UPDATE journeys SET arrival_time=departure_time
    WHERE car_id=1 AND journey_no=1
    """,

    """
    UPDATE journeys SET journey_no=1.5
    WHERE car_id=1 AND journey_no=1
    """,

    "INSERT INTO journeys SELECT * FROM journeys LIMIT 1",

    "DELETE FROM electric_cars WHERE car_id=1"
]

for sql in invalid_writes:
    must_reject(sql)

print("\nVALIDATION RESULTS")
print(f"Invalid-write tests passed: {len(invalid_writes)}")
print("Battery history and odometer checks: passed")
print("Charging compatibility, power and costs: passed")
print("Vehicle event overlaps:", vehicle_overlaps)
print("Charging-point overlaps:", point_overlaps)
print("Database integrity: OK")
print("Foreign-key violations: 0")

db.close()

print("\nDatabase created:", db_path.resolve())


# ============================================================
# 14. DOWNLOAD THE DATABASE
# ============================================================

try:
    from google.colab import files
except ImportError:
    print("Outside Colab: use the database path printed above.")
else:
    files.download(str(db_path))