# Recruitment Assistant Agent

An AI-powered recruitment agent that automatically processes resumes, matches them with job opportunities, and notifies recruiters when suitable candidates are found. This project is currently in early development stages with ongoing improvements and feature additions.

## ⚠️ Project Status: Early Development

This project is still in early stages with work to be done. The current implementation includes core functionality but may have bugs, incomplete features, and areas that need improvement. Use at your own risk and expect ongoing changes.

## Features

- **Resume Processing**: Extracts text from PDF files and identifies applicant information
- **Applicant Information Extraction**: Automatically extracts names and email addresses from resumes
- **Experience Extraction**: Identifies and extracts work experience sections
- **Job Matching**: Compares candidate experience with job requirements using OpenAI GPT-4
- **Google Drive Integration**: Reads job opportunities from Google Drive folders using OAuth2
- **Email Automation**: Sends personalized notifications to recruiters via Gmail
- **Calendar Integration**: Checks recruiter availability using Cal.com API
- **MCP Integration**: Uses Gentoro MCP server for external services
- **Batch Processing**: Processes multiple resumes from a folder automatically

## Prerequisites

- Python 3.8+
- Google Cloud Project with Drive API enabled
- OpenAI API key
- Gentoro MCP server access
- Cal.com account (for calendar functionality)

## Installation

### 1. Clone the Repository

```bash
git clone <repository-url>
cd recruitment-assistant-agent
```

### 2. Set Up Virtual Environment (Recommended)

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

## Configuration

### 1. Environment Variables

Create a `.env` file in the project root:

```bash
# OpenAI API Key (Required)
OPENAI_API_KEY=your_openai_api_key_here

# Gentoro MCP Server Key (if required)
gentoro_mcp_url=your_gentoro_key_here
```

### 2. Google Drive Setup (OAuth2)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing one
3. Enable the Google Drive API
4. Create OAuth2 credentials:
   - Go to "APIs & Services" > "Credentials"
   - Click "Create Credentials" > "OAuth 2.0 Client IDs"
   - Download the JSON credentials file
5. Place the credentials file in `google_project_json/client_secret_*.json`
6. The first time you run the agent, it will prompt you to authenticate
7. (Optional but recommended) Create a service account 
   - Go to "IAM & Admin" > "Service Account"
   - Click "Create Service Account" and provide ID, email, and description
   - Assign role of editor at minimum when creating the account

* NOTE: In this project, I use a Service Account with 'Editor' level authentication. 
This allows me to access the google drive without needing to authenticate myself everytime.
This is very useful if you are considering using one google drive to manage both processed and unprocessed resumes
note that the service account only deals with the google drive, it does not handle the emails, calendar, or any other interactions.


### 3. Folder Structure Setup

Create the following folders in your project directory:

```bash
mkdir resume_unscanned    # Place new resumes here
mkdir resume_processed     # Processed resumes are moved here
```

* Note: This is a temporary solution for local testing, in the future the folders will be in a Google Drive
* Will add a measure so if a resume is matched to no opportunities it is deleted
* Would like to add a feature so in a folder, a folder with a given recruiters name is made containing all their relevant resumes

### 4. Gentoro MCP Server

The agent uses Gentoro's MCP server for:
- **Gmail**: Email sending functionality
- **Tavily Search**: Web search capabilities (optional) # The goal was to use Tavily to scrape LinkedIN, LinkedIN does not like to be scraped :(
- **Cal.com**: Calendar availability checking (Still in testing)
- Feel Free to add any other MCP tools as needed.,m

The MCP server URL is configured in the code and should be provided by Gentoro.

## Project Structure

```
recruitment-assistant-agent/
├── recruit_agent.py              # Main agent implementation
├── requirements.txt              # Python dependencies
├── README.md                     # This file
├── .env                          # Environment variables (create this)
├── google_project_json/          # Google credentials directory
│   └── client_secret_*.json     # Google OAuth2 credentials
├── resume_unscanned/             # Place new resumes here
└── resume_processed/             # Processed resumes moved here
```

## Usage

### Basic Usage

1. **Prepare resumes**: Place PDF resume files in the `resume_unscanned` folder
2. **Set up job opportunities**: Upload job descriptions to your Google Drive folder
3. **Update the folder ID**: Modify the `folder_id` in `recruit_agent.py` (line ~750)
4. **Run the agent**:

```bash
python recruit_agent.py
```

The agent will:
- Process all PDF files in `resume_unscanned`
- Extract applicant information (name, email)
- Match resumes against job opportunities
- Send personalized emails to recruiters
- Move processed files to `resume_processed`



## How It Works

1. **Resume Processing**: Extracts text and applicant information from PDF files
2. **Experience Extraction**: Identifies and extracts the work experience section
3. **Google Drive Reading**: Downloads and processes all job opportunities from Google Drive
4. **Email Extraction**: Finds recruiter emails in job descriptions
5. **Matching**: Compares candidate experience with job requirements using OpenAI GPT-4
6. **Calendar Check**: Checks recruiter availability for the next day
7. **Email Sending**: Sends personalized notifications to recruiters with applicant details

## API Keys and Security

### Required API Keys

1. **OpenAI API Key**
   - Get from: https://platform.openai.com/api-keys
   - Add to `.env` file as `OPENAI_API_KEY=your_key_here`
   - Used for resume matching and experience extraction

2. **Gentoro MCP Server Key**
   - Provided by Gentoro
   - Add to `.env` file as `gentoro_mcp_url=your_key_here`
   - To get url: 
   1. Create a tool box and add desired integrations
   2. After creating and confirming, click "Use toolbox" in the upper right corner
   3. Select "Signed HTTP Streamable MCP URL" and paste that into your .env file

### Security Notes

- Never commit API keys to version control
- Use `.env` file for sensitive configuration
- Keep Google OAuth credentials secure
- The `.env` file is already in `.gitignore`

## Troubleshooting

### Common Issues and Solutions

#### 1. "Session terminated" Errors
- **Cause**: Usually indicates a misspelled tool name in MCP calls
- **Solution**: Check tool names in the code, especially in `recruit_agent.py`
- **Example**: `googlemail_send_email` vs `googlemail_send_email` (typo)

#### 2. "Key null" Errors
- **Cause**: API not authenticated on Gentoro's server side
- **Solution**: Contact Gentoro support to configure the API on their end
- **Note**: This is not a local configuration issue

#### 3. Google Drive Authentication Issues
- **Cause**: OAuth2 credentials not properly set up
- **Solution**: 
  - Ensure `client_secret_*.json` is in `google_project_json/`
  - Run the agent and follow the authentication prompts
  - Check that the Drive API is enabled in Google Cloud Console

#### 4. OpenAI API Errors
- **Cause**: Invalid API key or insufficient credits
- **Solution**: 
  - Verify your API key in the `.env` file
  - Check your OpenAI account has sufficient credits
  - Ensure the API key is properly formatted

#### 5. File Processing Errors
- **Cause**: Corrupted PDF files or unsupported formats
- **Solution**: 
  - Ensure files are valid PDFs
  - Check file permissions
  - Verify the resume folders exist

### Debug Mode

To enable debug output, add print statements or modify the logging level in the code. The agent includes extensive logging to help diagnose issues.

## Dependencies

Key dependencies from `requirements.txt`:

### Core Framework
- `langgraph==0.5.4`: Graph-based agent orchestration
- `langchain_core==0.3.72`: Core LangChain utilities
- `openai==1.86.0`: OpenAI API integration

### Google Services
- `google-auth-oauthlib==1.2.0`: OAuth2 authentication
- `google-auth-httplib2==0.2.0`: HTTP client for Google APIs
- `google-api-python-client==2.116.0`: Google Drive API

### File Processing
- `pypdf==5.8.0`: PDF text extraction
- `python-docx==1.2.0`: DOCX text extraction

### MCP and External Services
- `fastmcp==2.10.6`: MCP client for external services
- `tavily-python==0.7.10`: Web search capabilities

### Utilities
- `python-dotenv==1.1.1`: Environment variable management
- `pydantic==2.11.7`: Data validation
- `requests==2.32.4`: HTTP requests

### Web Framework (for potential UI)
- `fastapi==0.116.1`: Web framework
- `fastui==0.7.0`: UI components

### Monitoring
- `watchdog==6.0.0`: File system monitoring

## Known Issues and Limitations

1. **Early Development**: This project is in early stages with ongoing development
2. **Error Handling**: Some error handling may be incomplete
3. **Tool Name Dependencies**: MCP tool names may change and require updates
4. **Authentication**: OAuth2 flow may need manual intervention on first run
5. **File Formats**: Currently only supports PDF files for resumes
6. **Calendar Integration**: Cal.com integration is experimental

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

[Add your license information here]

## Support

For issues and questions:
- Check the troubleshooting section above
- Review the code comments for implementation details
- Contact the development team

## Roadmap

Future improvements planned:
- [ ] Better error handling and recovery
- [ ] Support for more file formats (DOCX, TXT)
- [ ] Web interface for easier configuration
- [ ] Improved applicant information extraction
- [ ] Better calendar integration
- [ ] Resume scoring and ranking
- [ ] Integration with ATS systems

---

**Note**: This agent is designed for automated recruitment workflows. Ensure compliance with local data protection and privacy regulations when processing candidate information. The project is in early development - use with caution and expect ongoing changes.

