# Description: This is where the agent is being created
# First, we create a LangGraph network


# Import necessary libraries
import asyncio
from langgraph.graph import StateGraph, END, START
from langgraph.graph.message import add_messages
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage
from typing import TypedDict, Optional, List, Union, Literal, Annotated
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2 import service_account
from email.mime.text import MIMEText
from pypdf import PdfReader
import io
import os
import re
import base64
 
# load environment variables
from dotenv import load_dotenv

load_dotenv()
print("Environment variables loaded")
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
credentials_path = os.path.join(os.getcwd(), 'google_project_json', 'credentials.json')
creds = service_account.Credentials.from_service_account_file(
    credentials_path,
    scopes=SCOPES
)

# Define MCP client
url= os.getenv('gentoro_mcp_url')
headers = {"Accept": "application/json, text/event-stream"}
transport = StreamableHttpTransport(url=url, headers=headers)
client = Client(transport)

# Initialize openai api key from env
llm = ChatOpenAI(model="gpt-4", temperature=0)

 
print("MCP Client initialized with transport")

class AgentState(TypedDict):
    # Resume fields
    r_file_path: str  # Path to the resume file
    raw_text: str # Raw text content of the resume
    experience_text: Optional[str] # Extracted experience section from the resume
    applicant_name: Optional[str] # Name of the applicant
    applicant_email: str # Email of the applicant

    # Google Drive fields
    folder_id: str # The path to the Google Drive with the opportunities
    drive_texts: List[dict[str,str]] # List of file names in the Google Drive folder
    recruiter_list: List[dict[str,str]] # List of recruiters to contact IF a match is found

    # Match results
    match_results: Optional[List[dict]] # List of match results with filenames and scores
    
    # Calendar fields
    calendar_info: Optional[dict] # Calendar availability information for recruiters
    

# function to search for the linkedin profile (WIP: Linkedin does not like being scraped via Tavily)
async def LinkedIn_search(topic):
    return await client.call_tool('tavily_search_information', 
                    {
                     'topic':topic,
                     'search_depth': 'basic',
                     'max_results': '3',
                     'include_images': 'false',
                    }
)

# Gets text from a PDF file
def parse_pdf_node(state: AgentState) -> AgentState:
    """Extracts text from a PDF file."""
    try:
        r_fp = state["r_file_path"]
        reader = PdfReader(r_fp)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        print(f"Extracted text from {r_fp}")

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
    except Exception as e:
        print(f"Error extracting text from PDF: {e}")
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
        print("⚠️ 'WORK EXPERIENCE' section not found.")
        return {"experience_text": ""}

    # Look for the next heading after WORK EXPERIENCE
    end_index = len(lines)
    for i in range(start_index + 1, len(lines)):
        if any(key in lines[i].upper() for key in end_keys):
            end_index = i
            break

    # Extract and return just the experience section
    experience_lines = lines[start_index:end_index]
    return {'experience_text':"\n".join(experience_lines)} 

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
            recruiter_list.append({
                "email": email,
                "job_file": filename
            })
            print(f"📧 Found recruiter email: {email} in {filename}")
        else:
            print(f"⚠️ No email found in {filename}")

    return {"recruiter_list": recruiter_list}

# Opens a google drive folder and reads all files in it, put files in a list
def read_drive_folder_node(state: AgentState) -> AgentState:
    folder_id = state["folder_id"]
    service = build('drive', 'v3', credentials=creds)  # assumes credentials already handled

    query = f"'{folder_id}' in parents and trashed = false"
    response = service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    files = response.get('files', [])

    all_texts = []

    for file in files:
        file_id = file['id']
        file_name = file['name']
        mime_type = file['mimeType']

        try:
            request = service.files().get_media(fileId=file_id) # Get the file content
            fh = io.BytesIO() 
            downloader = MediaIoBaseDownload(fh, request) 

            done = False
            while not done:
                status, done = downloader.next_chunk() 

            fh.seek(0) # Reset the file handle to the beginning

            if mime_type == 'application/pdf':
                reader = PdfReader(fh)
                text = ""
                for page in reader.pages:
                    text += page.extract_text() or ""
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

    match_results = []

    for entry in drive_texts: 
        requirements = entry.get("requirements", "")
        filename = entry.get("filename", "Unknown")

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
            response = llm.invoke([HumanMessage(content=prompt)])
            content = response.content.strip()

            if "Did Meet All Requirements: Yes" in content:
                match_results.append({
                    "filename": filename,
                    "match_score": content
                })
            print(f"✅ Match result for {filename}:\n{content}\n")
        except Exception as e:
            print(f"❌ Error matching {filename}: {e}")
    return {"match_results": match_results}

# Sends emails to recruiters with matched resumes
async def send_recruiter_emails_node(state: AgentState) -> AgentState:
    async with client:
        print("Preparing to send emails to recruiters...")

        recruiter_list = state.get("recruiter_list", [])
        match_results = state.get("match_results", [])
        resume_path = state.get("r_file_path", "")
        resume_filename = os.path.basename(resume_path)

        print("Matched Jobs: ", match_results)
        print("Recruiter List: ", recruiter_list)

        emails_sent = 0  # <-- tracking variable

        # Get applicant information
        applicant_name = state.get("applicant_name", "the candidate")
        applicant_email = state.get("applicant_email", "")
        
        for recruiter in recruiter_list:
            filename = recruiter.get("job_file")
            filename = filename.strip()  # Ensure no leading/trailing spaces
            email = recruiter.get("email")
            job_title = recruiter.get("job_title", "your job opportunity")
            print("Filename: ", filename)
            print(f"Checking recruiter {email} for job {job_title}...")
            

            if any(match["filename"] == filename for match in match_results) and email:
                subject = f"Candidate Resume Match for {job_title}"
                print("Subject of the email: ", subject)
                
                # Create email body with applicant information
                if applicant_name and applicant_name != "the candidate":
                    candidate_info = f"**{applicant_name}**"
                    if applicant_email:
                        candidate_info += f" ({applicant_email})"
                else:
                    candidate_info = "a candidate"
                
                body = f"""\
    Hi {email.split('@')[0].capitalize()},

    I'm reaching out on behalf of our recruiting team. We've reviewed your job listing for **{job_title}** and found {candidate_info} whose resume matches the key requirements closely.

    I've attached the candidate's resume for your review. If you'd like to connect with them, please let us know.

    Best regards,  
    Recruitment Assistant Agent  
    Gentoro AI
    """

                try:
                    # with open(resume_path, "rb") as f:
                    #     file_data = f.read()
                    #     encoded_file = base64.b64encode(file_data).decode('utf-8')

                    await client.call_tool('google_mail_send_email', {
                        'sender_id': "me",
                        'recipient_email': email,
                        'subject': subject,
                        'body': body,
                        # 'attachments': [
                        #     {
                        #         'filename': resume_filename,
                        #         'file_bytes': encoded_file
                        #     }
                        # ]
                    })
                    print(f"✅ Email sent to {email} with attachment {resume_filename}")
                    emails_sent += 1  # <-- update count

                except Exception as e:
                    print(f"❌ Failed to send email to {email}: {e}")
                    print(f"🔍 Error type: {type(e).__name__}")
                    print(f"🔍 Full error details: {str(e)}")

                if emails_sent == 0:
                    print("⚠️ No matching recruiters found or no emails sent.")

        return state

# After a matched recruiter is found, check their calendar for availability
async def check_calendar_node(state: AgentState) -> AgentState:
    """Check calendar availability for recruiters using Cal.com API through MCP"""
    print("📅 Checking calendar availability for recruiters...")
    
    from datetime import datetime, timedelta
    
    # Get matched recruiters
    match_results = state.get("match_results", [])
    recruiter_list = state.get("recruiter_list", [])
    
    if not match_results:
        print("⚠️ No matched recruiters found for calendar check")
        return state
    
    # Find recruiters with matches
    matched_recruiters = []
    for recruiter in recruiter_list:
        filename = recruiter.get("job_file", "").strip()
        if any(match["filename"] == filename for match in match_results):
            matched_recruiters.append(recruiter)
    
    if not matched_recruiters:
        print("⚠️ No matched recruiters found for calendar check")
        return state
    
    print(f"🔍 Found {len(matched_recruiters)} matched recruiters to check calendar for")
    
    # Check calendar for zjchouhry (assuming all recruiters use the same calendar)
    username = "zjchoudhry"
    tomorrow = datetime.now() + timedelta(days=1)
    tomorrow_str = tomorrow.strftime('%Y-%m-%d')
    
    print(f"📅 Checking availability for {username} on {tomorrow_str}")
    
    try:
        async with client:
            # Try different possible Cal.com tool names
            possible_tool_names = [
                'weekly_calendar_free_times'
            ]
            
            availability_data = None
            used_tool = None
            
            for tool_name in possible_tool_names:
                try:
                    print(f"🔍 Trying Cal.com tool: {tool_name}")
                    
                    # First, get calendar events for the day
                    print(f"📅 Fetching calendar events for {tomorrow_str}...")
                    
                    # Try to get calendar events using a calendar tool
                    calendar_events = []
                    try:
                        # Try different calendar event fetching tools
                        calendar_tools = [
                            'get_calendar_events',
                            'calendar_get_events',
                            'cal_events',
                            'fetch_calendar_events'
                        ]
                        
                        for cal_tool in calendar_tools:
                            try:
                                cal_result = await client.call_tool(cal_tool, {
                                    'username': username,
                                    'date': tomorrow_str,
                                    'timezone': 'America/New_York'
                                })
                                
                                if hasattr(cal_result, 'content') and cal_result.content:
                                    for content in cal_result.content:
                                        if hasattr(content, 'text'):
                                            events_text = content.text
                                            try:
                                                import json
                                                events_data = json.loads(events_text)
                                                if isinstance(events_data, list):
                                                    calendar_events = events_data
                                                    print(f"✅ Retrieved {len(calendar_events)} calendar events using {cal_tool}")
                                                    break
                                            except json.JSONDecodeError:
                                                print(f"⚠️ {cal_tool} returned non-JSON data")
                                                continue
                                
                                if calendar_events:
                                    break
                                    
                            except Exception as cal_error:
                                print(f"⚠️ {cal_tool} failed: {cal_error}")
                                continue
                        
                        if not calendar_events:
                            print("⚠️ Could not fetch calendar events, using empty array")
                            calendar_events = []
                            
                    except Exception as e:
                        print(f"❌ Error fetching calendar events: {e}")
                        calendar_events = []
                    
                    # Convert calendar events to JSON string
                    calendar_events_json = json.dumps(calendar_events)
                    
                    # Call the weekly_calendar_free_times function through MCP
                    result = await client.call_tool(tool_name, {
                        'working_hours_start': '09:00',
                        'working_hours_end': '17:00',
                        'timezone': 'America/New_York',
                        'calendar_events': calendar_events_json
                    })
                    
                    # Check if we got a successful result
                    if hasattr(result, 'content') and result.content:
                        for content in result.content:
                            if hasattr(content, 'text'):
                                text = content.text
                                if 'error' not in text.lower() and 'failure' not in text.lower():
                                    availability_data = text
                                    used_tool = tool_name
                                    print(f"✅ Successfully retrieved calendar data using {tool_name}")
                                    break
                    
                    if availability_data:
                        break
                        
                except Exception as e:
                    print(f"⚠️ Tool {tool_name} failed: {e}")
                    continue
            
            if not availability_data:
                print("❌ Could not retrieve calendar availability from any Cal.com tool")
                return state
            
            # Parse the availability data (assuming it's JSON or structured text)
            try:
                import json
                if isinstance(availability_data, str):
                    # Try to parse as JSON
                    try:
                        parsed_data = json.loads(availability_data)
                    except json.JSONDecodeError:
                        # If not JSON, treat as text and extract information
                        parsed_data = {'raw_text': availability_data}
                else:
                    parsed_data = availability_data
                
                # Extract free time slots from the parsed data
                free_slots = []
                
                # Try different ways to extract time slots based on the data structure
                if 'slots' in parsed_data:
                    # Direct slots format
                    for slot in parsed_data['slots']:
                        free_slots.append({
                            'start': slot.get('start', ''),
                            'end': slot.get('end', ''),
                            'formatted_start': slot.get('formatted_start', slot.get('start', '')),
                            'formatted_end': slot.get('formatted_end', slot.get('end', ''))
                        })
                elif 'free_slots' in parsed_data:
                    # Free slots format
                    free_slots = parsed_data['free_slots']
                elif 'availability' in parsed_data:
                    # Availability format
                    availability = parsed_data['availability']
                    if isinstance(availability, list):
                        free_slots = availability
                else:
                    # Fallback: generate default slots based on business hours
                    print("⚠️ Could not parse specific time slots, generating default business hours")
                    business_start = 9  # 9 AM
                    business_end = 17   # 5 PM
                    
                    slot_start = datetime.strptime(f"{tomorrow_str} {business_start:02d}:00:00", "%Y-%m-%d %H:%M:%S")
                    slot_end = datetime.strptime(f"{tomorrow_str} {business_end:02d}:00:00", "%Y-%m-%d %H:%M:%S")
                    
                    current_slot = slot_start
                    while current_slot + timedelta(minutes=30) <= slot_end:
                        slot_end_time = current_slot + timedelta(minutes=30)
                        free_slots.append({
                            'start': current_slot.isoformat(),
                            'end': slot_end_time.isoformat(),
                            'formatted_start': current_slot.strftime('%I:%M %p'),
                            'formatted_end': slot_end_time.strftime('%I:%M %p')
                        })
                        current_slot += timedelta(minutes=30)
                
                print(f"🕐 Found {len(free_slots)} free time slots for tomorrow")
                
                # Create booking link
                booking_link = f"https://cal.com/{username}"
                
                # Add calendar information to state
                calendar_info = {
                    'username': username,
                    'date': tomorrow_str,
                    'free_slots': free_slots,
                    'total_slots': len(free_slots),
                    'booking_link': booking_link,
                    'matched_recruiters': len(matched_recruiters),
                    'tool_used': used_tool
                }
                
                # Update recruiter list with calendar info
                for recruiter in matched_recruiters:
                    recruiter['calendar_available'] = len(free_slots) > 0
                    recruiter['available_slots'] = free_slots[:3]  # First 3 slots
                    recruiter['booking_link'] = booking_link
                
                print(f"✅ Calendar check completed. {len(free_slots)} slots available for {len(matched_recruiters)} recruiters")
                
                return {
                    'recruiter_list': recruiter_list,
                    'calendar_info': calendar_info
                }
                
            except Exception as e:
                print(f"❌ Error parsing calendar data: {e}")
                return state
                
    except Exception as e:
        print(f"❌ Error checking calendar: {e}")
        print(f"🔍 Error type: {type(e).__name__}")
        return state


# Build LangGraph
builder = StateGraph(AgentState)

# Nodes
builder.add_node("parse_pdf", parse_pdf_node)
builder.add_node("extract_experience", extract_experience_node)
builder.add_node("read_drive_folder", read_drive_folder_node)
builder.add_node("extract_recruiters_emails_node", extract_recruiter_emails_node)
builder.add_node("match_resume_node", match_resume_node)
builder.add_node("send_recruiter_emails_node", send_recruiter_emails_node)
#builder.add_node("check_calendar", check_calendar_node)

# Edges
builder.add_edge(START, "parse_pdf")
builder.add_edge("parse_pdf", "extract_experience")
builder.add_edge("extract_experience", "read_drive_folder")
builder.add_edge("read_drive_folder", "extract_recruiters_emails_node")
builder.add_edge("extract_recruiters_emails_node", "match_resume_node")
builder.add_edge("match_resume_node", "send_recruiter_emails_node")
builder.add_edge("send_recruiter_emails_node", END)

# Compile the graph
graph = builder.compile()

# Run the graph
async def main():
    async with client:
        # Define the resume folder to scan
        resume_folder = 'resume_unscanned'
        
        # Check if the folder exists
        if not os.path.exists(resume_folder):
            print(f"❌ Resume folder '{resume_folder}' not found")
            return
        
        # Get all PDF files in the resume folder
        pdf_files = []
        for file in os.listdir(resume_folder):
            if file.lower().endswith('.pdf'):
                pdf_files.append(os.path.join(resume_folder, file))
        
        if not pdf_files:
            print(f"❌ No PDF files found in '{resume_folder}' folder")
            return
        
        print(f"📁 Found {len(pdf_files)} PDF files in '{resume_folder}' folder")
        
        # Process each resume file one by one
        for i, resume_path in enumerate(pdf_files, 1):
            print(f"\n{'='*60}")
            print(f"📄 Processing Resume {i}/{len(pdf_files)}: {os.path.basename(resume_path)}")
            print(f"{'='*60}")
            
            try:
                # Initialize state for this resume
                state = {
                    "folder_id": '1-In_XSM7YPLHWjjFNChYtaYCOb989lXT',
                    "drive_texts": [],
                    "r_file_path": resume_path,
                    "raw_text": '',
                    "experience_text": None,
                    "applicant_name": None,  # Initialize applicant name as None
                    "applicant_email": '',   # Initialize applicant email as empty string
                    "recruiter_list": [],  # Initialize as empty list
                    "calendar_info": None  # Initialize calendar info as None
                }
                
                # Process this resume
                result = await graph.ainvoke(state)
                
                # Print summary for this resume
                print(f"\n✅ Completed processing: {os.path.basename(resume_path)}")
                
                # Move processed file to a different folder
                try:
                    processed_folder = 'resume_processed'
                    if not os.path.exists(processed_folder):
                        os.makedirs(processed_folder)
                    new_path = os.path.join(processed_folder, os.path.basename(resume_path))
                    os.rename(resume_path, new_path)
                    print(f"📁 Moved to: {new_path}")
                except Exception as move_error:
                    print(f"⚠️ Could not move file: {move_error}")
                
            except Exception as e:
                print(f"❌ Error processing {os.path.basename(resume_path)}: {e}")
                print(f"🔍 Error type: {type(e).__name__}")
                continue
        
        print(f"\n{'='*60}")
        print(f"🏁 Completed processing {len(pdf_files)} resume files")
        print(f"{'='*60}")

if __name__ == "__main__":
    asyncio.run(main())