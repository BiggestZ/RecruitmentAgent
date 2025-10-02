from flask import Flask, request, render_template_string, redirect, url_for, flash, session, redirect, jsonify
import os, json, requests
from datetime import datetime, timedelta
from googleapiclient.discovery import build
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
import io, uuid
from googleapiclient.http import MediaIoBaseUpload
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
import threading, time


load_dotenv()
# ======= CONFIG =======
UPLOAD_FOLDER = os.path.join("resume_unscanned")  # Local backup folder (Will make obsolete in future)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
FOLDER_ID = os.getenv("INPUT_FOLDER_ID")   # 👈 Replace with your Drive folder ID in the .env
PROCESSED_FOLDER_ID = os.getenv("OUTPUT_FOLDER_ID") # Later integrate moving matched files to output folder
# OPPORTUNITIES_FOLDER_ID = os.getenv("OPPORTUNITIES_FOLDER_ID") # Folder with job descriptions
RESUME_FOLDER_ID = os.getenv("RESUME_FOLDER_ID") # Folder with resumes
SERVICE_ACCOUNT_FILE = os.path.join("Langgraph_server", "google_oauth", "credentials.json")
script_dir = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT_FILE = os.path.abspath(os.path.join(script_dir, '..', 'google_oauth', 'credentials.json'))
SCOPES = ["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/gmail.send"]
# This is the URL that Google Drive sends notifications to (i.e., this app's endpoint)
webhook_url = os.getenv("LANGGRAPH_WEBHOOK_URL")
# This is the URL of the LangGraph API server
LANGGRAPH_API_URL = os.getenv("LANGGRAPH_API_URL")
stored_page_token = None # Store page token for changes API (in production, use database)

# Deduplication
recent_files = {}
lock = threading.Lock()
cooldown = 60 

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")  # Required for flash messages
TOKEN_PATH = os.path.join("Langgraph_server", "google_oauth", "token.json")
CLIENT_SECRET_FILE = os.path.join("Langgraph_server", "google_oauth", "client_secret.json")

# ======= GOOGLE DRIVE SETUP =======
def get_drive_service():
    """Get Google Drive service - FIXED VERSION"""
    """Initializes and returns a Google Drive service object using a service account."""
    try:
        creds = None
        # Load existing token if exists
        if os.path.exists(TOKEN_PATH):
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
            #print('Token Path: ', TOKEN_PATH)
         # If no valid creds, do OAuth flow
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
                print('Client Secret File: ', CLIENT_SECRET_FILE)
                creds = flow.run_local_server(port=8080,
                    access_type="offline",
                    include_granted_scopes="true",
                    prompt="consent"
                )  # opens browser for login, make sure https://localhost/'port' is added to oauth redirect uri.
            print("OAuth Accepted!")
            try:
                # Save token for next run
                with open(TOKEN_PATH, "w") as token:
                    token.write(creds.to_json())
            except Exception as e:
                print(f"❌ Error saving token: {e}")
                raise

        return build("drive", "v3", credentials=creds)
    except Exception as e:
        print(f"❌ Error creating Drive service: {e}")
        raise

# ======= INITIALIZE WEBHOOK ON STARTUP =======
def initialize_drive_webhook():
    """Set up webhook to monitor changes in the entire Drive"""
    global stored_page_token
    try:
        service = get_drive_service()
        # Get starting page token
        response = service.changes().getStartPageToken().execute()
        stored_page_token = response.get('startPageToken')
        
        # Set up webhook for all changes
        channel_id = str(uuid.uuid4())
        body = {
            "id": channel_id,
            "type": "web_hook",
            "address": webhook_url,  # Your ngrok webhook URL
            "token": "your-verification-token",  # Optional security token
            "expiration": int((datetime.now() + timedelta(hours=24)).timestamp() * 1000)  # 24 hours
        }
        
        result = service.changes().watch(
            pageToken=stored_page_token,
            body=body
        ).execute()
        print("Result executed, pageToken: ", stored_page_token)
        
        print(f"✅ Webhook initialized! Channel ID: {channel_id}")
        print(f"🔗 Webhook URL: {webhook_url}")
        return result
        
    except Exception as e:
        print(f"❌ Error setting up webhook: {e}")
        return None

# Uploads a file into a set google drive folder (set in enb)
def upload_to_drive(file, filename):
    """Upload a file to Google Drive inside the target folder"""
    try:
        service = get_drive_service()
        file_metadata = {"name": filename, "parents": [FOLDER_ID]}
        media = MediaIoBaseUpload(file, mimetype="application/pdf", resumable=True)
        uploaded = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, name, parents"
        ).execute()
        print(f"✅ Uploaded to Drive: {uploaded['name']} (ID: {uploaded['id']})")
        return uploaded
    except Exception as e:
        print(f"❌ Upload failed: {e}")
        raise

# Runs the langgraph agent
def trigger_langgraph_processing(file_id: str, file_name: str):
    """Sends a request to the LangGraph API to start processing a file."""
    if not LANGGRAPH_API_URL:
        print("❌ LANGGRAPH_API_URL not set in .env. Cannot trigger agent.")
        return False

    print("---------- URL IS SET ----------")

    # This is the endpoint for creating a new stateless run.
    # We assume the assistant is named 'recruit-agent'. Make sure this
    # matches the ID in your langgraph.json file.
    url = f"{LANGGRAPH_API_URL}/runs"
    print("Running Langgraph API")
    # The payload should match the AgentState of the LangGraph agent
    payload = {
        "assistant_id": "recruit-agent",
        "input": {
            "file_id": file_id, # Provided by webhook
            "file_name": file_name, # Provided by webhook
            "raw_text": "", # Generated in recruit_agent.py - parsepdf
            
            "experience_text": None, # Generated in recruit_agent.py - parsepdf
            "applicant_name": None, # Generated in recruit_agent.py - parsepdf
            "applicant_email": "", # Generated in recruit_agent.py - parsepdf"
            "resume_readable": True, # Default to True, will be set to False if work experience not found
            
            "input_folder_id": FOLDER_ID,
            "resume_folder_id": RESUME_FOLDER_ID,
            "output_folder_id": PROCESSED_FOLDER_ID,
            "drive_texts": [],
            "recruiter_list": [],
            "match_results": [],
        },
        # For stateless runs, we can ask the server to clean up the thread
        "on_completion": "delete",
    }
    try:
        response = requests.post(url, json=payload, timeout=20)
        response.raise_for_status()
        run_info = response.json()
        print(f"✅ LangGraph agent triggered for file: {file_name}. Run ID: {run_info.get('run_id')}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"❌ HTTP error for {file_name}: {e}")
        try:
            # Try to log server’s JSON error message
            print("↩️ Response body:", response.json())
        except Exception:
            # Fallback to raw text if JSON fails
            print("↩️ Raw response body:", response.text)
        return False
    except requests.exceptions.RequestException as e:
        print(f"❌ Request failed for {file_name}: {e}")
        return False


@app.route("/test-trigger")
def test_trigger():
    """A manual endpoint to test triggering the LangGraph agent.
    Helps isolate issues between webhook notifications and agent processing.
    """
    file_id = request.args.get("file_id")
    file_name = request.args.get("file_name")

    if not file_id or not file_name:
        flash("Please provide 'file_id' and 'file_name' as query parameters. Example: /test-trigger?file_id=...&file_name=...", "error")
        return redirect(url_for('upload_file'))

    success = trigger_langgraph_processing(file_id, file_name)

    if success:
        flash(f"✅ Successfully triggered agent for file: {file_name} (ID: {file_id})", "success")
    else:
        flash(f"❌ Failed to trigger agent for file: {file_name}", "error")
    
    return redirect(url_for('upload_file'))

# ======= HTML TEMPLATE (unchanged) =======
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Recruitment Assistant Agent - Upload Resumes</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .container {
            background: white;
            padding: 30px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        h1 {
            color: #333;
            text-align: center;
            margin-bottom: 30px;
        }
        h2 {
            color: #555;
            margin-top: 30px;
            margin-bottom: 15px;
        }
        .upload-form {
            background: #f8f9fa;
            padding: 20px;
            border-radius: 8px;
            border: 2px dashed #dee2e6;
            text-align: center;
            margin-bottom: 20px;
        }
        .file-input {
            margin: 10px 0;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            width: 100%;
            max-width: 400px;
        }
        .submit-btn {
            background: #007bff;
            color: white;
            padding: 12px 24px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
        }
        .submit-btn:hover {
            background: #0056b3;
        }
        .file-list {
            background: #f8f9fa;
            padding: 20px;
            border-radius: 8px;
            margin-top: 20px;
        }
        .file-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px;
            border-bottom: 1px solid #dee2e6;
        }
        .file-item:last-child {
            border-bottom: none;
        }
        .file-info {
            flex-grow: 1;
        }
        .file-name {
            font-weight: bold;
            color: #333;
        }
        .file-size {
            color: #666;
            font-size: 14px;
        }
        .file-date {
            color: #888;
            font-size: 12px;
        }
        .status-section {
            background: #e7f3ff;
            padding: 15px;
            border-radius: 8px;
            border-left: 4px solid #007bff;
            margin: 20px 0;
        }
        .action-buttons {
            text-align: center;
            margin-top: 20px;
        }
        .btn {
            display: inline-block;
            padding: 10px 20px;
            margin: 0 10px;
            text-decoration: none;
            border-radius: 4px;
            font-weight: bold;
        }
        .btn-primary {
            background: #28a745;
            color: white;
        }
        .btn-primary:hover {
            background: #218838;
        }
        .btn-secondary {
            background: #6c757d;
            color: white;
        }
        .btn-secondary:hover {
            background: #545b62;
        }
        .alert {
            padding: 15px;
            margin: 20px 0;
            border-radius: 4px;
        }
        .alert-success {
            background: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        .alert-error {
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        .stats {
            display: flex;
            justify-content: space-around;
            margin: 20px 0;
            text-align: center;
        }
        .stat-item {
            flex: 1;
            padding: 15px;
            background: #f8f9fa;
            margin: 0 10px;
            border-radius: 8px;
        }
        .stat-number {
            font-size: 24px;
            font-weight: bold;
            color: #007bff;
        }
        .stat-label {
            color: #666;
            font-size: 14px;
        }
        .setup-section {
            background: #fff3cd;
            padding: 15px;
            border-radius: 8px;
            border: 1px solid #ffeaa7;
            margin: 20px 0;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>📄 Recruitment Assistant Agent</h1>
        <p style="text-align: center; color: #666; margin-bottom: 30px;">
            Upload resumes to be processed and matched with job opportunities
        </p>

        <!-- Flash Messages -->
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="alert alert-{{ 'error' if category == 'error' else 'success' }}">
                        {{ message }}
                    </div>
                {% endfor %}
            {% endif %}
        {% endwith %}

        <!-- Setup Section -->
        <div class="setup-section">
            <h3>⚙️ First Time Setup</h3>
            <p>If you're getting permission errors, try these setup options:</p>
            <div class="action-buttons">
                <a href="/setup-permissions" class="btn btn-secondary">Share Existing Folder</a>
                <a href="/create-folder" class="btn btn-secondary">Create New Folder</a>
            </div>
        </div>

        <!-- Upload Form -->
        <h2>📤 Upload Resume</h2>
        <div class="upload-form">
            <form method="post" enctype="multipart/form-data">
                <p><strong>Select a PDF file to upload for processing:</strong></p>
                <input type="file" name="file" accept=".pdf" class="file-input" required>
                <br>
                <button type="submit" class="submit-btn">📤 Upload Resume</button>
            </form>
        </div>

        <!-- Statistics -->
        <div class="stats">
            <div class="stat-item">
                <div class="stat-number">{{ stats.unscanned }}</div>
                <div class="stat-label">Pending Processing</div>
            </div>
            <div class="stat-item">
                <div class="stat-number">{{ stats.processed }}</div>
                <div class="stat-label">Processed</div>
            </div>
            <div class="stat-item">
                <div class="stat-number">{{ stats.total }}</div>
                <div class="stat-label">Total Files</div>
            </div>
        </div>

        <!-- Status Section -->
        <div class="status-section">
            <h3>📋 Processing Status</h3>
            <p><strong>Files in 'resume_unscanned'</strong> will be processed by the recruitment agent.</p>
            <p><strong>Processed files</strong> will be moved to 'resume_processed' folder.</p>
        </div>

        <!-- File List -->
        <h2>📁 Uploaded Files (Pending Processing)</h2>
        <div class="file-list">
            {% if files %}
                {% for file in files %}
                    <div class="file-item">
                        <div class="file-info">
                            <div class="file-name">{{ file.filename }}</div>
                            <div class="file-size">{{ file.size_formatted }}</div>
                            <div class="file-date">Uploaded: {{ file.upload_date }}</div>
                        </div>
                    </div>
                {% endfor %}
            {% else %}
                <p style="text-align: center; color: #666;">No files uploaded yet.</p>
            {% endif %}
        </div>

        <!-- Action Buttons -->
        <div class="action-buttons">
            <a href="/" class="btn btn-secondary">🔄 Refresh</a>
            <a href="/run-agent" class="btn btn-primary">🚀 Run Recruitment Agent</a>
        </div>
    </div>
</body>
</html>"""

# ======= HELPERS (unchanged) =======
def get_file_stats():
    """Get statistics about uploaded and processed files (local only for now)"""
    unscanned_count = 0
    processed_count = 0
    
    if os.path.exists(UPLOAD_FOLDER):
        unscanned_count = len([f for f in os.listdir(UPLOAD_FOLDER) if f.lower().endswith('.pdf')])
    
    processed_folder = "resume_processed"
    if os.path.exists(processed_folder):
        processed_count = len([f for f in os.listdir(processed_folder) if f.lower().endswith('.pdf')])
    
    return {
        'unscanned': unscanned_count,
        'processed': processed_count,
        'total': unscanned_count + processed_count
    }

def get_uploaded_files():
    """Get list of uploaded files with metadata (local view)"""
    files = []
    if os.path.exists(UPLOAD_FOLDER):
        for filename in os.listdir(UPLOAD_FOLDER):
            if filename.lower().endswith('.pdf'):
                file_path = os.path.join(UPLOAD_FOLDER, filename)
                file_stat = os.stat(file_path)
                
                size_bytes = file_stat.st_size
                if size_bytes < 1024:
                    size_formatted = f"{size_bytes} B"
                elif size_bytes < 1024 * 1024:
                    size_formatted = f"{size_bytes / 1024:.1f} KB"
                else:
                    size_formatted = f"{size_bytes / (1024 * 1024):.1f} MB"
                
                upload_date = datetime.fromtimestamp(file_stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                
                files.append({
                    'filename': filename,
                    'size_formatted': size_formatted,
                    'upload_date': upload_date
                })
    
    files.sort(key=lambda x: x['upload_date'], reverse=True)
    return files
# ======= NEW SETUP ROUTES =======
@app.route('/webhooks/google-drive', methods=['POST'])
def webhook():
    global stored_page_token
    print("📢 Received a Google Drive webhook notification.")
    if request.data:
        print("Body:", request.get_json(silent=True) or request.data.decode('utf-8'))

    try:
        # Google Drive will send a notification when a folder change
        resource_state = request.headers.get("X-Goog-Resource-State")

        # We only care about new or updated files. `sync` is for the initial watch setup.
        if resource_state in ["update", "add", "change"]:
            service = get_drive_service()
            print("Getting Drive Service!")
            # Get changes since last check
            changes_response = service.changes().list(
                pageToken=stored_page_token,
                includeRemoved=False,  # Only new/modified files
                fields="changes(file(id,name,parents,mimeType)),newStartPageToken"
            ).execute()
            print("Changes.response occurs")

            changes = changes_response.get('changes', [])
            for change in changes:
                file = change.get('file')
                # print("📨 Resource state:", resource_state)
                if file and file.get('parents'):
                    # print("Parents: ", file.get('parents'))
                    # Check if file is in our target folder
                    if FOLDER_ID in file.get('parents', []):
                        # print("File parents:", file.get("parents"))
                        # print("Expected folder:", FOLDER_ID)
                        # Check if it's a PDF (or any file you want to process)
                        if file.get('name', '').lower().endswith('.pdf'):
                            file_id = file.get('id')
                            file_name = file.get('name')
                            print(f"🎯 New PDF in target folder: {file_name}")

                            # === Deduplication check ===
                            with lock:
                                now = time.time()
                                last_run = recent_files.get(file_id, 0)
                                if now - last_run < cooldown:
                                    print(f"⏩ Skipping duplicate event for {file_name} (ID: {file_id})")
                                    continue

                            # === Processing Wrapper ===
                            def process_file(file_id, file_name):
                                success = trigger_langgraph_processing(file_id, file_name)
                                if success:
                                    with lock:
                                        recent_files[file_id] = now
                                        print(f"✅ LangGraph triggered for {file_name}")
                                else:
                                    print(f"❌ Failed to trigger LangGraph for {file_name}")

                            # === Run in background thread ===
                            print("Starting up Thread...")
                            thread = threading.Thread(
                                target=process_file,
                                args=(file_id, file_name),
                                daemon=True
                            )
                            thread.start()
                            print("Thread started!")
            # Update stored page token for next time
            stored_page_token = changes_response.get('newStartPageToken')
            print(f"🔄 Updated page token to: {stored_page_token}")
        return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"❌ Webhook error: {e}")
        return jsonify({"error": str(e)}), 500

# Add this route to test webhook setup
@app.route("/check-webhook-status")
def check_webhook_status():
    try:
        service = get_drive_service()
        
        # This will show you if any webhooks are active
        # (Note: Google doesn't provide a direct way to list webhooks,
        # so we'll check if we can set one up)
        
        return jsonify({
            "webhook_url": webhook,
            "folder_id": FOLDER_ID,
        })
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/reset-processed", methods=["POST"])
def reset_processed():
    recent_files.clear()
    print("✅ Cleared processed files for testing.")
    return jsonify({"status": "reset", "message": "Processed files cleared"}), 200


# ======= MAIN ROUTES =======
@app.route("/", methods=["GET", "POST"])
def upload_file():
    if request.method == "POST":
        if 'file' not in request.files:
            flash('No file selected', 'error')
            return redirect(request.url)
        
        file = request.files['file']
        
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(request.url)
        
        if not file.filename.lower().endswith('.pdf'):
            flash('Only PDF files are supported', 'error')
            return redirect(request.url)
        
        try:
            filename = file.filename

            # ========= UPLOAD TO GOOGLE DRIVE =========
            file.stream.seek(0)
            upload_to_drive(file.stream, filename)

            # ========= LOCAL SAVE (COMMENTED OUT) =========
            # file_path = os.path.join(UPLOAD_FOLDER, filename)
            # counter = 1
            # original_path = file_path
            # while os.path.exists(file_path):
            #     name, ext = os.path.splitext(original_path)
            #     file_path = f"{name}_{counter}{ext}"
            #     counter += 1
            # file.save(file_path)

            flash(f'File "{filename}" uploaded to Google Drive successfully!', 'success')
            
        except Exception as e:
            flash(f'Error uploading file: {str(e)}', 'error')
        
        return redirect(request.url)
    
    stats = get_file_stats()
    files = get_uploaded_files()
    
    return render_template_string(HTML_TEMPLATE, stats=stats, files=files)

def start_watch(file_id):
    """Register a webhook w/ GDrive for a specific file"""
    service = get_drive_service()
    startPageToken = service.changes().getStartPageToken().execute()["startPageToken"] # Needed for changes().watch()
    channel_id = str(uuid.uuid4()) # Unique channel ID
    body = {
        "id": channel_id,
        "type": "web_hook",
        "address": webhook_url,
    }
    service.changes().watch(fileId=file_id, body=body).execute()
    print(f"Watch started for {file_id}")

@app.route("/notifications", methods=["POST"])
def notifications():
    # Google sends you info that "something changed"
    print("📢 Got change notification:", request.headers)
    service = get_drive_service()
    # Fetch actual files in folder
    results = service.files().list(
        q=f"'{FOLDER_ID}' in parents",
        fields="files(id, name)"
    ).execute()

    for f in results.get("files", []):
        print("New file:", f["name"])

@app.route("/run-agent")
def run_agent():
    """Page for running the recruitment agent"""
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Run Recruitment Agent</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 600px;
                margin: 0 auto;
                padding: 20px;
                background-color: #f5f5f5;
            }
            .container {
                background: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            h1 {
                color: #333;
                text-align: center;
            }
            .warning {
                background: #fff3cd;
                border: 1px solid #ffeaa7;
                padding: 15px;
                border-radius: 8px;
                margin: 20px 0;
            }
            .btn {
                display: inline-block;
                padding: 12px 24px;
                margin: 10px;
                text-decoration: none;
                border-radius: 4px;
                font-weight: bold;
            }
            .btn-primary {
                background: #28a745;
                color: white;
            }
            .btn-secondary {
                background: #6c757d;
                color: white;
            }
            .text-center {
                text-align: center;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🚀 Run Recruitment Agent</h1>
            <p>This will process all resumes in the 'resume_unscanned' folder.</p>
            
            <div class="warning">
                <h3>⚠️ This action will:</h3>
                <ul>
                    <li>Process all PDF files in resume_unscanned/</li>
                    <li>Extract applicant information (name, email)</li>
                    <li>Match resumes against job opportunities</li>
                    <li>Send emails to recruiters</li>
                    <li>Move processed files to resume_processed/</li>
                </ul>
            </div>
            
            <div class="text-center">
                <a href="/processing" class="btn btn-primary">Start Processing</a>
                <a href="/" class="btn btn-secondary">Go Back</a>
            </div>
        </div>
    </body>
    </html>
    """

@app.route("/processing")
def processing():
    """Processing status page"""
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Processing Resumes</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 600px;
                margin: 0 auto;
                padding: 20px;
                background-color: #f5f5f5;
            }
            .container {
                background: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                text-align: center;
            }
            h1 {
                color: #333;
            }
            .spinner {
                border: 4px solid #f3f3f3;
                border-top: 4px solid #007bff;
                border-radius: 50%;
                width: 40px;
                height: 40px;
                animation: spin 2s linear infinite;
                margin: 20px auto;
            }
            @keyframes spin {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }
            .btn {
                display: inline-block;
                padding: 12px 24px;
                margin: 10px;
                text-decoration: none;
                border-radius: 4px;
                font-weight: bold;
                background: #007bff;
                color: white;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔄 Processing Resumes</h1>
            <div class="spinner"></div>
            <p>The recruitment agent is now processing your resumes...</p>
            <p>This may take a few minutes depending on the number of files.</p>
            <p>You can check the console/terminal for detailed progress.</p>
            
            <a href="/" class="btn">Check File Status</a>
        </div>
    </body>
    </html>
    """


if __name__ == "__main__":
    print("🚀 Starting Flask application...")
    print(f"📁 Upload folder: {UPLOAD_FOLDER}")
    print(f"🔗 Target Drive folder ID: {FOLDER_ID}")
    
    # Print service account info for debugging
    try:
        creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE)
        print(f"🔐 Service account email: {creds.service_account_email}")
    except Exception as e:
        print(f"❌ Error reading service account: {e}")
    
    # Try automatic webhook setup if everything is configured
    print("\n🔔 WEBHOOK SETUP CHECK:")
    if webhook_url and FOLDER_ID and not stored_page_token:
        print("   🔄 Prerequisites met, attempting automatic setup...")
        try:
            initialize_drive_webhook()
            print(f"   ✅ Webhook automatically initialized!")
        except Exception as e:
            print(f"   ⚠️  Automatic setup failed: {e}")
            print("   📝 You can set it up manually at: http://localhost:8000/setup-webhook")
    elif not webhook_url:
        print("   ❌ WEBHOOK_URL not set in .env")
        print("   📝 Add: WEBHOOK_URL=https://your-ngrok-url.ngrok-free.app/webhooks/google-drive")
    elif not FOLDER_ID:
        print("   ❌ INPUT_FOLDER_ID not set in .env")
    elif stored_page_token:
        print("   ✅ Webhook already initialized")
    else:
        print("   📝 Visit http://localhost:8000/check-webhook-status to initialize")
    
    # Check for new required env vars
    if not LANGGRAPH_API_URL:
        print("   ❌ LANGGRAPH_API_URL not set in .env. This is required to trigger the agent.")
    print("\n💡 FOR TESTING:")
    print("   - To manually trigger the agent for a file already in Drive, visit:")
    print("     http://localhost:8000/test-trigger?file_id=YOUR_FILE_ID&file_name=YOUR_FILE_NAME.pdf")
    print("     (You can get a file's ID by right-clicking it in Google Drive -> Share -> Copy link)")
    
    
    app.run(debug=True, host="0.0.0.0", port=8000)