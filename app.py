from flask import Flask, render_template, request, jsonify, redirect, url_for
# Replaced mysql.connector with psycopg2 for PostgreSQL
import psycopg2 
from datetime import date, datetime
import json
import time
import os # Import OS for reading environment variables
from urllib.parse import urlparse # Import for parsing the complex DB URL
import psycopg2.extras # Needed for DictCursor

# --- FIREBASE ADMIN SDK IMPORTS ---
import firebase_admin
from firebase_admin import credentials, db 

# --- 1. FLASK APP INITIALIZATION & FIREBASE SETUP ---
app = Flask(__name__)
# Simple session replacement for a demo environment
USER_LOGGED_IN = False 

# --- FIREBASE SECURE CREDENTIAL LOADING ---
# 1. Read JSON content from the environment variable (Render setting)
SERVICE_ACCOUNT_JSON = os.environ.get("FIREBASE_CREDENTIALS_JSON")
FIREBASE_URL = "https://smart-garbage-b38f0-default-rtdb.asia-southeast1.firebasedatabase.app/" 

# Initialize Firebase Admin SDK
try:
    if SERVICE_ACCOUNT_JSON:
        # Load credentials from the parsed JSON string
        CRED = credentials.Certificate(json.loads(SERVICE_ACCOUNT_JSON))
        FIREBASE_APP = firebase_admin.initialize_app(CRED, {
            'databaseURL': FIREBASE_URL
        })
        FIREBASE_DB = db
        print("✅ Firebase Admin SDK Initialized Successfully.")
    else:
        # Deployment will fail if this is required but not set
        raise ValueError("FIREBASE_CREDENTIALS_JSON environment variable not set.")
        
except Exception as e:
    print(f"❌ FIREBASE ADMIN SDK SETUP FAILED. Error: {e}")
    FIREBASE_DB = None
    pass


# --- 2. DATABASE CONFIGURATION (PostgreSQL Cloud Settings - Individual Params) ---
# CRITICAL: Read individual connection parameters from Render environment variables
DB_HOST = os.environ.get('DB_HOST')
DB_USER = os.environ.get('DB_USER')
DB_PASSWORD = os.environ.get('DB_PASSWORD')
DB_PORT = os.environ.get('DB_PORT', '5432') # Default PostgreSQL port
DB_NAME = os.environ.get('DB_NAME')

def get_db_connection():
    """
    Establishes and returns a new PostgreSQL database connection using individual parameters.
    Updated to use sslmode='prefer' to bypass potential connection handshake issues.
    """
    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        print("FATAL: One or more database environment variables (HOST, USER, PASSWORD, NAME) are missing.")
        return None
        
    try:
        # Build connection parameters explicitly
        conn_params = {
            'dbname': DB_NAME, 
            'user': DB_USER,
            'password': DB_PASSWORD,
            'host': DB_HOST,
            'port': DB_PORT,
            'sslmode': 'prefer' # CHANGED to prefer to bypass handshake issue
        }

        conn = psycopg2.connect(**conn_params)
        return conn
    except psycopg2.Error as err:
        print(f"Error connecting to PostgreSQL: {err}")
        return None
    except Exception as e:
        print(f"Error parsing DB_URL or establishing connection: {e}")
        return None

# --- 3. CORE UTILITIES (PostgreSQL History & Logging) ---

def get_latest_alert_time(conn, bin_id):
    """
    Finds the time of the latest 'FULL' alert for a bin based on PostgreSQL Telemetry history.
    """
    try:
        cursor = conn.cursor()
        query = """
        SELECT timestamp FROM telemetry 
        WHERE bin_id = %s AND fill_percentage >= 90
        ORDER BY timestamp DESC
        LIMIT 1;
        """
        cursor.execute(query, (bin_id,))
        result = cursor.fetchone()
        
        if result:
            return result[0] 
        return None
    except Exception as e:
        print(f"Error fetching latest alert time: {e}")
        return None
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()

def get_collection_history(conn, bin_id):
    """Fetches the collection history and performance metrics for a bin."""
    try:
        cursor = conn.cursor()
        query = """
        SELECT collection_time, time_to_collect_min, is_on_time, reward_issued 
        FROM collection_log 
        WHERE bin_id = %s
        ORDER BY collection_time DESC;
        """
        cursor.execute(query, (bin_id,))
        columns = [desc[0] for desc in cursor.description]
        history_list = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return history_list
    except Exception as e:
        print(f"Error fetching collection history: {e}")
        return []
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()

# --- 4. CORE ROUTES ---

@app.route('/')
def index():
    """Default route: Renders the login page."""
    global USER_LOGGED_IN
    if USER_LOGGED_IN:
        return redirect(url_for('dashboard'))
    # NOTE: You need a 'login.html' template in your templates folder
    return render_template('login.html', title='Smart Waste Login')

@app.route('/login', methods=['POST'])
def login():
    """Handles the login form submission."""
    global USER_LOGGED_IN
    username = request.form.get('username')
    password = request.form.get('password')
    
    if username == "official" and password == "1234":
        USER_LOGGED_IN = True
        return jsonify({"success": True, "message": "Login successful."}), 200
    else:
        return jsonify({"success": False, "message": "Invalid username or password."}), 401

@app.route('/logout')
def logout():
    """Handles user logout: Resets session state and redirects to login."""
    global USER_LOGGED_IN
    USER_LOGGED_IN = False
    return redirect(url_for('index'))

@app.route('/dashboard')
def dashboard():
    """Renders the main dashboard page."""
    global USER_LOGGED_IN
    if not USER_LOGGED_IN:
        return redirect(url_for('index'))
    # NOTE: You need a 'dashboard.html' template in your templates folder
    return render_template('dashboard.html', title='Smart Waste Dashboard')

@app.route('/register')
def register_form():
    """Renders the dustbin registration form page."""
    global USER_LOGGED_IN
    if not USER_LOGGED_IN:
        return redirect(url_for('index'))
    # NOTE: You need a 'register_bin.html' template in your templates folder
    return render_template('register_bin.html', title='Register New Bin')

# --- 5. API ENDPOINTS ---

@app.route('/api/v1/register_bin', methods=['POST'])
def register_bin():
    """Handles POST requests to register a new dustbin and stores it in the Dustbins table."""
    data = request.json
    
    required_fields = ['bin_id', 'latitude', 'longitude', 'supervisor_name', 'max_capacity_cm']
    if not all(field in data for field in required_fields):
        return jsonify({"success": False, "message": "Missing required data fields."}), 400

    conn = get_db_connection()
    if conn is None:
        return jsonify({"success": False, "message": "Database connection failed. Check DB variables."}), 500

    try:
        cursor = conn.cursor()
        
        insert_query = """
        INSERT INTO dustbins 
        (bin_id, latitude, longitude, supervisor_name, location_name, bin_type, max_capacity_cm, installation_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        bin_data = (
            data['bin_id'],
            data['latitude'],
            data['longitude'],
            data['supervisor_name'],
            data.get('location_name', 'N/A'),
            data.get('bin_type', 'General'),
            data['max_capacity_cm'],
            date.today()
        )
        
        cursor.execute(insert_query, bin_data)
        conn.commit()
        
        return jsonify({
            "success": True, 
            "message": f"Dustbin {data['bin_id']} registered successfully. Please refresh dashboard.",
            "bin_id": data['bin_id']
        }), 201

    except psycopg2.IntegrityError as e:
        conn.rollback()
        # PostgreSQL error parsing is different from MySQL
        return jsonify({"success": False, "message": f"Registration failed. Bin ID may already exist or data is invalid."}), 409
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": f"An unexpected error occurred: {e}"}), 500
    finally:
        if conn and not conn.closed:
            cursor.close()
            conn.close()

@app.route('/api/v1/bins/registered', methods=['GET'])
def get_registered_bins():
    """Fetches all static information for all registered bins from PostgreSQL."""
    conn = get_db_connection()
    if conn is None:
        return jsonify({"success": False, "message": "Database connection failed. Check DB variables."}), 500
    
    try:
        cursor = conn.cursor()
        select_query = "SELECT bin_id, latitude, longitude, supervisor_name, location_name, bin_type, max_capacity_cm, installation_date FROM dustbins;"
        cursor.execute(select_query)
        
        # Fetch column names manually for dict output
        columns = [desc[0] for desc in cursor.description]
        bins = [dict(zip(columns, row)) for row in cursor.fetchall()]

        return jsonify({"success": True, "bins": bins}), 200

    except Exception as e:
        return jsonify({"success": False, "message": f"Error fetching bins: {e}"}), 500
    finally:
        if conn and not conn.closed:
            cursor.close()
            conn.close()

@app.route('/api/v1/telemetry/latest', methods=['GET'])
def get_latest_telemetry():
    """
    Fetches the latest live telemetry data directly from Firebase RTDB 
    for all currently registered bins (sourced from PostgreSQL).
    """
    if FIREBASE_DB is None:
        return jsonify({"success": False, "message": "Firebase is not initialized."}), 500

    # 1. Get list of registered bins from PostgreSQL (required to know which nodes to query)
    pg_conn = get_db_connection()
    if pg_conn is None:
        return jsonify({"success": False, "message": "Database connection failed."}), 500
        
    try:
        cursor = pg_conn.cursor(cursor_factory=psycopg2.extras.DictCursor) 
        cursor.execute("SELECT bin_id FROM dustbins;")
        registered_bins = cursor.fetchall()
    except Exception as e:
        print(f"Error fetching registered bins from PostgreSQL: {e}")
        return jsonify({"success": False, "message": "Could not retrieve registered bin list."}), 500
    finally:
        if 'cursor' in locals() and cursor and cursor.connection and not cursor.connection.closed:
            cursor.close()
            pg_conn.close()

    latest_data = []
    
    # 2. Query Firebase for the latest status of each registered bin
    for bin_info in registered_bins:
        bin_id = bin_info['bin_id']
        # Node structure: /dustbin-001/latest
        # Extracts "001" from "BIN-001"
        bin_suffix = bin_id.split("-")[-1]
        fb_node = f'dustbin-{bin_suffix}'
        
        try:
            # firebase-admin syntax: reference().get()
            fb_data = FIREBASE_DB.reference(fb_node).child("latest").get()
            
            if fb_data:
                # Map Firebase data fields to the expected Telemetry fields for the dashboard
                telemetry_record = {
                    "bin_id": bin_id,
                    "timestamp": fb_data.get('timestamp', datetime.now().isoformat()),
                    "fill_level_cm": fb_data.get('garbage_level_cm', 0),
                    "fill_percentage": fb_data.get('fill_percentage', 0),
                    "alert_triggered": fb_data.get('segregator_required', 0), # 1 if >= 98%
                    "is_lid_locked": 1 if fb_data.get('fill_percentage', 0) >= 90 else 0, # Simple lock rule
                    "collection_time": None, # This comes from Collection_Log, left as None for live status
                    "delay_minutes": 0,
                }
                latest_data.append(telemetry_record)

        except Exception as e:
            print(f"Error reading live data from Firebase for {bin_id}: {e}")
            continue

    return jsonify({"success": True, "latest_data": latest_data}), 200


@app.route('/api/v1/bin/analysis/<bin_id>', methods=['GET'])
def get_bin_analysis(bin_id):
    """Placeholder for Agentic AI analysis of a single bin, including performance history."""
    conn = get_db_connection()
    if conn is None:
        return jsonify({"success": False, "message": "Database connection failed. Check DB variables."}), 500
    
    try:
        # Fetch collection history (from PostgreSQL) to provide detailed performance data
        history = get_collection_history(conn, bin_id)
        
        analysis_report = {
            "bin_id": bin_id,
            "urgency": "CRITICAL" if bin_id == "BIN-002" else "ROUTINE",
            "core_issue": "Sensor Data Anomaly (Rapid Fluctuation)" if bin_id == "BIN-007" else ("Imminent Overflow Due to Missed Route" if bin_id == "BIN-002" else "Normal Fill Rate"),
            "precautions": [
                "Issue a high-priority alert to Collector Team B.",
                "Verify Ultrasonic sensor stability (Check logs for temperature/vibration spikes).",
                "Remotely lock the lid mechanism to prevent spillage in 2 hours."
            ] if bin_id == "BIN-002" else [
                "Maintain current collection schedule.",
                "Monitor fill rate variance hourly."
            ],
            "collection_history": [
                {
                    "time": item['collection_time'].isoformat() if item.get('collection_time') else None,
                    "delay": item['time_to_collect_min'],
                    "on_time": item['is_on_time'],
                    "reward": item['reward_issued']
                } for item in history
            ],
            "total_collections": len(history),
            "on_time_collections": len([h for h in history if h.get('is_on_time')]),
            "analysis_timestamp": datetime.now().isoformat()
        }

        return jsonify({"success": True, "analysis": analysis_report}), 200
        
    except Exception as e:
        return jsonify({"success": False, "message": f"Error generating analysis: {e}"}), 500
    finally:
        if conn and not conn.closed:
            conn.close()

@app.route('/api/v1/log_collection', methods=['POST'])
def log_collection():
    """Logs a successful collection event and calculates performance (PostgreSQL)."""
    data = request.json
    bin_id = data.get('bin_id')
    
    if not bin_id:
        return jsonify({"success": False, "message": "Missing bin_id."}), 400

    conn = get_db_connection()
    if conn is None:
        return jsonify({"success": False, "message": "Database connection failed. Check DB variables."}), 500
    
    collection_time = datetime.now()
    alert_time = get_latest_alert_time(conn, bin_id)
    
    MAX_DELAY_MINUTES = 180 
    
    time_to_collect_min = 0
    is_on_time = False
    reward_issued = False
    
    if alert_time:
        time_difference = collection_time - alert_time
        time_to_collect_min = int(time_difference.total_seconds() / 60)
        
        is_on_time = time_to_collect_min <= MAX_DELAY_MINUTES
        reward_issued = is_on_time
    
    try:
        cursor = conn.cursor()
        
        insert_query = """
        INSERT INTO collection_log 
        (bin_id, collection_time, alert_time, time_to_collect_min, is_on_time, reward_issued, collector_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        
        collector_id = "COL-A01" 
        
        log_data = (
            bin_id,
            collection_time,
            alert_time,
            time_to_collect_min,
            is_on_time,
            reward_issued,
            collector_id
        )
        
        cursor.execute(insert_query, log_data)
        conn.commit()
        
        return jsonify({
            "success": True, 
            "message": f"Collection logged successfully for {bin_id}. Time to clear: {time_to_collect_min} min. Reward issued: {reward_issued}",
            "reward_issued": reward_issued
        }), 200

    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": f"Error logging collection: {e}"}), 500
    finally:
        if conn and not conn.closed:
            cursor.close()
            conn.close()


# --- 6. VEHICLE SIMULATION LOGIC ---

SIMULATED_ROUTE = [
    (17.4300, 78.4100), 
    (17.4000, 78.4500), 
    (17.4450, 78.3950), 
    (17.3850, 78.4867), 
    (17.3900, 78.5000) 
]

def get_simulated_vehicle_route():
    """Simulates a collection vehicle moving along a fixed route over time."""
    current_time = time.time()
    route_index = int(current_time // 10) % len(SIMULATED_ROUTE)
    current_lat, current_lon = SIMULATED_ROUTE[route_index]
    route_path = SIMULATED_ROUTE[:route_index + 1]
    
    return {
        "vehicle_id": "TRK-A01",
        "current_position": {"latitude": current_lat, "longitude": current_lon},
        "path_history": [{"latitude": lat, "longitude": lon} for lat, lon in route_path],
        "status": "In Service, Route R03",
        "timestamp": datetime.now().isoformat()
    }

@app.route('/api/v1/collection/route', methods=['GET'])
def get_collection_route():
    """API endpoint to serve the simulated collection vehicle data."""
    route_data = get_simulated_vehicle_route()
    return jsonify({"success": True, "route": route_data}), 200

# --- 7. RUN SERVER ---
if __name__ == '__main__':
    print("-------------------------------------------------------")
    print("Flask Server running at: http://127.0.0.1:5000/")
    print("-------------------------------------------------------")
    # For production deployment (Render), we typically rely on gunicorn
    # app.run(debug=True, port=5000)
