from decomposer.representation import (
    canonical_text_runs, representation_model_config, representation_prompt_strategy,
)


def test_unicode_subscript_is_not_doubly_formatted():
    runs = canonical_text_runs(
        [{"text": "3", "source_text": "₃", "script": "subscript", "style": {}}],
        "₃", {"color": "#111111", "font_size": 48},
    )
    assert [(run["text"], run["source_text"], run["script"]) for run in runs] == [
        ("₃", "₃", "normal"),
    ]


def test_markup_subscript_keeps_native_script():
    runs = canonical_text_runs(
        [{"text": "3", "source_text": "_3", "script": "subscript", "style": {}}],
        "_3", {"color": "#111111", "font_size": 48},
    )
    assert [(run["text"], run["source_text"], run["script"]) for run in runs] == [
        ("3", "_3", "subscript"),
    ]


def test_representation_model_override_does_not_change_global_model():
    config = {
        "model": {"name": "base", "service_tier": "standard"},
        "representation": {"model_overrides": {"service_tier": "fast", "fast_mode": True}},
    }
    local = representation_model_config(config)
    assert local["model"] == {"name": "base", "service_tier": "fast", "fast_mode": True}
    assert config["model"] == {"name": "base", "service_tier": "standard"}
    assert representation_prompt_strategy(config) == {}


def test_representation_prompt_retains_only_figure_strategy_options():
    config = {"representation": {
        "strategy": "parallel_shards", "shard_count": 4,
        "model_overrides": {"service_tier": "fast"},
        "fidelity_guard": True,
        "geometry_precision_hint": True,
    }}
    assert representation_prompt_strategy(config) == {
        "strategy": "parallel_shards", "shard_count": 4,
    }
