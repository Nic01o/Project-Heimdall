import pytest
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from alarmclock.core.scheduler import Scheduler
from alarmclock.core.alarm import Weekday


class MockEventBus:
    def __init__(self):
        self.emitted = []

    async def emit(self, event_name, payload):
        self.emitted.append((event_name, payload))


@pytest.fixture
def mock_bus():
    return MockEventBus()


@pytest.fixture
def base_time():
    """Fixed timezone-aware datetime for deterministic tests."""
    # Friday 2023-10-27 at 10:00 UTC
    return datetime(2023, 10, 27, 10, 0, 0, tzinfo=ZoneInfo("UTC"))


@pytest.fixture
def scheduler(mock_bus, base_time):
    """Create a Scheduler with injected time function for testing."""
    return Scheduler(
        bus=mock_bus,
        timezone="UTC",
        now=lambda: base_time
    )


# -- Core calculation tests --------------------------------------------------

def test_next_date_for_weekday_basic(scheduler):
    """Test basic calculation of next occurrence without reference time."""
    date_result = scheduler._next_date_for_weekday(
        day=Weekday.MONDAY,
        after=scheduler._now(),
        reference_time=None
    )
    assert date_result == datetime(2023, 10, 30).date()


def test_next_date_with_past_reference(scheduler):
    """Test calculation when target time has already passed today."""
    mock_now = datetime(2023, 10, 30, 10, 0, 0, tzinfo=ZoneInfo("UTC"))
    local_scheduler = Scheduler(bus=scheduler.bus, timezone="UTC", now=lambda: mock_now)
    
    date_result = local_scheduler._next_date_for_weekday(
        day=Weekday.MONDAY,
        after=mock_now,
        reference_time=time(8, 0)
    )
    # Should roll to next week since it's past the time today (10 AM > 8 AM)
    assert date_result == datetime(2023, 11, 6).date()


def test_get_alarm_reference_date(scheduler):
    """Test finding the reference date for master toggle."""
    scheduler.create_group(frozenset([Weekday.MONDAY]), time(8, 0))
    ref = scheduler.get_alarm_reference_date()
    assert ref == datetime(2023, 10, 30).date()


def test_skip_next_alarm(scheduler):
    """Test skipping the next alarm."""
    scheduler.create_group(frozenset([Weekday.MONDAY]), time(8, 0))
    
    pre_date = scheduler.get_alarm_reference_date()
    assert pre_date == datetime(2023, 10, 30).date()
    result = scheduler.skip_next_alarm()
    assert isinstance(result, bool)


def test_create_group_conflict(scheduler):
    """Ensure conflict raises ValueError when week is already taken."""
    # Tag Monday with time 8:00 is newly created group
    scheduler.create_group(frozenset([Weekday.MONDAY]), time(8, 0))

    # Now try to create another conflicting group (same day)
    with pytest.raises(ValueError, match="already assigned"):
        scheduler.create_group(frozenset([Weekday.MONDAY]), time(9, 0))


async def try_to_assign_same_day_again():
    scheduler.create_group(frozenset([Weekday.TUESDAY]), time(9, 0)) # TUE not used yet. Try to assign again.
