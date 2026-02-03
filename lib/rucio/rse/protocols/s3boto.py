# Copyright European Organization for Nuclear Research (CERN) since 2012
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""S3 protocol implementation using boto3."""

import logging
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import urlparse

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from rucio.common import exception
from rucio.rse.protocols import protocol

if TYPE_CHECKING:
    from rucio.common.types import RSESettingsDict


class Default(protocol.RSEProtocol):
    """Implementing access to RSEs using the S3 protocol via boto3.

    Supports both path-style and virtual-host-style S3 URLs.
    Designed for read-only access to external S3 buckets.
    """

    def __init__(self, protocol_attr: dict[str, Any], rse_settings: "RSESettingsDict", logger=logging.log):
        """Initialize the S3 protocol.

        :param protocol_attr: Properties of the requested protocol
        :param rse_settings: The RSE settings
        :param logger: Optional decorated logger
        """
        super().__init__(protocol_attr, rse_settings, logger=logger)

        self.renaming = False  # S3 doesn't support atomic rename
        self.overwrite = True  # S3 allows overwrite by default
        self._s3_client: Optional[Any] = None

    @property
    def s3_client(self) -> Any:
        """Get or create S3 client (lazy initialization)."""
        if self._s3_client is None:
            self._s3_client = self._create_s3_client()
        return self._s3_client

    def _create_s3_client(self) -> Any:
        """Create boto3 S3 client from RSE attributes."""
        # Extract S3 configuration from RSE attributes
        access_key = self.rse.get('s3_access_key')
        secret_key = self.rse.get('s3_secret_key')

        if not access_key or not secret_key:
            raise exception.RSEAccessDenied(
                f'Missing S3 credentials for RSE {self.rse["rse"]}'
            )

        # Build endpoint URL
        endpoint_url = self.rse.get('s3_url')
        if not endpoint_url:
            hostname = self.attributes.get('hostname', 'localhost')
            port = self.attributes.get('port', 443)
            scheme = self.attributes.get('scheme', 'https')
            endpoint_url = f'{scheme}://{hostname}:{port}'

        # S3 configuration
        region = self.rse.get('region', 'us-east-1')
        signature_version = self.rse.get('signature_version', 's3v4')
        url_style = self.rse.get('s3_url_style', 'path')
        is_secure = self.rse.get('s3_is_secure', 'true').lower() == 'true'

        addressing_style = 'virtual' if url_style == 'host' else 'path'

        return boto3.client(
            's3',
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(
                signature_version=signature_version,
                region_name=region,
                s3={'addressing_style': addressing_style}
            ),
            use_ssl=is_secure
        )

    def _parse_pfn(self, pfn: str) -> tuple[str, str]:
        """Extract bucket and key from PFN.

        :param pfn: Physical file name (S3 URL)
        :returns: Tuple of (bucket, key)
        :raises RSEFileNameNotSupported: if PFN format is invalid
        """
        parsed = urlparse(pfn)
        url_style = self.rse.get('s3_url_style', 'path')

        if url_style == 'path':
            # Path-style: s3://endpoint:port/bucket/key/path
            parts = parsed.path.strip('/').split('/', 1)
            if len(parts) < 2:
                raise exception.RSEFileNameNotSupported(
                    f'Invalid S3 path-style URL: {pfn} (expected format: s3://host/bucket/key)'
                )
            bucket, key = parts
        else:
            # Virtual-host-style: s3://bucket.endpoint:port/key/path
            host_parts = parsed.netloc.split('.')
            if not host_parts:
                raise exception.RSEFileNameNotSupported(
                    f'Invalid S3 virtual-host URL: {pfn}'
                )
            bucket = host_parts[0].split(':')[0]  # Remove port if present
            key = parsed.path.strip('/')
            if not key:
                raise exception.RSEFileNameNotSupported(
                    f'Invalid S3 virtual-host URL: {pfn} (missing key)'
                )

        return bucket, key

    def connect(self, credentials: Optional[dict[str, Any]] = None) -> None:
        """Establish connection to S3.

        For S3, this just validates that we can create a client.
        The actual client is created lazily on first use.

        :param credentials: Not used for S3 (credentials come from RSE attributes)
        :raises RSEAccessDenied: if S3 client cannot be created
        """
        try:
            # Test client creation
            _ = self.s3_client
        except Exception as e:
            raise exception.RSEAccessDenied(f'Failed to connect to S3: {e}')

    def close(self) -> None:
        """Close S3 connection."""
        if self._s3_client is not None:
            # boto3 clients don't need explicit closing
            self._s3_client = None

    def exists(self, pfn: str) -> bool:
        """Check if file exists in S3.

        :param pfn: Physical file name
        :returns: True if file exists, False otherwise
        :raises ServiceUnavailable: on S3 errors
        """
        try:
            bucket, key = self._parse_pfn(pfn)
            self.s3_client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            if error_code in ('404', 'NoSuchKey'):
                return False
            self.logger(logging.ERROR, f'S3 error checking existence of {pfn}: {e}')
            raise exception.ServiceUnavailable(f'S3 error: {e}')

    def get(self, pfn: str, dest: str, transfer_timeout: Optional[int] = None) -> None:
        """Download file from S3.

        :param pfn: Physical file name (source)
        :param dest: Local destination path
        :param transfer_timeout: Transfer timeout (not used for S3)
        :raises SourceNotFound: if source file doesn't exist
        :raises DestinationNotAccessible: if destination cannot be written
        :raises ServiceUnavailable: on S3 errors
        """
        try:
            bucket, key = self._parse_pfn(pfn)
            self.s3_client.download_file(bucket, key, dest)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            if error_code in ('404', 'NoSuchKey'):
                raise exception.SourceNotFound(f'File not found in S3: {pfn}')
            self.logger(logging.ERROR, f'S3 error downloading {pfn}: {e}')
            raise exception.ServiceUnavailable(f'Failed to download from S3: {e}')
        except OSError as e:
            raise exception.DestinationNotAccessible(f'Cannot write to {dest}: {e}')

    def put(
        self,
        source: str,
        target: str,
        source_dir: Optional[str] = None,
        transfer_timeout: Optional[int] = None
    ) -> None:
        """Upload file to S3.

        :param source: Source file name
        :param target: Target PFN
        :param source_dir: Source directory (prepended to source if provided)
        :param transfer_timeout: Transfer timeout (not used for S3)
        :raises SourceNotFound: if source file doesn't exist
        :raises DestinationNotAccessible: on S3 upload errors
        """
        full_source = f'{source_dir}/{source}' if source_dir else source

        try:
            bucket, key = self._parse_pfn(target)
            self.s3_client.upload_file(full_source, bucket, key)
        except FileNotFoundError:
            raise exception.SourceNotFound(f'Source file not found: {full_source}')
        except ClientError as e:
            self.logger(logging.ERROR, f'S3 error uploading to {target}: {e}')
            raise exception.DestinationNotAccessible(f'Failed to upload to S3: {e}')

    def delete(self, pfn: str) -> None:
        """Delete file from S3.

        :param pfn: Physical file name
        :raises SourceNotFound: if file doesn't exist
        :raises ServiceUnavailable: on S3 errors
        """
        try:
            bucket, key = self._parse_pfn(pfn)
            # Check if exists first (S3 delete succeeds even if file doesn't exist)
            try:
                self.s3_client.head_object(Bucket=bucket, Key=key)
            except ClientError as e:
                if e.response.get('Error', {}).get('Code', '') in ('404', 'NoSuchKey'):
                    raise exception.SourceNotFound(f'File not found: {pfn}')
                raise

            self.s3_client.delete_object(Bucket=bucket, Key=key)
        except exception.SourceNotFound:
            raise
        except ClientError as e:
            self.logger(logging.ERROR, f'S3 error deleting {pfn}: {e}')
            raise exception.ServiceUnavailable(f'Failed to delete from S3: {e}')

    def rename(self, pfn: str, new_pfn: str) -> None:
        """Rename file in S3.

        S3 doesn't support atomic rename. This would require copy + delete.

        :param pfn: Current PFN
        :param new_pfn: New PFN
        :raises NotImplementedError: always (S3 doesn't support rename)
        """
        raise NotImplementedError('S3 does not support atomic rename operations')

    def stat(self, pfn: str) -> dict[str, Any]:
        """Get file metadata from S3.

        :param pfn: Physical file name
        :returns: Dictionary with 'filesize' and 'md5' keys
        :raises SourceNotFound: if file doesn't exist
        :raises ServiceUnavailable: on S3 errors
        """
        try:
            bucket, key = self._parse_pfn(pfn)
            response = self.s3_client.head_object(Bucket=bucket, Key=key)

            # ETag may be MD5 (for simple uploads) or something else (multipart)
            etag = response.get('ETag', '').strip('"')

            return {
                'filesize': response['ContentLength'],
                'adler32': None,  # S3 doesn't provide adler32
                'md5': etag if '-' not in etag else None,  # Only MD5 if not multipart
            }
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            if error_code in ('404', 'NoSuchKey'):
                raise exception.SourceNotFound(f'File not found: {pfn}')
            self.logger(logging.ERROR, f'S3 error stating {pfn}: {e}')
            raise exception.ServiceUnavailable(f'Failed to stat S3 file: {e}')


class ReadOnly(Default):
    """Read-only S3 protocol implementation.

    For external S3 buckets where write operations should be disabled.
    """

    def __init__(self, protocol_attr: dict[str, Any], rse_settings: "RSESettingsDict", logger=logging.log):
        """Initialize read-only S3 protocol."""
        super().__init__(protocol_attr, rse_settings, logger=logger)
        self.overwrite = False

    def put(
        self,
        source: str,
        target: str,
        source_dir: Optional[str] = None,
        transfer_timeout: Optional[int] = None
    ) -> None:
        """Upload not supported for read-only RSEs."""
        raise exception.RSEOperationNotSupported('Upload not supported for read-only S3 RSEs')

    def delete(self, pfn: str) -> None:
        """Delete not supported for read-only RSEs."""
        raise exception.RSEOperationNotSupported('Delete not supported for read-only S3 RSEs')
