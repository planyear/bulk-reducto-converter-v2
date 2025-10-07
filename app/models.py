from typing import Optional, List
from fastapi import Form
from pydantic import BaseModel, HttpUrl


class JobRequest(BaseModel):
    input_folder_url: HttpUrl
    output_folder_url: HttpUrl

    @classmethod
    def as_form(
        cls,
        input_folder_url: HttpUrl = Form(..., description="Google Drive INPUT folder URL"),
        output_folder_url: HttpUrl = Form(..., description="Google Drive OUTPUT folder URL"),
    ) -> "JobRequest":
        return cls(
            input_folder_url=input_folder_url,
            output_folder_url=output_folder_url,
        )


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
