# --------- Map Defaults -----------
DEFAULT_LAT = 55.6761
DEFAULT_LNG = 12.5683
DEFAULT_ZOOM = 10

# --- Flask server settings ---
# Use host="0.0.0.0" to expose on local network (e.g. team access)
# Use host="127.0.0.1" to keep it strictly local (only your machine)
FLASK_HOST  = "127.0.0.1"
FLASK_PORT  = 5000
FLASK_DEBUG = True       # Set to False in any production-like environment

# --- Tile provider ---
# For development we use the free OSM tile server.
# !! FOR PRODUCTION: Replace TILE_URL and TILE_ATTRIBUTION with your
#    commercial tile provider's URL and required attribution string.
#    E.g. Mapbox, Thunderforest, or your own hosted tile server.
#    See: https://wiki.openstreetmap.org/wiki/Tile_servers
# !!
TILE_URL         = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_ATTRIBUTION = "&copy; OpenStreetMap contributors"
TILE_MAX_ZOOM    = 19

# --- Data storage ---
# Folder where drawn shapes will be saved as GeoJSON files
DATA_DIR    = "data"
SHAPES_FILE = "shapes.geojson"
GATEWAY_FILE  = "gateway.json"