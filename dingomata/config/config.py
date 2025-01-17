import logging.config
import os
from functools import cached_property
from typing import Dict, Any, Set, List

import yaml
from discord_slash.model import SlashCommandPermissionType
from discord_slash.utils.manage_commands import create_permission
from pydantic import BaseSettings, SecretStr, BaseModel, Field, validator, FilePath

from dingomata.cogs.bedtime.config import BedtimeConfig
from dingomata.cogs.botadmin.config import BotAdminConfig
from dingomata.cogs.gamba.config import GambaConfig
from dingomata.cogs.game_code.config import GameCodeConfig
from dingomata.cogs.quote.config import QuoteConfig
from dingomata.cogs.text.config import TextConfig
from dingomata.cogs.twitch.config import TwitchConfig

_LOGGING_CONFIG = 'config/logging.cfg'

_logger = logging.getLogger(__name__)


class GuildConfig(BaseModel):
    """Per-guild configs"""

    #: Global list of role or user IDs who can use all commands.
    #: Users and roles in this list has access to all commands regardless of per-command permissions, as long as the
    #: command is enabled for the server.
    mod_roles: Set[int] = set()
    mod_users: Set[int] = set()
    commands: Dict[str, bool] = {}

    bedtime: BedtimeConfig = BedtimeConfig()
    botadmin: BotAdminConfig = BotAdminConfig()
    gamba: GambaConfig = GambaConfig()
    game_code: GameCodeConfig = GameCodeConfig()
    quote: QuoteConfig = QuoteConfig()
    text: TextConfig = TextConfig()
    twitch: TwitchConfig = TwitchConfig()

    class Config:
        keep_untouched = (cached_property,)
        extra = 'forbid'

    @cached_property
    def mod_permissions(self) -> List[Dict]:
        return [create_permission(role, SlashCommandPermissionType.ROLE, True) for role in self.mod_roles] \
                + [create_permission(role, SlashCommandPermissionType.USER, True) for role in self.mod_users]


class _BotConfig(BaseSettings):
    servers: Dict[int, GuildConfig]


class ServiceConfig(BaseSettings):
    token: SecretStr = Field(..., env='token')
    database_url: SecretStr = Field(..., env='database_Url')
    config_file: FilePath = Field(..., env='config_file')
    command_prefix: str = Field('\\', min_length=1, max_length=1)  # This is unused

    @validator('database_url', pre=True)
    def translate_postgres(cls, v: Any):
        if isinstance(v, str) and v.startswith('postgres://'):
            return v.replace('postgres://', 'postgresql+asyncpg://')
        return v

    class Config:
        env_prefix = 'dingomata'
        env_file = os.environ.get('ENV_FILE', '.env')
        keep_untouched = (cached_property,)
        extra = 'forbid'

    @cached_property
    def servers(self) -> Dict[int, GuildConfig]:
        config_data = yaml.safe_load(self.config_file.open())
        return _BotConfig.parse_obj(config_data).servers

    @cached_property
    def mod_permissions(self) -> Dict[int, List[Dict]]:
        return {guild: config.mod_permissions for guild, config in self.servers.items()}

    def get_command_guilds(self, command: str, default: bool = True) -> List[int]:
        return [server for server, config in self.servers.items() if config.commands.get(command, default)]


def get_logging_config():
    logging.config.fileConfig(_LOGGING_CONFIG, disable_existing_loggers=False)
    _logger.debug('Loaded logging config.')


service_config = ServiceConfig()
