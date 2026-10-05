"""Dataset capture constants."""

CONTENT_CAPTURE_LIMIT = 18
METRIC_SAMPLE_LIMIT = 12
SCHEMA_VERSION = "profile-capture-v2-deep-research"

# Creator cohort follower band. Small creators who already run brand deals are
# valuable, so the floor is low; adjust here and every gate follows.
COHORT_MIN_FOLLOWERS = 1_000
COHORT_MAX_FOLLOWERS = 100_000
BAND_LABEL = "1K-100K"
# Scraping keeps some slack around the band: only profiles clearly outside it are
# dropped. The Find page applies the exact band as a search criterion.
SCRAPE_MIN_FOLLOWERS = 500
SCRAPE_MAX_FOLLOWERS = 1_000_000
