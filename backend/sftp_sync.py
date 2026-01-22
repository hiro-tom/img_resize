import os
import stat
import logging
from pathlib import Path
from typing import Dict, List, Tuple

import paramiko

from schemas import SftpSettings

# Enable paramiko logging for debugging
paramiko.util.log_to_file('paramiko.log', level=logging.DEBUG)


def _connect(settings: SftpSettings) -> tuple[paramiko.SSHClient, paramiko.SFTPClient]:
    """Connect to SFTP server and return SSH client and SFTP client."""
    try:
        # Use SSHClient for more stable connection
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Connection parameters - explicitly configure authentication
        connect_kwargs = {
            'hostname': settings.host,
            'port': settings.port,
            'username': settings.username,
            'timeout': 30,
            'banner_timeout': 30,
            'auth_timeout': 30,
        }

        if settings.private_key_path:
            # Use key-based authentication
            connect_kwargs['key_filename'] = settings.private_key_path
            connect_kwargs['look_for_keys'] = True
            connect_kwargs['allow_agent'] = False
        elif settings.password:
            # Use password authentication
            connect_kwargs['password'] = settings.password
            connect_kwargs['look_for_keys'] = False
            connect_kwargs['allow_agent'] = False
        else:
            raise Exception("パスワードまたは秘密鍵のどちらかが必要です")

        # Connect via SSH
        ssh.connect(**connect_kwargs)

        # Open SFTP session
        sftp = ssh.open_sftp()
        if sftp is None:
            ssh.close()
            raise Exception("Failed to open SFTP session")

        return ssh, sftp
    except Exception as e:
        raise Exception(f"SFTP接続エラー: {str(e)}")


def test_connection(settings: SftpSettings) -> None:
    """Attempt SFTP connect and list remote_dir to validate access."""
    ssh, sftp = _connect(settings)
    try:
        # Ensure remote_dir is accessible
        items = sftp.listdir(settings.remote_dir)
        # Connection successful - just validate, don't log here
        # Logging happens in the endpoint that calls this
    finally:
        sftp.close()
        ssh.close()


def _list_remote(sftp: paramiko.SFTPClient, remote_dir: str) -> List[Tuple[str, paramiko.SFTPAttributes]]:
    """Recursively list all files and directories from remote_dir."""
    items: List[Tuple[str, paramiko.SFTPAttributes]] = []
    try:
        for entry in sftp.listdir_attr(remote_dir):
            remote_path = remote_dir.rstrip("/") + "/" + entry.filename
            items.append((remote_path, entry))
            if stat.S_ISDIR(entry.st_mode):
                # Recursively list subdirectories
                items.extend(_list_remote(sftp, remote_path))
    except Exception as e:
        # Log error but continue with other items
        print(f"Error listing remote directory {remote_dir}: {e}")
    return items


def _list_local(base: Path) -> Dict[str, os.stat_result]:
    results: Dict[str, os.stat_result] = {}
    if not base.exists():
        return results
    for root, _, files in os.walk(base):
        for fname in files:
            p = Path(root) / fname
            rel = p.relative_to(base).as_posix()
            results[rel] = p.stat()
    return results


def sync_once(settings: SftpSettings, log, should_stop=None) -> Dict[str, int]:
    """Sync remote -> local. Returns counts of copied files and skipped."""
    if should_stop is None:
        should_stop = lambda: False

    local_base = Path(settings.local_dir)
    local_base.mkdir(parents=True, exist_ok=True)
    log("INFO", f"ローカルベースディレクトリ作成/確認: {settings.local_dir}")

    copied = 0
    skipped = 0
    dirs_created = 0

    # Log SFTP connection start
    log("INFO", f"[SFTP実行] 接続開始: sftp://{settings.username}@{settings.host}:{settings.port}{settings.remote_dir}")
    log("INFO", f"[SFTP実行] 認証方式: {'秘密鍵' if settings.private_key_path else 'パスワード'}")

    ssh = None
    sftp = None
    try:
        ssh, sftp = _connect(settings)
        log("INFO", f"[SFTP実行] 接続成功: {settings.host}:{settings.port}")
        log("INFO", f"[SFTP実行] リモートディレクトリ: {settings.remote_dir}")
        log("INFO", f"[SFTP実行] ローカルディレクトリ: {settings.local_dir}")

        try:
            log("INFO", f"[SFTP実行] コマンド: listdir_attr('{settings.remote_dir}') - リモートファイル一覧取得開始")
            remote_items = _list_remote(sftp, settings.remote_dir)
            log("INFO", f"[SFTP実行] リモートファイル一覧取得完了: {len(remote_items)}件 (フォルダーとファイル含む)")
        except Exception as e:
            log("ERROR", f"[SFTP実行] リモートファイル一覧取得エラー", detail=str(e))
            raise

        try:
            log("INFO", f"[SFTP実行] ローカルファイル一覧取得開始: {settings.local_dir}")
            local_index = _list_local(local_base)
            log("INFO", f"[SFTP実行] ローカルファイル一覧取得完了: {len(local_index)}件")
        except Exception as e:
            log("ERROR", f"[SFTP実行] ローカルファイル一覧取得エラー", detail=str(e))
            raise

        for remote_path, attrs in remote_items:
            # Check if stop was requested
            if should_stop():
                log("WARN", "同期処理を中断します")
                break

            try:
                if stat.S_ISDIR(attrs.st_mode):
                    # ensure folder exists
                    rel_dir = Path(remote_path).relative_to(settings.remote_dir).as_posix()
                    target_dir = local_base / rel_dir
                    if not target_dir.exists():
                        target_dir.mkdir(parents=True, exist_ok=True)
                        dirs_created += 1
                        log("INFO", f"[フォルダー作成] {rel_dir}/")
                    continue

                rel_path = Path(remote_path).relative_to(settings.remote_dir).as_posix()
                target_file = local_base / rel_path
                target_file.parent.mkdir(parents=True, exist_ok=True)

                local_stat = local_index.get(rel_path)
                
                # Check if file already exists with same timestamp and size
                if local_stat:
                    local_mtime = int(local_stat.st_mtime)
                    remote_mtime = int(attrs.st_mtime)
                    
                    # Skip if timestamp and size are identical
                    if local_mtime == remote_mtime and local_stat.st_size == attrs.st_size:
                        log("INFO", f"[スキップ] {rel_path} (タイムスタンプとサイズが同一)")
                        skipped += 1
                        continue
                    elif local_mtime < remote_mtime:
                        log("INFO", f"[上書き] {rel_path} (リモートが新しい: ローカル={local_mtime}, リモート={remote_mtime})")
                    elif local_mtime > remote_mtime:
                        log("INFO", f"[上書き] {rel_path} (ローカルが新しいがリモートで上書き)")
                    else:
                        log("INFO", f"[上書き] {rel_path} (サイズが異なる)")
                else:
                    log("INFO", f"[新規] {rel_path} (ローカルに存在しない)")

                file_size_mb = attrs.st_size / (1024 * 1024)
                log("INFO", f"[コピー開始] {rel_path} ({file_size_mb:.2f} MB)")
                log("INFO", f"[SFTP実行] コマンド: get '{remote_path}' -> '{target_file}'")

                # Check stop before potentially long copy operation
                if should_stop():
                    log("WARN", "同期処理を中断します (コピー前)")
                    break

                with sftp.open(remote_path, "rb") as src:
                    with open(target_file, "wb") as dst:
                        # Read and write in chunks to allow stop checking
                        chunk_size = 1024 * 1024  # 1MB chunks
                        while True:
                            if should_stop():
                                log("WARN", f"同期処理を中断します (コピー中: {rel_path})")
                                break
                            chunk = src.read(chunk_size)
                            if not chunk:
                                break
                            dst.write(chunk)

                os.utime(target_file, (attrs.st_atime, attrs.st_mtime))
                copied += 1
                log("INFO", f"[コピー完了] {rel_path} ({file_size_mb:.2f} MB)")
            except Exception as e:
                log("ERROR", f"ファイル処理エラー: {remote_path}", detail=str(e))

    except Exception as e:
        log("ERROR", f"SFTP接続または同期処理中にエラーが発生しました: {str(e)}", detail=str(e))
        raise
    finally:
        if sftp:
            try:
                sftp.close()
            except Exception as e:
                log("WARN", f"SFTP接続クローズ時にエラー: {str(e)}")
        if ssh:
            try:
                ssh.close()
                log("INFO", f"[SFTP実行] 接続クローズ完了")
                log("INFO", f"[SFTP実行] 処理サマリー: 作成フォルダー={dirs_created}件, コピー={copied}件, スキップ={skipped}件")
            except Exception as e:
                log("WARN", f"[SFTP実行] SSH接続クローズ時にエラー: {str(e)}")

    return {"copied": copied, "skipped": skipped}
