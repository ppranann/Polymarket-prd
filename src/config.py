"""
Configuration Module
====================
Loads and manages configuration from config.yaml file.
Supports environment variable overrides.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml
from dataclasses import dataclass, field


@dataclass
class LocationConfig:
    """Location configuration for weather data"""
    lat: float
    lon: float


@dataclass
class RiskConfig:
    """Risk management parameters"""
    initial_capital: float
    max_trade_size: float
    max_daily_loss: float
    max_open_positions: int
    position_concentration: float
    profit_target: float


@dataclass
class FarmingConfig:
    """Liquidity farming parameters with Adjusted Midpoint (2026)"""
    enabled: bool
    price_threshold: float
    max_spread: float
    farming_size: float
    # 2026 Adjusted Midpoint Farming
    rebalance_interval: int = 60
    midpoint_movement_threshold: float = 0.005
    blue_zone_buffer: float = 0.01


@dataclass
class AlphaConfig:
    """Alpha snipe parameters"""
    enabled: bool
    threshold: float
    size_multiplier_max: float


@dataclass
class ScannerConfig:
    """Market scanner parameters with Lazy API (save credits)"""
    scan_interval: int
    price_threshold: float
    categories: List[str]
    filters: Dict[str, Any]
    # Lazy API Settings
    weather_cache_ttl: int = 900  # 15 minutes
    snipe_price_threshold: float = 0.85
    price_threshold: float = 0.85
    min_weather_update_interval: int = 15  # minutes


@dataclass
class LoggingConfig:
    """Logging configuration"""
    level: str
    format: str
    supabase: Dict[str, str]
    local: Dict[str, Any]


@dataclass
class HealthConfig:
    """Health check and alert configuration"""
    enabled: bool
    check_interval: int
    webhook: Dict[str, Any]
    alerts: Dict[str, bool]


@dataclass
class ExecutionConfig:
    """Order execution settings"""
    order_timeout: int
    retry_attempts: int
    retry_delay: int
    signing: Dict[str, Any]


@dataclass
class DevelopmentConfig:
    """Development/testing settings"""
    dry_run: bool
    paper_trading: bool
    log_all_requests: bool


@dataclass
class BotConfig:
    """Main configuration container"""
    # Polymarket settings
    chain_id: int
    api_url: str
    api_key: str
    clob_contract_address: str
    
    # Tomorrow.io settings
    tomorrow_io: Dict[str, Any]
    default_location: LocationConfig
    
    # AWS settings
    aws: Dict[str, Any]
    
    # Core parameters
    risk: RiskConfig
    farming: FarmingConfig
    alpha: AlphaConfig
    scanner: ScannerConfig
    logging: LoggingConfig
    health: HealthConfig
    execution: ExecutionConfig
    development: DevelopmentConfig


class ConfigLoader:
    """
    Configuration loader with environment variable support.
    
    Features:
    - Load from YAML file
    - Environment variable substitution using ${VAR} syntax
    - Type validation
    - Nested configuration access
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize config loader.
        
        Args:
            config_path: Path to config.yaml. If None, uses default path.
        """
        if config_path is None:
            # Look for config.yaml in common locations
            possible_paths = [
                Path.cwd() / "config.yaml",
                Path(__file__).parent.parent / "config.yaml",
                Path(__file__).parent.parent.parent / "config.yaml",
            ]
            for path in possible_paths:
                if path.exists():
                    config_path = str(path)
                    break
        
        self.config_path = config_path
        self._config: Optional[Dict[str, Any]] = None
        self._bot_config: Optional[BotConfig] = None
    
    def _resolve_env_vars(self, value: Any) -> Any:
        """
        Recursively resolve environment variables in config values.
        
        Args:
            value: Configuration value (may contain ${VAR} placeholders)
            
        Returns:
            Value with environment variables resolved
        """
        if isinstance(value, str):
            # Pattern for ${VAR} or ${VAR:default}
            pattern = r'\$\{([^}:]+)(?::([^}]*))?\}'
            
            def replace(match):
                env_var = match.group(1)
                default = match.group(2) or ''
                return os.environ.get(env_var, default)
            
            return re.sub(pattern, replace, value)
        
        elif isinstance(value, dict):
            return {k: self._resolve_env_vars(v) for k, v in value.items()}
        
        elif isinstance(value, list):
            return [self._resolve_env_vars(item) for item in value]
        
        return value
    
    def load(self) -> Dict[str, Any]:
        """
        Load configuration from YAML file.
        
        Returns:
            Dictionary containing configuration
            
        Raises:
            FileNotFoundError: If config file doesn't exist
            yaml.YAMLError: If config file is invalid
        """
        if self._config is not None:
            return self._config
        
        if not self.config_path or not Path(self.config_path).exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        
        with open(self.config_path, 'r') as f:
            raw_config = yaml.safe_load(f)
        
        # Resolve environment variables
        self._config = self._resolve_env_vars(raw_config)
        
        return self._config
    
    def parse(self) -> BotConfig:
        """
        Parse configuration into typed dataclasses.
        
        Returns:
            BotConfig with all settings
        """
        if self._bot_config is not None:
            return self._bot_config
        
        config = self.load()
        
        # Parse nested configurations
        default_loc = config['tomorrow_io']['default_location']
        
        risk_cfg = config['risk']
        farming_cfg = config['farming']
        alpha_cfg = config['alpha']
        scanner_cfg = config['scanner']
        logging_cfg = config['logging']
        health_cfg = config['health']
        exec_cfg = config['execution']
        dev_cfg = config['development']
        
        self._bot_config = BotConfig(
            chain_id=config['polymarket']['chain_id'],
            api_url=config['polymarket']['api_url'],
            api_key=config['polymarket']['api_key'],
            clob_contract_address=config['polymarket'].get('clob_contract_address', ''),
            tomorrow_io=config['tomorrow_io'],
            default_location=LocationConfig(
                lat=default_loc['lat'],
                lon=default_loc['lon']
            ),
            aws=config['aws'],
            risk=RiskConfig(
                initial_capital=risk_cfg['initial_capital'],
                max_trade_size=risk_cfg['max_trade_size'],
                max_daily_loss=risk_cfg['max_daily_loss'],
                max_open_positions=risk_cfg['max_open_positions'],
                position_concentration=risk_cfg['position_concentration'],
                profit_target=risk_cfg['profit_target']
            ),
            farming=FarmingConfig(
                enabled=farming_cfg['enabled'],
                price_threshold=farming_cfg['price_threshold'],
                max_spread=farming_cfg['max_spread'],
                farming_size=farming_cfg['farming_size'],
                rebalance_interval=farming_cfg.get('rebalance_interval', 60),
                midpoint_movement_threshold=farming_cfg.get('midpoint_movement_threshold', 0.005),
                blue_zone_buffer=farming_cfg.get('blue_zone_buffer', 0.01)
            ),
            alpha=AlphaConfig(
                enabled=alpha_cfg['enabled'],
                threshold=alpha_cfg['threshold'],
                size_multiplier_max=alpha_cfg['size_multiplier_max']
            ),
            scanner=ScannerConfig(
                scan_interval=scanner_cfg['scan_interval'],
                price_threshold=scanner_cfg['price_threshold'],
                categories=scanner_cfg['categories'],
                filters=scanner_cfg['filters'],
                weather_cache_ttl=scanner_cfg.get('weather_cache_ttl', 900),
                snipe_price_threshold=scanner_cfg.get('snipe_price_threshold', 0.15),
                min_weather_update_interval=scanner_cfg.get('min_weather_update_interval', 15)
            ),
            logging=LoggingConfig(
                level=logging_cfg['level'],
                format=logging_cfg['format'],
                supabase=logging_cfg['supabase'],
                local=logging_cfg['local']
            ),
            health=HealthConfig(
                enabled=health_cfg['enabled'],
                check_interval=health_cfg['check_interval'],
                webhook=health_cfg['webhook'],
                alerts=health_cfg['alerts']
            ),
            execution=ExecutionConfig(
                order_timeout=exec_cfg['order_timeout'],
                retry_attempts=exec_cfg['retry_attempts'],
                retry_delay=exec_cfg['retry_delay'],
                signing=exec_cfg['signing']
            ),
            development=DevelopmentConfig(
                dry_run=dev_cfg['dry_run'],
                paper_trading=dev_cfg['paper_trading'],
                log_all_requests=dev_cfg['log_all_requests']
            )
        )
        
        return self._bot_config
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value by key using dot notation.
        
        Args:
            key: Configuration key (e.g., 'risk.max_trade_size')
            default: Default value if key not found
            
        Returns:
            Configuration value
        """
        config = self.load()
        keys = key.split('.')
        
        value = config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
            
            if value is None:
                return default
        
        return value
    
    def reload(self) -> BotConfig:
        """Force reload of configuration"""
        self._config = None
        self._bot_config = None
        return self.parse()


def load_config(config_path: Optional[str] = None) -> BotConfig:
    """
    Load and parse configuration from YAML file.
    
    Args:
        config_path: Path to config.yaml file
        
    Returns:
        BotConfig object with all settings
    """
    loader = ConfigLoader(config_path)
    return loader.parse()


def get_nested(config: Dict, *keys: str, default: Any = None) -> Any:
    """
    Get nested configuration value using dot notation.
    
    Args:
        config: Configuration dictionary
        keys: Path to the value
        default: Default value if not found
        
    Returns:
        Configuration value
    """
    value = config
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            return default
        
        if value is None:
            return default
    
    return value

