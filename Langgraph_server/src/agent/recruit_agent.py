# Description: This is where the agent is being created
# First, we create a LangGraph network


# Import necessary libraries
import asyncio, ssl
from langgraph.graph import StateGraph, END, START
from langgraph.graph.message import add_messages
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage
from typing import TypedDict, Optional, List, Union, Literal, Annotated
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, HttpError
from google.oauth2 import service_account
from email.mime.text import MIMEText
import fitz  # PyMuPDF
import io
import os, sys
import re
import base64
import datetime
from datetime import timedelta, time, datetime
import requests
import textwrap
 
# load environment variables
from dotenv import load_dotenv

load_dotenv()
print("Environment variables loaded")
SCOPES = ['https://www.googleapis.com/auth/drive.readonly', "https://www.googleapis.com/auth/calendar"]

# Build a robust path to the credentials file relative to the script's location.
# This ensures the script works regardless of the current working directory.
script_dir = os.path.dirname(os.path.abspath(__file__))
credentials_path = os.path.abspath(os.path.join(script_dir, '..', '..', 'google_oauth', 'credentials.json'))

# Set up the Service Account (please make sure to add you service account to any google files / calendars you will use)
creds = service_account.Credentials.from_service_account_file(
    credentials_path,
    scopes=SCOPES
)
service = build("calendar", "v3", credentials=creds)  # connect to gcal (to create events)
service_drive = build('drive', 'v3', credentials=creds) # connect to gdrive (to read files)

# Define and connect to MCP client
url= os.getenv('gentoro_mcp_url')
headers = {"Accept": "application/json, text/event-stream"}
transport = StreamableHttpTransport(url=url, headers=headers)
client = Client(transport)

# Initialize openai api key from env
llm = ChatOpenAI(model="gpt-4", temperature=0)
print("MCP Client initialized with transport, and LLM initialized")


class AgentState(TypedDict):
    # Resume fields
    # r_file_path: str  # Path to the resume file (Obsolete)
    file_id: str # The GDrive ID for the given file (provided in webhook)
    file_name: str # The name of the given resume (provided in webhook)
    raw_text: str # Raw text content of the resume
    experience_text: Optional[str] # Extracted experience section from the resume
    applicant_name: Optional[str] # Name of the applicant
    applicant_email: str # Email of the applicant
    resume_readable: bool # Flag to track if resume could be processed successfully

    # Google Drive fields
    input_folder_id: str # The ID of the Google Drive folder with the opportunities
    resume_folder_id: str # Where resumes are
    output_folder_id: str # Path where matched resumes will be placed
    drive_texts: List[dict[str,str]] # List of file names in the Google Drive folder
    recruiter_list: List[dict[str,str]] # List of recruiters to contact IF a match is found

    # Match results
    match_results: Optional[List[dict]] # List of match results with filenames and scores
    
# Gets text from a PDF file (Extracts from a google doc in a GDrive folder)
def parse_pdf_node(state: AgentState) -> AgentState:
    """Extracts text from a PDF file using PyMuPDF for better text extraction."""
    file_id = state['file_id']
    file_name = state['file_name']
    print(f"--- Starting Agent Run for file: {file_name} (ID: {file_id}) ---")

    try:

        # Create temporary download of the file in memory 
        request = service_drive.files().get_media(fileId=file_id)
        pdf_buffer = io.BytesIO() 
        downloader = MediaIoBaseDownload(pdf_buffer, request)
        
        # Check statues of download
        done = False
        while done is False:
            status, done = downloader.next_chunk()
            print(f"Download progress: {int(status.progress() * 100)}%")
        
        # Reset pdf buffer
        pdf_buffer.seek(0)

        # Open the PDF with PyMuPDF
        doc = fitz.open(stream=pdf_buffer.getvalue(), filetype='pdf')
        text = ""
        
        # Extract text from all pages
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            page_text = page.get_text()
            text += page_text + "\n"
        
        # Close the document
        doc.close()
        pdf_buffer.close() 
        
        print(f"Extracted text from {file_name} using PyMuPDF")

        if len(text) < 100:
            print(f"⚠️ Text is too short: {len(text)} characters")
            return {"raw_text": "", "applicant_name": None, "applicant_email": ""}

        # Extract applicant information
        applicant_name, applicant_email = extract_applicant_info(text)
        
        return {
            'raw_text': text,
            'applicant_name': applicant_name,
            'applicant_email': applicant_email or ""
        }
    except ssl.SSLError as e:
        print(f"❌ SSL Error while downloading {file_name}: {e}")
        return {"raw_text": "", "applicant_name": None, "applicant_email": ""}

    except HttpError as e:
        print(f"❌ Google API Error while accessing {file_name}: {e}")
        return {"raw_text": "", "applicant_name": None, "applicant_email": ""}

    except Exception as e:
        print(f"❌ Unexpected error while processing {file_name}: {e}")
        return {"raw_text": "", "applicant_name": None, "applicant_email": ""}

# Extracts the experience section from the resume text
def extract_experience_node(state: AgentState) -> AgentState:
    """Extracts the experience section from the resume text."""
    text = state["raw_text"]
    start_key = "WORK EXPERIENCE"
    end_keys = ["CERTIFICATIONS", "EDUCATION", "SKILLS", "PROJECTS", "SUMMARY"]

    # Normalize and split text into lines
    lines = text.splitlines()
    lines = [line.strip() for line in lines]

    # Find where the WORK EXPERIENCE section starts
    try:
        start_index = next(i for i, line in enumerate(lines) if start_key in line.upper())
    except StopIteration:
        print("❌ 'WORK EXPERIENCE' section not found. Resume could not be read.")
        return {"experience_text": "", "resume_readable": False}

    # Look for the next heading after WORK EXPERIENCE
    end_index = len(lines)
    for i in range(start_index + 1, len(lines)):
        if any(key in lines[i].upper() for key in end_keys):
            end_index = i
            break

    # Extract and return just the experience section
    experience_lines = lines[start_index:end_index]
    return {'experience_text':"\n".join(experience_lines), "resume_readable": True} 

def resume_unreadable_end_node(state: AgentState) -> AgentState:
    """Handles the case when resume could not be read due to missing work experience section."""
    print(f"🛑 Processing terminated: Resume '{state['file_name']}' could not be read.")
    print("   Reason: 'WORK EXPERIENCE' section not found in the resume.")
    print("   The recruitment process has been stopped for this file.")
    return state

# Cleans up text to make information extraction easier.
def clean_job_text(text: str) -> str:
    lines = text.splitlines()
    cleaned_lines = []

    for line in lines:
        # Normalize whitespace but preserve line breaks
        line = line.replace('\u00a0', ' ')  # Non-breaking space
        line = line.replace('\uf0b7', '')   # Bullet character from PDFs
        line = ' '.join(line.split())       # Collapse multiple spaces
        cleaned_lines.append(line)

    return '\n'.join(cleaned_lines)

# Gets Applicant's Name and Email from the document, stores for later use.
def extract_applicant_info(text):
    """Extract applicant name and email from resume text"""
    applicant_name = None
    applicant_email = None
    
    # Email extraction using regex
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    email_match = re.search(email_pattern, text)
    if email_match:
        applicant_email = email_match.group(0)
        print(f"📧 Found email: {applicant_email}")
    
    # Name extraction - look for common patterns
    lines = text.split('\n')
    
    # Strategy 1: Look for name at the top of the resume (first few lines)
    for i, line in enumerate(lines[:10]):  # Check first 10 lines
        line = line.strip()
        if len(line) > 0 and len(line) < 100:  # Reasonable name length
            # Skip lines that are clearly not names
            if any(skip_word in line.lower() for skip_word in [
                'resume', 'cv', 'curriculum vitae', 'phone', 'email', 'address',
                'objective', 'summary', 'experience', 'education', 'skills',
                'linkedin', 'github', 'portfolio', 'website', 'http', 'www'
            ]):
                continue
            
            # Check if line looks like a name (contains letters, spaces, maybe dots)
            if re.match(r'^[A-Za-z\s\.]+$', line) and len(line.split()) <= 4:
                # Additional check: should not contain common non-name words
                if not any(word in line.lower() for word in [
                    'resume', 'cv', 'phone', 'email', 'address', 'objective',
                    'summary', 'experience', 'education', 'skills'
                ]):
                    applicant_name = line.strip()
                    print(f"👤 Found name: {applicant_name}")
                    break
    
    # Strategy 2: If no name found, look for patterns like "Name: John Doe"
    if not applicant_name:
        name_patterns = [
            r'name\s*:\s*([A-Za-z\s\.]+)',
            r'full\s+name\s*:\s*([A-Za-z\s\.]+)',
            r'contact\s+name\s*:\s*([A-Za-z\s\.]+)'
        ]
        
        for pattern in name_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                applicant_name = match.group(1).strip()
                print(f"👤 Found name (pattern): {applicant_name}")
                break
    
    # Strategy 3: Look for LinkedIn-style names (if email contains name)
    if not applicant_name and applicant_email:
        email_name = applicant_email.split('@')[0]
        # Clean email name (remove numbers, dots, underscores)
        clean_name = re.sub(r'[0-9._-]', ' ', email_name)
        clean_name = ' '.join(clean_name.split())
        if len(clean_name) > 2 and len(clean_name.split()) <= 3:
            applicant_name = clean_name.title()
            print(f"👤 Found name (from email): {applicant_name}")
    
    return applicant_name, applicant_email

# Extracts a recruiters email and filename.
def extract_recruiter_emails_node(state: AgentState) -> AgentState:
    recruiter_list = state.get("recruiter_list", [])
    drive_entries = state.get("drive_texts", [])

    for entry in drive_entries:
        filename = entry.get("filename", "Unknown")
        text = entry.get("text", "")

        # Use regex to find email in the beginning of the text
        email_matches = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", text)

        if email_matches:
            email = email_matches[0]  # Only get the first one
            
            # Extract recruiter name
            recruiter_name = None
            
            # Strategy 1: Look for common name patterns near the email
            name_patterns = [
                r'from\s*:\s*([A-Za-z\s\.]+)',
                r'sent\s+by\s*:\s*([A-Za-z\s\.]+)',
                r'contact\s*:\s*([A-Za-z\s\.]+)',
                r'recruiter\s*:\s*([A-Za-z\s\.]+)',
                r'hiring\s+manager\s*:\s*([A-Za-z\s\.]+)',
                r'contact\s+name\s*:\s*([A-Za-z\s\.]+)'
            ]
            
            for pattern in name_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    recruiter_name = match.group(1).strip()
                    print(f"👤 Found recruiter name (pattern): {recruiter_name}")
                    break
            
            # Strategy 2: Extract name from email if no pattern found
            if not recruiter_name:
                email_name = email.split('@')[0]
                # Clean email name (remove numbers, dots, underscores)
                clean_name = re.sub(r'[0-9._-]', ' ', email_name)
                clean_name = ' '.join(clean_name.split())
                if len(clean_name) > 2 and len(clean_name.split()) <= 3:
                    recruiter_name = clean_name.title()
                    print(f"👤 Found recruiter name (from email): {recruiter_name}")
            
            recruiter_list.append({
                "email": email,
                "name": recruiter_name,
                "job_file": filename
            })
            print(f"📧 Found recruiter email: {email} in {filename}")
        else:
            print(f"⚠️ No email found in {filename}")

    return {"recruiter_list": recruiter_list}

# Opens a google drive folder and reads all files in it, put files in a list
def read_drive_folder_node(state: AgentState) -> AgentState:
    folder_id = state["resume_folder_id"]

    query = f"'{folder_id}' in parents and trashed = false"
    response = service_drive.files().list(q=query, fields="files(id, name, mimeType)").execute()
    files = response.get('files', [])

    all_texts = []

    for file in files:
        file_id = file['id']
        file_name = file['name']
        mime_type = file['mimeType']

        try:
            request = service_drive.files().get_media(fileId=file_id) # Get the file content
            fh = io.BytesIO() 
            downloader = MediaIoBaseDownload(fh, request) 

            done = False
            while not done:
                try:
                    status, done = downloader.next_chunk() 
                except ssl.SSLError as e:
                    print(f"SSL error while downloading file: {e}")
                    raise
                except Exception as e:
                    print(f"Unexpected error: {e}")
                    raise


            fh.seek(0) # Reset the file handle to the beginning

            if mime_type == 'application/pdf':
                # Use PyMuPDF for better text extraction
                doc = fitz.open(stream=fh, filetype="pdf")
                text = ""
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    page_text = page.get_text()
                    text += page_text + "\n"
                doc.close()
                print(f"✅ Extracted PDF: {file_name}")
            elif mime_type.startswith("text/"): # 
                text = fh.read().decode("utf-8")
                print(f"✅ Extracted text file: {file_name}")
            else:
                print(f"⚠️ Skipping unsupported file type: {file_name} ({mime_type})")
                continue
            
            cleaned = clean_job_text(text)
            cleaned = cleaned.lower()  # Normalize to lowercase for easier matching

            # Check if the resume is blank or too short after cleaning
            if not cleaned or len(cleaned.strip()) < 50:
                print(f"⚠️ Skipping blank or too short resume: {file_name} (length: {len(cleaned.strip())})")
                continue

            # 🔍 Improved requirements extraction
            requirement_text = ""
            lines = cleaned.splitlines()
            lines = [line.strip() for line in lines if line.strip()]

            # Try finding a requirements block based on heading cues
            start_index = None
            end_index = len(lines)
            end = False

            # Expanded heading keys to match more patterns
            start_keys = [
                "skills required", "required skills", "job requirements"
            ]
            end_keys = [
                "nice to have", 
                "about you", "about the team", "about the company"
            ]

            print(f"🔍 Total non-empty lines after cleaning: {len(lines)}")
            for i, line in enumerate(lines[2:]):
                if any(key in line.lower() for key in start_keys):
                    start_index = i-1
                    #print(f"Found start key: {line}") # for sample opp. 2, cleaning turns it into 3 lines total, figure it out later
                    break

            if start_index is not None:
                end=False
                print("Found start key!")
                for j in range(start_index+1, len(lines)):
                    if any(key in lines[j].lower() for key in end_keys):
                        print("Found end key!")
                        end_index = j
                        end = True
                        break
            else:
                print("⚠️ No start key found, using entire text as requirements.")
                start_index = 0
         
            if not end:
                end_index = len(lines)
            
            requirement_text = "\n".join(lines[start_index-5:end_index])
            print(f"✅ Extracted requirements from {file_name}")

            # ✅ Append structured entry
            all_texts.append({
                "filename": file_name,
                "text": cleaned,
                "requirements": requirement_text
            })

        except Exception as e:
            print(f"❌ Error reading file {file_name}: {e}")
    return {"drive_texts": all_texts}

# Compares the resume experience with the job requirements using an llm
def match_resume_node(state: AgentState) -> AgentState:
    """Compares the resume experience with the job requirements using an LLM."""
    resume = state.get("experience_text")
    drive_texts = state.get("drive_texts")
    recruiter_list = state.get("recruiter_list")

    match_results = []

    for entry in drive_texts: 
        requirements = entry.get("requirements", "")
        filename = entry.get("filename", "Unknown")

        recruiter_email = None
        for recruiter in recruiter_list:
            if recruiter.get("job_file") == filename:
                recruiter_email = recruiter.get("email")
                name = recruiter.get("name")


        if not requirements.strip():
            print(f"⚠️ No requirements found in {filename}, skipping.")
            continue  
        prompt = f"""
        You're a recruiting assistant. Compare the resume experience below with the job requirements, and rate the match on a scale from 1 to 10. 
        It is most important that the candidate meets the job requirements. Please add a yes or no to the end pertaining to if all the requirements are met.
        If a resume has all the requirements, you will send an email to the recruiter, skills desired are not important. be consistent.
        If the candidate meets all job requirements, you will send an email to the recruiter. 
        Also give a two-sentence explanation. If a resume does not meet the requirements, explain why.
        Resume Experience:
        \"\"\"
        {resume}
        \"\"\"
        Job Requirements:
        \"\"\"
        {requirements}
        \"\"\"
        Return your answer in this format:
        Score: X/10
        Did Meet All Requirements: Yes/No (If yes, will be sending an email to the recruiter)
        Comment: <your explanation here>
        """
        try:
            response = llm.invoke(prompt)
            content = response.content.strip()

            # Check if requirements are met OR score is 8 or above
            requirements_met = "Did Meet All Requirements: Yes" in content
            
            # Extract score from the response
            score_match = re.search(r'Score:\s*(\d+)/10', content)
            score = None
            if score_match:
                score = int(score_match.group(1))
            
            # Accept if requirements are met OR score is 8 or above
            if requirements_met or (score is not None and score >= 8):
                match_results.append({
                    "recruiter_email": recruiter_email,
                    "name": name,
                    "filename": filename,
                    "match_score": content
                })
                print(f"✅ Match accepted for {filename} - Requirements met: {requirements_met}, Score: {score}")
            else:
                print(f"❌ Match rejected for {filename} - Requirements met: {requirements_met}, Score: {score}")
            
            print(f"✅ Match result for {filename}:\n{content}\n")
        except Exception as e:
            print(f"❌ Error matching {filename}: {e}")
    return {"match_results": match_results}

# Sends emails to recruiters with matched resumes { Note: Need to remove recruiter list, rec. email is now min matched results}
async def send_recruiter_emails_node(state: AgentState) -> AgentState:
    """Downloads the resume and emails it as an attachment to recruiters for matched jobs."""
    async with client:
        print("--- Sending Recruiter Emails ---")

        match_results = state.get("match_results", [])
        if not match_results:
            print("No matches found, skipping recruiter emails.")
            return state

        # Get resume info from state
        file_id = state.get("file_id")
        resume_filename = state.get("file_name", "resume.pdf")
        applicant_name = state.get("applicant_name", "the candidate")
        applicant_email = state.get("applicant_email", "")

        # Download resume content to attach it to emails
        encoded_file = None
        if file_id:
            try:
                print(f"⬇️  Downloading resume '{resume_filename}' to attach to emails...")
                request = service_drive.files().get_media(fileId=file_id)
                pdf_buffer = io.BytesIO()
                downloader = MediaIoBaseDownload(pdf_buffer, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                pdf_buffer.seek(0)
                file_data = pdf_buffer.read()
                encoded_file = base64.b64encode(file_data).decode('utf-8')
                print("✅ Resume downloaded and encoded for attachment.")
            except Exception as e:
                print(f"⚠️ Could not download resume for attachment: {e}")
        
        emails_sent = 0
        # Iterate through matches to send emails
        for match in match_results:
            recruiter_email = match.get("recruiter_email")
            recruiter_name = match.get("name", "Recruiter")
            job_filename = match.get("filename", "your job opportunity")

            if not recruiter_email:
                print(f"⚠️ No recruiter email for match with {job_filename}, skipping.")
                continue

            subject = f"Potential Candidate Match for {job_filename}"
            
            # Create email body
            if applicant_name and applicant_name != "the candidate":
                candidate_info = f"**{applicant_name}**"
                if applicant_email:
                    candidate_info += f" ({applicant_email})"
            else:
                candidate_info = "a candidate"
            
            llm_notes = match.get("match_score", "No specific notes from the LLM.")
            
            body = textwrap.dedent(f"""\
    Hi {recruiter_name},

    I'm reaching out on behalf of our recruiting team. We've reviewed your job listing for **{job_filename}** and found {candidate_info} whose resume appears to be a strong match.

    I've attached the candidate's resume for your review. If you'd like to connect with them, please let us know.

    Best regards,  
    Recruitment Assistant Agent  
    Gentoro AI
    
    ---
    LLM Match Analysis:
    {llm_notes}
    """)

            # Prepare payload with attachment if available
            email_payload = {
                'sender_id': "me",
                'recipient_email': recruiter_email,
                'subject': subject,
                'body': body,
            }
            if encoded_file:
                email_payload['attachments'] = [
                    {
                        'filename': resume_filename,
                        'file_bytes': encoded_file
                    }
                ]
                print_attachment_msg = f"with attachment '{resume_filename}'"
            else:
                print_attachment_msg = "without attachment"

            try:
                await client.call_tool('google_mail_send_email', email_payload)
                print(f"✅ Email sent to {recruiter_email} {print_attachment_msg}")
                emails_sent += 1
            except Exception as e:
                print(f"❌ Failed to send email to {recruiter_email}: {e}")

        if emails_sent == 0:
            print("⚠️ No recruiter emails were sent for this resume.")

        return state

# Helper Function to find all Free slots for a given time window (Set to 9am to 5pm PST)
def _collect_slots_in_window(busy_intervals, window_start, window_end, max_to_collect):
    """Collect up to max_to_collect 30-minute slots within [window_starts, window_end)."""
    slots = []
    cursor = window_start
    for start, end in busy_intervals:
        # Ignore busy blocks fully before our cursor, ignores all past events
        if end <= cursor:
            continue
        # Fill any free gap before the next busy start { If a busy block is found after cursor -> there is some free time }
        if start > cursor:
            free_until = min(start, window_end)
            while max_to_collect > 0 and cursor + timedelta(minutes=30) <= free_until:
                slots.append({
                    "start": cursor.isoformat(),
                    "end": (cursor + timedelta(minutes=30)).isoformat()
                })
                cursor += timedelta(minutes=30) # iterate in 30 min intervals to find all 30min slots
                max_to_collect -= 1
                if max_to_collect == 0:
                    return slots
        # Advance cursor to after this busy block
        cursor = max(cursor, end)
        if cursor >= window_end:
            return slots
    # After last busy block, try to fill until window end
    while max_to_collect > 0 and cursor + timedelta(minutes=30) <= window_end:
        slots.append({
            "start": cursor.isoformat(),
            "end": (cursor + timedelta(minutes=30)).isoformat()
        })
        cursor += timedelta(minutes=30)
        max_to_collect -= 1
    return slots

# Find a recruiters free times so they can be sent to the applicant for an interview
def find_free_time_(service, email, weeks_to_check=2, morning_needed=2, afternoon_needed=2):
    """
    Find two morning (9:00–12:00) and two afternoon (13:00–17:00) 30-min free slots.
    Starts searching from the day after today (or next Monday if today is Friday).
    Returns dict with lists of slots under keys "morning" and "afternoon".
    """
    # Want the variable for the day after
    today = datetime.now()
    tomorrow = today + timedelta(days=3) 

    # If Friday, start next Monday; otherwise, start tomorrow
    if today.weekday() == 4:
        search_start = today + timedelta(days=3)
    else:
        search_start = tomorrow
    # Monday of the start week
    monday = search_start - timedelta(days=search_start.weekday())

    print(f"🔍 Checking availability for: {email}")
    print(f"📅 Today is: {today.strftime('%A, %Y-%m-%d')}")
    print(f"📅 Starting search from: {search_start.strftime('%Y-%m-%d')}")
    print(f"📅 Checking {weeks_to_check} weeks ahead")
    print()

    morning_slots = []
    afternoon_slots = []

    for week_offset in range(weeks_to_check):
        week_start = monday + timedelta(days=week_offset * 7)
        week_end = week_start + timedelta(days=5)  # Mon–Fri
        print(f"📅 Week {week_offset + 1}: {week_start.strftime('%Y-%m-%d')} to {week_end.strftime('%Y-%m-%d')}")

        freebusy_query = {
            "timeMin": week_start.isoformat() + "Z",
            "timeMax": week_end.isoformat() + "Z",
            "timeZone": "America/Los_Angeles",
            "items": [{"id": email}],
        }

        try:
            response = service.freebusy().query(body=freebusy_query).execute()
            if email not in response.get("calendars", {}):
                print(f"⚠️ No calendar access for {email}")
                continue

            busy_times = response["calendars"][email].get("busy", [])
            # Normalize busy intervals to naive datetimes for comparison
            busy_intervals = [
                (datetime.fromisoformat(b["start"].replace("Z", "+00:00")).replace(tzinfo=None),
                 datetime.fromisoformat(b["end"].replace("Z", "+00:00")).replace(tzinfo=None))
                for b in busy_times
            ]
            busy_intervals.sort(key=lambda x: x[0])
            print(f"📊 Found {len(busy_intervals)} busy time blocks")

            for day_offset in range(5):
                if len(morning_slots) >= morning_needed and len(afternoon_slots) >= afternoon_needed:
                    break
                day = week_start + timedelta(days=day_offset)
                # Skip days before search_start
                if day.date() < search_start.date():
                    continue

                # Morning window 09:00–12:00
                if len(morning_slots) < morning_needed:
                    m_start = datetime.combine(day.date(), time(9, 0))
                    m_end = datetime.combine(day.date(), time(12, 0))
                    m_found = _collect_slots_in_window(busy_intervals, m_start, m_end, morning_needed - len(morning_slots))
                    for s in m_found:
                        morning_slots.append({
                            **s,
                            "week": week_offset + 1,
                            "day": day.strftime('%A'),
                            "date": day.strftime('%Y-%m-%d'),
                            "period": "morning"
                        })
                        print(f"✅ Morning slot: {day.strftime('%A %Y-%m-%d')} at {datetime.fromisoformat(s['start']).strftime('%I:%M %p')}")
                        if len(morning_slots) >= morning_needed:
                            break

                # Afternoon window 13:00–17:00
                if len(afternoon_slots) < afternoon_needed:
                    a_start = datetime.combine(day.date(), time(13, 0))
                    a_end = datetime.combine(day.date(), time(17, 0))
                    a_found = _collect_slots_in_window(busy_intervals, a_start, a_end, afternoon_needed - len(afternoon_slots))
                    for s in a_found:
                        afternoon_slots.append({
                            **s,
                            "week": week_offset + 1,
                            "day": day.strftime('%A'),
                            "date": day.strftime('%Y-%m-%d'),
                            "period": "afternoon"
                        })
                        print(f"✅ Afternoon slot: {day.strftime('%A %Y-%m-%d')} at {datetime.fromisoformat(s['start']).strftime('%I:%M %p')}")
                        if len(afternoon_slots) >= afternoon_needed:
                            break

            if len(morning_slots) >= morning_needed and len(afternoon_slots) >= afternoon_needed:
                break

        except Exception as e:
            print(f"❌ Error fetching freebusy for week {week_offset + 1}: {e}")
            continue

    return {"morning": morning_slots, "afternoon": afternoon_slots}

# After getting recruiter times, send applicant email to know of:
# 1. the match 2. potential meeting times 3. If reschedule, contact recruiter directly.

async def send_applicant_emails_node(state: AgentState) -> AgentState:
    ''' 
    Send the applicant an email that congratulates them, tells them which job, the name & email of the recruiter, 
    and shares 4 potential slots for an interview.
    '''
    async with client:
        print("Preparing to send emails to applicants...")

        # Get state variables
        match_list = state.get("match_results", [])
        applicant_name = state.get("applicant_name", "Candidate")
        applicant_email = state.get("applicant_email", "")
        
        if not applicant_email:
            print("⚠️ No applicant email found, cannot send notification")
            return state
        
        if not match_list:
            print("⚠️ No matches found, no email to send")
            return state

        emails_sent = 0

        # For every matched job
        for match in match_list:
            recruiter_email = match.get("recruiter_email")
            filename = match.get("filename", "").strip()
            # job_title = match.get("job_title", "Position") # Future Additions
            # company_name = match.get("company", "the company") # Future Additions
            
            if not recruiter_email:
                print(f"⚠️ No recruiter email found for match: {filename}")
                continue
            
            recruiter_name = recruiter_email.split('@')[0].replace('.', ' ').title()  # Extract name from email
            
            print(f"Processing match for recruiter {recruiter_email}")
            
            try:
                # Use your existing helper function to get 4 free slots - 2 morning, 2 afternoon
                slots = find_free_time_(service, recruiter_email, weeks_to_check=2, morning_needed=2, afternoon_needed=2)
                morning_slots = slots.get("morning", [])
                afternoon_slots = slots.get("afternoon", [])
                
                # Format available times
                times_text = ""
                all_slots = morning_slots + afternoon_slots
                
                if all_slots:
                    times_text = "Here are some available meeting times with the recruiter:\n\n"
                    
                    if morning_slots:
                        times_text += "**Morning Options (9 AM - 12 PM):**\n"
                        for i, slot in enumerate(morning_slots, 1):
                            start_time = datetime.fromisoformat(slot["start"])
                            end_time = datetime.fromisoformat(slot["end"])
                            times_text += f"   {i}. {slot['day']}, {slot['date']} - {start_time.strftime('%I:%M %p')} to {end_time.strftime('%I:%M %p')}\n"
                        times_text += "\n"
                    
                    if afternoon_slots:
                        times_text += "**Afternoon Options (1 PM - 5 PM):**\n"
                        for i, slot in enumerate(afternoon_slots, len(morning_slots) + 1):
                            start_time = datetime.fromisoformat(slot["start"])
                            end_time = datetime.fromisoformat(slot["end"])
                            times_text += f"   {i}. {slot['day']}, {slot['date']} - {start_time.strftime('%I:%M %p')} to {end_time.strftime('%I:%M %p')}\n"
                        times_text += "\n"
                    
                    times_text += "Please reply to this email with your preferred time slot number, and we'll coordinate with the recruiter to confirm the meeting.\n"
                else:
                    times_text = "We're working on coordinating meeting times with the recruiter and will follow up with availability soon.\n"
                
                # Get LLM match notes
                llm_notes = match.get("match_score", "")
                match_summary = ""
                if llm_notes:
                    # Clean up the LLM notes for better presentation
                    match_summary = llm_notes.replace('Score:', 'Match Score:').replace('Did Meet All Requirements:', 'Requirements Met:').replace('Comment:', 'Feedback:')
                
                subject = f"Exciting News! You're a Match!"

                # TO BE ADDED
                # 🏢 **Company:** {company_name}
                # 💼 **Position:** {job_title}
                
                body = textwrap.dedent(f"""\
Hi {applicant_name},

Congratulations! 🎉 Your resume has been matched with an exciting opportunity, and the recruiter is interested in connecting with you.

**Job Details:**
👤 **Recruiter:** {recruiter_name}
📧 **Contact:** {recruiter_email}

{times_text}

**Next Steps:**
1. Review the available meeting times above
2. Reply to this email with your preferred time slot number
3. We'll coordinate with {recruiter_name} to confirm the meeting
4. Alternatively, you can reach out directly to {recruiter_email} if you need different times

We're excited about this potential match and wish you the best of luck with your interview!

Best regards,  
Recruitment Assistant Agent  
Gentoro AI

---
This is an automated message. Please reply with your preferred meeting time or contact the recruiter directly.
""")

                # Send the email
                await client.call_tool('google_mail_send_email', {
                    'sender_id': "me",
                    'recipient_email': applicant_email,
                    'subject': subject,
                    'body': body
                })
                
                # print(f"✅ Email sent to {applicant_email} for {job_title} position at {company_name}")
                print(f" 📅 Included {len(all_slots)} available time slots")
                emails_sent += 1
                
            except Exception as e:
                # print(f"❌ Failed to process match for {job_title}: {e}")
                print(f"🔍 Error type: {type(e).__name__}")
                continue

        if emails_sent == 0:
            print("⚠️ No applicant emails sent.")
        else:
            print(f"✅ Successfully sent {emails_sent} notification(s) to {applicant_email}")

        return state

def should_continue_processing(state: AgentState) -> str:
    """Determines whether to continue processing or end due to unreadable resume."""
    if not state.get("resume_readable", True):
        return "resume_unreadable_end"
    else:
        return "read_drive_folder"

# Build LangGraph
builder = StateGraph(AgentState)

# Nodes
builder.add_node("parse_pdf", parse_pdf_node)
builder.add_node("extract_experience", extract_experience_node)
builder.add_node("resume_unreadable_end", resume_unreadable_end_node)
builder.add_node("read_drive_folder", read_drive_folder_node)
builder.add_node("extract_recruiters_emails_node", extract_recruiter_emails_node)
builder.add_node("match_resume_node", match_resume_node)
builder.add_node("send_recruiter_emails_node", send_recruiter_emails_node)
builder.add_node("send_app_email_node", send_applicant_emails_node)

# Edges
builder.add_edge(START, "parse_pdf")
builder.add_edge("parse_pdf", "extract_experience")
builder.add_conditional_edges(
    "extract_experience",
    should_continue_processing,
    {
        "resume_unreadable_end": "resume_unreadable_end",
        "read_drive_folder": "read_drive_folder"
    }
)
builder.add_edge("read_drive_folder", "extract_recruiters_emails_node")
builder.add_edge("extract_recruiters_emails_node", "match_resume_node")
builder.add_edge("match_resume_node", "send_recruiter_emails_node")
builder.add_edge("send_recruiter_emails_node", "send_app_email_node")
builder.add_edge("send_app_email_node", END)
builder.add_edge("resume_unreadable_end", END)


# Compile the graph for the Langgraph API
graph = builder.compile()
print("Graph is compiled!") 

# Run the graph locally (used for initial testing)
# async def main():
#     async with client:
#         # Define the resume folder to scan
#         resume_folder = 'resume_unscanned'
        
#         # Check if the folder exists
#         if not os.path.exists(resume_folder):
#             print(f"❌ Resume folder '{resume_folder}' not found")
#             return
        
#         # Get all PDF files in the resume folder
#         pdf_files = []
#         for file in os.listdir(resume_folder):
#             if file.lower().endswith('.pdf'):
#                 pdf_files.append(os.path.join(resume_folder, file))
        
#         if not pdf_files:
#             print(f"❌ No PDF files found in '{resume_folder}' folder")
#             sys.exit(0) # Kill the
        
#         print(f"📁 Found {len(pdf_files)} PDF files in '{resume_folder}' folder")
        
#         # Process each resume file one by one
#         for i, resume_path in enumerate(pdf_files, 1):
#             print(f"\n{'='*60}")
#             print(f"📄 Processing Resume {i}/{len(pdf_files)}: {os.path.basename(resume_path)}")
#             print(f"{'='*60}")
            
#             try:
#                 # Initialize state for this resume
#                 state = {
#                     "folder_id": '1-In_XSM7YPLHWjjFNChYtaYCOb989lXT',
#                     "drive_texts": [],
#                     "r_file_path": resume_path,
#                     "raw_text": '',
#                     "experience_text": None,
#                     "applicant_name": None,  # Initialize applicant name as None
#                     "applicant_email": '',   # Initialize applicant email as empty string
#                     "recruiter_list": [],  # Initialize as empty list
#            
#                 }
                
#                 # Process this resume
#                 result = await graph.ainvoke(state)
                
#                 # Print summary for this resume
#                 print(f"\n✅ Completed processing: {os.path.basename(resume_path)}")
                
#                 # Move processed file to a different folder
#                 try:
#                     processed_folder = 'resume_processed'
#                     if not os.path.exists(processed_folder):
#                         os.makedirs(processed_folder)
#                     new_path = os.path.join(processed_folder, os.path.basename(resume_path))
#                     os.rename(resume_path, new_path)
#                     print(f"📁 Moved to: {new_path}")
#                 except Exception as move_error:
#                     print(f"⚠️ Could not move file: {move_error}")
                
#             except Exception as e:
#                 print(f"❌ Error processing {os.path.basename(resume_path)}: {e}")
#                 print(f"🔍 Error type: {type(e).__name__}")
#                 continue
        
#         print(f"\n{'='*60}")
#         print(f"🏁 Completed processing {len(pdf_files)} resume files")
#         print(f"{'='*60}")

# if __name__ == "__main__":
#     asyncio.run(main())