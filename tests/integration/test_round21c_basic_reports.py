"""Round 21C — the five reports a shop asks for every day, computed by code.

No spec, no whitelist walk, no model in the number path (spec §5). These
are the numbers the owner said must always be right, so they are pinned
against a real database and against the pipeline card itself.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from chann_data.models import (
    ChannIdentity, LicenseMember, SatisfactionSurvey, ServiceTicket, TechnicianTeam,
)
from chann_data.repositories.basic_reports import REPORT_KEYS, BasicReportRepository
from chann_data.repositories.invoices import InvoiceRepository
from chann_data.repositories.phase9 import CustomerRepository, DealRepository
from chann_data.repositories.phase65 import RegistrationRepository
from chann_data.repositories.tenant_scope import TenantScope

TODAY = date(2026, 9, 23)


@contextmanager
def selects_of(session):
    """Every SELECT the block sends, so "one query" is a measurement and
    not a reading of the code."""
    seen: list[str] = []
    connection = session.connection()

    def _seen(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            seen.append(statement)

    event.listen(connection, "before_cursor_execute", _seen)
    try:
        yield seen
    finally:
        event.remove(connection, "before_cursor_execute", _seen)


@pytest.fixture
def world(migrated_db):
    """One shop with something to report on, and a second shop whose rows
    must never appear in the first one's answers."""
    tag = uuid.uuid4().hex[:6]
    uids = {"a": f"CHN-21CB{tag}A", "b": f"CHN-21CB{tag}B"}
    with Session(migrated_db) as s:
        for key, uid in uids.items():
            s.add(ChannIdentity(chann_uid=uid, line_user_id=f"line-{uid}",
                                primary_role="sales", display_name="เอ" if key == "a" else "บี"))
        s.commit()
    with Session(migrated_db) as s:
        reg = RegistrationRepository(s)
        a = reg.create_license(company_name=f"Basic A {tag}", created_by_chann_uid=uids["a"])
        b = reg.create_license(company_name=f"Basic B {tag}", created_by_chann_uid=uids["b"])
        ids = (a.id, b.id)
        s.commit()
    scope_a, scope_b = TenantScope(license_id=ids[0]), TenantScope(license_id=ids[1])
    with Session(migrated_db) as s:
        customers, deals = CustomerRepository(s), DealRepository(s)
        # 1 & 2 — deals: one open (30,000 of lines), one won this month
        # (99,000 typed), one won last month (10,000 typed).
        c1 = customers.create(scope_a, first_name="ลูกค้า", last_name="หนึ่ง", phone=f"081{tag[:7]}")
        s.flush()
        open_deal = deals.create(scope_a, contact_id=c1.id)
        s.flush()
        deals.add_product(scope_a, open_deal.id, product_id=None, product_name="แอร์",
                          quoted_unit_price=Decimal("15000.00"), qty=2)
        c2 = customers.create(scope_a, first_name="ลูกค้า", last_name="สอง", phone=f"082{tag[:7]}")
        s.flush()
        won_now = deals.create(scope_a, contact_id=c2.id, amount=Decimal("99000.00"))
        s.flush()
        won_now.stage = "won"
        won_now.closed_at = datetime(2026, 9, 10, 4, 0, tzinfo=timezone.utc)
        c3 = customers.create(scope_a, first_name="ลูกค้า", last_name="สาม", phone=f"083{tag[:7]}")
        s.flush()
        won_before = deals.create(scope_a, contact_id=c3.id, amount=Decimal("10000.00"))
        s.flush()
        won_before.stage = "won"
        won_before.closed_at = datetime(2026, 8, 15, 4, 0, tzinfo=timezone.utc)
        # 3 — tickets: two open for one technician, one unassigned, one done.
        member = uuid.uuid4()
        for status_, assignee in (("open", member), ("in_progress", member),
                                  ("assigned", None), ("completed", member)):
            s.add(ServiceTicket(
                id=uuid.uuid4(), license_id=ids[0], ticket_number=f"T-{uuid.uuid4().hex[:6]}",
                issue_description="แอร์ไม่เย็น", status=status_, assigned_to_ref=assignee,
                contact_id=c1.id))
        # 4 — invoices: one overdue, one not yet due, one paid, one draft.
        invoices = InvoiceRepository(s)
        for due, total, paid, issue in ((date(2026, 9, 1), "10000.00", "0", True),
                                        (date(2026, 10, 30), "5000.00", "0", True),
                                        (date(2026, 9, 1), "7000.00", "7000.00", True),
                                        (None, "999.00", "0", False)):
            row = invoices.create(scope_a, contact_id=c1.id, subtotal=Decimal(total),
                                  total=Decimal(total), data_snapshot={"line_items": [
                                      {"line_no": 1, "product_name": "x", "qty": 1,
                                       "unit_price": total, "line_total": total}]})
            s.flush()
            if issue:
                invoices.issue(scope_a, row.id, issue_date=date(2026, 9, 1), due_date=due)
            if Decimal(paid) > 0:
                invoices.add_payment(scope_a, row.id, amount=Decimal(paid), method="cash")
        # 5 — surveys: 3 and 1 answered this month, one never answered.
        # `satisfaction_surveys.ticket_id` is unique (one survey per
        # ticket), so each survey below needs its own ticket.
        tickets = s.execute(
            ServiceTicket.__table__.select().where(ServiceTicket.license_id == ids[0]).limit(3)
        ).all()
        for ticket, (score, submitted) in zip(tickets, (
                (3, datetime(2026, 9, 5, tzinfo=timezone.utc)),
                (1, datetime(2026, 9, 6, tzinfo=timezone.utc)),
                (None, None))):
            s.add(SatisfactionSurvey(
                id=uuid.uuid4(), license_id=ids[0], ticket_id=ticket.id,
                scale_config_json={"scale": 3}, score=score, submitted_at=submitted))
        # the neighbouring shop — something of its own in all five
        # reports, so "isolated" is a claim about five answers and not
        # only about the deal that used to be its only row.
        cb = customers.create(scope_b, first_name="อีกร้าน", last_name="ลูกค้า", phone=f"089{tag[:7]}")
        s.flush()
        other = deals.create(scope_b, contact_id=cb.id, amount=Decimal("500000.00"))
        s.flush()
        other.stage = "won"
        other.closed_at = datetime(2026, 9, 11, tzinfo=timezone.utc)
        their_tech = uuid.uuid4()
        their_tickets = []
        for status_ in ("open", "in_progress"):
            ticket = ServiceTicket(
                id=uuid.uuid4(), license_id=ids[1], ticket_number=f"T-{uuid.uuid4().hex[:6]}",
                issue_description="ของอีกร้าน", status=status_, assigned_to_ref=their_tech,
                contact_id=cb.id)
            s.add(ticket)
            their_tickets.append(ticket)
        their_bill = invoices.create(scope_b, contact_id=cb.id, subtotal=Decimal("20000.00"),
                                     total=Decimal("20000.00"), data_snapshot={"line_items": [
                                         {"line_no": 1, "product_name": "y", "qty": 1,
                                          "unit_price": "20000.00", "line_total": "20000.00"}]})
        s.flush()
        invoices.issue(scope_b, their_bill.id, issue_date=date(2026, 9, 1),
                       due_date=date(2026, 9, 1))
        s.add(SatisfactionSurvey(
            id=uuid.uuid4(), license_id=ids[1], ticket_id=their_tickets[0].id,
            scale_config_json={"scale": 3}, score=3,
            submitted_at=datetime(2026, 9, 7, tzinfo=timezone.utc)))
        s.commit()
    return migrated_db, scope_a, scope_b


class TestTheFive:
    def test_every_key_answers_with_the_same_envelope(self, world):
        engine, scope, _ = world
        with Session(engine) as s:
            for key in REPORT_KEYS:
                out = BasicReportRepository(s).run(scope, key, today=TODAY)
                assert out["key"] == key
                assert set(out) >= {"key", "title_th", "title_en", "unit", "headline",
                                    "rows", "notes_th", "notes_en", "generated_at"}
                assert set(out["headline"]) >= {"label_th", "label_en", "value"}

    def test_pipeline_value_is_the_pipeline_card(self, world):
        engine, scope, _ = world
        with Session(engine) as s:
            out = BasicReportRepository(s).run(scope, "pipeline_value", today=TODAY)
            card = DealRepository(s).pipeline_summary(scope)
        assert Decimal(str(out["headline"]["value"])) == sum(
            Decimal(b["value"]) for b in card["by_stage"].values())
        assert {row["key"] for row in out["rows"]} == {"new", "proposed", "won", "lost"}

    def test_won_this_month_counts_by_closed_at_and_compares(self, world):
        engine, scope, _ = world
        with Session(engine) as s:
            out = BasicReportRepository(s).run(scope, "won_this_month", today=TODAY)
        rows = {row["key"]: row for row in out["rows"]}
        assert Decimal(str(rows["this_month"]["value"])) == Decimal("99000")
        assert Decimal(str(rows["last_month"]["value"])) == Decimal("10000")
        assert rows["this_month"]["count"] == 1
        assert out["notes_th"], "the backfill caveat must be said in words"

    def test_won_this_month_asks_each_window_once(self, world):
        """The sum and the count of one window came from two queries; they
        come from one, the shape `_outstanding_invoices` already used."""
        engine, scope, _ = world
        with Session(engine) as s:
            with selects_of(s) as selects:
                BasicReportRepository(s).run(scope, "won_this_month", today=TODAY)
        assert len(selects) == 2, "\n\n".join(selects)   # this month, last month

    def test_open_jobs_count_three_statuses_and_keep_the_unassigned(self, world):
        engine, scope, _ = world
        with Session(engine) as s:
            out = BasicReportRepository(s).run(scope, "open_jobs_by_tech", today=TODAY)
        assert out["headline"]["value"] == 3
        assert any(row["label_th"] == "ยังไม่มอบหมาย" and row["value"] == 1 for row in out["rows"])

    def test_outstanding_splits_overdue_from_not_yet_due(self, world):
        engine, scope, _ = world
        with Session(engine) as s:
            out = BasicReportRepository(s).run(scope, "outstanding_invoices", today=TODAY)
        rows = {row["key"]: row for row in out["rows"]}
        assert Decimal(str(rows["overdue"]["value"])) == Decimal("10000")
        assert Decimal(str(rows["not_due"]["value"])) == Decimal("5000")
        assert Decimal(str(out["headline"]["value"])) == Decimal("15000")

    def test_satisfaction_averages_only_answered_forms(self, world):
        engine, scope, _ = world
        with Session(engine) as s:
            out = BasicReportRepository(s).run(scope, "satisfaction_avg", today=TODAY)
        assert out["headline"]["value"] == 2.0          # (3 + 1) / 2, not / 3
        assert out["rows"][0]["count"] == 2

    def test_an_unknown_key_is_refused(self, world):
        engine, scope, _ = world
        with Session(engine) as s:
            with pytest.raises(ValueError, match="unknown report"):
                BasicReportRepository(s).run(scope, "ยอดขายของคู่แข่ง", today=TODAY)


class TestTenantIsolation:
    #: What each shop's own rows add up to, report by report. Every pair
    #: differs, so a leak in either direction changes a number.
    MINE_AND_THEIRS = {
        "pipeline_value": (Decimal("139000"), Decimal("500000")),
        "won_this_month": (Decimal("99000"), Decimal("500000")),
        "open_jobs_by_tech": (Decimal("3"), Decimal("2")),
        "outstanding_invoices": (Decimal("15000"), Decimal("20000")),
        "satisfaction_avg": (Decimal("2"), Decimal("3")),
    }

    def test_every_one_of_the_five_answers_only_about_its_own_shop(self, world):
        engine, scope_a, scope_b = world
        assert set(self.MINE_AND_THEIRS) == set(REPORT_KEYS)
        with Session(engine) as s:
            repo = BasicReportRepository(s)
            for key, (mine, theirs) in self.MINE_AND_THEIRS.items():
                a = Decimal(str(repo.run(scope_a, key, today=TODAY)["headline"]["value"]))
                b = Decimal(str(repo.run(scope_b, key, today=TODAY)["headline"]["value"]))
                assert a == mine, key
                assert b == theirs, key

    def test_the_other_shops_rows_never_appear_by_name(self, world):
        """A number that happens to match is not proof; the rows must not
        carry the neighbour's work either."""
        engine, scope_a, scope_b = world
        with Session(engine) as s:
            repo = BasicReportRepository(s)
            mine = repo.run(scope_a, "open_jobs_by_tech", today=TODAY)
            theirs = repo.run(scope_b, "open_jobs_by_tech", today=TODAY)
        assert sum(row["count"] for row in mine["rows"]) == 3
        assert sum(row["count"] for row in theirs["rows"]) == 2
        assert not ({row["key"] for row in mine["rows"]}
                    & {row["key"] for row in theirs["rows"]})


class TestWhoTheJobsBelongTo:
    """The names on report 3. They used to be fetched one technician at a
    time (two `session.get` each); they are one query for the report now,
    and these pin the answers that rewrite must keep giving."""

    @pytest.fixture
    def jobs(self, migrated_db):
        tag = uuid.uuid4().hex[:6]
        uid, stranger_uid = f"CHN-21CW{tag}A", f"CHN-21CW{tag}B"
        with Session(migrated_db) as s:
            s.add(ChannIdentity(chann_uid=uid, line_user_id=f"line-{uid}",
                                primary_role="technician", display_name="ช่างเอก"))
            s.add(ChannIdentity(chann_uid=stranger_uid, line_user_id=f"line-{stranger_uid}",
                                primary_role="technician", display_name="ช่างของอีกร้าน"))
            s.commit()
        with Session(migrated_db) as s:
            reg = RegistrationRepository(s)
            mine = reg.create_license(company_name=f"Who A {tag}", created_by_chann_uid=uid)
            other = reg.create_license(company_name=f"Who B {tag}",
                                       created_by_chann_uid=stranger_uid)
            ids = (mine.id, other.id)
            s.commit()
        scope = TenantScope(license_id=ids[0])
        with Session(migrated_db) as s:
            customer = CustomerRepository(s).create(
                scope, first_name="ลูกค้า", last_name="ช่าง", phone=f"087{tag[:7]}")
            s.flush()
            member = LicenseMember(id=uuid.uuid4(), license_id=ids[0], chann_uid=uid,
                                   role="technician", channel="technician")
            stranger = LicenseMember(id=uuid.uuid4(), license_id=ids[1],
                                     chann_uid=stranger_uid, role="technician",
                                     channel="technician")
            team = TechnicianTeam(id=uuid.uuid4(), license_id=ids[0], team_name="ทีมเหนือ")
            s.add_all([member, stranger, team])
            s.flush()
            refs = {"member": member.id, "team": team.id, "stranger": stranger.id,
                    "nobody": uuid.uuid4(), "unassigned": None}
            for ref in refs.values():
                s.add(ServiceTicket(
                    id=uuid.uuid4(), license_id=ids[0],
                    ticket_number=f"T-{uuid.uuid4().hex[:6]}", issue_description="แอร์ไม่เย็น",
                    status="open", assigned_to_ref=ref, contact_id=customer.id))
            s.commit()
        return migrated_db, scope, refs, uid

    def test_a_person_a_team_and_a_stranger_each_read_correctly(self, jobs):
        engine, scope, refs, _uid = jobs
        with Session(engine) as s:
            out = BasicReportRepository(s).run(scope, "open_jobs_by_tech", today=TODAY)
        rows = {row["key"]: row for row in out["rows"]}
        assert out["headline"]["value"] == 5
        assert rows[str(refs["member"])]["label_th"] == "ช่างเอก"
        assert rows[str(refs["team"])]["label_th"] == "ทีม ทีมเหนือ"
        assert rows[str(refs["team"])]["label_en"] == "Team ทีมเหนือ"
        assert rows["unassigned"]["label_th"] == "ยังไม่มอบหมาย"
        # Neither the neighbour's member nor an id that is nothing at all
        # may borrow a name; both keep the row and show the id.
        assert rows[str(refs["stranger"])]["label_th"] == str(refs["stranger"])[:8]
        assert rows[str(refs["nobody"])]["label_th"] == str(refs["nobody"])[:8]

    def test_the_names_cost_one_query_no_matter_how_many_technicians(self, jobs):
        """The defect this replaced: two `session.get` per distinct ref,
        which was nine queries for these five rows."""
        engine, scope, _refs, _uid = jobs
        with Session(engine) as s:
            with selects_of(s) as selects:
                BasicReportRepository(s).run(scope, "open_jobs_by_tech", today=TODAY)
        assert len(selects) == 2, "\n\n".join(selects)   # the tickets, then the names
