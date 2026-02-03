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

"""Tests for S3 protocol implementation."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError

from rucio.common import exception
from rucio.rse.protocols.s3boto import Default as S3Protocol
from rucio.rse.protocols.s3boto import ReadOnly as S3ReadOnlyProtocol

# UNIT TESTS (mocked boto3)

@pytest.fixture
def rse_settings():
    """Mock RSE settings for S3."""
    return {
        'rse': 'TEST_S3_RSE',
        'id': 'test-rse-id-123',
        's3_access_key': 'test_access_key',
        's3_secret_key': 'test_secret_key',
        's3_url': 'https://s3.example.com',
        'region': 'us-east-1',
        'signature_version': 's3v4',
        's3_url_style': 'path',
        'deterministic': False,
    }


@pytest.fixture
def protocol_attr():
    """Mock protocol attributes."""
    return {
        'scheme': 'https',
        'hostname': 's3.example.com',
        'port': 443,
        'prefix': '/test-bucket',
        'auth_token': 'mock_token',
    }


@pytest.fixture
def s3_protocol(protocol_attr, rse_settings):
    """Create S3 protocol instance."""
    return S3Protocol(protocol_attr, rse_settings)


@pytest.mark.unit
class TestS3Protocol:
    """Test suite for S3 protocol implementation (unit tests with mocks)."""

    def test_init(self, s3_protocol):
        """Test protocol initialization."""
        assert s3_protocol.renaming is False
        assert s3_protocol.overwrite is True
        assert s3_protocol._s3_client is None

    @patch('rucio.rse.protocols.s3boto.boto3.client')
    def test_create_s3_client(self, mock_boto_client, s3_protocol):
        """Test S3 client creation."""
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client

        client = s3_protocol.s3_client

        assert client == mock_client
        mock_boto_client.assert_called_once()
        call_kwargs = mock_boto_client.call_args[1]
        assert call_kwargs['aws_access_key_id'] == 'test_access_key'
        assert call_kwargs['aws_secret_access_key'] == 'test_secret_key'
        assert call_kwargs['endpoint_url'] == 'https://s3.example.com'

    def test_parse_pfn_path_style(self, s3_protocol):
        """Test PFN parsing for path-style URLs."""
        pfn = 'https://s3.example.com/my-bucket/path/to/file.txt'
        bucket, key = s3_protocol._parse_pfn(pfn)

        assert bucket == 'my-bucket'
        assert key == 'path/to/file.txt'

    def test_parse_pfn_virtual_host_style(self, protocol_attr, rse_settings):
        """Test PFN parsing for virtual-host-style URLs."""
        rse_settings['s3_url_style'] = 'host'
        protocol = S3Protocol(protocol_attr, rse_settings)

        pfn = 'https://my-bucket.s3.example.com/path/to/file.txt'
        bucket, key = protocol._parse_pfn(pfn)

        assert bucket == 'my-bucket'
        assert key == 'path/to/file.txt'

    def test_parse_pfn_invalid(self, s3_protocol):
        """Test PFN parsing with invalid URL."""
        with pytest.raises(exception.RSEFileNameNotSupported):
            s3_protocol._parse_pfn('https://s3.example.com/bucket-only')

    @patch('rucio.rse.protocols.s3boto.boto3.client')
    def test_exists_true(self, mock_boto_client, s3_protocol):
        """Test exists method when file exists."""
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client
        mock_client.head_object.return_value = {'ContentLength': 1024}

        pfn = 'https://s3.example.com/bucket/file.txt'
        assert s3_protocol.exists(pfn) is True
        mock_client.head_object.assert_called_once_with(Bucket='bucket', Key='file.txt')

    @patch('rucio.rse.protocols.s3boto.boto3.client')
    def test_exists_false(self, mock_boto_client, s3_protocol):
        """Test exists method when file doesn't exist."""
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client
        mock_client.head_object.side_effect = ClientError(
            {'Error': {'Code': '404'}}, 'HeadObject'
        )

        pfn = 'https://s3.example.com/bucket/missing.txt'
        assert s3_protocol.exists(pfn) is False

    @patch('rucio.rse.protocols.s3boto.boto3.client')
    def test_get_success(self, mock_boto_client, s3_protocol):
        """Test successful file download."""
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            dest = tmp.name

        try:
            pfn = 'https://s3.example.com/bucket/file.txt'
            s3_protocol.get(pfn, dest)

            mock_client.download_file.assert_called_once_with('bucket', 'file.txt', dest)
        finally:
            if os.path.exists(dest):
                os.unlink(dest)

    @patch('rucio.rse.protocols.s3boto.boto3.client')
    def test_get_not_found(self, mock_boto_client, s3_protocol):
        """Test download of non-existent file."""
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client
        mock_client.download_file.side_effect = ClientError(
            {'Error': {'Code': '404'}}, 'GetObject'
        )

        pfn = 'https://s3.example.com/bucket/missing.txt'
        with pytest.raises(exception.SourceNotFound):
            s3_protocol.get(pfn, '/tmp/dest.txt')

    @patch('rucio.rse.protocols.s3boto.boto3.client')
    def test_put_success(self, mock_boto_client, s3_protocol):
        """Test successful file upload."""
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b'test content')
            source = tmp.name

        try:
            target = 'https://s3.example.com/bucket/uploaded.txt'
            s3_protocol.put(source, target)

            mock_client.upload_file.assert_called_once_with(source, 'bucket', 'uploaded.txt')
        finally:
            if os.path.exists(source):
                os.unlink(source)

    @patch('rucio.rse.protocols.s3boto.boto3.client')
    def test_delete_success(self, mock_boto_client, s3_protocol):
        """Test successful file deletion."""
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client
        mock_client.head_object.return_value = {'ContentLength': 1024}

        pfn = 'https://s3.example.com/bucket/file.txt'
        s3_protocol.delete(pfn)

        mock_client.head_object.assert_called_once()
        mock_client.delete_object.assert_called_once_with(Bucket='bucket', Key='file.txt')

    @patch('rucio.rse.protocols.s3boto.boto3.client')
    def test_delete_not_found(self, mock_boto_client, s3_protocol):
        """Test deletion of non-existent file."""
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client
        mock_client.head_object.side_effect = ClientError(
            {'Error': {'Code': '404'}}, 'HeadObject'
        )

        pfn = 'https://s3.example.com/bucket/missing.txt'
        with pytest.raises(exception.SourceNotFound):
            s3_protocol.delete(pfn)

    def test_rename_not_supported(self, s3_protocol):
        """Test that rename raises NotImplementedError."""
        with pytest.raises(NotImplementedError):
            s3_protocol.rename('old.txt', 'new.txt')

    @patch('rucio.rse.protocols.s3boto.boto3.client')
    def test_stat_success(self, mock_boto_client, s3_protocol):
        """Test successful stat operation."""
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client
        mock_client.head_object.return_value = {
            'ContentLength': 2048,
            'ETag': '"abc123def456"',
        }

        pfn = 'https://s3.example.com/bucket/file.txt'
        stats = s3_protocol.stat(pfn)

        assert stats['filesize'] == 2048
        assert stats['md5'] == 'abc123def456'
        assert stats['adler32'] is None


@pytest.mark.unit
class TestS3ReadOnlyProtocol:
    """Test suite for read-only S3 protocol."""

    @pytest.fixture
    def readonly_protocol(self, protocol_attr, rse_settings):
        """Create read-only S3 protocol instance."""
        return S3ReadOnlyProtocol(protocol_attr, rse_settings)

    def test_put_not_allowed(self, readonly_protocol):
        """Test that put raises exception for read-only."""
        with pytest.raises(exception.RSEOperationNotSupported):
            readonly_protocol.put('source.txt', 'target.txt')

    def test_delete_not_allowed(self, readonly_protocol):
        """Test that delete raises exception for read-only."""
        with pytest.raises(exception.RSEOperationNotSupported):
            readonly_protocol.delete('file.txt')

    @patch('rucio.rse.protocols.s3boto.boto3.client')
    def test_get_still_works(self, mock_boto_client, readonly_protocol):
        """Test that get still works for read-only."""
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client

        pfn = 'https://s3.example.com/bucket/file.txt'
        readonly_protocol.get(pfn, '/tmp/dest.txt')

        mock_client.download_file.assert_called_once()


# INTEGRATION TESTS (real S3 via LocalStack)

@pytest.fixture(scope='module')
def localstack_s3():
    """Setup LocalStack S3 bucket and test file."""
    s3_client = boto3.client(
        's3',
        endpoint_url='http://localstack:4566',
        aws_access_key_id='test',
        aws_secret_access_key='test',
        region_name='us-east-1'
    )

    bucket = 'rucio-test-bucket'
    test_key = 'test-data/test-file.txt'
    test_content = b'Hello from Rucio S3 protocol test!'

    # Setup
    try:
        s3_client.create_bucket(Bucket=bucket)
        s3_client.put_object(Bucket=bucket, Key=test_key, Body=test_content)
    except ClientError as e:
        if e.response['Error']['Code'] != 'BucketAlreadyOwnedByYou':
            pytest.skip(f"LocalStack not available: {e}")

    yield {
        'bucket': bucket,
        'key': test_key,
        'content': test_content,
        'endpoint': 'http://localstack:4566'
    }

    # Cleanup
    try:
        s3_client.delete_object(Bucket=bucket, Key=test_key)
    except ClientError:
        pass


@pytest.fixture
def localstack_rse_settings(localstack_s3):
    """RSE settings for LocalStack."""
    return {
        'rse': 'LOCALSTACK_S3_RSE',
        'id': 'localstack-test-id',
        's3_access_key': 'test',
        's3_secret_key': 'test',
        's3_url': localstack_s3['endpoint'],
        'region': 'us-east-1',
        'signature_version': 's3v4',
        's3_url_style': 'path',
        's3_is_secure': 'false',
        'deterministic': False,
    }


@pytest.fixture
def localstack_protocol_attr():
    """Protocol attributes for LocalStack."""
    return {
        'scheme': 'http',
        'hostname': 'localhost',
        'port': 4566,
        'prefix': '/rucio-test-bucket',
        'auth_token': 'mock_token',
    }


@pytest.mark.integration
class TestS3ProtocolIntegration:
    """Integration tests with real S3 (LocalStack).

    Run with: SKIP_S3_INTEGRATION=false pytest tests/test_rse_protocol_s3boto.py -m integration
    """

    def test_protocol_exists(self, localstack_protocol_attr, localstack_rse_settings, localstack_s3):
        """Test exists() with real S3."""
        protocol = S3Protocol(localstack_protocol_attr, localstack_rse_settings)

        pfn = f"{localstack_s3['endpoint']}/{localstack_s3['bucket']}/{localstack_s3['key']}"
        assert protocol.exists(pfn) is True

        # Non-existent file
        missing_pfn = f"{localstack_s3['endpoint']}/{localstack_s3['bucket']}/does-not-exist.txt"
        assert protocol.exists(missing_pfn) is False

    def test_protocol_stat(self, localstack_protocol_attr, localstack_rse_settings, localstack_s3):
        """Test stat() with real S3."""
        protocol = S3Protocol(localstack_protocol_attr, localstack_rse_settings)

        pfn = f"{localstack_s3['endpoint']}/{localstack_s3['bucket']}/{localstack_s3['key']}"
        stats = protocol.stat(pfn)

        assert stats['filesize'] == len(localstack_s3['content'])
        assert 'md5' in stats

    def test_protocol_get(self, localstack_protocol_attr, localstack_rse_settings, localstack_s3):
        """Test get() (download) with real S3."""
        protocol = S3Protocol(localstack_protocol_attr, localstack_rse_settings)

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            dest = tmp.name

        try:
            pfn = f"{localstack_s3['endpoint']}/{localstack_s3['bucket']}/{localstack_s3['key']}"
            protocol.get(pfn, dest)

            # Verify downloaded content
            with open(dest, 'rb') as f:
                content = f.read()
            assert content == localstack_s3['content']
        finally:
            if os.path.exists(dest):
                os.unlink(dest)

    def test_protocol_put_and_delete(self, localstack_protocol_attr, localstack_rse_settings, localstack_s3):
        """Test put() (upload) and delete() with real S3."""
        protocol = S3Protocol(localstack_protocol_attr, localstack_rse_settings)

        # Create test file
        upload_content = b'Uploaded via Rucio protocol!'
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(upload_content)
            source = tmp.name

        upload_key = 'test-data/uploaded-file.txt'
        target_pfn = f"{localstack_s3['endpoint']}/{localstack_s3['bucket']}/{upload_key}"

        try:
            # Upload
            protocol.put(source, target_pfn)

            # Verify upload
            assert protocol.exists(target_pfn) is True

            # Download and verify content
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                verify_dest = tmp.name

            protocol.get(target_pfn, verify_dest)
            with open(verify_dest, 'rb') as f:
                downloaded = f.read()
            assert downloaded == upload_content

            os.unlink(verify_dest)

            # Test delete
            protocol.delete(target_pfn)
            assert protocol.exists(target_pfn) is False

        finally:
            if os.path.exists(source):
                os.unlink(source)
