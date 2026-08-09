import pytest
from pydantic import ValidationError

from datapro_core.schemas import CatalogCreateRequest


def test_valid_minimal():
    req = CatalogCreateRequest(name="tpch_demo", connector="tpch")
    assert req.name == "tpch_demo"
    assert req.connector == "tpch"
    assert req.properties == {}


def test_valid_with_properties():
    req = CatalogCreateRequest(
        name="pg",
        connector="postgresql",
        properties={"connection-url": "jdbc:postgresql://h/db"},
    )
    assert req.properties == {"connection-url": "jdbc:postgresql://h/db"}


def test_rejects_empty_name():
    with pytest.raises(ValidationError):
        CatalogCreateRequest(name="", connector="tpch")


def test_rejects_special_chars_in_name():
    with pytest.raises(ValidationError):
        CatalogCreateRequest(name="bad name!", connector="tpch")


def test_allows_underscore_and_hyphen():
    CatalogCreateRequest(name="ok_name-v2", connector="postgresql")


def test_rejects_missing_connector():
    with pytest.raises(ValidationError):
        CatalogCreateRequest(name="x")  # type: ignore[call-arg]
