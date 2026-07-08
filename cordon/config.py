"""Experiment configuration: config.yaml + .env (secrets only).

The config file carries every knob that must be reproducible (model ids,
temperature, endpoints, repetition count); the .env file carries only
credentials and is never committed or recorded.
"""

from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


class PlannerConfig(BaseModel):
    model: str
    pilot_model: str
    temperature: float | None = None
    max_output_tokens: int = 2048
    max_retries: int = 4
    timeout_s: float = 120


class LocalStackConfig(BaseModel):
    endpoint_url: str = "http://localhost:4566"
    region: str = "us-east-1"


class RunConfig(BaseModel):
    repetitions: int = 3
    output_dir: str = "experiments"


class Config(BaseModel):
    planner: PlannerConfig
    localstack: LocalStackConfig = LocalStackConfig()
    run: RunConfig = RunConfig()


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> Config:
    load_dotenv(REPO_ROOT / ".env")  # OPENAI_API_KEY etc.; silently absent is fine
    raw = yaml.safe_load(Path(path).read_text())
    return Config.model_validate(raw)
