"""Translate known legacy chart settings when opening a saved workspace."""

import re


def adapt_chart_filters(config: dict) -> None:
    filters = config.get("chart_filters")
    if not isinstance(filters, dict):
        return
    rules = []
    for column, selection in filters.items():
        if isinstance(selection, list):
            mode, values = "include", selection
        elif isinstance(selection, dict) and len(selection) == 1:
            mode, values = next(iter(selection.items()))
        else:
            return
        if mode not in {"include", "exclude"} or not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            return
        if mode == "include" and values:
            rules.append({"column": column, "operator": "in", "values": values})
        if mode == "exclude":
            rules.extend({"column": column, "operator": "not_equal", "value": value} for value in values)
    if rules:
        config["filter_rules"] = [*(config.get("filter_rules") or []), *rules]
    del config["chart_filters"]


def adapt_value_formats(config: dict) -> None:
    formats = config.get("value_formats")
    if not isinstance(formats, dict):
        return
    adapted = {}
    for column, specification in formats.items():
        if not isinstance(specification, str):
            return
        if specification == "%":
            digits = 0
        elif match := re.fullmatch(r"Percent\(([0-6])\)", specification):
            digits = int(match[1])
        elif match := re.fullmatch(r"0\.(0{1,6})%", specification):
            digits = len(match[1])
        else:
            return
        adapted[column] = {"style": "percentage", "digits": digits, "group_digits": True}
    if adapted:
        config["number_formats"] = {**adapted, **(config.get("number_formats") or {})}
    del config["value_formats"]


def adapt_stored_chart(config: dict) -> dict:
    if not isinstance(config, dict):
        return config

    # These settings have no effect with the values stored in older charts.
    adapted = {
        key: value for key, value in config.items()
        if not (
            (key == "calculated_fields" and value == {})
            or (key in {"group_remaining", "table_distinct"} and value is False)
        )
    }
    if adapted.get("group_remaining") is True:
        adapted.setdefault("include_other", True)
        del adapted["group_remaining"]
    adapt_chart_filters(adapted)
    adapt_value_formats(adapted)

    condition = adapted.get("indicator_condition")
    if adapted.get("chart_type") != "indicator" or not isinstance(condition, dict):
        return adapted
    column = condition.get("count_field")
    if (
        isinstance(column, str) and column
        and condition.get("max_count") == 1
        and set(condition) <= {"count_field", "max_count", "hide_zero", "distinct"}
    ):
        distinct, hide_zero = condition.get("distinct", True), condition.get("hide_zero")
        if distinct is True and hide_zero is False:
            del adapted["indicator_condition"]
            adapted["indicator_single_value_columns"] = list(dict.fromkeys([
                *adapted.get("indicator_single_value_columns", []), column
            ]))
        if distinct is False and isinstance(hide_zero, bool):
            del adapted["indicator_condition"]
            adapted.setdefault("indicator_row_limit", {"column": column, "hide_zero": hide_zero})
    return adapted


def adapt_stored_workspace(workspace: dict) -> dict:
    if not workspace.get("widgets"):
        return workspace
    return {**workspace, "widgets": [
        {**widget, "config": adapt_stored_chart(widget["config"])} if "config" in widget else widget
        for widget in workspace["widgets"]
    ]}
