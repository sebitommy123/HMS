from datapro_core.trino_client import _quote_ident, _quote_str


def test_simple_identifier():
    assert _quote_ident("postgresql") == '"postgresql"'


def test_identifier_with_internal_quote_is_escaped():
    assert _quote_ident('weird"name') == '"weird""name"'


def test_simple_string():
    assert _quote_str("jdbc:postgresql://h/db") == "'jdbc:postgresql://h/db'"


def test_string_with_single_quote_is_escaped():
    assert _quote_str("o'malley") == "'o''malley'"


def test_empty_string():
    assert _quote_str("") == "''"
