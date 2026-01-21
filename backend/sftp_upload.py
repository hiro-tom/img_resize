"""SFTP upload module for uploading compressed images to remote server."""

import os
import shutil
import stat
import time
from pathlib import Path
from typing import Callable, Optional

import paramiko

from schemas import SftpSettings


def _connect_for_upload(settings: SftpSettings) -> tuple[paramiko.SSHClient, paramiko.SFTPClient]:
    """Connect to SFTP server for uploading."""
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            'hostname': settings.host,
            'port': settings.port,
            'username': settings.username,
            'timeout': 30,
            'banner_timeout': 30,
            'auth_timeout': 30,
        }

        if settings.private_key_path:
            connect_kwargs['key_filename'] = settings.private_key_path
            connect_kwargs['look_for_keys'] = True
            connect_kwargs['allow_agent'] = False
        elif settings.password:
            connect_kwargs['password'] = settings.password
            connect_kwargs['look_for_keys'] = False
            connect_kwargs['allow_agent'] = False
        else:
            raise Exception("パスワードまたは秘密鍵のどちらかが必要です")

        ssh.connect(**connect_kwargs)
        sftp = ssh.open_sftp()
        if sftp is None:
            ssh.close()
            raise Exception("Failed to open SFTP session")

        return ssh, sftp
    except Exception as e:
        raise Exception(f"SFTP接続エラー: {str(e)}")


def _ensure_remote_dir(sftp: paramiko.SFTPClient, remote_path: str) -> None:
    """Ensure remote directory exists, creating if necessary."""
    try:
        sftp.stat(remote_path)
    except IOError:
        # Directory doesn't exist, create it
        parent = os.path.dirname(remote_path.rstrip('/'))
        if parent and parent != '/':
            _ensure_remote_dir(sftp, parent)
        sftp.mkdir(remote_path)


def upload_file(
    sftp: paramiko.SFTPClient,
    local_file: str,
    remote_file: str,
    log: Optional[Callable[[str, str, Optional[str]], None]] = None,
) -> dict:
    """
    Upload a single file to SFTP server.
    
    Args:
        sftp: SFTP client connection
        local_file: Local file path
        remote_file: Remote file path
        log: Log callback function
        
    Returns:
        dict with file_size and status
    """
    def _log(level: str, message: str, detail: Optional[str] = None):
        if log:
            log(level, message, detail)

    try:
        # Ensure remote directory exists
        remote_dir = os.path.dirname(remote_file)
        _ensure_remote_dir(sftp, remote_dir)

        # Upload file
        file_size = os.path.getsize(local_file)
        file_size_mb = file_size / (1024 * 1024)
        
        _log("INFO", f"[アップロード開始] {os.path.basename(local_file)} ({file_size_mb:.2f} MB)")
        sftp.put(local_file, remote_file)
        
        # Set remote file timestamp to match local
        local_stat = os.stat(local_file)
        sftp.utime(remote_file, (local_stat.st_atime, local_stat.st_mtime))
        
        _log("INFO", f"[アップロード完了] {os.path.basename(local_file)}")
        
        return {'file_size': file_size, 'status': 'success'}
    except Exception as e:
        _log("ERROR", f"[アップロードエラー] {os.path.basename(local_file)}", detail=str(e))
        raise


def upload_folder(
    local_dir: str,
    remote_dir: str,
    settings: SftpSettings,
    log: Optional[Callable[[str, str, Optional[str]], None]] = None,
    stop_check: Optional[Callable[[], bool]] = None,
    delete_after_upload: bool = True,
) -> dict:
    """
    Upload entire folder to SFTP server and optionally delete local files.
    
    Args:
        local_dir: Local directory to upload
        remote_dir: Remote directory destination
        settings: SFTP connection settings
        log: Log callback function
        stop_check: Function to check if operation should stop
        delete_after_upload: Delete local files after successful upload
        
    Returns:
        dict with uploaded_files, uploaded_bytes, deleted_files counts
    """
    def _log(level: str, message: str, detail: Optional[str] = None):
        if log:
            log(level, message, detail)

    if stop_check is None:
        stop_check = lambda: False

    local_path = Path(local_dir)
    
    if not local_path.exists():
        _log("WARN", f"アップロード元フォルダーが存在しません: {local_dir}")
        return {
            'uploaded_files': 0,
            'uploaded_bytes': 0,
            'deleted_files': 0,
            'error_files': 0,
        }

    stats = {
        'uploaded_files': 0,
        'uploaded_bytes': 0,
        'deleted_files': 0,
        'error_files': 0,
    }

    # Collect all files to upload
    files_to_upload = []
    for root, dirs, files in os.walk(local_dir):
        if stop_check():
            _log("WARN", "アップロード処理が停止されました")
            return stats
            
        for filename in files:
            local_file = os.path.join(root, filename)
            rel_path = os.path.relpath(local_file, local_dir)
            remote_file = os.path.join(remote_dir, rel_path).replace('\\', '/')
            files_to_upload.append((local_file, remote_file, rel_path))

    if not files_to_upload:
        _log("INFO", "アップロード対象のファイルがありません")
        return stats

    _log("INFO", f"SFTPアップロード開始: {local_dir} → {remote_dir}")
    _log("INFO", f"アップロード対象: {len(files_to_upload)}件")

    ssh = None
    sftp = None
    uploaded_files = []

    try:
        ssh, sftp = _connect_for_upload(settings)
        _log("INFO", f"SFTP接続成功: {settings.host}:{settings.port}")

        for local_file, remote_file, rel_path in files_to_upload:
            if stop_check():
                _log("WARN", "アップロード処理を中断します")
                break

            try:
                result = upload_file(sftp, local_file, remote_file, _log)
                stats['uploaded_files'] += 1
                stats['uploaded_bytes'] += result['file_size']
                uploaded_files.append(local_file)
            except Exception as e:
                stats['error_files'] += 1
                _log("ERROR", f"ファイルアップロード失敗: {rel_path}", detail=str(e))

        # Delete uploaded files if requested
        if delete_after_upload and uploaded_files:
            _log("INFO", f"アップロード完了ファイルの削除開始: {len(uploaded_files)}件")
            
            for local_file in uploaded_files:
                if stop_check():
                    _log("WARN", "削除処理を中断します")
                    break
                    
                try:
                    os.remove(local_file)
                    stats['deleted_files'] += 1
                    _log("INFO", f"[削除完了] {os.path.basename(local_file)}")
                except Exception as e:
                    _log("ERROR", f"ファイル削除エラー: {os.path.basename(local_file)}", detail=str(e))

            # Delete empty directories
            _log("INFO", "空フォルダーの削除を実行します")
            for root, dirs, files in os.walk(local_dir, topdown=False):
                if stop_check():
                    break
                    
                for dir_name in dirs:
                    dir_path = os.path.join(root, dir_name)
                    try:
                        if not os.listdir(dir_path):  # Directory is empty
                            os.rmdir(dir_path)
                            _log("INFO", f"[空フォルダー削除] {os.path.relpath(dir_path, local_dir)}/")
                    except Exception as e:
                        _log("WARN", f"フォルダー削除エラー: {os.path.relpath(dir_path, local_dir)}/", detail=str(e))

    except Exception as e:
        _log("ERROR", "SFTPアップロード処理エラー", detail=str(e))
        raise
    finally:
        if sftp:
            try:
                sftp.close()
            except Exception:
                pass
        if ssh:
            try:
                ssh.close()
                _log("INFO", "SFTP接続クローズ完了")
            except Exception:
                pass

    uploaded_mb = stats['uploaded_bytes'] / (1024 * 1024)
    _log("INFO", f"SFTPアップロード完了: アップロード={stats['uploaded_files']}件 ({uploaded_mb:.2f}MB), 削除={stats['deleted_files']}件, エラー={stats['error_files']}件")

    return stats


def watch_and_upload(
    local_dir: str,
    remote_dir: str,
    settings: SftpSettings,
    log: Optional[Callable[[str, str, Optional[str]], None]] = None,
    stop_check: Optional[Callable[[], bool]] = None,
    interval: float = 10.0,
    delete_after_upload: bool = True,
) -> dict:
    """
    Continuously watch local folder and upload files to SFTP server.
    
    Args:
        local_dir: Local directory to watch
        remote_dir: Remote directory destination
        settings: SFTP connection settings
        log: Log callback function
        stop_check: Function to check if operation should stop
        interval: Seconds between each scan cycle
        delete_after_upload: Delete local files after successful upload
        
    Returns:
        dict with total stats from all cycles
    """
    def _log(level: str, message: str, detail: Optional[str] = None):
        if log:
            log(level, message, detail)

    local_path = Path(local_dir)
    
    total_stats = {
        'uploaded_files': 0,
        'uploaded_bytes': 0,
        'deleted_files': 0,
        'error_files': 0,
    }

    cycle_count = 0

    _log("INFO", f"SFTPアップロード監視開始: {local_dir} → {remote_dir}")
    _log("INFO", f"監視間隔: {interval}秒")

    while not (stop_check and stop_check()):
        cycle_count += 1

        try:
            # Check if directory exists and has files
            if not local_path.exists():
                # Wait for directory to be created
                for _ in range(int(interval * 10)):
                    if stop_check and stop_check():
                        break
                    time.sleep(0.1)
                continue

            # Check if there are any files to upload
            has_files = False
            for root, dirs, files in os.walk(local_dir):
                if files:
                    has_files = True
                    break

            if not has_files:
                # No files to upload, wait for next cycle
                for _ in range(int(interval * 10)):
                    if stop_check and stop_check():
                        break
                    time.sleep(0.1)
                continue

            # Upload files
            _log("INFO", f"[監視サイクル {cycle_count}] ファイル検出 - アップロード処理開始")
            
            stats = upload_folder(
                local_dir=local_dir,
                remote_dir=remote_dir,
                settings=settings,
                log=_log,
                stop_check=stop_check,
                delete_after_upload=delete_after_upload,
            )

            total_stats['uploaded_files'] += stats['uploaded_files']
            total_stats['uploaded_bytes'] += stats['uploaded_bytes']
            total_stats['deleted_files'] += stats['deleted_files']
            total_stats['error_files'] += stats['error_files']

            if stats['uploaded_files'] > 0:
                uploaded_mb = stats['uploaded_bytes'] / (1024 * 1024)
                _log("INFO", f"[監視サイクル {cycle_count}] アップロード={stats['uploaded_files']}件 ({uploaded_mb:.2f}MB), 削除={stats['deleted_files']}件")

        except Exception as e:
            _log("ERROR", f"[監視サイクル {cycle_count}] アップロード処理エラー", detail=str(e))
            total_stats['error_files'] += 1

        # Wait before next cycle
        if not (stop_check and stop_check()):
            for _ in range(int(interval * 10)):
                if stop_check and stop_check():
                    break
                time.sleep(0.1)

    _log("WARN", f"SFTPアップロード監視停止: 合計{cycle_count}サイクル実行")
    
    total_mb = total_stats['uploaded_bytes'] / (1024 * 1024)
    _log("INFO", f"SFTPアップロード監視終了: 総アップロード={total_stats['uploaded_files']}件 ({total_mb:.2f}MB), 総削除={total_stats['deleted_files']}件, エラー={total_stats['error_files']}件")
    
    return total_stats
