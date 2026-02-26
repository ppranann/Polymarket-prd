"""
Secrets Manager Module
======================
Handles secure retrieval of API keys and private keys from AWS Secrets Manager.
Uses AWS recommended boto3 pattern.
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
    polygon_private_key: str = ""
    polymarket_api_key: str = ""
    polymarket_api_secret: str = ""
    polymarket_api_passphrase: str = ""   # From Polymarket API credentials page
    polymarket_proxy_address: str = ""    # Proxy contract wallet address shown on Polymarket profile

    # Tomorrow.io
    tomorrow_io_api_key: str = ""

    # Supabase (optional - bot works without logging)
    supabase_url: str = ""
    supabase_service_key: str = ""

    # Webhook (optional)
    webhook_url: Optional[str] = None


class SecretsManager:
    """
    Secure secrets management using AWS Secrets Manager.
    
    Uses AWS recommended boto3 pattern:
    https://aws.amazon.com/developer/language/python/
    """
    
    def __init__(
        self,
        secret_name: str = "polymarket-bot/production",
        region_name: str = None
    ):
        self.secret_name = secret_name
        self.region_name = region_name or os.environ.get("AWS_DEFAULT_REGION", "eu-west-1")
    
    def get_secret(self) -> Dict[str, str]:
        """
        Retrieve secrets from AWS Secrets Manager.
        
        Uses the exact AWS recommended boto3 pattern.
        """
        # Create a Secrets Manager client
        session = boto3.session.Session()
        client = session.client(
            service_name='secretsmanager',
            region_name=self.region_name
        )

        try:
            get_secret_value_response = client.get_secret_value(
                SecretId=self.secret_name
            )
        except ClientError as e:
            logger.error(f"Failed to retrieve secret: {e}")
            raise e

        # Decrypts secret using the associated KMS key.
        secret = get_secret_value_response['SecretString']
        
        # Parse JSON secret
        try:
            return json.loads(secret)
        except json.JSONDecodeError:
            # Plain text secret (unlikely)
            return {"secret": secret}
    
    def load_secrets(self) -> BotSecrets:
        """
        Load all secrets from AWS Secrets Manager.
        
        Returns:
            BotSecrets: All required secrets for the bot
            
        Note:
            Supabase keys are optional - bot will work without logging
            if these are missing.
        """
        try:
            secrets_dict = self.get_secret()
            logger.info("Successfully loaded secrets from AWS Secrets Manager")
            
            # DEBUG: Log what keys were found
            logger.info(f"DEBUG - Keys found in AWS secret: {list(secrets_dict.keys())}")
            for key in ['polygon_private_key', 'polymarket_api_key', 'polymarket_api_secret', 'tomorrow_io_api_key', 'supabase_url', 'supabase_service_key']:
                value = secrets_dict.get(key, 'NOT_FOUND')
                if value == 'NOT_FOUND':
                    logger.warning(f"DEBUG - Missing key: {key}")
                elif len(str(value)) < 10:
                    logger.warning(f"DEBUG - Key too short: {key} = '{value}'")
                else:
                    logger.info(f"DEBUG - Key present: {key}")
                    
        except Exception as e:
            logger.warning(f"Failed to load from AWS: {e}")
            secrets_dict = {}
        
        # Create BotSecrets object (Supabase is optional)
        return BotSecrets(
            polygon_private_key=secrets_dict.get('polygon_private_key', ''),
            polymarket_api_key=secrets_dict.get('polymarket_api_key', ''),
            polymarket_api_secret=secrets_dict.get('polymarket_api_secret', ''),
            polymarket_api_passphrase=secrets_dict.get('polymarket_api_passphrase', ''),
            polymarket_proxy_address=secrets_dict.get('polymarket_proxy_address', ''),
            tomorrow_io_api_key=secrets_dict.get('tomorrow_io_api_key', ''),
            supabase_url=secrets_dict.get('supabase_url', ''),
            supabase_service_key=secrets_dict.get('supabase_service_key', ''),
            webhook_url=secrets_dict.get('webhook_url'),
        )
    
    def validate_secrets(self, secrets: BotSecrets, require_supabase: bool = False) -> bool:
        """
        Validate that required secrets are present.
        
        Args:
            secrets: BotSecrets object to validate
            require_supabase: If False, Supabase keys are optional
            
        Returns:
            True if required secrets are valid
        """
        required_fields = [
            'polymarket_api_key',
            'polymarket_api_secret',
            'tomorrow_io_api_key',
        ]

        for field in required_fields:
            value = getattr(secrets, field)
            if not value or len(value) < 10:
                logger.error(f"Invalid or missing secret: {field}")
                return False

        # Private key is required (signs every order)
        if not secrets.polygon_private_key:
            logger.error("polygon_private_key is required for order signing")
            return False

        # Supabase is optional
        if require_supabase:
            if not secrets.supabase_url or not secrets.supabase_service_key:
                logger.error("Supabase credentials required but not found")
                return False

        # Validate private key format
        if not secrets.polygon_private_key.startswith('0x'):
            logger.error("Polygon private key must start with 0x")
            return False

        if len(secrets.polygon_private_key) != 66:
            logger.error("Polygon private key should be 66 characters (0x + 64 hex)")
            return False
        
        return True


def get_secrets_manager(
    secret_name: str = "polymarket-bot/production",
    region_name: str = None
) -> SecretsManager:
    """
    Factory function to create a SecretsManager instance.
    """
    return SecretsManager(secret_name=secret_name, region_name=region_name)


def load_bot_secrets(
    secret_name: str = "polymarket-bot/production",
    region_name: str = None,
    require_supabase: bool = False
) -> BotSecrets:
    """
    Load all bot secrets in one call.
    
    Args:
        secret_name: Name of the secret in AWS Secrets Manager
        region_name: AWS region
        require_supabase: If True, Supabase keys are required
        
    Returns:
        BotSecrets object with all secrets
    """
    manager = SecretsManager(secret_name=secret_name, region_name=region_name)
    secrets = manager.load_secrets()
    
    if not manager.validate_secrets(secrets, require_supabase=require_supabase):
        raise ValueError("Secrets validation failed")
    
    return secrets

