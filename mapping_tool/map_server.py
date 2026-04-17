import json
from pathlib import Path
from flask import Flask, render_template, request, jsonify
import config
import mesh_solver

# -----------------------------------------------------------------------------
# App setup
# -----------------------------------------------------------------------------
app = Flask(__name__)

DATA_DIR = Path(config.DATA_DIR)
SHAPES_FILE = DATA_DIR / config.SHAPES_FILE
DATA_DIR.mkdir(exist_ok=True)
GATEWAY_FILE  = DATA_DIR / config.GATEWAY_FILE

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------

@app.route("/")
def index():
    """Serve the main map page."""
    return render_template(
        "map.html",
        center_lat    = config.DEFAULT_LAT,
        center_lng    = config.DEFAULT_LNG,
        zoom          = config.DEFAULT_ZOOM,
        tile_url      = config.TILE_URL,
        tile_attr     = config.TILE_ATTRIBUTION,
        tile_max_zoom = config.TILE_MAX_ZOOM,
    )


@app.route("/api/shapes", methods=["GET"])
def get_shapes():
    """
    Return all saved shapes as a GeoJSON FeatureCollection.
    Called on page load to restore previously drawn shapes.
    """
    if SHAPES_FILE.exists():
        with SHAPES_FILE.open("r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    else:
        # Return an empty FeatureCollection if nothing saved yet
        return jsonify({"type": "FeatureCollection", "features": []})


@app.route("/api/shapes", methods=["POST"])
def save_shapes():
    """
    Receive a full GeoJSON FeatureCollection from the front-end and persist it.
    This is called every time the user saves their work.

    Expected body: GeoJSON FeatureCollection
    Each feature has:
      - geometry: Point | Polygon | (encoded Circle, see notes in map.html)
      - properties:
          - shape_type : "marker" | "polygon" | "rectangle" | "circle"
          - radius     : float (metres, only present for circles)
          - label      : str (optional user label)
    """
    geojson = request.get_json()

    if not geojson:
        return jsonify({"status": "error", "message": "No JSON received"}), 400

    if geojson.get("type") != "FeatureCollection":
        return jsonify({"status": "error", "message": "Expected a GeoJSON FeatureCollection"}), 400

    with SHAPES_FILE.open("w", encoding="utf-8") as f:
        json.dump(geojson, f, indent=2, ensure_ascii=False)

    feature_count = len(geojson.get("features", []))
    print(f"[Saved] {feature_count} feature(s) → {SHAPES_FILE}")

    return jsonify({"status": "ok", "saved_features": feature_count})


@app.route("/api/shapes", methods=["DELETE"])
def clear_shapes():
    """
    Delete all saved shapes.
    Useful for starting a fresh scenario.
    """
    if SHAPES_FILE.exists():
        SHAPES_FILE.unlink()
    return jsonify({"status": "ok", "message": "All shapes cleared"})

# ---------------------------------------------------------------------
# Gateway endpoints – stored separately from shapes
# ---------------------------------------------------------------------

@app.route("/api/gateway", methods=["GET"])
def get_gateway():
    """
    Return the currently defined gateway as GeoJSON Feature, or null
    if none is set.

    Shape:
      {
        "type": "Feature",
        "geometry": { "type": "Point", "coordinates": [lng, lat] },
        "properties": { "shape_type": "gateway" }
      }
    """
    if GATEWAY_FILE.exists():
        with GATEWAY_FILE.open("r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    return jsonify(None)


@app.route("/api/gateway", methods=["POST"])
def set_gateway():
    """
    Set/overwrite the gateway position.

    Expected JSON body:
      {
        "lat": float,
        "lng": float
      }
    """
    data = request.get_json()
    if not data or "lat" not in data or "lng" not in data:
        return jsonify({"status": "error", "message": "lat and lng required"}), 400

    lat = float(data["lat"])
    lng = float(data["lng"])

    feature = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lng, lat]},
        "properties": {"shape_type": "gateway"},
    }

    with GATEWAY_FILE.open("w", encoding="utf-8") as f:
        json.dump(feature, f, indent=2, ensure_ascii=False)

    print(f"[Gateway] Set at lat={lat}, lng={lng}")
    return jsonify({"status": "ok"})


@app.route("/api/gateway", methods=["DELETE"])
def clear_gateway():
    """Remove the stored gateway (if any)."""
    if GATEWAY_FILE.exists():
        GATEWAY_FILE.unlink()
    return jsonify({"status": "ok", "message": "Gateway cleared"})

@app.route("/api/mesh", methods=["POST"])
def generate_mesh():
    """
    Trigger mesh generation based on current shapes + gateway.

    Returns:
      GeoJSON FeatureCollection with:
        - gateway (optional): properties.type = "gateway"
        - relay nodes:        properties.type = "relay"
        - links:              properties.type = "link"
    """
    try:
        mesh = mesh_solver.compute_mesh()
    except Exception as exc:
        # In early dev it's useful to see the error in logs
        print("[Mesh] Error:", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500

    return jsonify(mesh)

@app.route("/api/mesh", methods=["DELETE"])
def clear_mesh():
    """Clear saved mesh file (if any)."""
    try:
        mesh_solver.clear_mesh_file()
    except Exception as exc:
        print("[Mesh] Clear error:", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500
    return jsonify({"status": "ok", "message": "Mesh cleared"})

# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Starting Relay Planner Map at http://{config.FLASK_HOST}:{config.FLASK_PORT}")
    app.run(
        host  = config.FLASK_HOST,
        port  = config.FLASK_PORT,
        debug = config.FLASK_DEBUG,
    )