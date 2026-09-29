"""Auto locale from the proxy country: fill defaults, never overwrite a choice.

The language fill runs only on an explicit proxy check that comes back
reachable with a known country, and only while the profile still has the
default language settings. A hand-set language, an unknown country, or a
dead proxy must leave the settings exactly as they were.
"""

from camoufox_pm.core import locale_map


def test_known_countries_map_to_inclusive_lists():
    languages, locale = locale_map.languages_for_country("DE")
    assert languages[0] == "de-DE" and locale == "de_DE"
    languages, locale = locale_map.languages_for_country("jp")
    assert languages == ["ja-JP", "ja", "en"] and locale == "ja_JP"


def test_multilingual_countries_stay_inclusive():
    languages, _locale = locale_map.languages_for_country("CH")
    assert "de-CH" in languages and "fr-CH" in languages


def test_unknown_or_missing_countries_keep_current_settings():
    assert locale_map.languages_for_country("XX") is None
    assert locale_map.languages_for_country("") is None
    assert locale_map.languages_for_country(None) is None


def test_only_the_out_of_the_box_settings_count_as_default():
    assert locale_map.is_default_language(["en-US", "en"], None) is True
    assert locale_map.is_default_language(["de-DE", "de", "en"], "de_DE") is False
    assert locale_map.is_default_language(["en-US", "en"], "en_US") is False
    assert locale_map.is_default_language(["en-US", "en"], "de_DE") is False
