"""Canonical project set for Command Bridge cards and work tracking."""
from __future__ import annotations


PROJECTS = (
    {
        "id": "hermes",
        "name": "Hermes",
        "counter_label": "Hermes",
        "priority": 2,
        "stem_keywords": ("hermes",),
        "keywords": ("hermes", "command bridge", "webui", "web ui", "prime", "voce", "voice", "planet", "pianeta"),
        "tracking_pattern": r"\b(command-bridge|librarian|webui|prime|watchdog|hermes)\b",
    },
    {
        "id": "visionbuilts",
        "name": "VisionBuilts",
        "counter_label": "VisionBuilts",
        "priority": 2,
        "stem_keywords": ("visionbuilts", "vision builts", "giorgiof28", "creator earning", "creator-earning"),
        "keywords": (
            "visionbuilts", "vision builts", "giorgiof28", "creator earning", "creator-earning",
            "ebook", "e-book", "n8n", "console", "instagram", "crm", "webhook", "gotenberg",
        ),
        "tracking_pattern": r"\b(n8n|visionbuilts|ebook|pdf|recipe|console|instagram)\b",
    },
    {
        "id": "vending-machine",
        "name": "Vending Machine",
        "counter_label": "Vending",
        "priority": 2,
        "stem_keywords": ("vending machine", "vending", "up level sicilia"),
        "keywords": ("vending machine", "vending", "shaker proteici", "shaker proteico", "up level sicilia"),
        "tracking_pattern": r"\b(vending(?: machine)?|shaker proteic\w*|up level sicilia)\b",
    },
    {
        "id": "trading",
        "name": "Trading",
        "counter_label": "Trading",
        "priority": 2,
        "stem_keywords": ("trading",),
        "keywords": ("trading", "smart money", "supply and demand", "order block", "liquidità", "liquidita"),
        "tracking_pattern": r"\b(trading|smart money|supply and demand|order block|liquidit[àa])\b",
    },
    {
        "id": "ebook-cucina-amazon",
        "name": "Ebook Cucina Amazon",
        "counter_label": "Ebook Amazon",
        "priority": 2,
        "stem_keywords": ("ebook cucina amazon", "ebook amazon", "ricettario amazon", "amazon kdp"),
        "keywords": ("ebook cucina amazon", "ebook amazon", "ricettario amazon", "amazon kdp", "kdp"),
        "tracking_pattern": r"\b(ebook cucina amazon|ebook amazon|ricettario amazon|amazon kdp|kdp)\b",
    },
)

PROJECT_BY_ID = {project["id"]: project for project in PROJECTS}

# Specific business lines must win before broader VisionBuilts keywords such as
# "ebook" and "console". Hermes stays last because its vocabulary is broad.
ATTRIBUTION_ORDER = (
    "vending-machine",
    "trading",
    "ebook-cucina-amazon",
    "visionbuilts",
    "hermes",
)

# Worklog historically gave Hermes precedence over VisionBuilts when an event
# mentioned both. Keep that invariant while letting the three specific new
# businesses win before the generic VisionBuilts "ebook" keyword.
TRACKING_ATTRIBUTION_ORDER = (
    "vending-machine",
    "trading",
    "ebook-cucina-amazon",
    "hermes",
    "visionbuilts",
)


def tracked_projects_payload() -> list[dict[str, str]]:
    return [
        {
            "id": project["id"],
            "name": project["name"],
            "label": project["counter_label"],
        }
        for project in PROJECTS
    ]
