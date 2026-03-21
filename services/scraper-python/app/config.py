from __future__ import annotations

import os
from pathlib import Path

SERVICE_NAME = "catalog-python"
SERVICE_HOST = os.environ.get("AIXSTORE_PYTHON_HOST", "127.0.0.1")
SERVICE_PORT = int(os.environ.get("AIXSTORE_PYTHON_PORT", "8790"))

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "catalog.db"
IMAGE_CACHE_DIR = DATA_DIR / "session-images"
FIXTURES_DIR = BASE_DIR / "tests" / "fixtures"

DEFAULT_BOOTSTRAP_COUNT = 100
DEFAULT_PAGE_SIZE = 20
DEFAULT_SEARCH_PAGE_SIZE = 20
QUERY_COOLDOWN_SECONDS = 2
QUERY_POLL_SECONDS = 2
QUERY_RETRY_ATTEMPTS = 3
REQUEST_TIMEOUT_SECONDS = 20
PROVIDER_BLOCK_COOLDOWN_SECONDS = 300
AUTH_TOKEN_TTL_SECONDS = int(os.environ.get("AIXSTORE_AUTH_TOKEN_TTL_SECONDS", "2592000"))
PASSWORD_HASH_ITERATIONS = int(os.environ.get("AIXSTORE_PASSWORD_HASH_ITERATIONS", "240000"))
CATEGORY_STRICT_SCORE_THRESHOLD = 4.0
CATEGORY_SCORE_MARGIN = 2.0
STRONG_SEARCH_RESULT_THRESHOLD = 8
MAX_QUERY_VARIANTS = 5

PROXY_URL = os.environ.get("AIXSTORE_PROXY_URL", "").strip() or None
TARGET_PRIMARY_STORE_ID = os.environ.get("AIXSTORE_TARGET_STORE_ID", "1056")
TARGET_PURCHASABLE_STORE_IDS = os.environ.get(
    "AIXSTORE_TARGET_STORE_IDS",
    "1056,1508,2158,1474,1845",
)
TARGET_STORE_ZIP = os.environ.get("AIXSTORE_TARGET_STORE_ZIP", "13850")
TARGET_STORE_STATE = os.environ.get("AIXSTORE_TARGET_STORE_STATE", "NY")
TARGET_STORE_LATITUDE = os.environ.get("AIXSTORE_TARGET_STORE_LATITUDE", "42.094186")
TARGET_STORE_LONGITUDE = os.environ.get("AIXSTORE_TARGET_STORE_LONGITUDE", "-76.001181")
TARGET_TIMEZONE = os.environ.get("AIXSTORE_TARGET_TIMEZONE", "America/New_York")
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET")

CATEGORY_CONFIG: dict[str, dict[str, object]] = {
    "electronics": {
        "name": "Electronics",
        "icon": "phone-portrait-outline",
        "color": "#3884FF",
        "keywords": ["tv", "headphone", "tablet", "speaker", "monitor", "camera", "laptop", "watch", "smartwatch", "mac", "iphone"],
        "seed_queries": [
            "wireless headphones",
            "smart watch",
            "bluetooth speaker",
            "4k tv",
            "tablet",
            "camera",
            "iphone",
            "macbook",
        ],
        "section_queries": ["wireless headphones", "4k tv", "bluetooth speaker", "tablet", "laptop", "iphone", "macbook"],
        "include_terms": [
            "laptop",
            "tablet",
            "headphone",
            "speaker",
            "camera",
            "tv",
            "television",
            "monitor",
            "chromebook",
            "notebook",
            "pc",
            "computer",
            "gaming pc",
            "watch",
            "smartwatch",
            "apple watch",
            "macbook",
            "iphone",
            "mac",
            "imac",
            "apple computer",
        ],
        "exclude_terms": ["hoodie", "dress", "cotton", "serum", "chair", "bedding", "vitamin", "supplement"],
        "strong_phrases": [
            "4k tv",
            "bluetooth speaker",
            "gaming laptop",
            "wireless headphones",
            "smart watch",
            "laptop",
            "notebook computer",
            "desktop computer",
            "all-in-one pc",
            "external hard drive",
            "apple watch",
            "iphone",
            "macbook",
            "watch",
            "mac",
        ],
        "query_bonus_terms": ["electronics", "tech", "device", "gadget"],
    },
    "food": {
        "name": "Food",
        "icon": "restaurant-outline",
        "color": "#F59E0B",
        "keywords": [
            "snack",
            "chips",
            "cereal",
            "pasta",
            "rice",
            "sauce",
            "soup",
            "chocolate",
            "cookies",
            "coffee",
            "tea",
            "granola",
            "protein bar",
            "nuts",
        ],
        "seed_queries": [
            "protein bars",
            "pasta",
            "breakfast cereal",
            "potato chips",
            "coffee beans",
            "chocolate candy",
            "rice",
            "granola",
        ],
        "section_queries": [
            "protein bars",
            "pasta",
            "breakfast cereal",
            "potato chips",
            "coffee beans",
            "chocolate candy",
            "olive oil",
            "rice",
            "granola",
        ],
        "include_terms": [
            "food",
            "grocery",
            "snack",
            "chips",
            "cereal",
            "pasta",
            "rice",
            "sauce",
            "soup",
            "chocolate",
            "cookies",
            "coffee",
            "tea",
            "granola",
            "protein bar",
            "nuts",
            "crackers",
            "popcorn",
            "candy",
            "beverage",
        ],
        "exclude_terms": [
            "coffee maker",
            "air fryer",
            "microwave",
            "plate",
            "chair",
            "hoodie",
            "speaker",
            "tablet",
            "shampoo",
            "toy",
            "pet",
        ],
        "strong_phrases": [
            "protein bars",
            "breakfast cereal",
            "potato chips",
            "coffee beans",
            "chocolate candy",
            "olive oil",
            "instant noodles",
            "pasta sauce",
        ],
        "query_bonus_terms": ["food", "grocery", "snacks", "pantry", "beverage"],
    },
    "fashion": {
        "name": "Fashion",
        "icon": "shirt-outline",
        "color": "#F97316",
        "keywords": ["shoe", "hoodie", "jacket", "dress", "backpack", "bag", "sneaker"],
        "seed_queries": ["running shoes", "hoodie", "backpack", "jacket"],
        "section_queries": ["hoodie", "women's jacket", "men's sneakers", "dress", "backpack"],
        "include_terms": [
            "hoodie",
            "jacket",
            "dress",
            "shirt",
            "jeans",
            "sneaker",
            "shoe",
            "backpack",
            "pullover",
            "sweatshirt",
            "fleece",
            "fashion",
        ],
        "exclude_terms": ["laptop", "tablet", "vitamin", "serum", "desk", "chair", "speaker", "television", "pc"],
        "strong_phrases": ["running shoes", "lace up hoodie", "graphic hoodie", "women's jacket", "men's sneakers"],
        "query_bonus_terms": ["fashion", "clothes", "clothing", "apparel", "wear"],
    },
    "beauty": {
        "name": "Beauty",
        "icon": "sparkles-outline",
        "color": "#EC4899",
        "keywords": ["serum", "moisturizer", "lip", "shampoo", "skin", "beauty", "cleanser"],
        "seed_queries": ["face moisturizer", "vitamin c serum", "lip balm", "shampoo"],
        "section_queries": ["face moisturizer", "vitamin c serum", "lip balm", "cleanser", "shampoo"],
        "include_terms": [
            "serum",
            "moisturizer",
            "cleanser",
            "lip",
            "shampoo",
            "conditioner",
            "beauty",
            "skin",
            "facial",
            "cream",
        ],
        "exclude_terms": ["laptop", "chair", "hoodie", "sneaker", "speaker", "desk", "toy"],
        "strong_phrases": ["vitamin c serum", "face moisturizer", "water cream", "lip balm"],
        "query_bonus_terms": ["beauty", "skincare", "skin care", "makeup"],
    },
    "home": {
        "name": "Home",
        "icon": "home-outline",
        "color": "#10B981",
        "keywords": ["lamp", "coffee", "bedding", "air fryer", "chair", "desk", "kitchen", "rug", "carpet", "bed", "mattress", "microwave", "appliance", "clock"],
        "seed_queries": ["air fryer", "desk lamp", "coffee maker", "bedding set", "area rug", "bed frame", "microwave oven", "wall clock"],
        "section_queries": ["air fryer", "desk lamp", "coffee maker", "bedding set", "storage organizer", "area rug", "bed frame", "microwave oven", "wall clock"],
        "include_terms": [
            "air fryer",
            "lamp",
            "coffee maker",
            "bedding",
            "desk lamp",
            "kitchen",
            "storage",
            "organizer",
            "chair",
            "office chair",
            "rug",
            "carpet",
            "bed",
            "mattress",
            "bed frame",
            "platform bed",
            "microwave",
            "microwave oven",
            "countertop microwave",
            "over the range microwave",
            "kitchen appliance",
            "clock",
            "wall clock",
            "alarm clock",
            "desk clock",
        ],
        "exclude_terms": ["laptop", "hoodie", "serum", "doll", "speaker", "tablet", "vitamin", "smartwatch"],
        "strong_phrases": [
            "air fryer",
            "desk lamp",
            "coffee maker",
            "bedding set",
            "office chair",
            "area rug",
            "carpet runner",
            "microwave",
            "microwave oven",
            "countertop microwave",
            "over the range microwave",
            "clock",
            "wall clock",
            "alarm clock",
            "bed frame",
            "bed",
            "platform bed",
            "upholstered bed",
        ],
        "query_bonus_terms": ["kitchen", "furniture", "decor", "household", "appliance", "appliances"],
    },
    "toys": {
        "name": "Toys",
        "icon": "game-controller-outline",
        "color": "#8B5CF6",
        "keywords": ["lego", "board game", "toy", "plush", "rc car", "doll"],
        "seed_queries": ["lego set", "board game", "rc car", "plush toy"],
        "section_queries": ["lego set", "board game", "rc car", "plush toy", "doll"],
        "include_terms": ["lego", "toy", "board game", "plush", "doll", "ride-on", "blocks", "game"],
        "exclude_terms": ["laptop", "serum", "hoodie", "vitamin", "coffee maker", "chair"],
        "strong_phrases": ["lego set", "board game", "plush toy", "rc car"],
        "query_bonus_terms": ["toys", "kids", "play"],
    },
    "sports": {
        "name": "Sports",
        "icon": "fitness-outline",
        "color": "#14B8A6",
        "keywords": ["yoga", "dumbbell", "fitness", "sports", "mat", "golf", "club"],
        "seed_queries": ["yoga mat", "dumbbells"],
        "section_queries": ["yoga mat", "dumbbells", "golf clubs"],
        "include_terms": ["yoga", "dumbbell", "fitness", "sports", "golf", "club", "exercise"],
        "exclude_terms": ["hoodie", "serum", "laptop", "bedding"],
        "strong_phrases": ["yoga mat", "golf clubs", "dumbbells"],
        "query_bonus_terms": ["sports", "fitness", "workout"],
    },
    "others": {
        "name": "Others",
        "icon": "grid-outline",
        "color": "#64748B",
        "keywords": [],
        "seed_queries": ["office chair", "storage organizer"],
        "section_queries": ["office chair", "storage organizer"],
        "include_terms": [],
        "exclude_terms": [],
        "strong_phrases": [],
        "query_bonus_terms": [],
    },
}

CORE_CATEGORY_IDS = ["electronics", "food", "fashion", "beauty", "home", "toys"]

PROVIDER_PRIORITY = [
    "target_requests",
    "walmart_requests",
    "amazon_requests",
    "amazon_playwright",
]

FAST_PROVIDER_PRIORITY = [
    "target_requests",
    "walmart_requests",
]

SEARCH_FALLBACK_PROVIDER_PRIORITY = [
    "amazon_requests",
]

USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

SEARCH_SYNONYM_MAP = {
    "microwave": ["microwave oven", "countertop microwave", "kitchen microwave"],
    "microwaves": ["microwave oven", "countertop microwave", "kitchen microwave"],
    "iphone": ["apple iphone smartphone", "iphone unlocked phone", "ios smartphone", "apple phone"],
    "iphones": ["apple iphone smartphone", "iphone unlocked phone", "ios smartphone", "apple phone"],
    "mac": ["macbook laptop", "apple computer", "imac desktop", "apple macbook"],
    "macbook": ["apple laptop", "mac laptop", "apple computer"],
    "watch": ["smart watch", "wrist watch", "digital watch", "apple watch"],
    "watches": ["smart watch", "wrist watch", "digital watch", "apple watch"],
    "carpet": ["area rug", "floor rug", "rug", "carpet runner"],
    "carpets": ["area rug", "floor rug", "rug", "carpet runner"],
    "bed": ["bed frame", "platform bed", "mattress", "upholstered bed"],
    "beds": ["bed frame", "platform bed", "mattress", "upholstered bed"],
    "tv": ["television", "4k tv"],
    "television": ["tv", "4k tv"],
    "hoodie": ["sweatshirt", "pullover hoodie"],
    "sweatshirt": ["hoodie"],
    "laptop": ["notebook", "notebook computer", "ultrabook"],
    "notebook": ["laptop"],
    "computer": ["pc", "laptop", "notebook computer"],
    "pc": ["computer", "desktop computer", "laptop", "notebook computer"],
    "portable pc": ["portable computer", "laptop", "notebook computer"],
    "portable computer": ["portable pc", "laptop", "notebook computer"],
    "gaming laptop": ["gaming notebook", "gaming ultrabook"],
    "headphones": ["wireless headphones", "earbuds"],
    "headphone": ["wireless headphones", "earbuds"],
}

SEARCH_ACRONYM_MAP = {
    "pc": ["computer", "desktop computer"],
    "ssd": ["solid state drive"],
    "tv": ["television"],
}
