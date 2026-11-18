"""Generate SYNTHETIC medicine consumption for the SmartMed AI prototype.

Nothing in this file comes from a real clinic, pharmacy, or patient. The
numbers are invented so the rest of the app can be built and tested before
real, authorized facility records are available.

For each medicine we simulate one year, day by day:

1. Deliveries that were ordered earlier arrive and become new batches.
2. Batches that have passed their expiry date are removed from usable stock.
3. Demand for the day is drawn at random around an expected value that
   depends on the weekday, an illustrative malaria season, and occasional
   short demand spikes.
4. Stock is dispensed First Expiry, First Out (FEFO). If there is not
   enough stock, the shortfall is recorded as "unmet" demand.
5. If stock on hand plus stock already on order falls to the reorder level,
   a new order is placed. It arrives after the supplier lead time, and
   sometimes later than planned.

The same seed and end date always produce exactly the same dataset.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
import math

import numpy as np

DATASET_VERSION = "3"  # 3 = also saves orders still on the way when the history ends
DEFAULT_SEED = 2026
HISTORY_DAYS = 365

# How busy each weekday is compared with a normal weekday (Monday = first).
# Illustrative: Monday catches up after the weekend, Saturday is a half day,
# Sunday has emergency/on-call dispensing only.
WEEKDAY_FACTORS = (1.20, 1.05, 1.00, 1.00, 1.05, 0.65, 0.35)

# Illustrative malaria season: demand peaks around mid-August (day 227 of the
# year) and is lowest around mid-February. Real timing varies by region of
# Cameroon and by year; this is a simple curve, not an epidemiological model.
MALARIA_PEAK_DAY_OF_YEAR = 227

SPIKE_CHANCE_PER_DAY = 0.01      # chance that a short demand spike starts
LATE_DELAY_DAYS = (5, 21)         # extra days when a delivery is late
SHORT_DATED_CHANCE = 0.10         # chance a delivery arrives with short shelf life
SAFETY_DAYS = 10                  # reorder level covers lead time + this many days
ORDER_COVER_DAYS = 45             # each order tops stock up to roughly this many days


@dataclass(frozen=True)
class MedicineProfile:
    """The invented settings used to simulate one medicine."""

    name: str
    category: str
    unit: str
    base_daily_demand: float     # units on an ordinary weekday, outside any season or spike
    malaria_season: float        # 0 = no seasonal effect, 0.45 = demand swings about ±45%
    lead_days: int               # usual days between placing an order and receiving it
    late_delivery_chance: float  # chance that a delivery arrives late
    shelf_life_months: int       # usual shelf life of a newly delivered batch

    @property
    def reorder_level(self) -> int:
        return round(self.base_daily_demand * (self.lead_days + SAFETY_DAYS))

    @property
    def order_up_to(self) -> int:
        return round(self.base_daily_demand * (self.lead_days + ORDER_COVER_DAYS))


# 25 invented medicine profiles. Names are common essential medicines, but all
# quantities, lead times, and delivery behaviour are made up for demonstration.
MEDICINES = [
    MedicineProfile("Paracetamol 500 mg", "Analgesic", "tablets", 90, 0.15, 14, 0.10, 36),
    MedicineProfile("Ibuprofen 400 mg", "Analgesic", "tablets", 40, 0.00, 14, 0.10, 36),
    MedicineProfile("Diclofenac 50 mg", "Analgesic", "tablets", 30, 0.00, 14, 0.10, 36),
    MedicineProfile("Amoxicillin 500 mg", "Antibiotic", "capsules", 60, 0.05, 14, 0.15, 24),
    MedicineProfile("Amoxicillin 250 mg/5 mL suspension", "Antibiotic", "bottles", 6, 0.05, 21, 0.20, 18),
    MedicineProfile("Co-trimoxazole 480 mg", "Antibiotic", "tablets", 45, 0.00, 14, 0.10, 36),
    MedicineProfile("Metronidazole 250 mg", "Antibiotic", "tablets", 35, 0.10, 14, 0.10, 36),
    MedicineProfile("Ciprofloxacin 500 mg", "Antibiotic", "tablets", 20, 0.00, 21, 0.15, 36),
    MedicineProfile("Ceftriaxone 1 g injection", "Antibiotic", "vials", 5, 0.10, 21, 0.25, 24),
    MedicineProfile("Gentamicin 80 mg injection", "Antibiotic", "ampoules", 4, 0.00, 21, 0.20, 24),
    MedicineProfile("Artemether-lumefantrine 20/120 mg", "Antimalarial", "treatment packs", 14, 0.45, 21, 0.30, 24),
    MedicineProfile("Artesunate 60 mg injection", "Antimalarial", "vials", 6, 0.50, 28, 0.30, 24),
    MedicineProfile("Quinine 300 mg", "Antimalarial", "tablets", 25, 0.30, 21, 0.20, 36),
    MedicineProfile("Malaria rapid diagnostic test", "Diagnostic", "tests", 22, 0.45, 21, 0.25, 18),
    MedicineProfile("Oral rehydration salts", "Rehydration", "sachets", 18, 0.20, 14, 0.10, 24),
    MedicineProfile("Zinc 20 mg", "Rehydration", "tablets", 20, 0.20, 21, 0.20, 24),
    MedicineProfile("Ferrous sulfate + folic acid", "Supplement", "tablets", 50, 0.00, 14, 0.10, 36),
    MedicineProfile("Mebendazole 500 mg", "Anthelminthic", "tablets", 8, 0.00, 21, 0.10, 36),
    MedicineProfile("Metformin 500 mg", "Diabetes", "tablets", 40, 0.00, 14, 0.10, 36),
    MedicineProfile("Amlodipine 5 mg", "Cardiovascular", "tablets", 30, 0.00, 14, 0.10, 36),
    MedicineProfile("Hydrochlorothiazide 25 mg", "Cardiovascular", "tablets", 20, 0.00, 21, 0.15, 36),
    MedicineProfile("Salbutamol 100 mcg inhaler", "Respiratory", "inhalers", 2, 0.00, 28, 0.25, 24),
    MedicineProfile("Omeprazole 20 mg", "Gastrointestinal", "capsules", 25, 0.00, 14, 0.10, 24),
    MedicineProfile("Oxytocin 10 IU injection", "Maternal health", "ampoules", 3, 0.00, 21, 0.20, 12),
    MedicineProfile("Sodium chloride 0.9% 500 mL", "IV fluid", "bags", 8, 0.15, 21, 0.20, 24),
]


@dataclass
class Batch:
    code: str
    quantity: int
    expiry: date


@dataclass
class DailyRecord:
    day: date
    dispensed: int    # units actually handed out
    unmet: int        # units requested but not available (simulated)
    stockout: bool    # True if stock was empty or ran out this day


@dataclass
class MedicineHistory:
    """Everything the simulation produced for one medicine."""

    profile: MedicineProfile
    days: list[DailyRecord] = field(default_factory=list)
    batches: list[Batch] = field(default_factory=list)  # stock remaining on the last day
    orders_placed: int = 0
    late_orders: int = 0      # orders that arrived later than the usual lead time
    expired_units: int = 0
    # Orders still on the way on the last day: (order date, EXPECTED arrival, quantity).
    # The expected arrival is order date + usual lead time; the simulated delivery may be later.
    open_orders: list[tuple[date, date, int]] = field(default_factory=list)


def seasonal_factor(day: date, strength: float) -> float:
    """Multiply demand by this to add the illustrative malaria season."""
    angle = 2 * math.pi * (day.timetuple().tm_yday - MALARIA_PEAK_DAY_OF_YEAR) / 365.25
    return 1 + strength * math.cos(angle)


def simulate_medicine(profile: MedicineProfile, index: int, start: date, days: int, seed: int) -> MedicineHistory:
    """Simulate `days` days of demand, dispensing, and deliveries for one medicine."""
    # Each medicine gets its own random stream, so changing one medicine's
    # settings does not change the random numbers used for the others.
    rng = np.random.default_rng([seed, index])
    history = MedicineHistory(profile)
    stock: list[Batch] = []
    pending_orders: list[tuple[date, int, date]] = []  # (actual arrival date, quantity, order date)
    batch_number = 0

    def receive(day: date, quantity: int) -> None:
        nonlocal batch_number
        batch_number += 1
        months = profile.shelf_life_months
        if rng.random() < SHORT_DATED_CHANCE:
            months = int(rng.integers(3, 9))  # a short-dated delivery
        expiry = day + timedelta(days=round(months * 30.4))
        stock.append(Batch(f"SYN-{index:02d}-{batch_number:03d}", quantity, expiry))

    receive(start, profile.order_up_to)  # opening stock on the first day
    spike_days_left, spike_multiplier = 0, 1.0

    for offset in range(days):
        day = start + timedelta(days=offset)

        # 1. Deliveries due today arrive as new batches.
        for arrival, quantity, _ in [order for order in pending_orders if order[0] == day]:
            receive(day, quantity)
        pending_orders = [order for order in pending_orders if order[0] != day]

        # 2. Expired batches are removed from usable stock.
        for batch in stock:
            if batch.expiry <= day:
                history.expired_units += batch.quantity
        stock = [batch for batch in stock if batch.expiry > day]

        # 3. Today's demand.
        if spike_days_left == 0 and rng.random() < SPIKE_CHANCE_PER_DAY:
            spike_days_left = int(rng.integers(2, 5))
            spike_multiplier = float(rng.uniform(1.8, 3.0))
        spike = spike_multiplier if spike_days_left > 0 else 1.0
        spike_days_left = max(0, spike_days_left - 1)
        expected = (
            profile.base_daily_demand
            * WEEKDAY_FACTORS[day.weekday()]
            * seasonal_factor(day, profile.malaria_season)
            * spike
        )
        demand = int(rng.poisson(expected))  # random whole number around the expected value

        # 4. Dispense First Expiry, First Out.
        on_hand_at_start = sum(batch.quantity for batch in stock)
        remaining = demand
        for batch in sorted(stock, key=lambda b: b.expiry):
            take = min(batch.quantity, remaining)
            batch.quantity -= take
            remaining -= take
        stock = [batch for batch in stock if batch.quantity > 0]
        dispensed = demand - remaining
        history.days.append(DailyRecord(day, dispensed, remaining, remaining > 0 or on_hand_at_start == 0))

        # 5. Reorder when stock on hand plus stock on order is at the reorder level.
        on_hand = sum(batch.quantity for batch in stock)
        on_order = sum(quantity for _, quantity, _ in pending_orders)
        if on_hand + on_order <= profile.reorder_level:
            delay = profile.lead_days + int(rng.integers(-2, 3))
            if rng.random() < profile.late_delivery_chance:
                delay += int(rng.integers(*LATE_DELAY_DAYS, endpoint=True))
                history.late_orders += 1
            pending_orders.append((day + timedelta(days=delay), profile.order_up_to - on_hand - on_order, day))
            history.orders_placed += 1

    history.batches = sorted(stock, key=lambda b: b.expiry)
    history.open_orders = [
        (ordered, ordered + timedelta(days=profile.lead_days), quantity) for _, quantity, ordered in pending_orders
    ]
    return history


def generate_dataset(end_date: date, seed: int = DEFAULT_SEED, days: int = HISTORY_DAYS) -> list[MedicineHistory]:
    """Simulate every medicine for the `days` days ending on `end_date`."""
    start = end_date - timedelta(days=days - 1)
    return [simulate_medicine(profile, index, start, days, seed) for index, profile in enumerate(MEDICINES, 1)]
