import firebase_admin
from firebase_admin import credentials, db # Use db instead of realtime_db for brevity
import mysql.connector
from datetime import datetime
import time
import random
import json

# --- 1. CONFIGURATION ---

# MySQL Settings (MUST match the config in app.py)
DB_CONFIG = {
    "host": "localhost",
    "user": "root",        
    "password": "TeChAzSuRe786", # Your specified password
    "database": "smart_waste_db"
}

# Firebase Admin SDK Configuration (Requires a JSON Service Account File)
# 1. Download your service account JSON file from Firebase Console.
# 2. Update the variable below with the filename.
SERVICE_ACCOUNT_FILE = "service.json" # <--- MUST BE EDITED
FIREBASE_URL = "https://smart-garbage-b38f0-default-rtdb.asia-southeast1.firebasedatabase.app/" # Replace with your project URL

# Initialize Firebase Admin SDK
try:
    CRED = credentials.Certificate(SERVICE_ACCOUNT_FILE)
    FIREBASE_APP = firebase_admin.initialize_app(CRED, {
        'databaseURL': FIREBASE_URL
    })
    FIREBASE_DB = db
except Exception as e:
    print(f"❌ FIREBASE ADMIN SDK SETUP FAILED. Ensure '{SERVICE_ACCOUNT_FILE}' is correct and the URL is set. Error: {e}")
    pass

# Global variable for MySQL connection
conn = None
# Bin IDs to Simulate (MUST be registered in MySQL)
BIN_IDS_TO_SIMULATE = ["BIN-001", "BIN-002", "BIN-003", "BIN-004", "BIN-005", "BIN-006"]

# --- 2. MYSQL CONNECTION AND HELPERS ---

def get_db_connection():
    """Establishes and returns a new MySQL database connection."""
    try:
        conn = mysql.connector.connect(
            host=DB_CONFIG["host"], user=DB_CONFIG["user"], password=DB_CONFIG["password"],
            database=DB_CONFIG["database"], auth_plugin='mysql_native_password' 
        )
        return conn
    except mysql.connector.Error as err:
        print(f"❌ Error connecting to MySQL: {err}")
        return None

def get_bin_max_capacity(bin_id, cursor):
    """Retrieves the bin's max capacity from the Dustbins table (needed for % calculation)."""
    conn = get_db_connection()
    if conn is None: return None
    try:
        cursor = conn.cursor()
        query = "SELECT max_capacity_cm FROM Dustbins WHERE bin_id = %s;"
        cursor.execute(query, (bin_id,))
        result = cursor.fetchone()
        return result[0] if result else None
    except Exception:
        return None
    finally:
        if conn and conn.is_connected(): conn.close()

def calculate_fill_percentage(max_capacity_cm, fill_level_cm):
    """Calculates the fill percentage (0-100)."""
    # max_capacity_cm is guaranteed to be an integer here due to caller check
    if fill_level_cm is None or fill_level_cm < 0: return 0
    fill_level_cm = min(fill_level_cm, max_capacity_cm)
    fill_amount_cm = max_capacity_cm - fill_level_cm
    percentage = (fill_amount_cm / max_capacity_cm) * 100
    return min(100, max(0, int(percentage)))

# --- 3. CORE SIMULATION AND PUSH LOGIC (ESP32 Simulation) ---

def simulate_and_push_to_firebase(bin_levels):
    """Simulates the ESP32 pushing data directly to Firebase RTDB."""
    for bin_id, current_level in bin_levels.items():
        # 1. Simulate changing sensor level
        new_level = current_level - random.randint(-1, 5)
        new_level = max(10, min(new_level, 200))
        bin_levels[bin_id] = new_level
        
        # 2. Calculate Edge Logic for Segregator Requirement (98% threshold)
        
        # --- FIX FOR NONETYPE ERROR ---
        max_cap = get_bin_max_capacity(bin_id, None)
        
        if max_cap is None:
            max_cap_safe = 200
            print(f"⚠️ Warning: Bin {bin_id} not found in MySQL or DB error. Using default capacity of {max_cap_safe}cm for simulation.")
        else:
            max_cap_safe = max_cap
        # --- END FIX ---

        fill_percentage = calculate_fill_percentage(max_cap_safe, new_level)
        segregator_required = 1 if fill_percentage >= 98 else 0

        # 3. Build Payload (What the ESP32 would send)
        payload = {
            "garbage_level_cm": new_level,
            "fill_percentage": fill_percentage, 
            "segregator_required": segregator_required,
            "timestamp": datetime.now().isoformat(),
        }
        
        # 4. Push to Firebase RTDB under /dustbin-XXX node
        path = f'dustbin-{bin_id.split("-")[-1]}' 
        
        if 'FIREBASE_DB' in globals():
            try:
                # firebase-admin syntax: reference().set()
                FIREBASE_DB.reference(path).child("latest").set(payload)
                print(f"[FIREBASE PUSH] Bin: {bin_id} | Fill: {fill_percentage}% | Segregator: {segregator_required}")
            except Exception as e:
                 print(f"❌ FIREBASE WRITE FAILED for {bin_id}: {e}")
        else:
            print("❌ FIREBASE NOT CONNECTED. Skipping data push.")

    return bin_levels

# --- 4. FIREBASE TO MYSQL BRIDGE LOGIC ---

def bridge_firebase_to_mysql():
    """Reads all 'latest' data from Firebase and pushes it into the MySQL Telemetry table."""
    conn = get_db_connection()
    if conn is None: return

    for bin_id in BIN_IDS_TO_SIMULATE:
        path = f'dustbin-{bin_id.split("-")[-1]}/latest'
        
        # 1. Read data from Firebase (firebase-admin syntax)
        if 'FIREBASE_DB' not in globals():
            print("❌ FIREBASE NOT CONNECTED. Skipping bridge read.")
            return

        try:
            # firebase-admin syntax: reference().get()
            fb_data = FIREBASE_DB.reference(path).get()
        except Exception as e:
            print(f"⚠️ Warning: Failed to read from Firebase for {bin_id}. {e}")
            continue

        if not fb_data:
            print(f"⚠️ Warning: No data found in Firebase for {bin_id}.")
            continue

        try:
            cursor = conn.cursor()
            
            # --- FOREIGN KEY CHECK (FIX to avoid 1452 error) ---
            # Verify the bin is registered before attempting INSERT into Telemetry
            bin_check_query = "SELECT 1 FROM Dustbins WHERE bin_id = %s LIMIT 1;"
            cursor.execute(bin_check_query, (bin_id,))
            if cursor.fetchone() is None:
                print(f"❌ FK VIOLATION AVOIDED: Bin ID {bin_id} is not registered in Dustbins. Skipping insertion.")
                continue
            # --- END FOREIGN KEY CHECK ---
            
            # 2. Extract and format data for MySQL Telemetry table
            fill_level_cm = fb_data.get('garbage_level_cm', 0)
            fill_percentage = fb_data.get('fill_percentage', 0)
            segregator_required = fb_data.get('segregator_required', 0)
            
            # alert_triggered (for MySQL) = segregator_required (1 if >= 98%)
            alert_triggered = segregator_required
            is_lid_locked = 1 if fill_percentage >= 90 else 0 
            
            timestamp_str = fb_data.get('timestamp', datetime.now().isoformat())
            
            insert_query = """
            INSERT INTO Telemetry 
            (bin_id, timestamp, fill_level_cm, fill_percentage, is_lid_locked, alert_triggered, delay_minutes)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            
            data_to_insert = (
                bin_id, timestamp_str, fill_level_cm, fill_percentage, is_lid_locked, alert_triggered, 0
            )
            
            cursor.execute(insert_query, data_to_insert)
            conn.commit()
            
            print(f"[MYSQL INSERT] Bin: {bin_id} | Fill: {fill_percentage}% | Segregator: {segregator_required}")

        except Exception as e:
            print(f"MySQL Error during bridge insert for {bin_id}: {e}")
            conn.rollback() 
        finally:
            if 'cursor' in locals() and cursor:
                 cursor.close()
    
    if conn and conn.is_connected():
        conn.close()


# --- 5. MAIN EXECUTION ---

if __name__ == "__main__":
    
    # Initialize random levels
    bin_levels = {id: random.randint(30, 180) for id in BIN_IDS_TO_SIMULATE}
    print("--- Starting Firebase Simulation and MySQL Bridge ---")

    try:
        while True:
            # Step A: Simulate ESP32 pushing data to Firebase
            bin_levels = simulate_and_push_to_firebase(bin_levels)
            
            # Step B: Bridge reads from Firebase and writes to MySQL
            bridge_firebase_to_mysql()
            
            time.sleep(5)

    except KeyboardInterrupt:
        print("\nSimulator service stopped by user.")
    except Exception as e:
        print(f"An error occurred: {e}")