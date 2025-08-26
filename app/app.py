from flask import Flask, request, render_template_string, redirect, url_for, flash
import os
import shutil
from datetime import datetime
from pathlib import Path

# Create upload folder if it doesn't exist
UPLOAD_FOLDER = "resume_unscanned"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # Required for flash messages

# HTML template for the upload page
HTML_TEMPLATE = """
<!DOCTYPE html>
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
</html>
"""

def get_file_stats():
    """Get statistics about uploaded and processed files"""
    unscanned_count = 0
    processed_count = 0
    
    # Count files in resume_unscanned
    if os.path.exists(UPLOAD_FOLDER):
        unscanned_count = len([f for f in os.listdir(UPLOAD_FOLDER) if f.lower().endswith('.pdf')])
    
    # Count files in resume_processed
    processed_folder = "resume_processed"
    if os.path.exists(processed_folder):
        processed_count = len([f for f in os.listdir(processed_folder) if f.lower().endswith('.pdf')])
    
    return {
        'unscanned': unscanned_count,
        'processed': processed_count,
        'total': unscanned_count + processed_count
    }

def get_uploaded_files():
    """Get list of uploaded files with metadata"""
    files = []
    if os.path.exists(UPLOAD_FOLDER):
        for filename in os.listdir(UPLOAD_FOLDER):
            if filename.lower().endswith('.pdf'):
                file_path = os.path.join(UPLOAD_FOLDER, filename)
                file_stat = os.stat(file_path)
                
                # Format file size
                size_bytes = file_stat.st_size
                if size_bytes < 1024:
                    size_formatted = f"{size_bytes} B"
                elif size_bytes < 1024 * 1024:
                    size_formatted = f"{size_bytes / 1024:.1f} KB"
                else:
                    size_formatted = f"{size_bytes / (1024 * 1024):.1f} MB"
                
                # Format upload date
                upload_date = datetime.fromtimestamp(file_stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                
                files.append({
                    'filename': filename,
                    'size_formatted': size_formatted,
                    'upload_date': upload_date
                })
    
    # Sort by upload date (newest first)
    files.sort(key=lambda x: x['upload_date'], reverse=True)
    return files

@app.route("/", methods=["GET", "POST"])
def upload_file():
    if request.method == "POST":
        # Check if file was uploaded
        if 'file' not in request.files:
            flash('No file selected', 'error')
            return redirect(request.url)
        
        file = request.files['file']
        
        # Check if file was actually selected
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(request.url)
        
        # Check file type
        if not file.filename.lower().endswith('.pdf'):
            flash('Only PDF files are supported', 'error')
            return redirect(request.url)
        
        try:
            # Save file
            filename = file.filename
            file_path = os.path.join(UPLOAD_FOLDER, filename)
            
            # Handle duplicate filenames
            counter = 1
            original_path = file_path
            while os.path.exists(file_path):
                name, ext = os.path.splitext(original_path)
                file_path = f"{name}_{counter}{ext}"
                counter += 1
            
            file.save(file_path)
            flash(f'File "{os.path.basename(file_path)}" uploaded successfully!', 'success')
            
        except Exception as e:
            flash(f'Error uploading file: {str(e)}', 'error')
        
        return redirect(request.url)
    
    # GET request - show the upload page
    stats = get_file_stats()
    files = get_uploaded_files()
    
    return render_template_string(HTML_TEMPLATE, stats=stats, files=files)

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
    app.run(debug=True, host="0.0.0.0", port=8000)
    
