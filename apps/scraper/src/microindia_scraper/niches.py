"""One niche taxonomy for the whole system.

It drives three things:
- classification: which niche a creator belongs to (whole-word stems + hashtags);
- breadth sourcing: Instagram search queries per niche, combined with Indian cities;
- coverage: how many captured creators each niche has, so sourcing goes wide first
  (every niche to a baseline) and only then deep.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# niche -> (detection stems, search terms)
NICHES: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "food": (("food", "foodie", "recipe", "recipes", "cook", "cooking", "chef", "biryani", "kitchen", "street food",
              "thali", "snack", "dessert", "eats", "eating", "foodblogger", "homecook"),
             ("food blogger", "home chef", "street food", "food vlogger")),
    "baking": (("baker", "baking", "bake", "cake", "cakes", "cookies", "patisserie", "homebaker"),
               ("home baker", "cake artist")),
    "fashion": (("fashion", "style", "outfit", "ootd", "styling", "stylist", "lookbook", "streetwear"),
                ("fashion blogger", "fashion influencer", "stylist")),
    "ethnic wear": (("saree", "sarees", "lehenga", "kurti", "ethnic", "handloom", "anarkali"),
                    ("saree draping", "ethnic wear")),
    "beauty": (("makeup", "beauty", "mua", "makeupartist", "cosmetics", "nails", "nailart", "hairstyle", "hair"),
               ("makeup artist", "beauty blogger")),
    "skincare": (("skincare", "skin", "glowingskin", "derma", "acne", "sunscreen"),
                 ("skincare", "skincare tips")),
    "fitness": (("fitness", "workout", "gym", "fit", "bodybuilding", "crossfit", "calisthenics", "fitnesscoach"),
                ("fitness coach", "gym trainer", "fitness influencer")),
    "yoga & wellness": (("yoga", "wellness", "meditation", "mindfulness", "ayurveda", "holistic", "pilates"),
                        ("yoga teacher", "ayurveda", "wellness coach")),
    "nutrition & health": (("nutrition", "nutritionist", "dietitian", "diet", "health", "weightloss", "pcos", "doctor",
                            "physio", "physiotherapist", "dentist"),
                           ("nutritionist", "dietitian", "doctor")),
    "travel": (("travel", "traveller", "traveler", "trip", "wanderlust", "explore", "backpacking", "travelblogger"),
               ("travel blogger", "travel vlogger")),
    "trekking & outdoors": (("trek", "trekking", "hiking", "mountains", "camping", "himalayas", "adventure", "biking trip"),
                            ("trekking", "himalayan trek")),
    "tech": (("tech", "techie", "gadget", "gadgets", "smartphone", "unboxing", "techreview", "android", "iphone"),
             ("tech reviewer", "gadget review")),
    "coding & careers": (("coding", "developer", "programmer", "software", "datascience", "career", "jobs", "interview",
                          "placement", "resume", "linkedin"),
                         ("coding", "career tips", "placement tips")),
    "finance": (("finance", "investing", "investment", "stocks", "stockmarket", "trading", "mutualfunds", "money",
                 "personalfinance", "crypto", "sharemarket", "mutual fund", "mutual funds", "stock market",
                 "share market", "personal finance", "sip", "tax saving", "credit card"),
                ("personal finance", "stock market", "investing tips")),
    "business & startups": (("entrepreneur", "startup", "business", "founder", "marketing", "smallbusiness", "branding"),
                            ("entrepreneur", "startup founder", "small business")),
    "education": (("education", "teacher", "study", "studygram", "upsc", "neet", "jee", "exam", "learning", "tutor",
                   "english speaking"),
                  ("upsc", "teacher", "study tips")),
    "parenting": (("mom", "mother", "momlife", "parenting", "mommy", "baby", "kids", "toddler", "dad", "dadlife", "momblogger"),
                  ("mom blogger", "parenting")),
    "comedy": (("comedy", "comedian", "funny", "memes", "standup", "sketch", "skits", "humour", "humor"),
               ("comedy", "standup comedian", "funny reels")),
    "dance": (("dance", "dancer", "choreographer", "choreography", "bharatanatyam", "kathak", "bollywood dance"),
              ("dancer", "choreographer", "kathak")),
    "music": (("music", "singer", "musician", "guitarist", "rapper", "producer", "cover", "carnatic", "vocalist"),
              ("singer", "musician", "rapper")),
    "art & design": (("art", "artist", "illustration", "illustrator", "painting", "sketch", "digitalart", "mandala",
                      "calligraphy", "designer", "graphicdesign"),
                     ("artist", "illustrator", "painter")),
    "photography": (("photography", "photographer", "photo", "portrait", "streetphotography", "wildlife", "drone"),
                    ("photographer", "wedding photographer")),
    "film & acting": (("actor", "actress", "acting", "filmmaker", "cinema", "shortfilm", "director", "theatre"),
                      ("actor", "filmmaker")),
    "gaming": (("gaming", "gamer", "bgmi", "esports", "streamer", "valorant", "freefire", "pubg"),
               ("gamer", "bgmi", "esports")),
    "auto": (("car", "cars", "bike", "bikes", "automobile", "motorcycle", "royalenfield", "superbike", "carreview",
              "motovlog"),
             ("car reviewer", "motovlogger", "bike rider")),
    "pets": (("pets", "pet", "dog", "dogs", "puppy", "cat", "cats", "dogsofinstagram", "petlover"),
             ("dog lover", "pet influencer")),
    "books": (("books", "bookstagram", "reader", "reading", "bookworm", "author", "poetry", "poet", "writer"),
              ("bookstagram", "poet", "author")),
    "home & decor": (("homedecor", "decor", "interior", "interiors", "homedesign", "organizing", "gardening", "plants",
                      "garden"),
                     ("home decor", "interior designer", "gardening")),
    "diy & crafts": (("diy", "crafts", "craft", "handmade", "resin", "crochet", "embroidery", "pottery"),
                     ("diy crafts", "handmade")),
    "weddings": (("wedding", "bride", "bridal", "weddingplanner", "mehendi", "mehndi", "weddingphotography"),
                 ("bridal makeup", "wedding planner", "mehendi artist")),
    "spirituality": (("spiritual", "spirituality", "devotional", "bhakti", "temple", "astrology", "tarot", "vastu"),
                     ("spiritual", "astrology")),
    "sports": (("cricket", "cricketer", "football", "athlete", "sports", "running", "runner", "marathon", "badminton",
                "kabaddi", "chess"),
               ("cricket", "athlete", "runner")),
    "sustainability": (("sustainable", "sustainability", "zerowaste", "ecofriendly", "climate", "thrift", "upcycling"),
                       ("sustainable living", "zero waste")),
    "farming & rural": (("farming", "farmer", "agriculture", "organic farming", "village", "rural", "dairy"),
                        ("farmer", "village life")),
    "law & public affairs": (("lawyer", "advocate", "law", "legal", "politics", "policy", "civic"),
                             ("lawyer", "legal tips")),
    "lifestyle & vlogs": (("lifestyle", "vlog", "vlogger", "dailyvlog", "dayinmylife", "minivlog"),
                          ("lifestyle blogger", "daily vlog")),
}

CITIES = ("delhi", "mumbai", "bangalore", "hyderabad", "chennai", "kolkata", "pune", "ahmedabad", "jaipur", "lucknow",
          "chandigarh", "kochi", "indore", "goa", "guwahati", "bhubaneswar", "surat", "coimbatore", "nagpur", "patna")

# How a place is written (lowercase alias) -> the one city name we show and filter on.
# The only city alias map in the system (profile_text uses it to place a creator).
CITY_ALIASES: Dict[str, str] = {
    "delhi": "Delhi", "new delhi": "Delhi", "dilli": "Delhi", "ncr": "Delhi NCR", "noida": "Delhi NCR", "gurgaon": "Delhi NCR", "gurugram": "Delhi NCR",
    "mumbai": "Mumbai", "bombay": "Mumbai", "thane": "Mumbai", "bangalore": "Bengaluru", "bengaluru": "Bengaluru",
    "hyderabad": "Hyderabad", "chennai": "Chennai", "kolkata": "Kolkata", "calcutta": "Kolkata", "pune": "Pune",
    "ahmedabad": "Ahmedabad", "jaipur": "Jaipur", "lucknow": "Lucknow", "chandigarh": "Chandigarh", "indore": "Indore",
    "kochi": "Kochi", "cochin": "Kochi", "trivandrum": "Thiruvananthapuram", "thiruvananthapuram": "Thiruvananthapuram",
    "goa": "Goa", "surat": "Surat", "nagpur": "Nagpur", "bhopal": "Bhopal", "coimbatore": "Coimbatore", "guwahati": "Guwahati",
    "bhubaneswar": "Bhubaneswar", "patna": "Patna", "vizag": "Visakhapatnam", "visakhapatnam": "Visakhapatnam",
    "mysore": "Mysuru", "mysuru": "Mysuru", "dehradun": "Dehradun", "amritsar": "Amritsar", "ludhiana": "Ludhiana",
    "kanpur": "Kanpur", "varanasi": "Varanasi", "madurai": "Madurai", "udaipur": "Udaipur", "jodhpur": "Jodhpur",
    "kerala": "Kerala", "punjab": "Punjab",
}


def _compile() -> Tuple["re.Pattern[str]", Dict[str, str], List[Tuple[str, str]]]:
    """One alternation over every stem (longest first) plus a stem->niche map: one scan per text."""
    owner: Dict[str, str] = {}
    for niche, (stems, _) in NICHES.items():
        for stem in stems:
            owner.setdefault(stem, niche)
    words = re.compile(r"(?<![a-z])(" + "|".join(sorted(map(re.escape, owner), key=len, reverse=True)) + r")(?![a-z])")
    tag_stems = sorted(((stem, niche) for stem, niche in owner.items() if " " not in stem and len(stem) >= 4),
                       key=lambda item: -len(item[0]))
    return words, owner, tag_stems


_WORDS, _OWNER, _TAG_STEMS = _compile()
_HASHTAG = re.compile(r"#([a-z0-9_]+)")


def niche_scores(text: str) -> Dict[str, int]:
    lowered = text.lower()
    scores = dict.fromkeys(NICHES, 0)
    for stem in _WORDS.findall(lowered):
        scores[_OWNER[stem]] += 1
    for tag in _HASHTAG.findall(lowered):  # #delhifoodie, #dogsofinstagram
        for stem, niche in _TAG_STEMS:
            if stem in tag:
                scores[niche] += 1
                break
    return scores


def primary_niche(text: str) -> Optional[str]:
    scores = niche_scores(text)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None


def queries_for(niche: str) -> List[str]:
    """Search queries for one niche: plain terms first, then term × city."""
    terms = NICHES[niche][1]
    # City × niche finds real local creators; bare "indian <niche>" mostly returns aggregator pages.
    return [f"{city} {term}" for city in CITIES for term in terms]
