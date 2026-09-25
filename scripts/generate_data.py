"""
Generate synthetic sales data for the Power BI lab.

Writes two copies of the same dataset:
  data/clean/  no defects         -> load into QA
  data/dev/    planted defects    -> load into DEV (your tests should fail here)

Planted story: across 2025-Q3 and Q4, EMEA loses about 35% of its customers.
The remaining customers keep ordering at the same frequency and basket size,
so the decline is driven by fewer customers, not by order volume or AOV.

Planted defects (dev copy only):
  - Orphan customer key: some fact_sales rows point to CustomerKey 9999
  - Duplicate product key: ProductKey 17 appears twice in dim_product

Run from the repo root:  python scripts/generate_data.py
"""
import csv
import math
import re
import random
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

SEED = 42                      # same seed -> same data every run
random.seed(SEED)
Faker.seed(SEED)

START, END = date(2024, 1, 1), date(2025, 12, 31)
N_CUSTOMERS, N_PRODUCTS = 200, 50
BASE_ORDERS_PER_MONTH = 6.2    # tuned to give about 50k order lines
STORY_REGION, STORY_CHURN = "EMEA", 0.35
# Churn lands on quarter boundaries so quarterly customer counts show it cleanly.
STORY_CHURN_DATES = [date(2025, 7, 1), date(2025, 10, 1)]
ORPHAN_KEY, ORPHAN_ROWS, DUPLICATE_PRODUCT_KEY = 9999, 25, 17
OUT = Path("data")

# (GeographyKey, Country, CountryCode, Region, Faker locale, share of customers)
GEOGRAPHY = [
    (1, "United Kingdom", "GB", "EMEA", "en_GB", 14),
    (2, "Germany", "DE", "EMEA", "de_DE", 12),
    (3, "France", "FR", "EMEA", "fr_FR", 10),
    (4, "Spain", "ES", "EMEA", "es_ES", 6),
    (5, "United States", "US", "Americas", "en_US", 22),
    (6, "Canada", "CA", "Americas", "en_CA", 8),
    (7, "Brazil", "BR", "Americas", "pt_BR", 6),
    (8, "Japan", "JP", "APAC", "en_US", 8),
    (9, "Australia", "AU", "APAC", "en_AU", 7),
    (10, "India", "IN", "APAC", "en_IN", 7),
]
SEGMENTS = {"Enterprise": (20, 1.6), "Mid-Market": (35, 1.0), "Small Business": (45, 0.7)}
CATEGORIES = {  # category: (price range, product nouns)
    "Accessories": ((10, 60), ["Cable", "Adapter", "Mouse", "Headset", "Stand"]),
    "Hardware": ((150, 1500), ["Laptop", "Monitor", "Dock", "Tablet", "Router"]),
    "Software": ((50, 400), ["Suite", "License", "Toolkit", "Platform", "Studio"]),
    "Services": ((100, 800), ["Setup", "Support Plan", "Training", "Audit", "Migration"]),
}
SEASONALITY = {1: 0.9, 2: 0.95, 3: 1.05, 4: 1.0, 5: 1.0, 6: 1.05,
               7: 0.95, 8: 0.85, 9: 1.05, 10: 1.05, 11: 1.15, 12: 1.2}
YEAR_GROWTH = {2024: 1.0, 2025: 1.06}


def poisson(lam):
    """Random count of events with average lam (Knuth's method)."""
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= random.random()
        if p <= limit:
            return k
        k += 1


def months(start, end):
    d = start
    while d <= end:
        yield d
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def month_end(d):
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1)


def date_key(d):
    return int(d.strftime("%Y%m%d"))


def build_dim_date():
    rows, d = [], START
    while d <= END:
        q = (d.month - 1) // 3 + 1
        rows.append({
            "DateKey": date_key(d), "Date": d.isoformat(), "Year": d.year,
            "QuarterNumber": q, "Quarter": f"Q{q}", "YearQuarter": f"{d.year}-Q{q}",
            "MonthNumber": d.month, "MonthName": d.strftime("%B"),
            "MonthShort": d.strftime("%b"), "YearMonth": d.strftime("%Y-%m"),
            "YearMonthNumber": d.year * 100 + d.month,
            "DayOfWeekNumber": d.isoweekday(), "DayOfWeekName": d.strftime("%A"),
            "IsWeekend": d.isoweekday() >= 6,
        })
        d += timedelta(days=1)
    return rows


def build_dim_geography():
    return [{"GeographyKey": k, "Country": c, "CountryCode": cc, "Region": r}
            for k, c, cc, r, _, _ in GEOGRAPHY]


def product_name(fake, nouns):
    colour = re.sub(r"(?<!^)(?=[A-Z])", " ", fake.color_name())  # "DarkOliveGreen" -> "Dark Olive Green"
    return f"{colour} {random.choice(nouns)}"


def build_dim_product():
    fake, rows = Faker("en_US"), []
    names = set()
    for key in range(1, N_PRODUCTS + 1):
        category = list(CATEGORIES)[(key - 1) % len(CATEGORIES)]
        (lo, hi), nouns = CATEGORIES[category]
        name = product_name(fake, nouns)
        while name in names:
            name = product_name(fake, nouns)
        names.add(name)
        price = round(random.uniform(lo, hi), 2)
        rows.append({
            "ProductKey": key, "SKU": f"P{key:04d}", "ProductName": name,
            "Category": category, "UnitPrice": price,
            "UnitCost": round(price * random.uniform(0.45, 0.7), 2),
        })
    return rows


def build_dim_customer():
    geo_weights = [g[5] for g in GEOGRAPHY]
    seg_names, seg_weights = list(SEGMENTS), [s[0] for s in SEGMENTS.values()]
    fakers = {g[4]: Faker(g[4]) for g in GEOGRAPHY}
    rows, hidden = [], {}
    for key in range(1, N_CUSTOMERS + 1):
        geo = random.choices(GEOGRAPHY, weights=geo_weights)[0]
        segment = random.choices(seg_names, weights=seg_weights)[0]
        joined = START if random.random() < 0.8 else START + timedelta(days=random.randint(30, 600))
        rows.append({
            "CustomerKey": key, "CustomerID": f"C{key:04d}",
            "CustomerName": fakers[geo[4]].company(), "Segment": segment,
            "GeographyKey": geo[0], "JoinDate": joined.isoformat(),
        })
        # Hidden behaviour: never exported, only drives order generation.
        churned = None
        if geo[3] == STORY_REGION and random.random() < STORY_CHURN:
            churned = random.choice(STORY_CHURN_DATES)
        rate = (BASE_ORDERS_PER_MONTH * SEGMENTS[segment][1]
                * random.lognormvariate(0, 0.35) / math.exp(0.35 ** 2 / 2))
        hidden[key] = {"geo": geo[0], "joined": joined, "churned": churned, "rate": rate}
    return rows, hidden


def build_fact_sales(hidden, products):
    product_weights = [random.paretovariate(1.5) for _ in products]
    rows, order_no = [], 0
    for m in months(START, END):
        days_in_month = month_end(m).day
        factor = SEASONALITY[m.month] * YEAR_GROWTH[m.year]
        for cust_key, c in hidden.items():
            for _ in range(poisson(c["rate"] * factor)):
                order_date = m + timedelta(days=random.randint(0, days_in_month - 1))
                if order_date < c["joined"] or (c["churned"] and order_date >= c["churned"]):
                    continue
                order_no += 1
                n_lines = random.choices([1, 2, 3, 4], weights=[40, 30, 20, 10])[0]
                for line, p in enumerate(random.choices(products, weights=product_weights, k=n_lines), 1):
                    qty = random.choices([1, 2, 3, 4, 5], weights=[45, 25, 15, 10, 5])[0]
                    rows.append({
                        "OrderID": f"SO{order_no:06d}", "OrderLineNumber": line,
                        "OrderDateKey": date_key(order_date), "CustomerKey": cust_key,
                        "ProductKey": p["ProductKey"], "GeographyKey": c["geo"],
                        "Quantity": qty, "UnitPrice": p["UnitPrice"],
                        "SalesAmount": round(qty * p["UnitPrice"], 2),
                        "CostAmount": round(qty * p["UnitCost"], 2),
                    })
    rows.sort(key=lambda r: (r["OrderDateKey"], r["OrderID"], r["OrderLineNumber"]))
    return rows


def build_fact_target(sales):
    """Targets at country x month. 2025 budget = 2024 actuals + 8%."""
    actual = {}
    for r in sales:
        k = (r["GeographyKey"], r["OrderDateKey"] // 100)
        actual[k] = actual.get(k, 0) + r["SalesAmount"]
    rows = []
    for g in GEOGRAPHY:
        for m in months(START, END):
            ym = m.year * 100 + m.month
            if m.year == 2024:
                target = actual.get((g[0], ym), 0) * random.uniform(0.95, 1.08)
            else:
                target = actual.get((g[0], ym - 100), 0) * 1.08
            rows.append({"GeographyKey": g[0], "MonthDateKey": date_key(m),
                         "TargetAmount": int(round(target, -2))})
    return rows


def write(folder, name, rows):
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def quarter_of(date_key_value):
    d = str(date_key_value)
    return f"{d[:4]}-Q{(int(d[4:6]) - 1) // 3 + 1}"


def story_check(sales, customers, geography):
    """Print the numbers that prove the planted story is in the data."""
    region_of = {g["GeographyKey"]: g["Region"] for g in geography}
    active = {}
    for r in sales:
        key = (region_of[r["GeographyKey"]], quarter_of(r["OrderDateKey"]))
        active.setdefault(key, set()).add(r["CustomerKey"])
    quarters = sorted({q for _, q in active})
    print("\nActive customers by region and quarter:")
    print(f"{'Region':<10}" + "".join(f"{q:>9}" for q in quarters))
    for region in sorted({r for r, _ in active}):
        print(f"{region:<10}" + "".join(f"{len(active.get((region, q), ())):>9}" for q in quarters))

    stats = {}
    for r in sales:
        if region_of[r["GeographyKey"]] != STORY_REGION:
            continue
        s = stats.setdefault(quarter_of(r["OrderDateKey"]), {"sales": 0, "orders": set(), "custs": set()})
        s["sales"] += r["SalesAmount"]
        s["orders"].add(r["OrderID"])
        s["custs"].add(r["CustomerKey"])
    print(f"\n{STORY_REGION} decomposition (Sales = Customers x Orders per customer x AOV):")
    print(f"{'Quarter':<9}{'Sales':>12}{'Customers':>11}{'Orders/cust':>13}{'AOV':>9}")
    for q in quarters:
        s = stats[q]
        n_orders, n_custs = len(s["orders"]), len(s["custs"])
        print(f"{q:<9}{s['sales']:>12,.0f}{n_custs:>11}{n_orders / n_custs:>13.1f}{s['sales'] / n_orders:>9,.0f}")


def main():
    dim_date = build_dim_date()
    dim_geo = build_dim_geography()
    dim_product = build_dim_product()
    dim_customer, hidden = build_dim_customer()
    fact_sales = build_fact_sales(hidden, dim_product)
    fact_target = build_fact_target(fact_sales)

    clean = {"dim_date": dim_date, "dim_geography": dim_geo, "dim_product": dim_product,
             "dim_customer": dim_customer, "fact_sales": fact_sales,
             "fact_sales_target": fact_target}
    for name, rows in clean.items():
        write(OUT / "clean", name, rows)

    # DEV copy: same data plus the planted defects.
    dev_sales = [dict(r) for r in fact_sales]
    for r in random.sample(dev_sales, ORPHAN_ROWS):
        r["CustomerKey"] = ORPHAN_KEY
    dup = dict(dim_product[DUPLICATE_PRODUCT_KEY - 1], ProductName="Legacy Duplicate Item",
               SKU=f"P{DUPLICATE_PRODUCT_KEY:04d}-OLD")
    dev = dict(clean, fact_sales=dev_sales, dim_product=dim_product + [dup])
    for name, rows in dev.items():
        write(OUT / "dev", name, rows)

    print("Rows written:")
    for name, rows in clean.items():
        print(f"  {name:<20} {len(rows):>7,}")
    print(f"  Total sales: {sum(r['SalesAmount'] for r in fact_sales):,.0f}")
    story_check(fact_sales, dim_customer, dim_geo)
    print(f"\nDEV defects: {ORPHAN_ROWS} rows with CustomerKey {ORPHAN_KEY}, "
          f"ProductKey {DUPLICATE_PRODUCT_KEY} duplicated in dim_product.")


if __name__ == "__main__":
    main()