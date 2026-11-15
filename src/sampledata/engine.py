"""Pure, bounded-memory retail generation with independent deterministic streams."""

import hashlib
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterator

from .config import Scenario
from .corpus import corpus
from .schema import MUTABLE


def token(value: str, size: int = 8) -> str:
    return hashlib.blake2b(value.encode(), digest_size=size).hexdigest()


def timestamp(value) -> str:
    return value.isoformat(sep=" ", timespec="seconds")


@dataclass(slots=True)
class Record:
    source: str
    record_id: str
    entity_id: str
    data: dict
    original: dict
    mutations: list[dict]
    duplicate: bool = False


def corrupt(value: str | None, kind: str, rng: random.Random) -> tuple[str | None, str]:
    """Every input, including empty/missing values, has a defined return value."""
    if not value:
        return value, "unchanged"
    if kind == "given_name":
        alternatives = corpus()[0].nicknames.get(value.lower(), [])
        if alternatives and rng.random() < 0.6:
            return rng.choice(alternatives), "nickname"
        if rng.random() < 0.1:
            return corpus()[0].pick("common_given_names", rng).title(), "alternate_given_name"
    if kind == "birthdate":
        try:
            birthdate = datetime.strptime(value, "%m/%d/%Y")
        except ValueError:
            return value, "unchanged"
        if rng.randrange(2):
            return birthdate.strftime("%Y-%m-%d"), "date_format"
        return (birthdate + timedelta(days=365)).strftime("%m/%d/%Y"), "birth_year_shift"
    if kind == "address" and rng.random() < 0.2:
        return f"{rng.randint(100, 9999)} {corpus()[0].pick('streetnames', rng)}", "address_change"
    if kind in {"city", "state", "postal"} and rng.random() < 0.5:
        dataset = {"city": "cities", "state": "states", "postal": "postalcodes"}[kind]
        return corpus()[0].pick(dataset, rng), f"{kind}_replacement"
    if kind == "email":
        local, separator, _ = value.partition("@")
        return (
            (local + "@" + corpus()[0].pick("domains", rng), "email_domain")
            if separator
            else (value, "unchanged")
        )
    if kind == "phone":
        return "".join(c for c in value if c.isdigit()), "phone_format"
    choice = rng.randrange(3)
    if choice == 0:
        return value.upper(), "uppercase"
    if choice == 1 and len(value) > 1:
        index = rng.randrange(len(value) - 1)
        return value[:index] + value[index + 1] + value[index] + value[index + 2 :], "transpose"
    return value[:1], "initial_only"


class Engine:
    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.data, self.corpus_checksum = corpus()

    def rng(self, stream: str, index: int = 0) -> random.Random:
        return random.Random(int(token(f"{self.scenario.seed}:{stream}:{index}"), 16))

    def person(self, index: int, namespace: str = "primary") -> tuple[str, dict]:
        rng = self.rng(namespace, index)
        data = self.data
        female = rng.random() < 0.54
        first = data.pick("firstnames_female" if female else "firstnames_male", rng)
        last = data.pick("lastnames", rng)
        geography = rng.choice(data.rows("us_cities_states_counties"))
        city, state = geography[0], geography[rng.choice((1, 2))]
        postal = data.pick("postalcodes", rng)
        identity = ("E" if namespace == "primary" else "P") + f"{index + 1:010}"
        employer = data.pick("companies", rng)
        local = "".join(c.lower() for c in first + "." + last if c.isalnum() or c == ".")
        if rng.randrange(2):
            domain = data.pick("domains", rng)
        else:
            domain = (
                "".join(c.lower() for c in employer if c.isalnum()) + "." + data.pick("tld", rng)
            )
            local = data.pick("randowords", rng) + "_" + local
        address = f"{rng.randint(100, 9999)} {data.pick('streetnames', rng)}"
        if rng.random() < 0.25:
            address += " " + data.pick("streetnames2", rng)
        return identity, {
            "name_prefix": data.pick("prefix_female" if female else "prefix_male", rng),
            "name_first": first,
            "name_last": last,
            "gender": "F" if female else "M",
            "email": f"{local}.{token(identity, 3)}@{domain}",
            "account_status": rng.choice(("Active", "Active", "Active", "Closed", "DNC")),
            "addr_ln_1_txt": address,
            "city": city,
            "state": state,
            "postal_code": postal,
            "birth_dt": data.pick("birthdates", rng),
            "employment": employer,
            "job_title": data.pick("jobs", rng),
            "phone": data.pick("phones", rng),
            "dtUpdateDate": timestamp(self.scenario.reference_time),
        }

    def customer_id(self, index: int, source: str, variant: int = 0) -> str:
        start = 1_000_000_000 if source == "customers" else 2_000_000_000
        return f"{start + (index + 1) * 16 + variant}-{token(f'{self.scenario.seed}:{source}:{index}:{variant}', 4)}"

    def variants(self, index: int, stream: int = 0) -> range:
        s = self.scenario
        count = s.copies_per_duplicate if s.selects(index, s.duplicate_fraction, stream) else 0
        return range(count + 1)

    def record(
        self, source: str, record_id: str, entity_id: str, row: dict, index: int, variant: int = 0
    ) -> Record:
        rng = self.rng(f"mutate:{source}:{record_id}", index)
        for target, dataset in self.scenario.field_pools.items():
            target_source, _, field = target.partition(".")
            if target_source == source:
                row[field] = self.data.pick(
                    dataset, self.rng(f"binding:{target}:{record_id}", index)
                )
        original = row.copy()
        mutations = []
        for key, value in row.items():
            kind = MUTABLE.get(key)
            if kind is None:
                continue
            after, operation = value, "unchanged"
            if rng.random() < self.scenario.missing_probability:
                after, operation = "", "missing"
            elif variant and rng.random() < self.scenario.corruption_probability:
                after, operation = corrupt(value, kind, rng)
            if after != value:
                row[key] = after
                mutations.append(
                    {"field": key, "operation": operation, "before": value, "after": after}
                )
        return Record(source, record_id, entity_id, row, original, mutations, variant > 0)

    def event_time(self, rng: random.Random, dataset: str | None = None):
        dataset = dataset or (
            "last24hoursdates"
            if self.scenario.transaction_window == "daily"
            else "lastfiveyeardates"
        )
        return self.data.sample_time(
            dataset,
            rng,
            self.scenario.reference_time,
            original=self.scenario.date_mode == "original",
        )

    def transactions(
        self, index: int, source: str, customer_id: str, entity_id: str, variant: int = 0
    ) -> Iterator[Record]:
        rng = self.rng(f"events:{source}:{variant}", index)
        for event in range(rng.randint(1, self.scenario.max_transactions)):
            record_id = f"{source}-{index + 1}-{variant}-{event + 1}"
            cents, items = rng.randint(10000, 200000), rng.randint(1, 15)
            product = rng.randint(700000000, 900000000)
            row = {
                "customer_id": customer_id,
                "orderid": record_id,
                "purchasedate": timestamp(self.event_time(rng)),
                "transactiontotal": cents / 100,
                "pointsearned": round(cents / 100 * self.scenario.points_percent / 100),
                "numberofitems": items,
                "avgitemprice": round(cents / 100 / items, 2),
                "productcode": product,
                "productcategory": "PR" + str(product)[:3],
                "storeorwebid": rng.randint(100, 300),
            }
            yield self.record(source, record_id, entity_id, row, index)

    def records(self, limit_people: int | None = None) -> Iterator[Record]:
        s = self.scenario
        enabled = set(s.sources)
        count = s.people if limit_people is None else min(s.people, limit_people)
        for index in range(count):
            entity_id, person = self.person(index)
            primary_id = self.customer_id(index, "customers")
            for source, trans, stream in (
                ("customers", "transactions", 0),
                ("pos", "pos_transactions", 1),
            ):
                if source not in enabled:
                    continue
                identity, base = entity_id, person
                if source == "pos" and not s.selects(index, s.overlap_fraction, 2):
                    identity, base = self.person(index, "pos")
                for variant in self.variants(index, stream):
                    record_id = self.customer_id(index, source, variant)
                    row = {"customer_id": record_id, **base}
                    yield self.record(source, record_id, identity, row, index, variant)
                    if trans in enabled:
                        yield from self.transactions(index, trans, record_id, identity, variant)

            for source in s.sources:
                if source in {"customers", "transactions", "pos", "pos_transactions"}:
                    continue
                rng = self.rng(source, index)
                date_pool = (
                    "last1hourdates"
                    if source in {"mobile", "wifi"}
                    else "last24hoursdates"
                    if source == "clickstream"
                    else None
                )
                when = self.event_time(rng, date_pool)
                dt = timestamp(when)
                update = person["dtUpdateDate"]
                ip = self.data.pick("ipv6" if source == "clickstream" else "ip_addresses", rng)
                device_rng = self.rng("device", index)
                device = self.data.pick("uuid4", device_rng)
                device_hash = self.data.pick("sha256", device_rng)
                page = self.data.pick("uris", rng)
                base_id = f"{source}-{index + 1}"
                if source == "loyalty":
                    row = {
                        "lm_id": "LM" + primary_id,
                        **{
                            k: person[k]
                            for k in (
                                "name_first",
                                "name_last",
                                "email",
                                "gender",
                                "city",
                                "state",
                                "postal_code",
                                "phone",
                            )
                        },
                        "address1": person["addr_ln_1_txt"],
                        "birthdate": person["birth_dt"],
                        "created": self.data.pick("lmscreatedates", rng),
                        "points": rng.randint(0, 120) * 1000,
                        "current_tier": self.data.pick("loyaltytiers", rng),
                        "lmProgramName": self.data.pick("loyaltyprograms", rng),
                        "dtUpdateDate": update,
                    }
                elif source == "email":
                    captured = when
                    stored = captured + timedelta(seconds=rng.randint(0, 120))
                    if s.date_mode == "relative":
                        stored = min(s.reference_time, stored)
                    row = {
                        "EVENT_ID": base_id,
                        "EVENT_TYPE_ID": self.data.pick("emailcampaigns", rng),
                        "ACCOUNT_ID": 101,
                        "LIST_ID": 12,
                        "CUSTOMER_ID": primary_id,
                        "EVENT_CAPTURED_DT": timestamp(captured),
                        "EVENT_STORED_DT": timestamp(stored),
                        "CAMPAIGN_ID": rng.randint(1, 99),
                        "LAUNCH_ID": rng.randint(1, 20),
                        "EMAIL": person["email"],
                        "EMAIL_FORMAT": "HTML",
                        "MESSAGE_SIZE": rng.randint(100, 2000),
                        "LAUNCH_DATE": timestamp(captured),
                        "dtUpdateDate": update,
                    }
                elif source == "clickstream":
                    row = {
                        "cust_visid": primary_id,
                        "daily_visitor": 1,
                        "hit_time_gmt": dt,
                        "page_url": page,
                        "campaign": self.data.pick("emailcampaigns", rng),
                        "ip": ip,
                        "first_hit_time_gmt": dt,
                        "last_hit_time_gmt": timestamp(
                            when + timedelta(seconds=60)
                            if s.date_mode == "original"
                            else min(s.reference_time, when + timedelta(seconds=60))
                        ),
                        "evar1": device,
                        "evar11": person["email"],
                        "user_agent": self.data.pick("user_agent", rng),
                        "referrer": self.data.pick("uris", rng),
                        "product_list": "PR001",
                        "uuid": base_id,
                        "dtUpdateDate": update,
                    }
                elif source == "mobile":
                    row = {
                        "email": person["email"],
                        "time_on_page": rng.randint(10, 90),
                        "page": page,
                        "client_ip": ip,
                        "login_time": dt,
                        "sha": device_hash,
                        "device_uuid": device,
                        "dtUpdateDate": update,
                    }
                elif source == "wifi":
                    row = {
                        "email": person["email"],
                        "fname": person["name_first"],
                        "lname": person["name_last"],
                        "roomOrStoreID": rng.randint(100, 900),
                        "postal_code": person["postal_code"],
                        "client_ip": ip,
                        "login_time": dt,
                        "sha": device_hash,
                        "device_uuid": device,
                        "dtUpdateDate": update,
                    }
                elif source == "safety":
                    row = {
                        "customer_id": primary_id,
                        "ReportTimestamp": dt,
                        "ReportType": rng.choice(("Incident", "Near Miss", "Observation")),
                        "ReportPriority": rng.choice(("Normal", "Low", "Critical")),
                        "Description": self.data.pick("injuries", rng),
                    }
                elif source == "locations":
                    row = {
                        "customer_id": primary_id,
                        "TileTimestamp": dt,
                        "TileType": "Tile",
                        "TileLocation": self.data.pick("latlongs", rng),
                    }
                else:
                    row = {
                        "Email": person["email"],
                        "FirstName": person["name_first"],
                        "LastName": person["name_last"],
                        "StreetAddress": person["addr_ln_1_txt"],
                        "City": person["city"],
                        "State": person["state"],
                        "PostalCode": person["postal_code"],
                        "Country": "USA",
                    }
                variants = (
                    self.variants(index, 3) if source in {"loyalty", "wifi", "custom"} else range(1)
                )
                for variant in variants:
                    record_id = base_id + (f"-copy-{variant}" if variant else "")
                    copy = row.copy()
                    if source == "loyalty":
                        copy["lm_id"] = record_id
                    yield self.record(source, record_id, entity_id, copy, index, variant)


def preview(scenario: Scenario, people: int = 5, max_records: int = 500) -> dict:
    engine = Engine(scenario)
    rows = []
    for record in engine.records(limit_people=people):
        if len(rows) >= max_records:
            break
        rows.append(
            {
                "source": record.source,
                "record_id": record.record_id,
                "entity_id": record.entity_id,
                "duplicate": record.duplicate,
                "data": record.data,
                "original": record.original,
                "mutations": record.mutations,
            }
        )
    return {
        "scenario": scenario.model_dump(mode="json"),
        "records": rows,
        "preview_people": min(people, scenario.people),
        "truncated": len(rows) == max_records,
        "corpus_checksum": engine.corpus_checksum,
    }
