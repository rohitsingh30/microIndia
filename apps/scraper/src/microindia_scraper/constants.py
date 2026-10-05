"""Dataset capture constants. Every follower band lives here; other modules import them."""

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

# Bands shown to users and used for peer comparison (engagement vs creators of similar size).
DISPLAY_BANDS = ((0, 10_000, "1K–10K"), (10_000, 50_000, "10K–50K"), (50_000, 100_000, "50K–100K"),
                 (100_000, 10**9, "100K+"))
# Size slices the sourcer's random exploration focus picks from.
EXPLORATION_BANDS = ((500, 5_000, "500–5K"), (5_000, 20_000, "5K–20K"), (20_000, 100_000, "20K–100K"),
                     (100_000, 1_000_000, "100K–1M"))
