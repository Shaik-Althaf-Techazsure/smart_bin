from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_socketio import SocketIO, emit
import psycopg2
import psycopg2.extras
from datetime import date, datetime
import json
import time
import os
from urllib.parse import urlparse

# --- 1. APP & SOCKETIO INITIALIZATION ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'volvoway_industrial_secret_2024'
# Initialize SocketIO for real-time dashboard updates from hardware
socketio = SocketIO(app, cors_allowed_origins="*")

# Simple session replacement for a demo environment
USER_LOGGED_IN = False 

# --- 2. DATABASE CONFIGURATION (PostgreSQL Settings) ---
DB_HOST = os.environ.get('DB_HOST')
DB_USER = os.environ.get('DB_USER')
DB_PASSWORD = os.environ.get('DB_PASSWORD')
DB_PORT = os.environ.get('DB_PORT', '5432')
DB_NAME = os.environ.get('DB_NAME')

# Unified SQL Schema for Hardware, Registration, and Collection Logging
DB_INIT_SQL = """
-- 1. Table structure for table dustbins
DROP TABLE IF EXISTS dustbins CASCADE;
CREATE TABLE dustbins (
  bin_id VARCHAR(10) PRIMARY KEY,
  latitude DECIMAL(9, 6) NOT NULL,
  longitude DECIMAL(9, 6) NOT NULL,
  supervisor_name VARCHAR(100) DEFAULT NULL,
  location_name VARCHAR(255) DEFAULT NULL,
  bin_type VARCHAR(50) DEFAULT NULL,
  max_capacity_cm INTEGER NOT NULL,
  installation_date DATE DEFAULT NULL
);

-- 2. Table structure for table telemetry (Expanded for Hardware Sync)
DROP TABLE IF EXISTS telemetry CASCADE;
CREATE TABLE telemetry (
  record_id SERIAL PRIMARY KEY,
  bin_id VARCHAR(10) NOT NULL,
  timestamp TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
  fill_percentage INTEGER DEFAULT NULL,
  is_lid_locked BOOLEAN DEFAULT NULL,
  status_msg TEXT DEFAULT 'Monitoring',
  lat DECIMAL(9, 6) DEFAULT 0.0,
  lon DECIMAL(9, 6) DEFAULT 0.0,
  sats INTEGER DEFAULT 0,
  
  CONSTRAINT telemetry_fk_bin_id 
    FOREIGN KEY (bin_id) 
    REFERENCES dustbins (bin_id)
);

-- 3. Table structure for table collection_log
DROP TABLE IF EXISTS collection_log CASCADE;
CREATE TABLE collection_log (
  log_id SERIAL PRIMARY KEY,
  bin_id VARCHAR(10) NOT NULL,
  collection_time TIMESTAMP WITHOUT TIME ZONE NOT NULL,
  alert_time TIMESTAMP WITHOUT TIME ZONE,
  time_to_collect_min INTEGER,
  is_on_time BOOLEAN,
  reward_issued BOOLEAN,
  collector_id VARCHAR(10) DEFAULT NULL,
  
  CONSTRAINT collection_log_fk_bin_id
    FOREIGN KEY (bin_id)
    REFERENCES dustbins (bin_id)
);
"""

def get_db_connection():
    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        return None
    try:
        conn_params = {
            'dbname': DB_NAME, 'user': DB_USER, 'password': DB_PASSWORD,
            'host': DB_HOST, 'port': DB_PORT, 'sslmode': 'require'
        }
        return psycopg2.connect(**conn_params)
    except Exception as e:
        print(f"Error connecting to PostgreSQL: {e}")
        return None

def initialize_database():
    conn = get_db_connection()
    if conn is None: return {"success": False, "message": "DB Connection Failed"}, 500
    try:
        cursor = conn.cursor()
        cursor.execute(DB_INIT_SQL)
        conn.commit()
        return {"success": True, "message": "Database schema initialized successfully."}, 200
    except Exception as e:
        conn.rollback()
        return {"success": False, "message": str(e)}, 500
    finally:
        if conn: conn.close()

# --- 3. HARDWARE CORE: THE LIVE UPLINK ---

@app.route('/api/v1/update', methods=['POST'])
def update_telemetry():
    """
    RECEIVES DATA FROM ESP32.
    Saves to PostgreSQL AND Emits to SocketIO Dashboard.
    """
    data = request.json
    if not data:
        return jsonify({"success": False, "message": "No data"}), 400

    bin_id = data.get('bin_id', 'BIN-001')
    fill = data.get('fill_percentage', 0)
    locked = bool(data.get('is_locked', 0))
    status = data.get('status_msg', "Monitoring")
    lat = data.get('lat', 0.0)
    lon = data.get('lon', 0.0)
    sats = data.get('sats', 0)

    # 1. Real-time Push to Dashboard via SocketIO
    socketio.emit('bin_update', {
        "bin_id": bin_id,
        "fill_percentage": fill,
        "is_locked": locked,
        "status_msg": status,
        "lat": lat,
        "lon": lon,
        "sats": sats,
        "time": datetime.now().strftime("%H:%M:%S")
    })

    # 2. Persistent Save to PostgreSQL Telemetry Table
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            query = """
                INSERT INTO telemetry (bin_id, fill_percentage, is_lid_locked, status_msg, lat, lon, sats)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            cur.execute(query, (bin_id, fill, locked, status, lat, lon, sats))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            print(f"PostgreSQL Storage Error: {e}")

    return jsonify({"success": True}), 200

# --- 4. CORE UTILITIES ---

def get_latest_alert_time(conn, bin_id):
    try:
        cursor = conn.cursor()
        query = "SELECT timestamp FROM telemetry WHERE bin_id = %s AND fill_percentage >= 90 ORDER BY timestamp DESC LIMIT 1;"
        cursor.execute(query, (bin_id,))
        result = cursor.fetchone()
        return result[0] if result else None
    except: return None
    finally: cursor.close()

def get_collection_history(conn, bin_id):
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        query = "SELECT collection_time, time_to_collect_min, is_on_time, reward_issued FROM collection_log WHERE bin_id = %s ORDER BY collection_time DESC;"
        cursor.execute(query, (bin_id,))
        return [dict(row) for row in cursor.fetchall()]
    except: return []
    finally: cursor.close()

# --- 5. WEB ROUTES ---

@app.route('/')
def index():
    global USER_LOGGED_IN
    if USER_LOGGED_IN: return redirect(url_for('dashboard_page'))
    return render_template('login.html', title='Smart Waste Login')

@app.route('/login', methods=['POST'])
def login():
    global USER_LOGGED_IN
    username = request.form.get('username')
    password = request.form.get('password')
    if username == "official" and password == "1234":
        USER_LOGGED_IN = True
        return jsonify({"success": True}), 200
    return jsonify({"success": False}), 401

@app.route('/dashboard')
def dashboard_page():
    global USER_LOGGED_IN
    if not USER_LOGGED_IN: return redirect(url_for('index'))
    return render_template('dashboard.html')

@app.route('/api/v1/init_db', methods=['POST'])
def init_db_endpoint():
    return jsonify(initialize_database())

@app.route('/api/v1/register_bin', methods=['POST'])
def register_bin():
    data = request.json
    conn = get_db_connection()
    if not conn: return jsonify({"success": False}), 500
    try:
        cursor = conn.cursor()
        query = "INSERT INTO dustbins (bin_id, latitude, longitude, supervisor_name, max_capacity_cm, installation_date) VALUES (%s, %s, %s, %s, %s, %s)"
        cursor.execute(query, (data['bin_id'], data['latitude'], data['longitude'], data['supervisor_name'], data['max_capacity_cm'], date.today()))
        conn.commit()
        return jsonify({"success": True}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 400
    finally:
        conn.close()

@app.route('/api/v1/log_collection', methods=['POST'])
def log_collection():
    data = request.json
    bin_id = data.get('bin_id')
    conn = get_db_connection()
    if not conn: return jsonify({"success": False}), 500
    
    collection_time = datetime.now()
    alert_time = get_latest_alert_time(conn, bin_id)
    time_to_collect_min = int((collection_time - alert_time).total_seconds() / 60) if alert_time else 0
    is_on_time = time_to_collect_min <= 180 # 3 Hours
    
    try:
        cursor = conn.cursor()
        query = "INSERT INTO collection_log (bin_id, collection_time, alert_time, time_to_collect_min, is_on_time, reward_issued, collector_id) VALUES (%s, %s, %s, %s, %s, %s, %s)"
        cursor.execute(query, (bin_id, collection_time, alert_time, time_to_collect_min, is_on_time, is_on_time, "COL-A01"))
        conn.commit()
        return jsonify({"success": True, "reward_issued": is_on_time}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        conn.close()

# --- 6. SIMULATION & ANALYSIS ---

@app.route('/api/v1/telemetry/latest', methods=['GET'])
def get_latest_telemetry():
    """Fallback: Generates simulated data if hardware is offline."""
    return jsonify({"success": True, "latest_data": [{"bin_id": "BIN-001", "fill_percentage": 45, "status_msg": "Simulated Ready"}]})

@app.route('/api/v1/collection/route', methods=['GET'])
def get_collection_route():
    """Simulates a vehicle moving along fixed coordinates."""
    current_time = time.time()
    route = [(17.43, 78.41), (17.40, 78.45), (17.44, 78.39)]
    idx = int(current_time // 10) % len(route)
    return jsonify({"success": True, "route": {"vehicle_id": "TRK-A01", "current_position": {"latitude": route[idx][0], "longitude": route[idx][1]}}})

# --- 7. START SERVER ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True)
