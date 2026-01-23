"""
Secrets Manager Module
======================
Handles secure retrieval of API keys and private keys from AWS Secrets Manager.
Provides fallback to environment variables for development.
"""

import os
import json
import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


@dataclass
class BotSecrets:
    """Container for all bot secrets"""
    # Polymarket
    polygon_private_key: str
    polymarket_api_key: str
    polymarket_api_secret: str
    
    # Tomorrow.io
    tomorrow_io_api_key: str
    
    # Supabase
    supabase_url: str
    supabase_service_key: str
    
    # Webhook (optional)
    webhook_url: Optional[str] = None


class SecretsManager:
    """
    Secure secrets management using AWS Secrets Manager.
    
    Falls back to environment variables if AWS is unavailable
    or if USE_ENV_FILE is configured.
    """
    
    def __init__(
        self,
        secret_name: str = "polymarket-bot/production",
        region: str = "us-east-1",
        use_env_fallback: bool = True
    ):
        self.secret_name = secret_name
        self.region = region
        self.use_env_fallback = use_env_fallback
        self._secrets: Optional[BotSecrets] = None
        self._boto3_client = None
    
    def _get_boto3_client(self) -> boto3.client:
        """Lazy initialization of boto3 client"""
        if self._boto3_client is None:
            self._boto3_client = boto3.client(
                service_name='secretsmanager',
                region_name=self.region
            )
        return self._boto3_client
    
    def _load_from_aws(self) -> Dict[str, str]:
        """Load secrets from AWS Secrets Manager"""
        try:
            client = self._get_boto3_client()
            response = client.get_secret_value(SecretId=self.secret_name)
            
            # Secrets can be stored as JSON string or plain text
            if 'SecretString' in response:
                secret_string = response['SecretString']
                try:
                    return json.loads(secret_string)
                except json.JSONDecodeError:
                    # Plain text secret
                    return {"secret": secret_string}
            else:
                # Binary secret (unlikely for this use case)
                raise ValueError("Binary secrets not supported")
                
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'ResourceNotFoundException':
                logger.warning(f"Secret {self.secret_name} not found in AWS")
            elif error_code == 'DecryptionFailureException':
                logger.error("AWS KMS decryption failed for secret")
            elif error_code == 'AccessDeniedException':
                logger.error("Access denied to AWS Secrets Manager")
            else:
                logger.error(f"AWS Secrets Manager error: {e}")
            raise
    
    def _load_from_env(self) -> Dict[str, str]:
        """Load secrets from environment variables"""
        env_mapping = {
            'polygon_private_key': 'POLYGON_PRIVATE_KEY',
            'polymarket_api_key': 'POLYMARKET_API_KEY',
            'polymarket_api_secret': 'POLYMARKET_API_SECRET',
            'tomorrow_io_api_key': 'TOMORROW_IO_API_KEY',
            'supabase_url': 'SUPABASE_URL',
            'supabase_service_key': 'SUPABASE_SERVICE_KEY',
            'webhook_url': 'WEBHOOK_URL',
        }
        
        secrets = {}
        missing_keys = []
        
        for key, env_var in env_mapping.items():
            value = os.environ.get(env_var)
            if value:
                secrets[key] = value
            else:
                # Webhook is optional
                if key != 'webhook_url':
                    missing_keys.append(env_var)
        
        if missing_keys:
            logger.warning(f"Missing environment variables: {missing_keys}")
        
        return secrets
    
    def load_secrets(self) -> BotSecrets:
        """
        Load all secrets from AWS or environment.
        
        Returns:
            BotSecrets: All required secrets for the bot
            
        Raises:
            ValueError: If secrets cannot be loaded
        """
        if self._secrets is not None:
            return self._secrets
        
        secrets_dict = {}
        
        # Try AWS first
        if self.use_env_fallback:
            try:
                secrets_dict = self._load_from_aws()
                logger.info("Successfully loaded secrets from AWS Secrets Manager")
            except Exception as e:
                logger.warning(f"Failed to load from AWS: {e}, falling back to env vars")
                secrets_dict = self._load_from_env()
        else:
            secrets_dict = self._load_from_aws()
        
        # Create BotSecrets object
        try:
            self._secrets = BotSecrets(
                polygon_private_key=secrets_dict.get('polygon_private_key', ''),
                polymarket_api_key=secrets_dict.get('polymarket_api_key', ''),
                polymarket_api_secret=secrets_dict.get('polymarket_api_secret', ''),
                tomorrow_io_api_key=secrets_dict.get('tomorrow_io_api_key', ''),
                supabase_url=secrets_dict.get('supabase_url', ''),
                supabase_service_key=secrets_dict.get('supabase_service_key', ''),
                webhook_url=secrets_dict.get('webhook_url'),
            )
        except KeyError as e:
            raise ValueError(f"Missing required secret: {e}")
        
        return self._secrets
    
    def reload_secrets(self) -> BotSecrets:
        """Force reload of secrets"""
        self._secrets = None
        return self.load_secrets()
    
    def validate_secrets(self, secrets: BotSecrets) -> bool:
        """
        Validate that all required secrets are present.
        
        Args:
            secrets: BotSecrets object to validate
            
        Returns:
            True if all secrets are valid
        """
        required_fields = [
            'polygon_private_key',
            'polymarket_api_key',
            'polymarket_api_secret',
            'tomorrow_io_api_key',
            'supabase_url',
            'supabase_service_key',
        ]
        
        for field in required_fields:
            value = getattr(secrets, field)
            if not value or len(value) < 10:
                logger.error(f"Invalid or missing secret: {field}")
                return False
        
        # Validate private key format (should start with 0x and be 64 hex chars)
        if not secrets.polygon_private_key.startswith('0x'):
            logger.error("Polygon private key must start with 0x")
            return False
        
        if len(secrets.polygon_private_key) != 66:  # 0x + 64 hex chars
            logger.error("Polygon private key should be 66 characters (0x + 64 hex)")
            return False
        
        return True


def get_secrets_manager(
    secret_name: str = "polymarket-bot/production",
    region: str = "us-east-1"
) -> SecretsManager:
    """
    Factory function to create a SecretsManager instance.
    
    Args:
        secret_name: Name of the secret in AWS Secrets Manager
        region: AWS region
        
    Returns:
        Configured SecretsManager instance
    """
    return SecretsManager(secret_name=secret_name, region=region)


# Convenience function for quick access
def load_bot_secrets(
    secret_name: str = "polymarket-bot/production",
    region: str = "us-east-1"
) -> BotSecrets:
    """
    Load all bot secrets in one call.
    
    Args:
        secret_name: Name of the secret in AWS Secrets Manager
        region: AWS region
        
    Returns:
        BotSecrets object with all secrets
    """
    manager = SecretsManager(secret_name=secret_name, region=region)
    secrets = manager.load_secrets()
    
    if not manager.validate_secrets(secrets):
        raise ValueError("Secrets validation failed")
    
    return secrets

