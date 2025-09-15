## Recruitment Assistant Agent

An end-to-end workflow that watches a Google Drive folder for new resumes, triggers a LangGraph agent via HTTP, analyzes resumes against job requirements, and sends emails to recruiters and applicants.

> Note: This project is under active development and has not yet been tested on LangGraph Cloud. Local development with `langgraph dev` is supported and documented below.

### Features
- Resume Processing: Extracts text from PDFs via PyMuPDF and identifies applicant info
- Applicant Information Extraction: Parses names and email addresses from resumes
- Experience Extraction: Captures the WORK EXPERIENCE section
- Recruiter Info Extraction: Finds recruiter email/name from opportunity docs
- Job Matching: Uses an LLM to compare resume experience vs. job requirements
- Flexible Match Criteria: Accepts matches when requirements are met or score ≥ 8/10
- Google Drive Integration: Reads opportunities from Drive via OAuth/Service Account
- Email Automation (Recruiters): Sends tailored messages with optional resume attachment
- Email Automation (Applicants): Notifies candidates and proposes time slots
- Calendar Integration: Uses Google Calendar Free/Busy to suggest times
- MCP Integration: Uses Gentoro MCP tools (e.g., Gmail)
- Batch Processing: Processes multiple resumes automatically

### Prerequisites
- Python 3.12
- A Google Cloud project with OAuth client and a service account that has access to your Drive and Calendar assets
- ngrok (or a public URL) for receiving Google Drive webhooks 
- LangGraph CLI and runtime

### Quick Start
1) Clone and enter the project directory
```bash
git clone <your-repo-url>
cd "Recruitment Assistant Agent"
```

2) Create and activate a virtual environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

3) Create a new LangGraph project (if you don’t already have one)
```bash
langgraph new
```
- Follow the prompts. Ensure your assistant id in `langgraph.json` is `recruit-agent` (or update the Flask app to match).

4) Configure environment variables
- If you have the provided `example.env`, copy it to the your langgraph server project directory and rename it to `.env` (same folder where this README is located):
```bash
cp example.env .env
```
- Or manually create a `.env` file at the project root:
```env
# Public webhook URL where Google will POST change notifications
LANGGRAPH_WEBHOOK_URL=https://<your-ngrok-subdomain>.ngrok-free.app/webhooks/google-drive

# Base URL of the LangGraph HTTP server (started with `langgraph dev`)
# Example: http://127.0.0.1:8123 or your remote URL
LANGGRAPH_API_URL=http://127.0.0.1:8123

# Google Drive folder IDs
# Folder watched for new/updated resumes
INPUT_FOLDER_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxx
# Where processed resumes may be moved (optional for future use)
OUTPUT_FOLDER_ID=yyyyyyyyyyyyyyyyyyyyyyyyyyyy
# Folder containing job descriptions/opportunities the agent reads
RESUME_FOLDER_ID=zzzzzzzzzzzzzzzzzzzzzzzzzz
```

5) Add Google OAuth files
- Place `client_secret.json` and `token.json` in `Langgraph_server/google_oauth/`.
- Place your service account `credentials.json` in `Langgraph_server/google_oauth/`.

Directory should contain:
```
Langgraph_server/google_oauth/
  client_secret.json
  token.json
  credentials.json
```

*** NOTICE:
If using ngrok to tunnel flask - Run flask and ngrok first and change the `LANGGRAPH_WEBHOOK_URL` in your `.env` file. This way to webhooks will be sent to the api properly.
*** 

### Start the LangGraph API
From your LangGraph project directory [From 'Recruitment Assistant Agent' do: 'cd langgraph_server'] (where `langgraph.json` lives), run:
```bash
langgraph dev
```

Notes:
- This starts the LangGraph HTTP server. The Flask app calls `POST {LANGGRAPH_API_URL}/runs` with `assistant_id: recruit-agent`.
- Make sure `LANGGRAPH_API_URL` in `.env` points to this server (e.g., `http://127.0.0.1:8123`).

### Run the Flask App
In a new terminal:
```bash
source venv/bin/activate
python Langgraph_server/app/app.py
```
On startup the app logs your config and can attempt to initialize a Google Drive webhook automatically when the required environment variables are present.

Visit:
- http://localhost:8000/ to open the UI
- http://localhost:8000/test-trigger?file_id=YOUR_FILE_ID&file_name=YOUR_FILE_NAME.pdf to manually trigger the agent for an existing file

### Expose Your Local Server (Webhooks)
For local testing, this project uses ngrok to expose the Flask server so Google can reach your webhook endpoint.
```bash
ngrok http 8000
```
Copy the public URL and set it as `LANGGRAPH_WEBHOOK_URL` in your `.env`.

Production note:
- Hosting the Flask app on a publicly reachable domain and configuring a proper HTTPS webhook endpoint works fine. In that case, set `LANGGRAPH_WEBHOOK_URL` to your hosted URL (for example, `https://your-domain.com/webhooks/google-drive`) and ensure Google can reach it.

### Configure the Google Drive Webhook
The app provides an endpoint and helper flow to register a webhook that listens for file changes and then triggers the agent when new PDFs land in your input folder.

- Endpoint that Google will call: `/webhooks/google-drive`
- Automatic setup attempt is performed at app startup when `LANGGRAPH_WEBHOOK_URL` and `INPUT_FOLDER_ID` are set
- You can also check status at:
  - http://localhost:8000/check-webhook-status

Webhook behavior:
- When Google Drive notifies about a change, the app fetches changes since the last token, filters for files in `INPUT_FOLDER_ID` that end with `.pdf`, and spawns a background thread to call the LangGraph API.
- A cooldown-based dedup prevents triggering the agent multiple times for the same file in quick succession.

### How Processing is Triggered
- The app posts to `{LANGGRAPH_API_URL}/runs` with payload:
  - `assistant_id: "recruit-agent"`
  - `input` contains: `file_id`, `file_name`, `input_folder_id`, `resume_folder_id`, `output_folder_id` and additional fields used by the agent
- The agent defined in `Langgraph_server/src/agent/recruit_agent.py`:
  - Downloads and parses the resume
  - Extracts applicant info
  - Reads job descriptions
  - Matches resume to jobs
  - Sends recruiter and applicant emails via MCP tools

### Required Environment Variables (recap)
- `LANGGRAPH_WEBHOOK_URL`: Public HTTPS URL to your Flask app `/webhooks/google-drive`
- `LANGGRAPH_API_URL`: Base URL of your running LangGraph HTTP server
- `INPUT_FOLDER_ID`: Google Drive folder ID to watch for resumes
- `OUTPUT_FOLDER_ID`: Google Drive folder ID where processed files can go
- `RESUME_FOLDER_ID`: Google Drive folder ID containing job descriptions/opportunities

Environment file placement:
- Place your `.env` file in your langgraph project directory (but not with the agent). If you are given an `example.env`, put it in the same location and rename to `.env`.

### Project Structure (key paths)
```
/Users/Zahir/Desktop/Gentoro Agent Work/Recruitment Assistant Agent/
  requirements.txt

  Langgraph_server/
    app/
      app.py
    google_oauth/
      client_secret.json
      token.json
      credentials.json
    langgraph.json
    src/
      agent/
        recruit_agent.py
```

### Common Tips
- Ensure the service account and OAuth client have access to the target Drive folders and any calendars you query.
- Verify `assistant_id` in `langgraph.json` matches the value used by the Flask app (`recruit-agent`).
- If the webhook doesn’t initialize automatically, ensure your public URL is reachable and `.env` values are correct. You can recreate the channel daily since Google webhooks expire.

### Development Commands
```bash
# Start LangGraph server
langgraph dev

# Run Flask app
python Langgraph_server/app/app.py

# Expose 8000 for webhooks
ngrok http 8000
```

### Status and Cloud Support
- This repository is still in development; expect changes and potential issues.
- It has not been tested on LangGraph Cloud yet. The documented flow targets local development using `langgraph dev`.

### Troubleshooting
- "Session terminated" errors: Often a tool name typo in MCP calls. Verify tool names.
- "Key null" errors: API not authenticated on Gentoro’s server. Configure server-side API.
- Google Drive auth issues: Ensure OAuth credentials exist and Drive API enabled; re-run auth.
- OpenAI API errors: Check key, credits, and formatting in `.env`.
- File processing errors: Ensure valid PDFs and that folders exist.

### Dependencies (from requirements.txt)
- langgraph, langchain_core, openai
- google-auth-oauthlib, google-auth-httplib2, google-api-python-client
- pymupdf, python-docx
- fastmcp, tavily-python
- python-dotenv, pydantic, requests
- fastapi, fastui (optional UI)
- watchdog (monitoring)

### Known Issues and Limitations
1. Early development; ongoing changes.
2. Some error handling may be incomplete.
3. MCP tool names can change and require updates.
4. First-time OAuth may require manual approval.
5. PDF-only for resumes at the moment.
6. Calendar integration may evolve.


