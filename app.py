import os
import datetime
import json
import logging

from flask import Flask, request, jsonify, render_template
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from bson import ObjectId

# Text Extraction Libraries
import PyPDF2
import docx

# Gemini API Library
import google.generativeai as genai

# --- Paths (Relative to this file in src/) ---
src_dir = os.path.abspath(os.path.dirname(__file__))
project_root = os.path.abspath(os.path.join(src_dir, '..'))

# --- Load .env from Project Root ---
dotenv_path = os.path.join(project_root, '.env')
if os.path.exists(dotenv_path): load_dotenv(dotenv_path=dotenv_path)
else: logging.warning(f".env file not found at: {dotenv_path}")

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Flask App (Finds templates/static inside src/) ---
app = Flask(__name__, template_folder='src/templates', static_folder='src/static')

# --- Configuration ---
app.config['UPLOAD_FOLDER'] = os.path.join(src_dir, 'uploads') # Uploads inside src/
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
ALLOWED_EXTENSIONS = {'pdf', 'docx', 'txt'}

logging.info(f"UPLOAD_FOLDER set to: {app.config['UPLOAD_FOLDER']}")

# --- MongoDB Setup ---
MONGO_URI = os.getenv("MONGO_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME")
db = None
resumes_collection = None
# (Keep the MongoDB connection logic the same as previous version)
if MONGO_URI and DATABASE_NAME:
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.admin.command('ismaster')
        logging.info("MongoDB connection successful.")
        db = client[DATABASE_NAME]
        resumes_collection = db['resumes']
        logging.info(f"Using database '{DATABASE_NAME}' and collection 'resumes'.")
    except ConnectionFailure as e:
        logging.error(f"Could not connect to MongoDB: {e}")
    except Exception as e:
        logging.error(f"MongoDB setup error: {e}")
else:
     logging.warning("MongoDB connection skipped (URI or DB Name missing).")


# --- Gemini API Setup ---
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
gemini_model = None
# (Keep the Gemini connection logic the same as previous version)
if GOOGLE_API_KEY:
    try:
        genai.configure(api_key=GOOGLE_API_KEY)
        gemini_model = genai.GenerativeModel('gemini-1.5-flash')
        logging.info("Gemini API configured successfully.")
    except Exception as e:
        logging.error(f"Failed to configure Gemini API: {e}")
else:
    logging.warning("GOOGLE_API_KEY not found. LLM analysis disabled.")


# --- Helper Functions (Keep allowed_file, text extractors, parse_mongo same) ---
def allowed_file(filename):
    """Checks if the file extension is allowed."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text_from_pdf(pdf_path):
    """Extracts text from a PDF file."""
    text = ""
    try:
        with open(pdf_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            if reader.is_encrypted:
                logging.warning(f"PDF file {os.path.basename(pdf_path)} is encrypted.")
                raise ValueError("Cannot process encrypted PDF files.")
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        return text.strip()
    except FileNotFoundError:
        logging.error(f"PDF file not found at {pdf_path}")
        raise
    except PyPDF2.errors.PdfReadError as pdf_err:
        logging.error(f"Error reading PDF file {os.path.basename(pdf_path)}: {pdf_err}")
        raise ValueError(f"Invalid or corrupted PDF file: {pdf_err}")
    except Exception as e:
        logging.exception(f"Unexpected error reading PDF {os.path.basename(pdf_path)}: {e}")
        raise ValueError(f"Could not extract text from PDF: {e}")

def extract_text_from_docx(docx_path):
    """Extracts text from a DOCX file."""
    text = ""
    try:
        doc = docx.Document(docx_path)
        for para in doc.paragraphs:
            text += para.text + "\n"
        return text.strip()
    except FileNotFoundError:
        logging.error(f"DOCX file not found at {docx_path}")
        raise
    except Exception as e:
        logging.exception(f"Error reading DOCX {os.path.basename(docx_path)}: {e}")
        raise ValueError(f"Could not extract text from DOCX: {e}")

def extract_text_from_txt(txt_path):
    """Extracts text from a TXT file."""
    try:
        encodings_to_try = ['utf-8', 'latin-1', 'cp1252']
        for enc in encodings_to_try:
            try:
                with open(txt_path, 'r', encoding=enc) as file:
                    return file.read().strip()
            except UnicodeDecodeError:
                logging.debug(f"Failed to decode {os.path.basename(txt_path)} with {enc}, trying next...")
                continue
        logging.error(f"Could not decode TXT file {os.path.basename(txt_path)} with tried encodings.")
        raise ValueError("Could not determine text encoding for TXT file.")
    except FileNotFoundError:
        logging.error(f"TXT file not found at {txt_path}")
        raise
    except Exception as e:
        logging.exception(f"Error reading TXT {os.path.basename(txt_path)}: {e}")
        raise ValueError(f"Could not extract text from TXT: {e}")

def parse_mongo(data):
    """Converts MongoDB docs to JSON serializable format."""
    if isinstance(data, list):
        return [parse_mongo(item) for item in data]
    if isinstance(data, dict):
        parsed_dict = {}
        for key, value in data.items():
            if key == '_id' and isinstance(value, ObjectId):
                parsed_dict['_id'] = str(value)
            else:
                parsed_dict[key] = parse_mongo(value)
        return parsed_dict
    if isinstance(data, datetime.datetime):
        return data.isoformat()
    return data


# --- LLM Analysis Function (UPDATED) ---
def analyze_resume_with_llm(file_path, job_description_text=""):
    """
    Extracts text, sends to Gemini API with optional job description context,
    returns analysis dict or error dict.
    """
    base_filename = os.path.basename(file_path)
    if gemini_model is None:
        logging.error("LLM analysis skipped: Gemini API client not configured.")
        return {"llm_error": "LLM service not available.", "match_score": 0}

    # 1. Extract Resume Text
    file_ext = file_path.rsplit('.', 1)[1].lower()
    resume_text = ""
    try:
        logging.info(f"Extracting text from {base_filename} (type: {file_ext})")
        if file_ext == 'pdf': resume_text = extract_text_from_pdf(file_path)
        elif file_ext == 'docx': resume_text = extract_text_from_docx(file_path)
        elif file_ext == 'txt': resume_text = extract_text_from_txt(file_path)
        else: raise ValueError(f"Unsupported file type for extraction: {file_ext}")

        if not resume_text or len(resume_text.strip()) < 30:
             logging.warning(f"Extracted text from {base_filename} seems empty/short.")
             # Proceed anyway for now

        logging.info(f"Text extracted from {base_filename}. Length: {len(resume_text)} chars.")

    except (ValueError, FileNotFoundError) as e:
        logging.error(f"Text extraction failed for {base_filename}: {e}")
        return {"llm_error": f"Text extraction failed: {e}", "match_score": 0}
    except Exception as e:
        logging.exception(f"Unexpected extraction error for {base_filename}: {e}")
        return {"llm_error": f"Unexpected text extraction error: {e}", "match_score": 0}

    # 2. Prepare Prompt (Conditionally include JD)
    jd_section = ""
    output_keys = [
        '"extracted_name": Candidate\'s full name (string, null if not found).',
        '"extracted_email": Candidate\'s primary email address (string, null if not found).',
        '"extracted_phone": Candidate\'s primary phone number (string, null if not found).',
        '"skills": List of key technical/soft skills (list of strings, [] if none).',
        '"experience_summary": Concise summary (max 3 sentences) of work experience (string, null if not found).',
        '"education_summary": Concise summary (max 2 sentences) of education (string, null if not found).'
    ]

    if job_description_text:
        logging.info(f"Analyzing resume against provided Job Description for {base_filename}.")
        jd_section = f"""
        Job Description Context:
        ---
        {job_description_text[:5000]}
        ---
        """
        # Add JD-specific output requests
        output_keys.append('"match_score": An estimated score (0-100) indicating how well the resume matches the Job Description (integer, null if cannot determine).')
        output_keys.append('"matching_keywords": List of keywords/skills from the resume that strongly match the Job Description requirements (list of strings, [] if none).')
        analysis_task = "Analyze the following resume text in the context of the provided Job Description."
    else:
        logging.info(f"Analyzing resume without Job Description context for {base_filename}.")
        analysis_task = "Analyze the following resume text."
        # Add placeholder score if no JD
        output_keys.append('"match_score": A general score (0-100) based on overall quality/clarity (integer, null if cannot determine).')


    prompt = f"""
    {analysis_task} Extract the specified information precisely.
    {jd_section}
    Resume Text:
    ---
    {resume_text[:20000]}
    ---

    Respond ONLY with a valid JSON object containing these keys:
    - {output_keys[0]}
    - {output_keys[1]}
    - {output_keys[2]}
    - {output_keys[3]}
    - {output_keys[4]}
    - {output_keys[5]}
    - {output_keys[6]}""" # Match score is always key 7 now
    if len(output_keys) > 7: # Add matching keywords if JD was provided
         prompt += f"\n    - {output_keys[7]}"

    prompt += """

    Important: Use JSON `null` for missing strings, `[]` for missing lists. No text outside the single JSON object.
    JSON Output:
    """

    # 3. Call Gemini API & Parse Response
    analysis_result = {}
    try:
        logging.info(f"Sending request to Gemini API for {base_filename}...")
        response = gemini_model.generate_content(prompt, generation_config=genai.types.GenerationConfig(temperature=0.2))
        logging.info(f"Received response from Gemini API for {base_filename}.")

        if not response.candidates or not hasattr(response.candidates[0], 'content'):
             block_reason = response.prompt_feedback.block_reason if hasattr(response, 'prompt_feedback') else 'Unknown'
             logging.error(f"Gemini response blocked/empty for {base_filename}. Reason: {block_reason}.")
             raise ValueError(f"LLM response blocked or empty (Reason: {block_reason})")

        response_text = response.text.strip()
        start_index = response_text.find('{')
        end_index = response_text.rfind('}')
        if start_index != -1 and end_index != -1 and end_index > start_index:
            json_string = response_text[start_index : end_index + 1]
            analysis_result = json.loads(json_string)
            logging.info(f"Parsed JSON response from Gemini for {base_filename}.")
            analysis_result['llm_error'] = None # Success
        else:
            logging.error(f"Could not find valid JSON {{...}} in Gemini response: {response_text}")
            raise ValueError("LLM response did not contain a valid JSON object.")

    except Exception as api_e:
        logging.exception(f"Gemini API call/processing error for {base_filename}: {api_e}")
        return {"llm_error": f"LLM analysis failed: {api_e}", "match_score": 0} # Return error dict

    # Set defaults for ALL expected keys based on whether JD was present
    analysis_result.setdefault('extracted_name', None)
    analysis_result.setdefault('extracted_email', None)
    analysis_result.setdefault('extracted_phone', None)
    analysis_result.setdefault('skills', [])
    analysis_result.setdefault('experience_summary', None)
    analysis_result.setdefault('education_summary', None)
    analysis_result.setdefault('match_score', None) # LLM should provide this
    if job_description_text:
        analysis_result.setdefault('matching_keywords', [])

    # Add a fallback default score if LLM fails to provide one
    if analysis_result.get('match_score') is None:
         analysis_result['match_score'] = 50 # Assign a neutral default if missing
         logging.warning(f"LLM did not provide 'match_score' for {base_filename}, using default.")


    return analysis_result


# --- Flask Routes ---
@app.route('/')
def index():
    """Renders the main HTML page."""
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_resume():
    """Handles resume file upload, triggers analysis, stores results."""
    upload_start_time = datetime.datetime.now()

    if resumes_collection is None:
        logging.error("Upload failed: Database service unavailable.")
        return jsonify({"error": "Database service unavailable."}), 503

    # --- File Handling ---
    if 'resume' not in request.files:
        return jsonify({"error": "No file part in the request"}), 400
    file = request.files['resume']
    if not file or file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    # --- Get Job Description (Optional) ---
    # Use .get() to safely retrieve from form data, default to empty string
    job_description_text = request.form.get('job_description', '').strip()
    jd_provided = bool(job_description_text) # Flag if JD was actually sent

    # --- File Validation and Saving ---
    if allowed_file(file.filename):
        filename = secure_filename(file.filename)
        upload_dir = app.config['UPLOAD_FOLDER'] # Inside src/

        if not os.path.exists(upload_dir):
             try: os.makedirs(upload_dir); logging.info(f"Created upload directory: {upload_dir}")
             except OSError as e: logging.error(f"Could not create upload dir {upload_dir}: {e}"); return jsonify({"error": "Server config error."}), 500

        file_path = os.path.join(upload_dir, filename)
        file_saved = False
        try:
            file.save(file_path)
            file_saved = True
            logging.info(f"File saved: {file_path}")

            # --- Trigger LLM Analysis (Pass JD Text) ---
            logging.info(f"Starting analysis for: {filename} {'with JD' if jd_provided else 'without JD'}")
            # Pass the job description text to the analysis function
            analysis_data = analyze_resume_with_llm(file_path, job_description_text)
            logging.info(f"Analysis completed for: {filename}.")

            if analysis_data.get("llm_error"):
                logging.warning(f"Analysis for {filename} has error: {analysis_data['llm_error']}")

            # --- Store results in MongoDB ---
            db_entry = {
                "original_filename": filename,
                "analysis": analysis_data,
                "job_description_provided": jd_provided, # Store whether JD was used
                # Optionally store a snippet of the JD for reference, be mindful of size/PII
                # "job_description_snippet": job_description_text[:300] if jd_provided else None,
                "upload_timestamp": datetime.datetime.now(datetime.timezone.utc)
            }
            insert_result = resumes_collection.insert_one(db_entry)
            inserted_id = insert_result.inserted_id
            logging.info(f"Data inserted into MongoDB for {filename} with ID: {inserted_id}")

            db_entry['_id'] = str(inserted_id) # Add stringified ID for response
            upload_duration = (datetime.datetime.now() - upload_start_time).total_seconds()
            logging.info(f"Processed {filename} in {upload_duration:.2f}s.")
            return jsonify(db_entry), 200 # OK

        except Exception as e:
            logging.exception(f"Unexpected error during upload/processing of {filename}: {e}")
            if file_saved and os.path.exists(file_path):
                 try: os.remove(file_path); logging.info(f"Cleaned up file on error: {file_path}")
                 except OSError as remove_error: logging.error(f"Error removing file on error: {remove_error}")
            return jsonify({"error": "Internal server error during processing."}), 500
        finally:
             # Optional: Clean up successfully processed file
             if file_saved and os.path.exists(file_path):
                 try:
                     os.remove(file_path) # Delete after success
                     if not os.path.exists(file_path) : logging.info(f"Cleaned up successfully processed file: {file_path}")
                 except OSError as remove_error: logging.error(f"Error removing processed file: {remove_error}")
    else:
        logging.warning(f"Upload failed: File type not allowed for '{file.filename}'.")
        return jsonify({"error": f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"}), 400


@app.route('/resumes', methods=['GET'])
def get_resumes():
    """Fetches all analyzed resume records from MongoDB."""
    if resumes_collection is None:
        logging.error("History fetch failed: Database unavailable.")
        return jsonify({"error": "Database service unavailable."}), 503

    try:
        # Fetch results, ensuring the parse_mongo handles potential new fields
        all_resumes = list(resumes_collection.find().sort("upload_timestamp", -1))
        results = parse_mongo(all_resumes)
        return jsonify(results), 200
    except Exception as e:
        logging.exception(f"Error fetching resumes from MongoDB: {e}")
        return jsonify({"error": "Failed to retrieve resume history."}), 500


# --- Main Execution ---
if __name__ == '__main__':
    # Ensure upload folder exists inside src/ at startup
    upload_dir = app.config['UPLOAD_FOLDER']
    if not os.path.exists(upload_dir):
        try: os.makedirs(upload_dir); logging.info(f"Upload directory created: {upload_dir}")
        except OSError as e: logging.error(f"CRITICAL: Could not create upload dir '{upload_dir}': {e}. Exiting."); exit(1)

    # --- Startup Checks ---
    logging.info("--- Performing Startup Checks ---")
    if db is None: logging.warning("Startup Check: MongoDB connection FAILED/Skipped.")
    else: logging.info("Startup Check: MongoDB connection OK.")
    if gemini_model is None: logging.warning(f"Startup Check: Gemini API client FAILED/Disabled (Key found: {bool(GOOGLE_API_KEY)}).")
    else: logging.info("Startup Check: Gemini API client OK.")
    logging.info("--- Startup Checks Complete ---")

    logging.info("Starting Flask development server (host=0.0.0.0, port=5000)...")
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)