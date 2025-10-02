from pydantic import BaseModel, HttpUrl
from typing import Optional, List

class JobRequest(BaseModel):
    input_folder_url: HttpUrl
    output_folder_url: HttpUrl

class FileResult(BaseModel):
    source_id: str
    source_name: str
    status: str  # ok | skipped | error
    message: Optional[str] = None
    output_file_id: Optional[str] = None
    output_name: Optional[str] = None

class JobStatus(BaseModel):
    job_id: str
    requested_by: str
    total: int
    done: int
    failed: int
    skipped: int
    items: List[FileResult]