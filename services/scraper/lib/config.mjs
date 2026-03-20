export const RUNTIME_PORT = Number(process.env.PORT || process.env.AIXSTORE_RUNTIME_PORT || 7860);
export const RUNTIME_HOST = process.env.AIXSTORE_RUNTIME_HOST || "0.0.0.0";

export const CATEGORY_CONFIG = {
  electronics: {
    name: "Electronics",
    icon: "phone-portrait-outline",
    color: "#3884FF",
    keywords: ["tv", "headphone", "tablet", "speaker", "monitor", "camera", "laptop", "watch"],
    domains: ["bestbuy.com", "walmart.com", "target.com"],
    seedQueries: ["wireless headphones", "smart watch", "bluetooth speaker", "4k tv", "tablet", "camera"],
  },
  fashion: {
    name: "Fashion",
    icon: "shirt-outline",
    color: "#F97316",
    keywords: ["shoe", "hoodie", "jacket", "dress", "backpack", "bag", "sneaker"],
    domains: ["nike.com", "target.com", "walmart.com"],
    seedQueries: ["running shoes", "hoodie", "backpack", "jacket"],
  },
  beauty: {
    name: "Beauty",
    icon: "sparkles-outline",
    color: "#EC4899",
    keywords: ["serum", "moisturizer", "lip", "shampoo", "skin", "beauty", "cleanser"],
    domains: ["ulta.com", "sephora.com", "target.com"],
    seedQueries: ["face moisturizer", "vitamin c serum", "lip balm", "shampoo"],
  },
  home: {
    name: "Home",
    icon: "home-outline",
    color: "#10B981",
    keywords: ["lamp", "coffee", "bedding", "air fryer", "chair", "desk", "home", "kitchen"],
    domains: ["target.com", "walmart.com"],
    seedQueries: ["air fryer", "desk lamp", "coffee maker", "bedding set"],
  },
  toys: {
    name: "Toys",
    icon: "game-controller-outline",
    color: "#8B5CF6",
    keywords: ["lego", "board game", "toy", "plush", "rc car", "doll"],
    domains: ["target.com", "walmart.com"],
    seedQueries: ["lego set", "board game", "rc car", "plush toy"],
  },
  sports: {
    name: "Sports",
    icon: "fitness-outline",
    color: "#14B8A6",
    keywords: ["yoga", "dumbbell", "fitness", "sports", "mat", "golf", "club"],
    domains: ["nike.com", "target.com", "walmart.com"],
    seedQueries: ["yoga mat", "dumbbells"],
  },
  others: {
    name: "Others",
    icon: "grid-outline",
    color: "#64748B",
    keywords: [],
    domains: ["target.com", "walmart.com"],
    seedQueries: ["office chair", "storage organizer"],
  },
};

export const SOURCE_CONFIG = [
  { siteName: "Best Buy", domains: ["bestbuy.com"] },
  { siteName: "Walmart", domains: ["walmart.com"] },
  { siteName: "Target", domains: ["target.com"] },
  { siteName: "Nike", domains: ["nike.com"] },
  { siteName: "Ulta Beauty", domains: ["ulta.com"] },
  { siteName: "Sephora", domains: ["sephora.com"] },
];

export const DEFAULT_BOOTSTRAP_COUNT = 100;
export const DEFAULT_SEARCH_LIMIT = 10;
