#include <WiFi.h>
#include <FirebaseESP32.h> // Using the stable, non-async library
#include <time.h> 

// --- 1. DEVICE & SENSOR CONFIGURATION ---
// IMPORTANT: This must match a bin registered in your MySQL Dustbins table!
#define BIN_ID "BIN-003" 
const int MAX_CAPACITY_CM = 150; // Max depth of the bin (e.g., 150 cm)
const int MIN_DISTANCE_CM = 10;  // Minimum distance sensor can read (full bin)

// Network and Firebase credentials
#define WIFI_SSID "TechAZsure"
#define WIFI_PASSWORD "TeChAzSuRe786"

// NOTE: DO NOT include "https://" or trailing slash in FIREBASE_HOST.
#define FIREBASE_HOST "smart-garbage-b38f0-default-rtdb.asia-southeast1.firebasedatabase.app" 

// --- AUTHENTICATION FIX: Using Email/App Password ---
// Your Web API Key is needed for UserAuth
const char* FIREBASE_API_KEY = "AIzaSyDECPVxVJbtctt64dPEXjcvAtI-cqarQBs"; 
// Your Email and App Password
const char* USER_EMAIL = "rtprojects.24@gmail.com";
const char* USER_PASSWORD = "zlib gnvc kmvt cuea"; 
// --------------------------------------------------

// Firebase RTDB Node Structure: /dustbin-003/latest
String FIREBASE_NODE_PATH = "/dustbin-" + String(BIN_ID).substring(4) + "/latest";

// Required objects for the Firebase library structure
FirebaseData firebaseData;
FirebaseAuth firebaseAuth;
FirebaseConfig firebaseConfig;

// Timer variables for sending data every 5 seconds (5000ms is standard push)
unsigned long lastPushTime = 0;
const long pushInterval = 5000; // Push every 5 seconds

// Mock sensor state (replace with actual sensor reading)
int simulatedDistance = MAX_CAPACITY_CM;

// --- UTILITY FUNCTIONS ---

// Placeholder for actual Ultrasonic Sensor Reading (Simulation)
int readDistanceCm() {
    // Simulate garbage accumulation (distance decreases by a random amount)
    simulatedDistance -= random(0, 4); 
    
    // Ensure distance stays within realistic bounds
    simulatedDistance = constrain(simulatedDistance, MIN_DISTANCE_CM, MAX_CAPACITY_CM);
    
    return simulatedDistance;
}

int calculateFillPercentage(int distanceCm) {
    int fillAmountCm = MAX_CAPACITY_CM - distanceCm;
    if (fillAmountCm < 0) fillAmountCm = 0;
    
    float percentage = ( (float)fillAmountCm / (float)MAX_CAPACITY_CM ) * 100.0;
    
    return (int)constrain(percentage, 0, 100);
}

String getTimestamp() {
    time_t now;
    struct tm timeinfo;
    time(&now);
    localtime_r(&now, &timeinfo);
    char timestampStr[30];
    strftime(timestampStr, 30, "%Y-%m-%dT%H:%M:%SZ", &timeinfo);
    return String(timestampStr);
}


// --- SETUP ---
void setup(){
    Serial.begin(115200);

    // Connect to Wi-Fi
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    Serial.print("Connecting to WiFi...");
    while (WiFi.status() != WL_CONNECTED) {
        Serial.print(".");
        delay(500);
    }
    Serial.println("\nWiFi connected.");
    Serial.print("IP Address: ");
    Serial.println(WiFi.localIP());

    // Set time zone for accurate timestamps
    configTime(3 * 3600, 0, "pool.ntp.org"); 
    setenv("TZ", "IST-5:30", 1); 
    tzset();

    // --- FIREBASE INITIALIZATION ---
    // 1. Assign Host and API Key
    firebaseConfig.host = FIREBASE_HOST;
    firebaseConfig.api_key = FIREBASE_API_KEY; 
    
    // 2. Assign Email and Password for User Authentication
    firebaseAuth.user.email = USER_EMAIL;
    firebaseAuth.user.password = USER_PASSWORD;
    
    // 3. Initialize Firebase
    // Pass both the Config and Auth structures
    Firebase.begin(&firebaseConfig, &firebaseAuth); 
    Firebase.reconnectWiFi(true);

    Serial.println("Firebase initialized.");
}

// --- MAIN LOOP ---

void loop() {
    if (millis() - lastPushTime > pushInterval) {
        lastPushTime = millis();
        
        if (WiFi.status() != WL_CONNECTED) {
            Serial.println("WiFi disconnected. Skipping push.");
            return;
        }
        
        // Ensure authentication token is valid before pushing data
        if (Firebase.ready()) {

            // 1. READ & CALCULATE SENSOR DATA
            int distanceCm = readDistanceCm();
            int fillPercentage = calculateFillPercentage(distanceCm);
            
            // 2. EDGE LOGIC: 98% Segregator Alert
            int segregatorRequired = (fillPercentage >= 98) ? 1 : 0;
            
            // 3. Get Timestamp
            String timestampStr = getTimestamp();

            // 4. Build JSON Payload using FirebaseJson
            FirebaseJson json;
            json.set("garbage_level_cm", distanceCm);
            json.set("fill_percentage", fillPercentage);
            json.set("segregator_required", segregatorRequired);
            json.set("timestamp", timestampStr);

            // 5. Push to Firebase RTDB (Using firebaseData object)
            if (Firebase.set(firebaseData, FIREBASE_NODE_PATH, json)) { 
                Serial.printf("[SUCCESS] Pushed %s data: Fill=%d%%, Segregator=%d\n", 
                              BIN_ID, fillPercentage, segregatorRequired);
            } else {
                // Error retrieval for this library version
                Serial.printf("[FAILED] Firebase Error: %s\n", firebaseData.errorReason().c_str()); 
            }
        } else {
            // Display error if not authenticated yet
            Serial.printf("[AUTH_WAIT] Not authenticated. Waiting for token... Error: %s\n", firebaseData.errorReason().c_str());
        }
    }
}