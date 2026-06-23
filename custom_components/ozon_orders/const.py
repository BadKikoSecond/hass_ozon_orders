"""Constants for Ozon Orders integration."""

DOMAIN = "ozon_orders"
MANUFACTURER = "Ozon"

CONF_COOKIES = "cookies"

# User-configurable antibot / polling options (stored in config entry options).
CONF_SCAN_INTERVAL = "scan_interval"
CONF_FETCH_DETAILS = "fetch_details"
CONF_DETAILS_TTL_HOURS = "details_ttl_hours"
CONF_DETAILS_BATCH_SIZE = "details_batch_size"
CONF_REQUEST_DELAY_SEC = "request_delay_sec"
CONF_MANUAL_REFRESH_MINUTES = "manual_refresh_minutes"

DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 15
MAX_SCAN_INTERVAL = 180

DEFAULT_FETCH_DETAILS = True
DEFAULT_DETAILS_TTL_HOURS = 6
MIN_DETAILS_TTL_HOURS = 1
MAX_DETAILS_TTL_HOURS = 24

DEFAULT_DETAILS_BATCH_SIZE = 1
MIN_DETAILS_BATCH_SIZE = 0
MAX_DETAILS_BATCH_SIZE = 3

DEFAULT_REQUEST_DELAY_SEC = 2.0
MIN_REQUEST_DELAY_SEC = 1.0
MAX_REQUEST_DELAY_SEC = 10.0

DEFAULT_MANUAL_REFRESH_MINUTES = 10
MIN_MANUAL_REFRESH_MINUTES = 5
MAX_MANUAL_REFRESH_MINUTES = 60

# Internal antibot backoff (not exposed in UI).
ANTIBOT_BACKOFF_BASE_MINUTES = 60
ANTIBOT_BACKOFF_MAX_MINUTES = 360

ANTIBOT_COOKIE_NAME = "abt_data"

ATTR_ORDER_NUMBER = "order_number"
ATTR_ETA = "eta"
ATTR_DELIVERY_TYPE = "delivery_type"
ATTR_STORAGE_UNTIL = "storage_until"
ATTR_PRODUCTS_COUNT = "products_count"
ATTR_PAYMENT_STATUS = "payment_status"
ATTR_DETAIL_URL = "detail_url"
ATTR_DAYS_REMAINING = "days_remaining"
ATTR_ACCESS_TOKEN_EXPIRES = "access_token_expires"
ATTR_REFRESH_TOKEN_EXPIRES = "refresh_token_expires"
ATTR_FETCHED_AT = "fetched_at"
ATTR_LAST_ERROR = "last_error"
ATTR_BACKOFF_UNTIL = "backoff_until"
ATTR_ORDER_DATE = "order_date"
ATTR_ORDER_TITLE = "order_title"
ATTR_PRODUCTS = "products"
ATTR_PRODUCT_TITLES = "product_titles"

SERVICE_REFRESH = "refresh"
