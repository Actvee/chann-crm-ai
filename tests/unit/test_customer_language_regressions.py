"""Boundaries around the language fixes, including cases that must still act."""
from datetime import time
import pytest
from chann_app.services.chat import _command_like, _denies_repair_request, _is_continuation
from chann_app.services.thai_datetime import parse_thai_time


@pytest.mark.parametrize('message', ['ยังไม่ได้เช็คอิน T-2026-0001', 'อย่าเช็คอิน T-2026-0001',
 'ถ้าถึงหน้างาน T-2026-0001 ค่อยเช็คอิน', 'เช็คอิน T-2026-0001 หรือยัง', 'ลูกค้าบอกว่าเช็คอิน T-2026-0001'])
def test_information_is_not_checkin(message):
    assert not _command_like(message, ('เช็คอิน', 'ถึงหน้างาน'))


@pytest.mark.parametrize('message', ['เช็คอิน T-2026-0001', 'ถึงหน้างาน T-2026-0001 แล้วครับ'])
def test_affirmative_checkin_still_works(message):
    assert _command_like(message, ('เช็คอิน', 'ถึงหน้างาน'))


@pytest.mark.parametrize('message', ['แอร์ไม่เย็น', 'เครื่องซักผ้าไม่ทำงาน', 'พัดลมไม่หมุน', 'แอร์น้ำไม่ไหล'])
def test_negative_symptom_is_still_a_fault(message):
    assert not _denies_repair_request(message)


@pytest.mark.parametrize('message, expected', [('บ่ายสอง', time(14)), ('บ่ายสองครึ่ง', time(14,30)),
 ('บ่าย๓ครึ่ง', time(15,30)), ('สิบเอ็ดโมง', time(11)), ('๑๔:๓๐', time(14,30)),
 ('บ่ายเก้า', None), ('25:00', None), ('บ่าย', time(13))])
def test_clock_words_and_invalid_hours(message, expected):
    assert parse_thai_time(message) == expected


def test_new_customer_name_does_not_merge_with_old_pending_customer():
    pending={'action':'create','entity':'customer','fields':{'first_name':'สมชาย'},'missing':['phone']}
    assert not _is_continuation(pending, {'action':'create','entity':'customer','fields':{'first_name':'สมหญิง','phone':'0899999999'}})
    assert not _is_continuation(pending, {'action':'read','entity':'customer','fields':{'phone':'0899999999'}})
    assert _is_continuation(pending, {'action':'create','entity':'customer','fields':{'address':'99/1 ถนนสุขุมวิท'}})
