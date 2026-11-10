"""Explicit config version dispatch; legacy experiment implementation untouched."""

import model_reasoning as v1
import model_reasoning_v2 as v2
from model import ModelFailure as ModelFailure, validate_claims as validate_claims


def implementation(config):
    return v2 if config.get("config_version") == v2.CONFIG_VERSION else v1


def validate_settings(config):
    if config.get("provider") == "responses":
        raise ValueError("Responses已停用，仅保留历史结果查看")
    if (
        config.get("config_version") != v2.CONFIG_VERSION
        and "stage_max_output_tokens" in config
    ):
        raise ValueError("分阶段预算必须使用 v2 配置版本，不能静默忽略")
    return implementation(config).validate_settings(config)


def configuration_record(config):
    return implementation(config).configuration_record(config)


def Model(config, transport=None):
    validate_settings(config)
    return implementation(config).Model(config, transport)
