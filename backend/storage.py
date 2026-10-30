"""Snapshot backups of the SQLite database to the CloudRun object storage.

The CloudRun container does not need permanent COS keys: the platform endpoint
`/_/cos/getauth` returns temporary credentials scoped to the environment bucket.
Backups are only meaningful for a single-instance deployment (SQLite lives on
the instance disk); the app therefore keeps max instances at 1.
"""
import json
import logging
import os
import sqlite3
import tempfile
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

LOG = logging.getLogger('rates')
TZ = ZoneInfo('Asia/Shanghai')
AUTH_URL = 'http://api.weixin.qq.com/_/cos/getauth'
DEFAULT_BUCKET = '7072-prod-d9gjkq67xd89186c3-1521599365'
DEFAULT_REGION = 'ap-shanghai'
DEFAULT_PREFIX = 'backups/'
DEFAULT_INTERVAL = 600
DEFAULT_KEEP = 24


def temp_credentials(timeout=20):
    request = urllib.request.Request(AUTH_URL, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or not payload.get('TmpSecretId'):
        raise ValueError('平台未返回对象存储临时密钥')
    return {
        'secret_id': payload['TmpSecretId'],
        'secret_key': payload['TmpSecretKey'],
        'token': payload.get('Token'),
    }


def snapshot_bytes(db_path):
    """Consistent copy of a live SQLite database via the online backup API."""
    handle, name = tempfile.mkstemp(suffix='.sqlite3')
    os.close(handle)
    try:
        source = sqlite3.connect(db_path, timeout=15)
        target = sqlite3.connect(name)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        return Path(name).read_bytes()
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass


class BackupManager:
    def __init__(self, db_path, logger=LOG):
        self.path = str(db_path)
        self.bucket = os.getenv('COS_BUCKET', DEFAULT_BUCKET).strip()
        self.region = os.getenv('COS_REGION', DEFAULT_REGION).strip()
        self.prefix = os.getenv('COS_BACKUP_PREFIX', DEFAULT_PREFIX).strip()
        self.interval = self._int_env('BACKUP_INTERVAL_SECONDS', DEFAULT_INTERVAL)
        self.keep = self._int_env('BACKUP_KEEP', DEFAULT_KEEP)
        self.logger = logger
        self.last_ok_at = None
        self.last_error = None
        self.restored_from = None
        self._sdk = None
        if os.getenv('BACKUP_ENABLED', '1').strip().lower() not in ('0', 'false', 'no'):
            try:
                from qcloud_cos import CosConfig, CosS3Client  # third-party, optional
                self._sdk = (CosConfig, CosS3Client)
            except Exception as error:  # pragma: no cover - depends on image
                self.last_error = 'cos sdk unavailable: ' + type(error).__name__
                self.logger.warning('object storage backup disabled: %s', self.last_error)

    def _int_env(self, name, default):
        try:
            value = int(os.getenv(name, str(default)))
            return value if value > 0 else default
        except ValueError:
            return default

    @property
    def enabled(self):
        return self._sdk is not None and bool(self.bucket and self.region)

    def _client(self):
        if not self.enabled:
            raise RuntimeError('对象存储备份未启用')
        CosConfig, CosS3Client = self._sdk
        credentials = temp_credentials()
        config = CosConfig(Region=self.region, SecretId=credentials['secret_id'],
                           SecretKey=credentials['secret_key'],
                           Token=credentials['token'], Scheme='https')
        return CosS3Client(config)

    def backup_once(self):
        if not self.enabled:
            return False
        if not Path(self.path).exists():
            return False
        try:
            body = snapshot_bytes(self.path)
            client = self._client()
            key = self.prefix + 'rates-' + datetime.now(TZ).strftime('%Y%m%d-%H%M%S') + '.sqlite3'
            client.put_object(Bucket=self.bucket, Body=body, Key=key)
            self.last_ok_at = datetime.now(TZ).isoformat(timespec='seconds')
            self.last_error = None
            self.logger.info('database snapshot uploaded: %s (%s bytes)', key, len(body))
            self._prune(client)
            return True
        except Exception as error:
            self.last_error = type(error).__name__
            self.logger.warning('database snapshot failed: %s', error)
            return False

    def _prune(self, client):
        listing = client.list_objects(Bucket=self.bucket, Prefix=self.prefix)
        keys = sorted(item['Key'] for item in (listing.get('Contents') or [])
                      if item['Key'].endswith('.sqlite3'))
        for key in keys[:-self.keep]:
            client.delete_object(Bucket=self.bucket, Key=key)
            self.logger.info('old snapshot removed: %s', key)

    def restore_if_missing(self):
        target = Path(self.path)
        if target.exists() and target.stat().st_size > 0:
            return False
        if not self.enabled:
            return False
        try:
            client = self._client()
            listing = client.list_objects(Bucket=self.bucket, Prefix=self.prefix)
            keys = sorted(item['Key'] for item in (listing.get('Contents') or [])
                          if item['Key'].endswith('.sqlite3'))
            if not keys:
                return False
            latest = keys[-1]
            response = client.get_object(Bucket=self.bucket, Key=latest)
            body = response['Body'].get_raw_stream().read()
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = str(target) + '.restore'
            Path(temporary).write_bytes(body)
            os.replace(temporary, target)
            self.restored_from = latest
            self.logger.info('database restored from snapshot: %s', latest)
            return True
        except Exception as error:
            self.last_error = type(error).__name__
            self.logger.warning('database restore failed: %s', error)
            return False

    def start(self):
        if not self.enabled:
            return False

        def loop():
            # Take one snapshot shortly after startup so /health can prove the
            # upload path works, then follow the regular interval.
            delay = 10
            while True:
                time.sleep(delay)
                self.backup_once()
                delay = self.interval

        threading.Thread(target=loop, daemon=True).start()
        return True

    def status(self):
        return {'enabled': self.enabled, 'bucket': self.bucket if self.enabled else None,
                'last_ok_at': self.last_ok_at, 'last_error': self.last_error,
                'restored_from': self.restored_from}
