"""Match a profile's browser language to its proxy's country.

The proxy check already reports where traffic exits (ISO country from the
local GeoIP database); this table turns that country into the language list
and locale a browser from there would normally send. It is a creation-time
default and an explicit user action only:

* fill only when the profile still has the default ``["en-US", "en"]`` /
  ``locale=None`` — a hand-set language is never overwritten;
* unknown countries return ``None`` (leave the settings alone) rather than
  guessing;
* multilingual countries get inclusive lists;
* a failed or unreachable check never touches the settings (the caller only
  calls this with a country in hand).

Deliberately separate from ``fingerprint_generator``'s region maps: those are
keyed by region *names* (``us``/``uk``/``russia``…) for synthetic generation,
while GeoIP reports ISO codes (``US``/``GB``/``RU``…).
"""

from __future__ import annotations

DEFAULT_LANGUAGES: list[str] = ["en-US", "en"]
DEFAULT_LOCALE: str | None = None

# ISO 3166-1 alpha-2 → (Accept-Language list, Camoufox locale).
LOCALE_FOR_COUNTRY: dict[str, tuple[list[str], str]] = {
    "US": (["en-US", "en"], "en_US"),
    "GB": (["en-GB", "en"], "en_GB"),
    "CA": (["en-CA", "en", "fr-CA", "fr"], "en_CA"),
    "AU": (["en-AU", "en"], "en_AU"),
    "DE": (["de-DE", "de", "en"], "de_DE"),
    "AT": (["de-AT", "de", "en"], "de_AT"),
    "CH": (["de-CH", "fr-CH", "it-CH", "de", "fr", "it", "en"], "de_CH"),
    "FR": (["fr-FR", "fr", "en"], "fr_FR"),
    "BE": (["fr-BE", "nl-BE", "de-BE", "fr", "nl", "en"], "fr_BE"),
    "ES": (["es-ES", "es", "en"], "es_ES"),
    "MX": (["es-MX", "es", "en"], "es_MX"),
    "AR": (["es-AR", "es", "en"], "es_AR"),
    "IT": (["it-IT", "it", "en"], "it_IT"),
    "PT": (["pt-PT", "pt", "en"], "pt_PT"),
    "BR": (["pt-BR", "pt", "en"], "pt_BR"),
    "NL": (["nl-NL", "nl", "en"], "nl_NL"),
    "SE": (["sv-SE", "sv", "en"], "sv_SE"),
    "NO": (["nb-NO", "nb", "no", "en"], "nb_NO"),
    "DK": (["da-DK", "da", "en"], "da_DK"),
    "FI": (["fi-FI", "fi", "sv", "en"], "fi_FI"),
    "PL": (["pl-PL", "pl", "en"], "pl_PL"),
    "CZ": (["cs-CZ", "cs", "en"], "cs_CZ"),
    "GR": (["el-GR", "el", "en"], "el_GR"),
    "RU": (["ru-RU", "ru", "en-US"], "ru_RU"),
    "UA": (["uk-UA", "uk", "ru", "en"], "uk_UA"),
    "TR": (["tr-TR", "tr", "en"], "tr_TR"),
    "IL": (["he-IL", "he", "en"], "he_IL"),
    "AE": (["ar-AE", "ar", "en"], "ar_AE"),
    "CN": (["zh-CN", "zh", "en"], "zh_CN"),
    "TW": (["zh-TW", "zh", "en"], "zh_TW"),
    "HK": (["zh-HK", "zh", "en", "zh-CN"], "zh_HK"),
    "SG": (["en-SG", "zh-SG", "ms-SG", "ta-SG", "en"], "en_SG"),
    "JP": (["ja-JP", "ja", "en"], "ja_JP"),
    "KR": (["ko-KR", "ko", "en"], "ko_KR"),
    "IN": (["en-IN", "hi-IN", "hi", "en"], "en_IN"),
    "TH": (["th-TH", "th", "en"], "th_TH"),
    "VN": (["vi-VN", "vi", "en"], "vi_VN"),
    "ID": (["id-ID", "id", "en"], "id_ID"),
    "MY": (["ms-MY", "ms", "en"], "ms_MY"),
    "PH": (["en-PH", "fil-PH", "fil", "en"], "en_PH"),
    "ZA": (["en-ZA", "en", "af"], "en_ZA"),
}


def languages_for_country(country: str | None) -> tuple[list[str], str] | None:
    """The (languages, locale) for an ISO country code, or ``None`` to keep."""
    if not country:
        return None
    return LOCALE_FOR_COUNTRY.get(country.strip().upper())


def is_default_language(languages: list[str] | None, locale: str | None) -> bool:
    """Whether the profile still has the out-of-the-box language settings."""
    return (languages or []) == DEFAULT_LANGUAGES and locale is None
