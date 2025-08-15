from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import HTMLResponse
from fastui import FastUI, AnyComponent, prebuilt_html, components as c
from fastui.components.display import DisplayMode, DisplayLookup
from fastui.events import GoToEvent, BackEvent
from pydantic import BaseModel
from typing import List, Optional
import os
from pathlib import Path

# Create FastAPI app
app = FastAPI()

# Create uploads directory if it doesn't exist
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# Pydantic models for data validation
class UploadResponse(BaseModel):
    message: str
    filename: str
    size: int

class FileInfo(BaseModel):
    filename: str
    size: int
    path: str

# Store uploaded files info (in production, use a database)
uploaded_files: List[FileInfo] = []

@app.get("/api/", response_model=FastUI, response_model_exclude_none=True)
def homepage() -> List[AnyComponent]:
    """Main page with file upload form and uploaded files list"""
    return [
        c.Page(
            components=[
                c.Heading(text='File Upload Demo', level=1),
                c.Paragraph(text='Upload files using the form below. Supported formats: images, documents, text files.'),
                
                # File upload form
                c.ModelForm(
                    model=UploadFile,
                    submit_url='/api/upload',
                    method='POST',
                    submit_text='Upload File',
                    loading_text='Uploading...',
                ),
                
                c.Div(
                    components=[c.Text(text='')],  # Spacer
                    class_name='my-4'
                ),
                
                # Display uploaded files
                c.Heading(text='Uploaded Files', level=2),
                
                # Show uploaded files if any exist
                *([
                    c.Table(
                        data=uploaded_files,
                        columns=[
                            DisplayLookup(field='filename', title='File Name'),
                            DisplayLookup(field='size', title='Size (bytes)'),
                            DisplayLookup(field='path', title='Path'),
                        ],
                    )
                ] if uploaded_files else [
                    c.Paragraph(text='No files uploaded yet.')
                ]),
                
            ],
        ),
    ]

@app.post("/api/upload", response_model=FastUI, response_model_exclude_none=True)
async def upload_file(file: UploadFile = File(...)) -> List[AnyComponent]:
    """Handle file upload"""
    try:
        # Check if file was actually uploaded
        if file.filename == "":
            return [
                c.Page(
                    components=[
                        c.Alert(
                            text="No file selected. Please choose a file to upload.",
                            type='error'
                        ),
                        c.Link(
                            components=[c.Text(text='← Back to Upload')],
                            on_click=BackEvent(),
                        ),
                    ]
                )
            ]
        
        # Create safe filename
        safe_filename = file.filename.replace(" ", "_")
        file_path = UPLOAD_DIR / safe_filename
        
        # Handle file name conflicts by adding a number
        counter = 1
        original_path = file_path
        while file_path.exists():
            name_parts = original_path.stem, counter, original_path.suffix
            file_path = UPLOAD_DIR / f"{name_parts[0]}_{name_parts[1]}{name_parts[2]}"
            counter += 1
        
        # Save file
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)
        
        # Store file info
        file_info = FileInfo(
            filename=file_path.name,
            size=len(content),
            path=str(file_path)
        )
        uploaded_files.append(file_info)
        
        return [
            c.Page(
                components=[
                    c.Alert(
                        text=f"File '{file_info.filename}' uploaded successfully! ({file_info.size} bytes)",
                        type='success'
                    ),
                    c.Paragraph(text=f"File saved to: {file_info.path}"),
                    c.Link(
                        components=[c.Text(text='← Back to Upload')],
                        on_click=GoToEvent(url='/'),
                    ),
                ]
            )
        ]
        
    except Exception as e:
        return [
            c.Page(
                components=[
                    c.Alert(
                        text=f"Error uploading file: {str(e)}",
                        type='error'
                    ),
                    c.Link(
                        components=[c.Text(text='← Back to Upload')],
                        on_click=BackEvent(),
                    ),
                ]
            )
        ]

# Serve the FastUI HTML page
@app.get('/{path:path}')
async def html_landing() -> HTMLResponse:
    """Serve the FastUI frontend"""
    return HTMLResponse(prebuilt_html(title='File Upload Demo'))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)