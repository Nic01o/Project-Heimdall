"""Tests for alarmclock.modules.settings_types — validate_against_schema."""

import pytest
from alarmclock.modules.settings_types import (
    FIELD_TYPES,
    SettingsValidationError,
    validate_against_schema,
)


# -- Schema helpers -----------------------------------------------------------

def _int_schema():
    return {"volume": {"type": "int", "min": 0, "max": 100}}

def _float_schema():
    return {"brightness": {"type": "float", "min": 0.0, "max": 1.0}}

def _bool_schema():
    return {"active": {"type": "bool"}}

def _string_schema():
    return {"name": {"type": "string"}}

def _select_schema():
    return {"color": {"type": "select", "options": ["red", "green", "blue"]}}

def _multiselect_schema():
    return {"days": {"type": "multiselect", "options": ["mon", "tue", "wed", "thu", "fri"]}}


# -- int -----------------------------------------------------------------------

class TestIntValidation:
    def test_valid_int(self):
        result = validate_against_schema({"volume": 50}, _int_schema())
        assert result == {"volume": 50}

    def test_rejects_bool_as_int(self):
        with pytest.raises(SettingsValidationError, match="must be an int"):
            validate_against_schema({"volume": True}, _int_schema())

    def test_rejects_string_as_int(self):
        with pytest.raises(SettingsValidationError, match="must be an int"):
            validate_against_schema({"volume": "loud"}, _int_schema())

    def test_range_min_exceeded(self):
        with pytest.raises(SettingsValidationError, match=">= 0"):
            validate_against_schema({"volume": -1}, _int_schema())

    def test_range_max_exceeded(self):
        with pytest.raises(SettingsValidationError, match="<= 100"):
            validate_against_schema({"volume": 101}, _int_schema())

    def test_range_boundary_min(self):
        result = validate_against_schema({"volume": 0}, _int_schema())
        assert result == {"volume": 0}

    def test_range_boundary_max(self):
        result = validate_against_schema({"volume": 100}, _int_schema())
        assert result == {"volume": 100}


# -- float ---------------------------------------------------------------------

class TestFloatValidation:
    def test_valid_float(self):
        result = validate_against_schema({"brightness": 0.5}, _float_schema())
        assert result == {"brightness": 0.5}

    def test_int_accepted_as_float(self):
        result = validate_against_schema({"brightness": 1}, _float_schema())
        assert result == {"brightness": 1.0}

    def test_rejects_bool(self):
        with pytest.raises(SettingsValidationError, match="must be a number"):
            validate_against_schema({"brightness": False}, _float_schema())

    def test_range_min_exceeded(self):
        with pytest.raises(SettingsValidationError, match=">= 0.0"):
            validate_against_schema({"brightness": -0.1}, _float_schema())

    def test_range_max_exceeded(self):
        with pytest.raises(SettingsValidationError, match="<= 1.0"):
            validate_against_schema({"brightness": 1.5}, _float_schema())


# -- bool ----------------------------------------------------------------------

class TestBoolValidation:
    def test_valid_bool_true(self):
        result = validate_against_schema({"active": True}, _bool_schema())
        assert result == {"active": True}

    def test_valid_bool_false(self):
        result = validate_against_schema({"active": False}, _bool_schema())
        assert result == {"active": False}

    def test_rejects_int_as_bool(self):
        with pytest.raises(SettingsValidationError, match="must be a bool"):
            validate_against_schema({"active": 1}, _bool_schema())

    def test_rejects_string_as_bool(self):
        with pytest.raises(SettingsValidationError, match="must be a bool"):
            validate_against_schema({"active": "yes"}, _bool_schema())


# -- string / password / color -------------------------------------------------

class TestStringValidation:
    def test_valid_string(self):
        schema = {"name": {"type": "string"}}
        result = validate_against_schema({"name": "hello"}, schema)
        assert result == {"name": "hello"}

    def test_rejects_int_as_string(self):
        schema = {"name": {"type": "string"}}
        with pytest.raises(SettingsValidationError, match="must be a string"):
            validate_against_schema({"name": 42}, schema)

    def test_valid_password(self):
        schema = {"secret": {"type": "password"}}
        result = validate_against_schema({"secret": "mypw"}, schema)
        assert result == {"secret": "mypw"}

    def test_valid_color(self):
        schema = {"theme": {"type": "color"}}
        result = validate_against_schema({"theme": "#ff0000"}, schema)
        assert result == {"theme": "#ff0000"}


# -- select --------------------------------------------------------------------

class TestSelectValidation:
    def test_valid_option(self):
        result = validate_against_schema({"color": "green"}, _select_schema())
        assert result == {"color": "green"}

    def test_invalid_option(self):
        with pytest.raises(SettingsValidationError, match="must be one of"):
            validate_against_schema({"color": "yellow"}, _select_schema())


# -- multiselect ---------------------------------------------------------------

class TestMultiselectValidation:
    def test_valid_subset(self):
        result = validate_against_schema({"days": ["mon", "wed"]}, _multiselect_schema())
        assert result == {"days": ["mon", "wed"]}

    def test_empty_list(self):
        result = validate_against_schema({"days": []}, _multiselect_schema())
        assert result == {"days": []}

    def test_contains_invalid_option(self):
        with pytest.raises(SettingsValidationError, match="must be a subset"):
            validate_against_schema({"days": ["mon", "sat"]}, _multiselect_schema())

    def test_rejects_non_list(self):
        with pytest.raises(SettingsValidationError, match="must be a subset"):
            validate_against_schema({"days": "mon"}, _multiselect_schema())


# -- general schema validation -------------------------------------------------

class TestSchemaValidation:
    def test_unknown_key_rejected(self):
        with pytest.raises(SettingsValidationError, match="unknown setting"):
            validate_against_schema({"typo": True}, {"active": {"type": "bool"}})

    def test_unknown_field_type_rejected(self):
        with pytest.raises(SettingsValidationError, match="unknown field type"):
            validate_against_schema({"x": 1}, {"x": {"type": "nonexistent"}})

    def test_empty_values_returns_empty(self):
        result = validate_against_schema({}, _bool_schema())
        assert result == {}

    def test_validated_subset_only(self):
        schema = {
            "volume": {"type": "int"},
            "active": {"type": "bool"},
        }
        result = validate_against_schema({"volume": 75}, schema)
        assert result == {"volume": 75}
        assert "active" not in result
