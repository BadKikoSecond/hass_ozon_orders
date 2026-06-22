class OzonOrdersError(Exception):
    """Base error for Ozon orders client."""


class OzonAuthError(OzonOrdersError):
    """Session expired or user is not logged in."""


class OzonAntibotError(OzonOrdersError):
    """Variti / antibot blocked the request (often 403 + HTML)."""
