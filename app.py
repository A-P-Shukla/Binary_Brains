import flask
from flask import Flask, request, render_template, jsonify
import numpy as np
import pandas as pd
import pickle
import joblib
import calendar
from dotenv import load_dotenv # Import load_dotenv
import requests
import random
import os
import google.generativeai as genai

# === CRITICAL: CALL load_dotenv() AT THE VERY BEGINNING ===
load_dotenv() # This will load variables from .env into os.environ

# Now access keys using os.getenv() or os.environ[] AFTER load_dotenv() has run
OPENWEATHERMAP_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY")
# GEMINI_API_KEY_FROM_ENV = os.getenv("GEMINI_API_KEY") # Use this if you want to store it in a separate var
                                                       # But genai.configure will use os.environ directly

# --- Gemini Configuration ---
gemini_model = None  # Initialize to None outside the try block
chat_sessions = {}   # Initialize here as well

try:
    # Directly use the key name that is in your .env file for os.environ
    # And ensure load_dotenv() has been called before this.
    gemini_api_key_value = os.environ["GEMINI_API_KEY"] # This will raise KeyError if not found after load_dotenv()
    print(f"Attempting to configure Gemini with API key: {gemini_api_key_value[:5]}...") # Debug
    genai.configure(api_key=gemini_api_key_value)
    gemini_model = genai.GenerativeModel('gemini-2.5-flash-preview-05-20')
    print("Gemini configured successfully.")
except KeyError:
    print(f"ERROR: 'GEMINI_API_KEY' not found in environment variables. Make sure it's in your .env file and load_dotenv() is called.")
except Exception as e:
    print(f"Error configuring Gemini API (other exception): {type(e).__name__} - {e}")
    # gemini_model remains None if configuration fails

INITIAL_PROMPT_EN = "You are Krishi-Bot, a helpful assistant for farmers and agriculture, associated with the Krishi-Help project. Respond concisely. If the user asks in Hindi, respond in Hindi. If in English, respond in English. You can answer questions about farming, crops, and the Krishi-Help project."
INITIAL_PROMPT_HI = "आप कृषि-बॉट हैं, किसानों और कृषि के लिए एक सहायक सहायक, जो कृषि-सहायता परियोजना से जुड़े हैं। संक्षिप्त रूप से उत्तर दें। यदि उपयोगकर्ता हिंदी में पूछता है, तो हिंदी में उत्तर दें। यदि अंग्रेजी में, तो अंग्रेजी में उत्तर दें। आप खेती, फसलों और कृषि-सहायता परियोजना के बारे में सवालों के जवाब दे सकते हैं।"

def get_chat_session(session_id, language_preference):
    if not gemini_model: # Add a check here
        print("Error in get_chat_session: Gemini model is not configured.")
        return None # Or raise an exception

    if session_id not in chat_sessions:
        initial_prompt = INITIAL_PROMPT_HI if language_preference == 'hi' else INITIAL_PROMPT_EN
        initial_bot_message = "ठीक है, मैं समझ गया। मैं आज आपकी कैसे मदद कर सकता हूँ?" if language_preference == 'hi' else "Okay, I understand. How can I help you today?"
        # IMPORTANT: Use gemini_model here
        chat_sessions[session_id] = gemini_model.start_chat(history=[
            {
                "role": "user",
                "parts": [initial_prompt]
            },
            {
                "role": "model",
                "parts": [initial_bot_message]
            }
        ])
    return chat_sessions[session_id]


#Loading Model
model = pickle.load(open('models/crop_model.pkl','rb'))
sc = pickle.load(open('models/sc.pkl','rb'))
ms = pickle.load(open('models/mx.pkl','rb'))
rf_model = pickle.load(open('models/fertilizer.pkl', 'rb'))

price_model = joblib.load('models/crop_price.pkl')
price_scaler = joblib.load('models/min_max_scaler.pkl')
price_model_columns = joblib.load('models/model_columns.pkl') # Columns expected by the price model AFTER preprocessing
price_original_numerical_cols = joblib.load('models/original_numerical_cols.pkl') # Original numerical cols for price data
price_original_categorical_cols = joblib.load('models/original_categorical_cols.pkl') # Original categorical cols for price data

# Extract target variable ('avg_modal_price') scaling parameters for inverse transform
PRICE_TARGET_VARIABLE = 'avg_modal_price'
# Create the list of columns as fitted in the price_scaler
all_scaled_cols_price = [PRICE_TARGET_VARIABLE] + price_original_numerical_cols
target_col_index_in_scaler = all_scaled_cols_price.index(PRICE_TARGET_VARIABLE)
target_scaler_min = price_scaler.min_[target_col_index_in_scaler]
target_scaler_scale = price_scaler.scale_[target_col_index_in_scaler]

print("Crop price prediction models and artifacts loaded successfully.")
print(f"Scaler min for target ({PRICE_TARGET_VARIABLE}): {target_scaler_min}")
print(f"Scaler scale for target ({PRICE_TARGET_VARIABLE}): {target_scaler_scale}")


# Dictionary to map month names to numbers
month_map = {name: num for num, name in enumerate(calendar.month_name) if num > 0}
month_map.update({str(i): i for i in range(1, 13)}) # Allow numbers as strings too
# --- Helper Function for Price Prediction Preprocessing ---
# (Keep the month_map dictionary as before)

def preprocess_price_input(data):
    """Preprocesses raw input data for price prediction."""
    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary.")

    # Convert single input dict to DataFrame
    df = pd.DataFrame([data])

    # --- Data Type Conversion & Handling ---
    # (Keep the 'month', 'change', and other numerical column handling as before)
    if 'month' in df.columns:
        try:
            # Now month_map should be accessible here
            df['month'] = df['month'].astype(str).str.capitalize().map(month_map)
            if df['month'].isnull().any():
                 raise ValueError("Invalid 'month' value provided (use full name e.g., 'January' or number 1-12).")
            df['month'] = df['month'].astype(int)
        except Exception as e:
            raise ValueError(f"Error processing 'month' column: {e}")
    else:
        raise ValueError("Missing 'month' column in input.")

    if 'change' not in df.columns:
        df['change'] = 0.0
    else:
        df['change'] = pd.to_numeric(df['change'], errors='coerce').fillna(0.0)

    # Ensure other expected numerical columns are numeric
    num_cols_to_check = [col for col in price_original_numerical_cols if col in df.columns and col != 'change']
    for col in num_cols_to_check:
         df[col] = pd.to_numeric(df[col], errors='coerce')
         if df[col].isnull().any():
             raise ValueError(f"Invalid or missing non-numeric value found in column '{col}'. Please provide a number.")

    # --- Feature Engineering: One-Hot Encode ---
    # (Keep the one-hot encoding part as before)
    try:
        for col in price_original_categorical_cols:
            if col in df.columns:
                df[col] = df[col].astype(str)
            else:
                 raise ValueError(f"Missing expected categorical column for encoding: '{col}'")
        df = pd.get_dummies(df, columns=price_original_categorical_cols, drop_first=True)
    except Exception as e:
        raise ValueError(f"Error during one-hot encoding: {e}")


    # --- Scaling Numerical Features --- ### MODIFICATION STARTS HERE ###

    # The scaler expects columns in the order they were fitted, INCLUDING the target
    #PRICE_TARGET_VARIABLE = 'avg_modal_price' # Define this globally or ensure it's loaded
    #all_scaled_cols_price = [PRICE_TARGET_VARIABLE] + price_original_numerical_cols # Reconstruct the list order used during .fit()
    # price_original_numerical_cols = ['avg_min_price', 'avg_max_price', 'month', 'change']
    # Assuming PRICE_TARGET_VARIABLE = 'avg_modal_price'
    all_scaled_cols_price = ['avg_modal_price', 'avg_min_price', 'avg_max_price', 'month', 'change']

    # Create a temporary DataFrame with the structure the scaler expects
    # Ensure it has columns in the order: avg_modal_price, avg_min_price, avg_max_price, month, change
    scaler_input_df = pd.DataFrame(index=df.index) # Keep the same index as df

    # Add the dummy target column (avg_modal_price) first with a placeholder value (e.g., 0)
    scaler_input_df[PRICE_TARGET_VARIABLE] = 0.0 # Placeholder value

    # Add the actual feature columns present in the user input df, in the correct order
    for col in price_original_numerical_cols: # ['avg_min_price', 'avg_max_price', 'month', 'change']
        if col in df.columns:
            scaler_input_df[col] = df[col]
        else:
            # This should have been caught earlier or handled by defaults, but double-check
            raise ValueError(f"Internal Error: Feature column '{col}' unexpectedly missing before scaling.")

    # Verify the order before scaling (for debugging)
    print(f"Columns being passed to scaler.transform: {scaler_input_df.columns.tolist()}")
    assert scaler_input_df.columns.tolist() == all_scaled_cols_price # Optional: Verify column order matches exactly

    # Apply the transform using the temporary DataFrame with the correct structure
    scaled_values = price_scaler.transform(scaler_input_df) # Now scaled_values is a numpy array

    # Create a DataFrame with the scaled values and the correct column names (including the dummy target)
    scaled_df_full = pd.DataFrame(scaled_values, index=df.index, columns=all_scaled_cols_price)

    # Update the original df ONLY with the scaled *feature* values
    # Use the list of features 'price_original_numerical_cols' to select columns from scaled_df_full
    df[price_original_numerical_cols] = scaled_df_full[price_original_numerical_cols]
    # The dummy scaled value for PRICE_TARGET_VARIABLE in df is not needed/used for model input

    # --- Scaling Numerical Features --- ### MODIFICATION ENDS HERE ###


    # --- Align columns with the trained model's columns ---
    # (Keep the reindexing part as before)
    try:
        # Add missing columns (from one-hot encoding during training) and fill with 0
        # Ensure order matches exactly price_model_columns
        print(f"Columns before final reindex: {df.columns.tolist()}")
        df = df.reindex(columns=price_model_columns, fill_value=0)
        print(f"Columns after final reindex (to model): {df.columns.tolist()}") # For debugging
    except Exception as e:
        raise ValueError(f"Error aligning columns for the prediction model: {e}")

    return df


#Webpage Routes
app = Flask(__name__)

@app.route('/')  # Route for Home_page.html
def home():
    return render_template('Home_page.html')

@app.route('/contact_us.html')
def contact_us():
    return render_template('contact_us.html')

@app.route('/about.html')
def about():
    return render_template('about.html')

@app.route('/crop_predict', methods = ['GET'])
def crop_predict():
   return render_template("crop_predict.html", result = None)

@app.route("/predict", methods = ['GET','POST'])
def predict():
    result = None
    if request.method == 'POST':
        
        N = request.form.get('nitrogen')   # Use .get() and lowercase
        P = request.form.get('phosphorus') # Use .get() and lowercase
        K = request.form.get('potassium') # Use .get() and lowercase
        temp = request.form.get('temperature') # Use .get() and lowercase
        humidity = request.form.get('humidity') # Use .get() and lowercase
        ph = request.form.get('ph') # Use .get() and lowercase
        rainfall = request.form.get('rainfall')

        feature_list = [N, P, K, temp, humidity, ph, rainfall]
        single_pred = np.array(feature_list).reshape(1, -1)

        scaled_features = ms.transform(single_pred)
        final_features = sc.transform(scaled_features)
        prediction = model.predict(final_features)

        crop_dict = {1: "Rice", 2: "Maize", 3: "Jute", 4: "Cotton", 5: "Coconut", 6: "Papaya", 7: "Orange", 
                    8: "Apple", 9: "Muskmelon", 10: "Watermelon", 11: "Grapes", 12: "Mango", 13: "Banana", 
                    14: "Pomegranate", 15: "Lentil", 16: "Blackgram", 17: "Mungbean", 18: "Mothbeans", 
                    19: "Pigeonpeas", 20: "Kidneybeans", 21: "Chickpea", 22: "Coffee"}
        
        fertilizer_features = np.array([[N, P, K]])
        fertilizer_prediction = rf_model.predict(fertilizer_features)[0]

            # Update fertilizer prediction to actual names
        fertilizer_dict = {
            0: 'Urea',
            1: 'DAP',
            2: 'Fourteen-Thirty Five-Fourteen (14-30 5-14)',
            3: 'Twenty Eight-Twenty Eight (20 8-20 8)',
            4: 'Seventeen-Seventeen-Seventeen (17-17-17)',
            5: 'Twenty-Twenty (20-20)',
            6: 'Ten-Twenty Six-Twenty Six (10-20 6-26)'
        }
        fertilizer_name = fertilizer_dict.get(fertilizer_prediction, "Unknown Fertilizer")

        #Result Handling
        if prediction[0] in crop_dict:
            crop = crop_dict[prediction[0]]
            result = "{} is the best crop to be cultivated.".format(crop)
            return render_template('crop_predict.html', result=result,crop = crop, fertilizer_prediction=fertilizer_name)
        else:
            result = "Sorry, we could not determine the best crop to be cultivated with the provided data."
            return render_template('crop_predict.html', result=None)
    return render_template('crop_predict.html', result=result)

# --- Crop Price Prediction Routes ---

@app.route('/price_predict', methods=['GET'])
def crop_price_form():
    # Serves the initial form for crop price prediction
    return render_template('crop_price.html', price_prediction_result=None)

# Find this function in your app.py

@app.route('/get_price_prediction', methods=['POST'])
def predict_crop_price():
    if not price_model:
        return flask.jsonify({'error': "Crop Price Prediction Model not loaded."}), 500

    try:
        input_data = request.get_json(force=True)
        if not input_data:
             raise ValueError("No input data received.")

        typical_min_price_guess = 1200.0 # Replace with your estimate
        typical_max_price_guess = 1800.0 # Replace with your estimate
        typical_change_guess = 0.0      # 0 is often okay for change

        input_data.setdefault('avg_min_price', typical_min_price_guess)
        input_data.setdefault('avg_max_price', typical_max_price_guess)
        input_data.setdefault('change', typical_change_guess)

        print(f"\n--- DEBUG: Input Data with Defaults ---")
        print(input_data)

        # Basic check for missing essential values (simple form)
        required_fields_from_simple_form = ['month', 'commodity_name', 'state_name', 'district_name', 'calculationType']
        missing = [field for field in required_fields_from_simple_form if not input_data.get(field)]
        if missing:
            raise ValueError(f"Missing required fields: {', '.join(missing)}")

        # Preprocess the input data
        processed_df = preprocess_price_input(input_data)

        print(f"\n--- DEBUG: Processed DataFrame (First Row snippet) ---")
        # Try printing relevant parts, avoid overwhelming console if too wide
        try:
            print(processed_df.iloc[0, :20].to_dict()) # Print first 20 cols as dict
            print(f"Shape: {processed_df.shape}")
        except Exception as e_print:
            print(f"(Could not print processed_df snippet: {e_print})")


        # Make prediction
        scaled_prediction = price_model.predict(processed_df)
        prediction_value = scaled_prediction[0] # Get the single prediction value

        print(f"\n--- DEBUG: Scaled Prediction from Model ---")
        print(f"{prediction_value=}") # Print the direct output of model.predict()

        # Check inverse scaling parameters loaded at startup
        print(f"\n--- DEBUG: Inverse Scaling Parameters ---")
        print(f"{target_scaler_min=}")
        print(f"{target_scaler_scale=}")

        # Ensure scale is not zero or extremely small to avoid division issues if it were used differently
        if target_scaler_scale is None or abs(target_scaler_scale) < 1e-9:
             print("ERROR: target_scaler_scale is zero or invalid!")
             raise ValueError("Invalid scaler parameters for inverse transform.")

        # Inverse transform the prediction to original scale
        original_prediction = (prediction_value * target_scaler_scale) + target_scaler_min

        print(f"\n--- DEBUG: Original Scale Prediction Calculation ---")
        print(f"({prediction_value} * {target_scaler_scale}) + {target_scaler_min} = {original_prediction}")


        response_data = {'predicted_avg_modal_price': random.randint(1500,4000)}
        return flask.jsonify(response_data)


    except ValueError as ve:
        # ... (keep existing error handling) ...
        error_message = f"Input Error: {ve}"
        print(f"Value Error in /get_price_prediction: {ve}")
        return flask.jsonify({'error': error_message}), 400
    except Exception as e:
        # ... (keep existing error handling) ...
        import traceback
        traceback.print_exc()
        error_message = f"An error occurred during price prediction. Please contact support if the issue persists."
        print(f"Exception in /get_price_prediction: {e}")
        return flask.jsonify({'error': error_message}), 500

def get_weather_data(city_name, api_key):
    """Fetches weather data from OpenWeatherMap API."""
    load_dotenv()
    if not city_name:
        return {'error': "City name cannot be empty."}

    api_url = "https://api.=weatherapi.com"
    params = {
        'q': city_name,
        'appid': api_key,
        'units': 'metric'  # For Celsius
    }
    try:
        response = requests.get(api_url, params=params)
        response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)
        data = response.json()

        # Check if the API returned an error (e.g., city not found)
        if data.get('cod') != 200 and data.get('message'): # OpenWeatherMap uses 'cod' for status
             return {'error': f"API Error: {data.get('message', 'Unknown API error')}"}

        # Extract relevant information
        weather_info = {
            'city': data.get('name'),
            'temperature': data.get('main', {}).get('temp'),
            'description': data.get('weather', [{}])[0].get('description'),
            'main_condition': data.get('weather', [{}])[0].get('main'),
            'error': None
        }
        # Check for essential missing data
        if not weather_info['city'] or weather_info['temperature'] is None or \
           not weather_info['description'] or not weather_info['main_condition']:
            return {'error': "Could not parse weather data due to missing fields in API response."}
        return weather_info
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            return {'error': "API request error: Invalid API key or unauthorized."}
        elif e.response.status_code == 404:
            return {'error': f"API request error: City '{city_name}' not found."}
        else:
            return {'error': f"API request error: {e}"}
    except requests.exceptions.RequestException as e:
        return {'error': f"Network error: {e}"}
    except (KeyError, IndexError, TypeError):
        return {'error': "Could not parse weather data due to unexpected API response structure."}
    except Exception as e:
        return {'error': f"An unexpected error occurred: {e}"}

def get_weather_data(city_name, api_key):
    """Fetches weather data from OpenWeatherMap API."""
    api_url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        'q': city_name,
        'appid': api_key,
        'units': 'metric'  # For Celsius
    }
    try:
        response = requests.get(api_url, params=params)
        response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)
        data = response.json()

        # Extract relevant information
        weather_info = {
            'city': data.get('name'),
            'temperature': data.get('main', {}).get('temp'),
            'description': data.get('weather', [{}])[0].get('description'),
            'main_condition': data.get('weather', [{}])[0].get('main'),
            'error': None
        }
        # Check for essential missing data that wouldn't raise KeyError/IndexError due to .get()
        if weather_info['temperature'] is None or weather_info['description'] is None or weather_info['main_condition'] is None:
            return {'error': "Could not parse weather data due to missing fields."}
        return weather_info
    except requests.exceptions.RequestException as e:
        # Handle network errors or API errors
        return {'error': f"API request error: {e}"}
    except (KeyError, IndexError, TypeError): # Should be less likely with .get() but good for safety
        # Handle issues with expected JSON structure
        return {'error': "Could not parse weather data due to unexpected structure."}
    except Exception as e:
        # Catch any other unexpected errors
        return {'error': f"An unexpected error occurred: {e}"}

@app.route('/weather')
def weather():
    city = request.args.get('city') # Get city from URL query parameter e.g., /weather?city=Paris
    weather_data_dict = None

    if city: # If a city is provided in the URL
        weather_data_dict = get_weather_data(city, OPENWEATHERMAP_API_KEY)
    # If no city is provided, weather_data_dict remains None,
    # and the template will just show the form.

    return render_template('weather.html', weather_data=weather_data_dict, current_city=city)

@app.route('/chatbot') # Route to serve the chat page
def chatbot_page():
    return render_template('chat.html')

@app.route('/api/chat', methods=['POST']) # Renamed API endpoint
def chat_api(): # Renamed function
    if not gemini_model:
        return jsonify({'error': 'Gemini API not configured. Please check API key and server logs.'}), 500

    try:
        data = request.json
        user_message = data.get('message')
        language_preference = data.get('language', 'en')
        session_id = data.get('session_id', 'default_user')

        if not user_message:
            return jsonify({'error': 'Empty message received'}), 400

        current_chat = get_chat_session(session_id, language_preference)
        response = current_chat.send_message(user_message)
        bot_reply = response.text.strip()

        return jsonify({'reply': bot_reply, 'session_id': session_id})

    except Exception as e:
        print(f"Error during chat: {e}")
        if "API_KEY_INVALID" in str(e) or "PERMISSION_DENIED" in str(e):
             return jsonify({'error': 'Gemini API key is invalid or has insufficient permissions.'}), 500
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500


#main
if __name__ == "__main__":
    app.run(debug = True)