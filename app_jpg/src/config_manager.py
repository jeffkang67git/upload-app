"""
ConfigManager - JSON配置持久化
"""
import json, os
from pathlib import Path

class ConfigManager:
    def __init__(self, config_dir=None):
        if config_dir is None:
            config_dir = Path(__file__).parent.parent / 'config'
        self.config_file = Path(config_dir) / 'config.json'
        self.config_file.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict:
        defaults = {
            'base_dir': '/Users/jeffreykang/Documents/Projects/体检报告上传',
            'desktop_output': True,
            'last_user_id': '',
            'window_width': 1100,
            'window_height': 750,
        }
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            defaults.update(saved)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return defaults

    def save(self, config: dict):
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)