"""Recorded-shape API payloads.

These are written to match the documented response shape of each API, not to
match what the parser happens to expect — that is the point. If a parser assumes
the wrong shape, the assertions in test_jmatch.py fail here rather than on the
user's first real run.
"""

# --- OpenAlex --------------------------------------------------------------

SOURCE_LANCET = {
    "id": "https://openalex.org/S49861241",
    "issn_l": "0140-6736",
    "issn": ["1474-547X", "0140-6736"],
    "display_name": "The Lancet",
    "host_organization_name": "Elsevier BV",
    "type": "journal",
    "apc_usd": 5800,
    "is_oa": False,
    "is_in_doaj": False,
    "is_core": True,
    "homepage_url": "https://www.sciencedirect.com/journal/the-lancet",
    "works_count": 412093,
    "cited_by_count": 8_100_000,
    "summary_stats": {"2yr_mean_citedness": 31.7, "h_index": 909, "i10_index": 51234},
    "counts_by_year": [
        {"year": 2025, "works_count": 2100, "cited_by_count": 210000},
        {"year": 2024, "works_count": 2050, "cited_by_count": 480000},
        {"year": 2023, "works_count": 1980, "cited_by_count": 520000},
        {"year": 2022, "works_count": 1900, "cited_by_count": 495000},
    ],
    "topics": [{"display_name": "Global Health"}, {"display_name": "Cardiology"}],
}

SOURCE_FARM = {
    "id": "https://openalex.org/S99999999",
    "issn_l": "2999-0001",
    "issn": ["2999-0001"],
    "display_name": "Global Journal of Advanced Clinical Insights",
    "host_organization_name": "Meridian Open Science Group",
    "type": "journal",
    "apc_usd": 1490,
    "is_oa": True,
    "is_in_doaj": False,
    "homepage_url": "https://example.org/gjaci",
    "works_count": 9100,
    "cited_by_count": 4200,
    "summary_stats": {"2yr_mean_citedness": 0.4, "h_index": 11, "i10_index": 40},
    "counts_by_year": [
        {"year": 2025, "works_count": 4100, "cited_by_count": 900},
        {"year": 2024, "works_count": 1800, "cited_by_count": 700},
        {"year": 2023, "works_count": 120, "cited_by_count": 90},
    ],
    "topics": [{"display_name": "Medicine (miscellaneous)"}],
}

# `works?...&group_by=primary_location.source.id`
GROUP_BY_SOURCE = {
    "meta": {"count": 1837, "groups_count": 3},
    "group_by": [
        {"key": "https://openalex.org/S49861241", "key_display_name": "The Lancet", "count": 41},
        {"key": "https://openalex.org/S99999999",
         "key_display_name": "Global Journal of Advanced Clinical Insights", "count": 17},
        {"key": "https://openalex.org/S1111", "key_display_name": "Tiny Cardiology Letters", "count": 1},
        {"key": "unknown", "key_display_name": None, "count": 22},
    ],
}

GROUP_BY_TYPE = {"group_by": [
    {"key": "article", "key_display_name": "article", "count": 720},
    {"key": "review", "key_display_name": "review", "count": 180},
    {"key": "editorial", "key_display_name": "editorial", "count": 100},
]}

GROUP_BY_COUNTRY = {"group_by": [
    {"key": "DE", "key_display_name": "Germany", "count": 300},
    {"key": "GB", "key_display_name": "United Kingdom", "count": 250},
    {"key": "US", "key_display_name": "United States", "count": 200},
    {"key": "IT", "key_display_name": "Italy", "count": 150},
    {"key": "unknown", "key_display_name": None, "count": 10},
]}

GROUP_BY_COUNTRY_NARROW = {"group_by": [
    {"key": "IN", "key_display_name": "India", "count": 780},
    {"key": "NG", "key_display_name": "Nigeria", "count": 120},
    {"key": "EG", "key_display_name": "Egypt", "count": 100},
]}

GROUP_BY_YEAR = {"group_by": [
    {"key": "2025", "key_display_name": "2025", "count": 900},
    {"key": "2024", "key_display_name": "2024", "count": 640},
    {"key": "2023", "key_display_name": "2023", "count": 300},
    {"key": "2022", "key_display_name": "2022", "count": 180},
    {"key": "2021", "key_display_name": "2021", "count": 120},
    {"key": "2020", "key_display_name": "2020", "count": 90},
]}

WORKS_SAMPLE = {"results": [
    {"biblio": {"first_page": "1201", "last_page": "1212"}, "referenced_works_count": 44, "type": "article"},
    {"biblio": {"first_page": "88", "last_page": "97"}, "referenced_works_count": 38, "type": "article"},
    {"biblio": {"first_page": None, "last_page": None}, "referenced_works_count": 52, "type": "review"},
    {"biblio": {"first_page": "e1", "last_page": "e9"}, "referenced_works_count": 61, "type": "article"},
]}

# `works?filter=doi:...` — the manuscript's own reference list
WORKS_BY_DOI = {"results": [
    {"id": "https://openalex.org/W1", "primary_location": {"source": {
        "id": "https://openalex.org/S49861241", "display_name": "The Lancet"}},
     "publication_year": 2023},
    {"id": "https://openalex.org/W2", "primary_location": {"source": {
        "id": "https://openalex.org/S49861241", "display_name": "The Lancet"}},
     "publication_year": 2021},
    {"id": "https://openalex.org/W3", "primary_location": {"source": {
        "id": "https://openalex.org/S2222", "display_name": "Circulation"}},
     "publication_year": 2014},
    {"id": "https://openalex.org/W4", "primary_location": {"source": None}, "publication_year": 2020},
]}

NEAREST_WORKS = {"results": [
    {"id": "https://openalex.org/W9", "doi": "https://doi.org/10.1056/NEJMoa2107038",
     "title": "Empagliflozin in Heart Failure with a Preserved Ejection Fraction",
     "publication_year": 2024, "cited_by_count": 2100,
     "primary_location": {"source": {"display_name": "New England Journal of Medicine"}}},
]}

# --- DOAJ ------------------------------------------------------------------

DOAJ_HIT = {"results": [{"bibjson": {
    "apc": {"has_apc": True, "max": [{"price": 2690, "currency": "EUR"}]},
    "license": [{"type": "CC BY"}],
    "editorial": {"review_process": [{"type": "Double anonymous peer review"}]},
    "plagiarism": {"detection": True},
    "preservation": {"has_preservation": True},
    "publication_time_weeks": 9,
}}]}

DOAJ_MISS = {"results": []}

# --- NCBI E-utilities (XML) ------------------------------------------------

ESEARCH_HIT = """<?xml version="1.0"?><eSearchResult><Count>1</Count>
<IdList><Id>101089123</Id></IdList></eSearchResult>"""

ESEARCH_EMPTY = """<?xml version="1.0"?><eSearchResult><Count>0</Count><IdList/></eSearchResult>"""

# nlmcatalog esummary nests Items inside a List Item — the parser must not be
# fooled into reading the list container's whitespace as the title.
ESUMMARY_MEDLINE = """<?xml version="1.0"?><eSummaryResult><DocSum><Id>101089123</Id>
  <Item Name="TitleMainList" Type="List">
    <Item Name="TitleMain" Type="Structure">
      <Item Name="Title" Type="String">The Lancet</Item>
    </Item>
  </Item>
  <Item Name="ISSN" Type="String">0140-6736</Item>
  <Item Name="currentindexingstatus" Type="String">Y</Item>
</DocSum></eSummaryResult>"""

ESUMMARY_NOT_MEDLINE = """<?xml version="1.0"?><eSummaryResult><DocSum><Id>101777777</Id>
  <Item Name="TitleMainList" Type="List">
    <Item Name="TitleMain" Type="Structure">
      <Item Name="Title" Type="String">Global Journal of Advanced Clinical Insights</Item>
    </Item>
  </Item>
  <Item Name="currentindexingstatus" Type="String">N</Item>
</DocSum></eSummaryResult>"""

ESEARCH_PMIDS = """<?xml version="1.0"?><eSearchResult><Count>3</Count>
<IdList><Id>39000001</Id><Id>39000002</Id><Id>39000003</Id></IdList></eSearchResult>"""


def _article(received, accepted, entrez):
    def d(status, ymd):
        y, m, dd = ymd
        return (f'<PubMedPubDate PubStatus="{status}"><Year>{y}</Year>'
                f'<Month>{m}</Month><Day>{dd}</Day></PubMedPubDate>')
    return ("<PubmedArticle><PubmedData><History>"
            + d("received", received) + d("accepted", accepted) + d("entrez", entrez)
            + "</History></PubmedData></PubmedArticle>")


EFETCH_NORMAL = ("<?xml version='1.0'?><PubmedArticleSet>"
                 + _article((2024, 1, 10), (2024, 4, 18), (2024, 5, 2))    # 99 d
                 + _article((2024, 2, 1), (2024, 4, 1), (2024, 4, 20))     # 60 d
                 + _article((2024, 3, 5), (2024, 7, 3), (2024, 7, 25))     # 120 d
                 + "<PubmedArticle><PubmedData><History/></PubmedData></PubmedArticle>"
                 + "</PubmedArticleSet>")

EFETCH_FAST = ("<?xml version='1.0'?><PubmedArticleSet>"
               + "".join(_article((2024, 5, 1), (2024, 5, 6), (2024, 5, 9)) for _ in range(12))
               + "</PubmedArticleSet>")
