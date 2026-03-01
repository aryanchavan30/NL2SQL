import logging
import re
import uuid
from typing import Any

from indexing.store import Document

logger = logging.getLogger("nl2sql")


def clean_display_name(display_name: str) -> str:
    if not display_name:
        return display_name

    prefix_invalid = set(
        "-&%=+'\"><#|!()*,/;[\\]^{}~0123456789\x00."
    )
    middle_invalid = set(
        "-&%=+'\"><#|!()/? [\\]^`{}~.*@$"
    )
    suffix_invalid = set(
        "-&%=+:'\"><#|!(),./@ [\\]^{}~"
    )

    result = list(display_name)
    prefix_prepended = False

    if len(result) > 0 and result[0] in prefix_invalid:
        if result[0].isdigit():
            result.insert(0, "_")
            prefix_prepended = True
        else:
            result[0] = "_"

    start_idx = 2 if prefix_prepended else 1
    end_idx = len(result) - 1
    for i in range(start_idx, end_idx):
        if result[i] in middle_invalid:
            result[i] = "_"

    if len(result) > 1 and result[-1] in suffix_invalid:
        result[-1] = "_"

    original_len = len(display_name)
    if original_len == 1:
        char = display_name[0]
        if char in prefix_invalid or char in suffix_invalid:
            result = ["_"]

    cleaned = "".join(result)
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned


class DDLChunker:
    """Converts MDL dict into TABLE_SCHEMA, TABLE_COLUMNS, FOREIGN_KEY, VIEW, METRIC documents."""

    def run(
        self,
        mdl_dict: dict[str, Any],
        column_batch_size: int = 50,
        project_id: str | None = None,
    ) -> list[Document]:
        models = mdl_dict.get("models", [])
        relationships = mdl_dict.get("relationships", [])
        views = mdl_dict.get("views", [])
        metrics = mdl_dict.get("metrics", [])

        chunks = (
            self._convert_models_and_relationships(
                models, relationships, column_batch_size
            )
            + self._convert_views(views)
            + self._convert_metrics(metrics)
        )

        meta_extra = {"project_id": project_id} if project_id else {}

        documents = []
        for chunk in chunks:
            documents.append(
                Document(
                    id=str(uuid.uuid4()),
                    content=chunk["payload"],
                    meta={
                        "type": "TABLE_SCHEMA",
                        "name": chunk["name"],
                        **meta_extra,
                    },
                )
            )
        return documents

    def _convert_models_and_relationships(
        self,
        models: list[dict],
        relationships: list[dict],
        column_batch_size: int,
    ) -> list[dict]:
        primary_keys_map = {
            model.get("name", ""): model.get("primaryKey", "") for model in models
        }

        result = []
        for model in models:
            result.extend(
                self._column_batch(model, relationships, primary_keys_map, column_batch_size)
            )
            result.append(self._model_command(model))
        return result

    def _model_command(self, model: dict) -> dict:
        properties = model.get("properties", {})
        model_properties = {
            "alias": clean_display_name(properties.get("displayName", "")),
            "description": properties.get("description", ""),
        }
        comment = f"\n/* {str(model_properties)} */\n"
        table_name = model["name"]
        payload = {
            "type": "TABLE",
            "comment": comment,
            "name": table_name,
        }
        return {"name": table_name, "payload": str(payload)}

    def _column_command(self, column: dict, model: dict) -> dict | None:
        if column.get("relationship"):
            return None

        if column.get("isHidden", False):
            return None

        comments = []

        # Properties comment
        props = column.get("properties", {})
        if props:
            comments.append(f"-- {str(props)}\n  ")

        # Calculated field comment
        if column.get("isCalculated", False):
            expr = column.get("expression", "")
            comments.append(f"-- This column is a Calculated Field\n  -- column expression: {expr}\n  ")

        return {
            "type": "COLUMN",
            "comment": "".join(comments),
            "name": column.get("name", ""),
            "data_type": column.get("type", ""),
            "is_primary_key": column.get("name", "") == model.get("primaryKey", ""),
        }

    def _relationship_command(
        self, relationship: dict, table_name: str, primary_keys_map: dict
    ) -> dict | None:
        condition = relationship.get("condition", "")
        join_type = relationship.get("joinType", "")
        models = relationship.get("models", [])

        if len(models) != 2 or table_name not in models:
            return None

        if join_type not in ["MANY_TO_ONE", "ONE_TO_MANY", "ONE_TO_ONE"]:
            return None

        is_source = table_name == models[0]
        related_table = models[1] if is_source else models[0]
        condition_parts = condition.split(" = ")
        if len(condition_parts) != 2:
            return None

        fk_column = condition_parts[0 if is_source else 1].split(".")[-1]
        related_pk = primary_keys_map.get(related_table, "id")
        fk_constraint = (
            f"FOREIGN KEY ({fk_column}) REFERENCES {related_table}({related_pk})"
        )

        return {
            "type": "FOREIGN_KEY",
            "comment": f'-- {{"condition": {condition}, "joinType": {join_type}}}\n  ',
            "constraint": fk_constraint,
            "tables": models,
        }

    def _column_batch(
        self,
        model: dict,
        relationships: list[dict],
        primary_keys_map: dict,
        column_batch_size: int,
    ) -> list[dict]:
        commands = [
            self._column_command(col, model) for col in model.get("columns", [])
        ] + [
            self._relationship_command(rel, model["name"], primary_keys_map)
            for rel in relationships
        ]
        filtered = [cmd for cmd in commands if cmd is not None]

        table_name = model["name"]
        return [
            {
                "name": table_name,
                "payload": str(
                    {
                        "type": "TABLE_COLUMNS",
                        "columns": filtered[i : i + column_batch_size],
                    }
                ),
            }
            for i in range(0, max(len(filtered), 1), column_batch_size)
            if filtered[i : i + column_batch_size]
        ]

    def _convert_views(self, views: list[dict]) -> list[dict]:
        result = []
        for view in views:
            payload = {
                "type": "VIEW",
                "comment": f"/* {view.get('properties', {})} */\n"
                if view.get("properties")
                else "",
                "name": view["name"],
                "statement": view.get("statement", ""),
            }
            result.append({"name": view["name"], "payload": str(payload)})
        return result

    def _convert_metrics(self, metrics: list[dict]) -> list[dict]:
        result = []
        for metric in metrics:
            dimensions = [
                {
                    "type": "COLUMN",
                    "comment": "-- This column is a dimension\n  ",
                    "name": dim.get("name", ""),
                    "data_type": dim.get("type", ""),
                }
                for dim in metric.get("dimension", [])
            ]
            measures = [
                {
                    "type": "COLUMN",
                    "comment": f"-- This column is a measure\n  -- expression: {m.get('expression', '')}\n  ",
                    "name": m.get("name", ""),
                    "data_type": m.get("type", ""),
                }
                for m in metric.get("measure", [])
            ]
            payload = {
                "type": "METRIC",
                "comment": f"\n/* This table is a metric */\n/* Metric Base Object: {metric.get('baseObject', '')} */\n",
                "name": metric["name"],
                "columns": dimensions + measures,
            }
            result.append({"name": metric["name"], "payload": str(payload)})
        return result


class TableDescriptionChunker:
    """Creates 1 document per model/metric/view with name, description, column names."""

    def run(
        self, mdl_dict: dict[str, Any], project_id: str | None = None
    ) -> list[Document]:
        meta_extra = {"project_id": project_id} if project_id else {}
        documents = []

        for model in mdl_dict.get("models", []):
            col_names = ", ".join(c.get("name", "") for c in model.get("columns", []))
            content = str({
                "name": model["name"],
                "description": model.get("properties", {}).get("description", ""),
                "columns": col_names,
            })
            documents.append(
                Document(
                    id=str(uuid.uuid4()),
                    content=content,
                    meta={
                        "type": "TABLE_DESCRIPTION",
                        "name": model["name"],
                        **meta_extra,
                    },
                )
            )

        for metric in mdl_dict.get("metrics", []):
            dims = [d.get("name", "") for d in metric.get("dimension", [])]
            measures = [m.get("name", "") for m in metric.get("measure", [])]
            col_names = ", ".join(dims + measures)
            content = str({
                "name": metric["name"],
                "description": metric.get("properties", {}).get("description", ""),
                "columns": col_names,
            })
            documents.append(
                Document(
                    id=str(uuid.uuid4()),
                    content=content,
                    meta={
                        "type": "TABLE_DESCRIPTION",
                        "name": metric["name"],
                        **meta_extra,
                    },
                )
            )

        for view in mdl_dict.get("views", []):
            content = str({
                "name": view["name"],
                "description": view.get("properties", {}).get("description", ""),
                "columns": "",
            })
            documents.append(
                Document(
                    id=str(uuid.uuid4()),
                    content=content,
                    meta={
                        "type": "TABLE_DESCRIPTION",
                        "name": view["name"],
                        **meta_extra,
                    },
                )
            )

        return documents


class ViewChunker:
    """Creates 1 document per view with historical queries + question as content."""

    def run(
        self, mdl_dict: dict[str, Any], project_id: str | None = None
    ) -> list[Document]:
        meta_extra = {"project_id": project_id} if project_id else {}
        documents = []

        for view in mdl_dict.get("views", []):
            properties = view.get("properties", {})
            historical_queries = properties.get("historical_queries", [])
            question = properties.get("question", "")
            content = " ".join(historical_queries + [question])

            documents.append(
                Document(
                    id=str(uuid.uuid4()),
                    content=content,
                    meta={
                        "summary": properties.get("summary", ""),
                        "statement": view.get("statement", ""),
                        "viewId": properties.get("viewId", ""),
                        **meta_extra,
                    },
                )
            )

        return documents


class SqlPairsConverter:
    """Creates 1 document per SQL pair (question -> SQL mapping)."""

    def run(
        self,
        sql_pairs: list[dict[str, str]],
        project_id: str | None = None,
    ) -> list[Document]:
        meta_extra = {"project_id": project_id} if project_id else {}
        documents = []

        for pair in sql_pairs:
            documents.append(
                Document(
                    id=str(uuid.uuid4()),
                    content=pair.get("question", ""),
                    meta={
                        "sql_pair_id": pair.get("id", str(uuid.uuid4())),
                        "sql": pair.get("sql", ""),
                        **meta_extra,
                    },
                )
            )

        return documents
