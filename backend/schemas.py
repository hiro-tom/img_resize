from typing import Optional
from pydantic import BaseModel, Field


class SftpSettings(BaseModel):
    host: str = Field(..., description="SFTP host")
    port: int = Field(22, description="SFTP port")
    username: str = Field(..., description="SFTP username")
    password: Optional[str] = Field(None, description="SFTP password")
    private_key_path: Optional[str] = Field(None, description="Path to private key for SFTP")
    remote_dir: str = Field(..., description="Remote directory on SFTP server")
    local_dir: str = Field(..., description="Local client directory to sync")
    compress_output_dir: Optional[str] = Field(None, description="Image compression output directory")
    compress_quality: int = Field(85, description="Image compression quality (1-100)")
    resize_width_dpi: Optional[int] = Field(None, description="Output image width in pixels (dpi)")
    remote_output_dir: Optional[str] = Field(None, description="SFTP remote directory output destination")
    sync_interval_seconds: int = Field(5, description="SFTP sync watch interval in seconds")
    compress_interval_seconds: int = Field(10, description="Image compression watch interval in seconds")
    upload_interval_seconds: int = Field(10, description="SFTP upload watch interval in seconds")


class SyncRequest(BaseModel):
    force: bool = False


class LogEntry(BaseModel):
    id: int
    created_at: str
    level: str
    message: str
    detail: Optional[str] = None


class Status(BaseModel):
    last_run: Optional[str] = None
    running: bool = False
